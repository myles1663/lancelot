import os
import json
import secrets

from src.core.onboarding_snapshot import OnboardingSnapshot, OnboardingState
from src.core import recovery_commands
from src.core.local_utility_setup import handle_local_utility_setup

# ---------------------------------------------------------------------------
# V16: Provider configuration — mirrors installer/src/constants.mjs
# ---------------------------------------------------------------------------
PROVIDERS = {
    "gemini": {
        "name": "Google Gemini",
        "env_var": "GEMINI_API_KEY",
        "env_provider": "gemini",
        "prefix": "AIza",
        "signup": "https://aistudio.google.com/apikey",
        "recommended": True,
        "description": "Generous free tier, fast models",
    },
    "openai": {
        "name": "OpenAI",
        "env_var": "OPENAI_API_KEY",
        "env_provider": "openai",
        "prefix": "sk-",
        "signup": "https://platform.openai.com/api-keys",
        "description": "GPT-4o, pay-as-you-go",
    },
    "anthropic": {
        "name": "Anthropic",
        "env_var": "ANTHROPIC_API_KEY",
        "env_provider": "anthropic",
        "prefix": "sk-ant-",
        "signup": "https://console.anthropic.com/",
        "description": "Claude, pay-as-you-go",
    },
    "xai": {
        "name": "xAI (Grok)",
        "env_var": "XAI_API_KEY",
        "env_provider": "xai",
        "prefix": "xai-",
        "signup": "https://console.x.ai/",
        "description": "Grok models, pay-as-you-go",
    },
}

# ---------------------------------------------------------------------------
# V16: Comms connector definitions — all supported messaging platforms
# ---------------------------------------------------------------------------
COMMS_CONNECTORS = {
    "telegram": {
        "name": "Telegram",
        "description": "Simple setup via BotFather",
        "recommended": True,
        "setup_type": "custom",  # Uses existing detailed flow
    },
    "google_chat": {
        "name": "Google Chat",
        "description": "Requires Google Cloud project",
        "setup_type": "custom",  # Uses existing ADC flow
    },
    "slack": {
        "name": "Slack",
        "description": "Slack workspace integration",
        "setup_type": "guided",
        "steps": [
            {
                "key": "slack_bot_token",
                "prompt": (
                    "**Slack Setup**\n\n"
                    "1. Go to [Slack API Apps](https://api.slack.com/apps) and create a new app\n"
                    "2. Under **OAuth & Permissions**, add these scopes:\n"
                    "   `channels:read`, `channels:history`, `chat:write`, `reactions:write`, `files:write`\n"
                    "3. Install the app to your workspace\n"
                    "4. Copy the **Bot User OAuth Token** (starts with `xoxb-`)\n\n"
                    "Paste your Bot Token below:"
                ),
                "env_var": "SLACK_BOT_TOKEN",
                "vault_key": "slack.bot_token",
                "prefix": "xoxb-",
            },
            {
                "key": "slack_channel",
                "prompt": (
                    "Token accepted.\n\n"
                    "Enter the **Channel ID** where Lancelot should listen.\n"
                    "(Right-click a channel > View channel details > copy the Channel ID at the bottom)"
                ),
                "env_var": "SLACK_CHANNEL_ID",
                "vault_key": None,
            },
        ],
    },
    "discord": {
        "name": "Discord",
        "description": "Discord server integration",
        "setup_type": "guided",
        "steps": [
            {
                "key": "discord_bot_token",
                "prompt": (
                    "**Discord Setup**\n\n"
                    "1. Go to [Discord Developer Portal](https://discord.com/developers/applications)\n"
                    "2. Create a **New Application**\n"
                    "3. Go to **Bot** tab > click **Add Bot**\n"
                    "4. Under **Token**, click **Copy**\n"
                    "5. Under **Privileged Gateway Intents**, enable **Message Content Intent**\n"
                    "6. Use the OAuth2 URL Generator to invite the bot to your server\n"
                    "   (scopes: `bot`; permissions: `Send Messages`, `Read Message History`)\n\n"
                    "Paste your Bot Token below:"
                ),
                "env_var": "DISCORD_BOT_TOKEN",
                "vault_key": "discord.bot_token",
            },
            {
                "key": "discord_channel_id",
                "prompt": (
                    "Token accepted.\n\n"
                    "Enter the **Channel ID** where Lancelot should operate.\n"
                    "(Enable Developer Mode in Discord settings, then right-click channel > Copy Channel ID)"
                ),
                "env_var": "DISCORD_CHANNEL_ID",
                "vault_key": None,
            },
        ],
    },
    "teams": {
        "name": "Microsoft Teams",
        "description": "Teams channel integration via Graph API",
        "setup_type": "guided",
        "steps": [
            {
                "key": "teams_token",
                "prompt": (
                    "**Microsoft Teams Setup**\n\n"
                    "1. Register an app in [Azure Portal](https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade)\n"
                    "2. Add API permissions: `ChannelMessage.Send`, `Chat.ReadWrite`, `Team.ReadBasic.All`\n"
                    "3. Create a client secret and generate an access token\n"
                    "4. Copy the **Access Token**\n\n"
                    "Paste your Microsoft Graph API access token below:"
                ),
                "env_var": "TEAMS_ACCESS_TOKEN",
                "vault_key": "teams.graph_token",
            },
            {
                "key": "teams_team_id",
                "prompt": (
                    "Token accepted.\n\n"
                    "Enter your **Team ID**.\n"
                    "(In Teams, click the three dots next to your team > Get link to team > extract the team ID from the URL)"
                ),
                "env_var": "TEAMS_TEAM_ID",
                "vault_key": None,
            },
        ],
    },
    "whatsapp": {
        "name": "WhatsApp Business",
        "description": "WhatsApp via Meta Cloud API",
        "setup_type": "guided",
        "steps": [
            {
                "key": "whatsapp_token",
                "prompt": (
                    "**WhatsApp Business Setup**\n\n"
                    "1. Go to [Meta for Developers](https://developers.facebook.com/apps/)\n"
                    "2. Create a Business app with WhatsApp product\n"
                    "3. In the WhatsApp section, get your **Permanent Access Token**\n"
                    "   (temporary tokens expire in 24 hours)\n\n"
                    "Paste your WhatsApp Access Token below:"
                ),
                "env_var": "WHATSAPP_ACCESS_TOKEN",
                "vault_key": "whatsapp.access_token",
            },
            {
                "key": "whatsapp_phone_id",
                "prompt": (
                    "Token accepted.\n\n"
                    "Enter your **Phone Number ID**.\n"
                    "(Found in your WhatsApp Business settings at Meta for Developers)"
                ),
                "env_var": "WHATSAPP_PHONE_NUMBER_ID",
                "vault_key": "whatsapp.phone_number_id",
            },
        ],
    },
    "email": {
        "name": "Email (SMTP)",
        "description": "Email via SMTP/IMAP",
        "setup_type": "guided",
        "steps": [
            {
                "key": "smtp_host",
                "prompt": (
                    "**Email (SMTP) Setup**\n\n"
                    "Enter your **SMTP Host** (e.g. `smtp.gmail.com`, `smtp.office365.com`):"
                ),
                "env_var": "SMTP_HOST",
                "vault_key": "email.smtp_host",
            },
            {
                "key": "smtp_port",
                "prompt": "Enter your **SMTP Port** (usually `587` for TLS or `465` for SSL):",
                "env_var": "SMTP_PORT",
                "vault_key": "email.smtp_port",
            },
            {
                "key": "smtp_username",
                "prompt": "Enter your **SMTP Username** (usually your email address):",
                "env_var": "SMTP_USERNAME",
                "vault_key": "email.smtp_username",
            },
            {
                "key": "smtp_password",
                "prompt": (
                    "Enter your **SMTP Password** or **App Password**.\n"
                    "(For Gmail, use an [App Password](https://myaccount.google.com/apppasswords))"
                ),
                "env_var": "SMTP_PASSWORD",
                "vault_key": "email.smtp_password",
            },
            {
                "key": "smtp_from",
                "prompt": "Enter the **From Address** (your email address):",
                "env_var": "SMTP_FROM_ADDRESS",
                "vault_key": "email.smtp_from_address",
            },
        ],
    },
    "sms": {
        "name": "SMS (Twilio)",
        "description": "SMS/MMS via Twilio",
        "setup_type": "guided",
        "steps": [
            {
                "key": "twilio_sid",
                "prompt": (
                    "**SMS (Twilio) Setup**\n\n"
                    "1. Sign up at [Twilio Console](https://console.twilio.com/)\n"
                    "2. Find your **Account SID** on the dashboard\n\n"
                    "Paste your Account SID below:"
                ),
                "env_var": "TWILIO_ACCOUNT_SID",
                "vault_key": "sms.account_sid",
            },
            {
                "key": "twilio_token",
                "prompt": "Enter your **Auth Token** (found next to Account SID on the Twilio dashboard):",
                "env_var": "TWILIO_AUTH_TOKEN",
                "vault_key": "sms.auth_token",
            },
            {
                "key": "twilio_from",
                "prompt": "Enter your **Twilio phone number** (e.g. `+15551234567`):",
                "env_var": "TWILIO_FROM_NUMBER",
                "vault_key": "sms.from_number",
            },
        ],
    },
}

# Default feature flags to write during FINAL_CHECKS (matches installer)
_DEFAULT_FEATURE_FLAGS = {
    "FEATURE_SOUL": "true",
    "FEATURE_SKILLS": "true",
    "FEATURE_HEALTH_MONITOR": "true",
    "FEATURE_SCHEDULER": "true",
    "FEATURE_AGENTIC_LOOP": "true",
    "FEATURE_LOCAL_AGENTIC": "true",
}


class OnboardingOrchestrator:
    def __init__(self, data_dir="/home/lancelot/data"):
        self.data_dir = data_dir
        self.user_file = os.path.join(data_dir, "USER.md")
        # .env is at the project root (mounted as /home/lancelot/app/.env in Docker)
        self.env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".env")
        self.fail_count = 0
        self.temp_data = {}  # Store transient data like webhook url before verification
        self.snapshot = OnboardingSnapshot(data_dir)
        self.state = self._determine_state()
        self._sync_snapshot()

    # ------------------------------------------------------------------
    # State management
    # ------------------------------------------------------------------

    def _sync_snapshot(self):
        """Sync dynamically determined state to the snapshot file."""
        # V27: Complete state map covering all onboarding steps
        state_map = {
            "WELCOME": OnboardingState.WELCOME,
            "FLAGSHIP_SELECTION": OnboardingState.FLAGSHIP_SELECTION,
            "HANDSHAKE": OnboardingState.CREDENTIALS_CAPTURE,
            "PROVIDER_MODE_SELECTION": OnboardingState.CREDENTIALS_CAPTURE,  # V27: shares credential phase
            "LOCAL_UTILITY_SETUP": OnboardingState.LOCAL_UTILITY_SETUP,
            "COMMS_SELECTION": OnboardingState.COMMS_SELECTION,
            "FINAL_CHECKS": OnboardingState.FINAL_CHECKS,
            "READY": OnboardingState.READY,
        }

        target_state = state_map.get(self.state)
        if target_state is None:
            return  # Intermediate state, don't overwrite

        # Respect active cooldowns
        if self.snapshot.state == OnboardingState.COOLDOWN and self.snapshot.is_in_cooldown():
            return

        if self.snapshot.state != target_state:
            updates = {}
            if self.state == "READY":
                provider = self._get_env_value("LANCELOT_PROVIDER")
                if provider:
                    updates["flagship_provider"] = provider
                elif os.getenv("GEMINI_API_KEY"):
                    updates["flagship_provider"] = "gemini"
                elif os.getenv("OPENAI_API_KEY"):
                    updates["flagship_provider"] = "openai"
                elif os.getenv("ANTHROPIC_API_KEY"):
                    updates["flagship_provider"] = "anthropic"
                elif os.getenv("XAI_API_KEY"):
                    updates["flagship_provider"] = "xai"
                updates["credential_status"] = "verified"
                updates["local_model_status"] = "verified"

            self.snapshot.transition(target_state, **updates)

    def _determine_state(self):
        """Determines current state based on filesystem/env — self-healing on restart.

        V27 flow: WELCOME -> FLAGSHIP_SELECTION -> HANDSHAKE -> PROVIDER_MODE_SELECTION
                  -> LOCAL_UTILITY_SETUP -> COMMS_SELECTION -> [comms sub-states]
                  -> FINAL_CHECKS -> READY
        """
        # v4: COOLDOWN replaces permanent LOCKDOWN — check snapshot first
        if self.snapshot.state == OnboardingState.COOLDOWN:
            if self.snapshot.is_in_cooldown():
                return "COOLDOWN"

        # Step 1: USER.md must exist (identity bonded)
        if not os.path.exists(self.user_file):
            return "WELCOME"

        # Step 2: V16 — Provider must be selected
        provider = self._get_env_value("LANCELOT_PROVIDER")
        if not provider:
            provider = self._infer_provider_from_keys()
            if not provider:
                return "FLAGSHIP_SELECTION"

        # Step 3: API key (or ADC for Gemini) must exist for selected provider
        provider_info = PROVIDERS.get(provider, {})
        env_var = provider_info.get("env_var", "")
        api_key = self._get_env_value(env_var) if env_var else None
        if not api_key:
            api_key = os.getenv(env_var)

        adc_exists = False
        if provider == "gemini":
            adc_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
            adc_exists = adc_path and os.path.exists(adc_path)
            if not adc_exists:
                default_adc = "/home/lancelot/.config/gcloud/application_default_credentials.json"
                if os.path.exists(default_adc):
                    adc_exists = True

        if not api_key and not adc_exists:
            return "HANDSHAKE"

        # Step 3.5: V27 — Provider mode (SDK/API) must be selected
        provider_mode = self._get_env_value("LANCELOT_PROVIDER_MODE")
        if not provider_mode:
            return "PROVIDER_MODE_SELECTION"

        # Step 4: V16 — Local model must be verified
        if self.snapshot.local_model_status != "verified":
            return "LOCAL_UTILITY_SETUP"

        # Step 5: Comms must be configured (or explicitly skipped)
        comms_type = self._get_env_value("LANCELOT_COMMS_TYPE")
        if not comms_type:
            comms_type = os.getenv("LANCELOT_COMMS_TYPE")
        if not comms_type:
            return "COMMS_SELECTION"

        # Step 6: V16 — Security tokens must exist
        if not self._has_security_tokens():
            return "FINAL_CHECKS"

        return "READY"

    # ------------------------------------------------------------------
    # Env file helpers
    # ------------------------------------------------------------------

    def _get_env_value(self, key):
        """Read a value from .env file (not just os.environ)."""
        val = os.getenv(key)
        if val:
            return val
        if os.path.exists(self.env_file):
            try:
                with open(self.env_file, "r") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith(f"{key}="):
                            return line.split("=", 1)[1].strip()
            except Exception:
                pass
        return None

    def _infer_provider_from_keys(self):
        """Backward compat: infer provider from which API key exists."""
        for provider_id, info in PROVIDERS.items():
            if self._get_env_value(info["env_var"]):
                return provider_id
        adc_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        if adc_path and os.path.exists(adc_path):
            return "gemini"
        default_adc = "/home/lancelot/.config/gcloud/application_default_credentials.json"
        if os.path.exists(default_adc):
            return "gemini"
        return None

    def _has_security_tokens(self):
        """Check if all three security tokens exist in .env or env."""
        for key in ("LANCELOT_OWNER_TOKEN", "LANCELOT_API_TOKEN", "LANCELOT_VAULT_KEY"):
            if not self._get_env_value(key):
                return False
        return True

    def _write_env_values(self, values: dict, section_comment: str = None):
        """Append key=value pairs to .env file. Only writes keys not already present."""
        to_write = {}
        for key, val in values.items():
            if not self._get_env_value(key):
                to_write[key] = val

        if not to_write:
            return

        try:
            with open(self.env_file, "a") as f:
                if section_comment:
                    f.write(f"\n# {section_comment}\n")
                for key, val in to_write.items():
                    f.write(f"{key}={val}\n")
                    os.environ[key] = val
        except Exception as e:
            print(f"Error writing to .env: {e}")

    # ------------------------------------------------------------------
    # Cooldown
    # ------------------------------------------------------------------

    def _enter_cooldown(self, seconds: int = 300, reason: str = "Too many failures"):
        """Enter time-based cooldown (v4: replaces permanent LOCKDOWN)."""
        self.snapshot.enter_cooldown(seconds, reason)
        self.state = "COOLDOWN"

    # ------------------------------------------------------------------
    # WELCOME state
    # ------------------------------------------------------------------

    def _bond_identity(self, user: str) -> str:
        """Creates USER.md and bonds identity."""
        try:
            with open(self.user_file, "w") as f:
                f.write(f"# User Profile\n- Name: {user}\n- Role: Commander\n- Bonded: True")

            self.state = "FLAGSHIP_SELECTION"
            return (f"Welcome, {user}. I've bonded to your identity.\n\n"
                    + self._flagship_selection_prompt())
        except Exception as e:
            return f"Error bonding identity: {e}"

    # ------------------------------------------------------------------
    # V16: FLAGSHIP_SELECTION state
    # ------------------------------------------------------------------

    def _flagship_selection_prompt(self) -> str:
        """Render the provider selection menu."""
        return (
            "**LLM Provider Selection**\n"
            "Choose your flagship AI provider:\n\n"
            "[1] Google Gemini (Recommended) — Generous free tier, fast models\n"
            "    Get a key at: https://aistudio.google.com/apikey\n\n"
            "[2] OpenAI — GPT-4o, pay-as-you-go\n"
            "    Get a key at: https://platform.openai.com/api-keys\n\n"
            "[3] Anthropic — Claude, pay-as-you-go\n"
            "    Get a key at: https://console.anthropic.com/\n\n"
            "[4] xAI (Grok) — Grok models, pay-as-you-go\n"
            "    Get a key at: https://console.x.ai/\n\n"
            "Enter the number of your choice:"
        )

    def _handle_flagship_selection(self, text: str) -> str:
        """Handles FLAGSHIP_SELECTION state — user picks provider."""
        choice = text.strip()

        provider_map = {
            "1": "gemini", "gemini": "gemini", "google": "gemini",
            "2": "openai", "openai": "openai",
            "3": "anthropic", "anthropic": "anthropic", "claude": "anthropic",
            "4": "xai", "xai": "xai", "grok": "xai",
        }

        provider_id = provider_map.get(choice.lower())
        if not provider_id:
            return "Invalid selection.\n\n" + self._flagship_selection_prompt()

        self.temp_data["provider"] = provider_id
        provider = PROVIDERS[provider_id]
        self.state = "HANDSHAKE"

        msg = f"**{provider['name']} Selected.**\n\n"
        msg += "**API Key Required**\n"
        msg += f"Get your key at: [{provider['name']}]({provider['signup']})\n\n"
        msg += f"Paste your API key below (starts with `{provider['prefix']}...`)."

        if provider_id == "gemini":
            msg += ("\n\nAlternatively, type **'scan'** to detect Google Cloud "
                    "Application Default Credentials (advanced).")

        return msg

    # ------------------------------------------------------------------
    # HANDSHAKE (CREDENTIALS_CAPTURE) state
    # ------------------------------------------------------------------

    def _handle_auth_options(self, text: str) -> str:
        """Handles HANDSHAKE state — user provides API key."""
        stripped = text.strip()
        provider_id = self.temp_data.get("provider")

        if not provider_id:
            self.state = "FLAGSHIP_SELECTION"
            return ("Provider selection not found. Let's start there.\n\n"
                    + self._flagship_selection_prompt())

        provider = PROVIDERS.get(provider_id, PROVIDERS["gemini"])

        if stripped.lower() == "scan" and provider_id == "gemini":
            result = self._verify_oauth_creds()
            if self.state == "COMMS_CHAT_SCAN":
                self.state = "COMMS_SELECTION"
                return ("**Identity Verified.** (Google ADC detected)\n\n"
                        + self._comms_selection_prompt())
            return result

        return self._verify_api_key(stripped)

    def _verify_api_key(self, text: str) -> str:
        """Verifies, live-validates, and saves API Key."""
        key = text.strip()
        provider_id = self.temp_data.get("provider", "gemini")
        provider = PROVIDERS.get(provider_id, PROVIDERS["gemini"])

        expected_prefix = provider["prefix"]
        if not key.startswith(expected_prefix):
            if provider_id == "gemini" and key.startswith("AI"):
                pass
            else:
                return (f"Invalid key format for {provider['name']}. "
                        f"Expected prefix: `{expected_prefix}`\n\n"
                        f"Get your key at: {provider['signup']}\n"
                        "Paste your API key:")

        validation = self._validate_api_key_live(provider_id, key)
        if not validation.get("valid"):
            self.fail_count += 1
            if self.fail_count >= 5:
                self._enter_cooldown(300, "Too many failed API key attempts")
                return "Too many failed attempts. System in cooldown for 5 minutes."
            error = validation.get("error", "Unknown validation error")
            return (f"**API Key Invalid**\n\n"
                    f"{error}\n\n"
                    f"Please check your key and try again.\n"
                    f"Get a new key at: {provider['signup']}")

        try:
            env_var = provider["env_var"]
            self._write_env_values({
                env_var: key,
                "LANCELOT_PROVIDER": provider_id,
            }, section_comment="LLM Provider (V16)")

            self.fail_count = 0

            msg = f"**API Key Verified.** ({provider['name']})\n\n"

            warning = validation.get("warning")
            if warning:
                msg += f"*Note: {warning}*\n\n"

            self.state = "PROVIDER_MODE_SELECTION"
            msg += self._provider_mode_prompt()
            return msg

        except Exception as e:
            return f"Error saving API Key: {e}"

    def _validate_api_key_live(self, provider: str, key: str) -> dict:
        """Live HTTP probe to validate API key. Non-blocking on network errors."""
        import requests
        try:
            if provider == "gemini":
                r = requests.get(
                    f"https://generativelanguage.googleapis.com/v1beta/models?key={key}",
                    timeout=10,
                )
                if r.ok:
                    return {"valid": True}
                if r.status_code in (400, 403):
                    return {"valid": False, "error": "Invalid API key — rejected by Google"}
                return {"valid": False, "error": f"Unexpected response (HTTP {r.status_code})"}

            elif provider == "openai":
                r = requests.get(
                    "https://api.openai.com/v1/models",
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=10,
                )
                if r.ok:
                    return {"valid": True}
                if r.status_code == 401:
                    return {"valid": False, "error": "Invalid API key — rejected by OpenAI"}
                return {"valid": False, "error": f"Unexpected response (HTTP {r.status_code})"}

            elif provider == "anthropic":
                r = requests.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": "claude-3-5-haiku-latest",
                        "max_tokens": 1,
                        "messages": [{"role": "user", "content": "hi"}],
                    },
                    timeout=10,
                )
                if r.status_code == 401:
                    return {"valid": False, "error": "Invalid API key — rejected by Anthropic"}
                return {"valid": True}

            elif provider == "xai":
                r = requests.get(
                    "https://api.x.ai/v1/models",
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=10,
                )
                if r.ok:
                    return {"valid": True}
                if r.status_code == 401:
                    return {"valid": False, "error": "Invalid API key — rejected by xAI"}
                return {"valid": False, "error": f"Unexpected response (HTTP {r.status_code})"}

            return {"valid": False, "error": f"Unknown provider: {provider}"}

        except Exception as e:
            return {"valid": True, "warning": f"Could not reach {provider} API to validate: {e}"}

    # ------------------------------------------------------------------
    # V27: PROVIDER_MODE_SELECTION state — SDK vs API
    # ------------------------------------------------------------------

    def _provider_mode_prompt(self) -> str:
        """Render the SDK/API mode selection menu."""
        provider_id = self.temp_data.get("provider", "")
        provider_name = PROVIDERS.get(provider_id, {}).get("name", provider_id)
        return (
            f"**{provider_name} — Connection Mode**\n\n"
            "[1] SDK Mode (Recommended) — Full Python SDK with extended thinking, "
            "streaming, and native tool calling\n\n"
            "[2] API Mode — Lightweight REST API calls. Fewer features but lower "
            "overhead\n\n"
            "Enter your choice:"
        )

    def _handle_provider_mode(self, text: str) -> str:
        """Handles PROVIDER_MODE_SELECTION state — user picks SDK or API."""
        choice = text.strip().lower()
        mode_map = {
            "1": "sdk", "sdk": "sdk",
            "2": "api", "api": "api",
        }
        mode = mode_map.get(choice)
        if not mode:
            return "Invalid selection.\n\n" + self._provider_mode_prompt()

        self._write_env_values(
            {"LANCELOT_PROVIDER_MODE": mode},
            "Provider Mode (V27)",
        )
        self.state = "LOCAL_UTILITY_SETUP"
        return (
            f"**{mode.upper()} mode selected.**\n\n"
            "Proceeding to local model setup..."
        )

    # ------------------------------------------------------------------
    # ADC / OAuth (Gemini only)
    # ------------------------------------------------------------------

    def _verify_oauth_creds(self) -> str:
        """Checks for ADC file presence and provides complete setup walkthrough."""
        adc_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        default_adc = "/home/lancelot/.config/gcloud/application_default_credentials.json"
        adc_exists = (adc_path and os.path.exists(adc_path)) or os.path.exists(default_adc)

        if adc_exists:
            try:
                self._write_env_values({
                    "LANCELOT_AUTH_MODE": "OAUTH",
                    "LANCELOT_PROVIDER": "gemini",
                }, section_comment="Google ADC Auth (V16)")

                self.state = "COMMS_CHAT_SCAN"

                return (
                    "**Identity Verified.** (Google ADC detected)\n\n"
                    "---\n"
                    "## Google Chat Setup Walkthrough\n\n"
                    "Before I can connect to Google Chat, please complete these steps:\n\n"
                    "### Step 1: Enable the Google Chat API\n"
                    "1. Go to: [Google Cloud Console - Chat API](https://console.cloud.google.com/apis/library/chat.googleapis.com)\n"
                    "2. Click **'Enable'**\n"
                    "3. If prompted, select or create a project\n\n"
                    "### Step 2: Authorize with Chat Scopes\n"
                    "Open a terminal on your **host machine** (NOT inside Docker) and run:\n\n"
                    "```\n"
                    "gcloud auth application-default login --scopes=https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/chat.messages,https://www.googleapis.com/auth/chat.spaces.readonly,https://www.googleapis.com/auth/generative-language.retriever\n"
                    "```\n\n"
                    "This will open a browser window. Sign in with your Google account.\n\n"
                    "### Step 3: Create a Google Chat Space\n"
                    "1. Open [Google Chat](https://chat.google.com)\n"
                    "2. Click the **'+'** button next to 'Spaces'\n"
                    "3. Select **'Create a space'**\n"
                    "4. Name it **'Lancelot'** (or any name you prefer)\n"
                    "5. Click **'Create'**\n\n"
                    "### Step 4: Restart Lancelot\n"
                    "After completing the above steps, **restart Lancelot** to reload credentials.\n\n"
                    "---\n"
                    "When ready, type **'scan'** to search for your spaces.\n"
                    "Type **'skip'** to configure this later."
                )
            except Exception as e:
                return f"Error saving auth mode: {e}"
        else:
            return (
                "**Google Credentials Not Found**\n\n"
                "---\n"
                "## Complete Google Chat Setup\n\n"
                "Follow these steps to connect Lancelot to Google Chat:\n\n"
                "### Step 1: Install Google Cloud CLI\n"
                "If you haven't already, install the gcloud CLI:\n"
                "[Download Google Cloud CLI](https://cloud.google.com/sdk/docs/install)\n\n"
                "### Step 2: Login to Google Cloud\n"
                "Open a terminal and run:\n"
                "```\n"
                "gcloud auth login\n"
                "```\n\n"
                "### Step 3: Enable the Chat API\n"
                "Go to: [Enable Chat API](https://console.cloud.google.com/apis/library/chat.googleapis.com)\n"
                "Click **'Enable'**\n\n"
                "### Step 4: Create Application Default Credentials\n"
                "Run this command with the required scopes:\n"
                "```\n"
                "gcloud auth application-default login --scopes=https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/chat.messages,https://www.googleapis.com/auth/chat.spaces.readonly,https://www.googleapis.com/auth/generative-language.retriever\n"
                "```\n\n"
                "### Step 5: Create a Chat Space\n"
                "1. Open [Google Chat](https://chat.google.com)\n"
                "2. Click '+' > 'Create a space'\n"
                "3. Name it 'Lancelot'\n\n"
                "### Step 6: Restart Lancelot\n"
                "Close and reopen Lancelot to pick up the new credentials.\n\n"
                "---\n"
                "After completing these steps, restart Lancelot and select Google Chat again."
            )

    def _calibrate(self) -> str:
        """Mock calibration step."""
        pass

    # ------------------------------------------------------------------
    # V16: COMMS_SELECTION — all connectors
    # ------------------------------------------------------------------

    def _comms_selection_prompt(self) -> str:
        """Render the full comms selection menu with all connectors."""
        return (
            "**Secure Comms Link**\n"
            "Select your communication channel:\n\n"
            "**Messaging (Bidirectional)**\n"
            "[1] Telegram (Recommended) — Simple setup via BotFather\n"
            "[2] Google Chat — Requires Google Cloud project\n"
            "[3] Slack — Slack workspace integration\n"
            "[4] Discord — Discord server integration\n"
            "[5] Microsoft Teams — Teams via Graph API\n\n"
            "**Notifications & Outreach**\n"
            "[6] WhatsApp Business — WhatsApp via Meta Cloud API\n"
            "[7] Email (SMTP) — Email via SMTP/IMAP\n"
            "[8] SMS (Twilio) — SMS/MMS via Twilio\n\n"
            "[9] Skip (Configure later in the War Room)"
        )

    def _handle_comms_selection(self, text: str) -> str:
        """Handles comms connector selection — V16: all connectors."""
        choice = text.strip().lower()

        # Map user input to connector ID
        selection_map = {
            "1": "telegram", "telegram": "telegram",
            "2": "google_chat", "google chat": "google_chat", "google": "google_chat", "gchat": "google_chat",
            "3": "slack", "slack": "slack",
            "4": "discord", "discord": "discord",
            "5": "teams", "microsoft teams": "teams", "ms teams": "teams", "teams": "teams",
            "6": "whatsapp", "whatsapp": "whatsapp",
            "7": "email", "email": "email", "smtp": "email",
            "8": "sms", "sms": "sms", "twilio": "sms",
            "9": "skip", "skip": "skip",
        }

        connector_id = selection_map.get(choice)
        if not connector_id:
            return "Invalid selection.\n\n" + self._comms_selection_prompt()

        # Skip
        if connector_id == "skip":
            self._write_env_values({"LANCELOT_COMMS_TYPE": "none"}, "Communications (skipped)")
            self.state = "FINAL_CHECKS"
            return self._handle_final_checks()

        self.temp_data["comms_type"] = connector_id

        # --- Telegram: existing detailed flow ---
        if connector_id == "telegram":
            self.state = "COMMS_TELEGRAM_TOKEN"
            return (
                "**Telegram Selected.**\n\n"
                "**Setup Instructions:**\n"
                "1. Open Telegram and search for **@BotFather**\n"
                "2. Send `/newbot` and follow the prompts to create your bot\n"
                "   [BotFather Guide](https://core.telegram.org/bots/features#botfather)\n"
                "3. Copy the **Bot Token** BotFather gives you\n"
                "4. Paste your **Bot Token** below."
            )

        # --- Google Chat: existing ADC flow ---
        if connector_id == "google_chat":
            self.state = "COMMS_ADC_CHECK"
            return self._verify_oauth_creds()

        # --- Guided setup connectors (Slack, Discord, Teams, WhatsApp, Email, SMS) ---
        connector = COMMS_CONNECTORS.get(connector_id)
        if connector and connector.get("setup_type") == "guided":
            self.temp_data["guided_step"] = 0
            self.state = "COMMS_GUIDED_SETUP"
            # Return the first step's prompt
            first_step = connector["steps"][0]
            return first_step["prompt"]

        return "Invalid selection.\n\n" + self._comms_selection_prompt()

    # ------------------------------------------------------------------
    # V16: Guided connector setup (generic multi-step flow)
    # ------------------------------------------------------------------

    def _handle_guided_setup(self, text: str) -> str:
        """Handles guided multi-step connector credential collection."""
        connector_id = self.temp_data.get("comms_type")
        connector = COMMS_CONNECTORS.get(connector_id)
        if not connector or connector.get("setup_type") != "guided":
            self.state = "COMMS_SELECTION"
            return "Configuration error. Returning to comms selection.\n\n" + self._comms_selection_prompt()

        steps = connector["steps"]
        step_idx = self.temp_data.get("guided_step", 0)

        if step_idx >= len(steps):
            return self._finish_guided_setup()

        current_step = steps[step_idx]
        value = text.strip()

        # Validate prefix if specified
        prefix = current_step.get("prefix")
        if prefix and not value.startswith(prefix):
            return f"Invalid format. Expected value starting with `{prefix}`.\n\nPlease try again:"

        # Store the value
        self.temp_data[current_step["key"]] = value

        # Move to next step
        step_idx += 1
        self.temp_data["guided_step"] = step_idx

        if step_idx < len(steps):
            # Return next step's prompt
            return steps[step_idx]["prompt"]
        else:
            # All steps collected — finish setup
            return self._finish_guided_setup()

    def _finish_guided_setup(self) -> str:
        """Write collected credentials to .env and enable the connector."""
        connector_id = self.temp_data.get("comms_type")
        connector = COMMS_CONNECTORS.get(connector_id)
        if not connector:
            self.state = "COMMS_SELECTION"
            return "Configuration error.\n\n" + self._comms_selection_prompt()

        steps = connector["steps"]
        env_values = {"LANCELOT_COMMS_TYPE": connector_id}

        for step in steps:
            key = step["key"]
            env_var = step.get("env_var")
            value = self.temp_data.get(key, "")
            if env_var and value:
                env_values[env_var] = value

        # Enable the connector feature flag
        env_values["FEATURE_CONNECTORS"] = "true"

        self._write_env_values(env_values, f"Communications — {connector['name']}")

        # Create restart flag
        flags_dir = os.path.join(self.data_dir, "FLAGS")
        os.makedirs(flags_dir, exist_ok=True)
        with open(os.path.join(flags_dir, "RESTART_REQUIRED"), "w") as f:
            f.write("CONFIG_UPDATED")

        # Advance to FINAL_CHECKS
        self.state = "FINAL_CHECKS"
        return (
            f"**{connector['name']} Configured.**\n\n"
            "Credentials saved. Proceeding to final checks...\n\n"
            + self._handle_final_checks()
        )

    # ------------------------------------------------------------------
    # Google Chat scan/select (existing flow)
    # ------------------------------------------------------------------

    def _handle_chat_scan(self, text: str) -> str:
        """Scans for Google Chat spaces."""
        cmd = text.strip().lower()
        if cmd == "skip":
            self._write_env_values({"LANCELOT_COMMS_TYPE": "none"}, "Communications (skipped)")
            self.state = "FINAL_CHECKS"
            return self._handle_final_checks()

        from chat_poller import ChatPoller

        poller = ChatPoller(self.data_dir)
        spaces = poller.list_spaces()

        if not spaces:
            return (
                "**No Spaces Found.**\n\n"
                "This usually means the **Google Chat API** isn't set up correctly.\n\n"
                "**Step 1: Enable Chat API**\n"
                "[Enable Chat API in Cloud Console](https://console.cloud.google.com/apis/library/chat.googleapis.com)\n\n"
                "**Step 2: Authenticate with Chat Scopes**\n"
                "Run this command on your **host machine** (not in Docker):\n"
                "```\n"
                "gcloud auth application-default login --scopes=https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/chat.messages,https://www.googleapis.com/auth/chat.spaces.readonly,https://www.googleapis.com/auth/generative-language.retriever\n"
                "```\n\n"
                "**Step 3: Create a Chat Space**\n"
                "Open [Google Chat](https://chat.google.com), create a new Space, and name it 'Lancelot'.\n\n"
                "**Step 4: Restart Lancelot**\n"
                "After completing the above, restart Lancelot and type **'scan'** again.\n\n"
                "Type **'retry'** to scan again, or **'skip'** to configure later."
            )

        self.temp_data["available_spaces"] = spaces
        self.state = "COMMS_CHAT_SELECT"

        msg = ["**Spaces Found:**\n"]
        for idx, space in enumerate(spaces):
            display_name = space.get('displayName', 'Unnamed Space')
            space_type = space.get('type', 'UNKNOWN')
            msg.append(f"[{idx+1}] {display_name} ({space_type})")

        msg.append("\nEnter the number of the Space to bond with:")
        return "\n".join(msg)

    def _handle_chat_select(self, text: str) -> str:
        """Handles space selection."""
        try:
            idx = int(text.strip()) - 1
            spaces = self.temp_data.get("available_spaces", [])

            if 0 <= idx < len(spaces):
                selected_space = spaces[idx]
                space_name = selected_space['name']
                display_name = selected_space.get('displayName')

                self.temp_data["chat_space_name"] = space_name
                self.temp_data["chat_display_name"] = display_name

                return self._initiate_handshake("google_chat")
            else:
                return "Invalid number. Try again."
        except ValueError:
            return "Please enter a number."

    # ------------------------------------------------------------------
    # Telegram comms setup (existing flow)
    # ------------------------------------------------------------------

    def _handle_telegram_token(self, text: str) -> str:
        token = text.strip()
        if len(token) < 20 or ":" not in token:
            return "Invalid Token format. It typically looks like `123456:ABC-DEF...`. Try again."
        self.temp_data["telegram_token"] = token
        self.state = "COMMS_TELEGRAM_CHAT"
        return "Token Accepted.\n\nNow, please enter your **Chat ID** (user or group ID).\n(You can use @userinfobot to find it)."

    def _handle_telegram_chat(self, text: str) -> str:
        chat_id = text.strip()
        self.temp_data["telegram_chat_id"] = chat_id
        return self._initiate_handshake("telegram")

    # ------------------------------------------------------------------
    # Comms verification handshake (Telegram + Google Chat)
    # ------------------------------------------------------------------

    def _initiate_handshake(self, provider: str) -> str:
        """Sends verification code via provider."""
        import random
        import string
        import requests
        from chat_poller import ChatPoller

        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        self.temp_data["verification_code"] = code

        msg = f"Lancelot Handshake Check\n\nYour verification code is: {code}"

        try:
            if provider == "google_chat":
                poller = ChatPoller(self.data_dir)
                poller.send_message(msg, self.temp_data["chat_space_name"])

            elif provider == "telegram":
                token = self.temp_data["telegram_token"]
                chat_id = self.temp_data["telegram_chat_id"]
                url = f"https://api.telegram.org/bot{token}/sendMessage"
                resp = requests.post(url, json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"}, timeout=10)

                if resp.status_code != 200:
                    result = resp.json() if resp.text else {}
                    error_desc = result.get("description", "Unknown error")
                    return (f"**Telegram Send Failed**\n\n"
                            f"Error: {error_desc}\n\n"
                            "**Common Fixes:**\n"
                            "1. Make sure you've started a conversation with your bot (send `/start` to it)\n"
                            "2. Verify the Chat ID is correct (use @userinfobot)\n"
                            "3. Check the Bot Token is valid\n\n"
                            "Type 'retry' to try again.")

            self.state = "COMMS_VERIFY"
            return (f"**Handshake Initiated.**\n"
                    f"I have sent a code to your {provider} ({self.temp_data.get('chat_display_name', self.temp_data.get('telegram_chat_id', ''))}).\n"
                    "Please enter the 6-character code here to verify the link.")

        except requests.exceptions.Timeout:
            return "**Connection Timeout** - Could not reach service. Check internet connection."
        except Exception as e:
            return f"**Connection Failed:** {e}"

    def _verify_handshake(self, text: str) -> str:
        """Verifies the code entered by the user."""
        input_code = text.strip().upper()
        expected_code = self.temp_data.get("verification_code")

        if input_code == expected_code:
            try:
                comms_values = {"LANCELOT_COMMS_TYPE": self.temp_data['comms_type']}
                if self.temp_data['comms_type'] == 'google_chat':
                    comms_values["LANCELOT_CHAT_SPACE_NAME"] = self.temp_data['chat_space_name']
                elif self.temp_data['comms_type'] == 'telegram':
                    comms_values["LANCELOT_TELEGRAM_TOKEN"] = self.temp_data['telegram_token']
                    comms_values["LANCELOT_TELEGRAM_CHAT_ID"] = self.temp_data['telegram_chat_id']

                self._write_env_values(comms_values, "Communications")

                flags_dir = os.path.join(self.data_dir, "FLAGS")
                os.makedirs(flags_dir, exist_ok=True)
                with open(os.path.join(flags_dir, "RESTART_REQUIRED"), "w") as f:
                    f.write("CONFIG_UPDATED")

                self.state = "FINAL_CHECKS"
                return ("**Handshake Verified.**\n"
                        "Secure Comms Link established.\n\n"
                        + self._handle_final_checks())
            except Exception as e:
                return f"Error saving configuration: {e}"
        else:
            self.fail_count += 1
            if self.fail_count >= 5:
                self._enter_cooldown(300, "Too many failed verification attempts")
                return "Too many failed attempts. System in cooldown for 5 minutes."
            return "Verification Failed. Code does not match. Try again."

    # ------------------------------------------------------------------
    # V16: FINAL_CHECKS state
    # ------------------------------------------------------------------

    def _handle_final_checks(self) -> str:
        """Auto-generate missing config, display summary, advance to READY."""
        generated = []

        # 1. Generate security tokens if missing
        tokens = {}
        for token_name in ("LANCELOT_OWNER_TOKEN", "LANCELOT_API_TOKEN", "LANCELOT_VAULT_KEY"):
            if not self._get_env_value(token_name):
                tokens[token_name] = secrets.token_urlsafe(32)
        if tokens:
            self._write_env_values(tokens, "Security Tokens (auto-generated — keep secret)")
            generated.append(f"Security tokens generated ({len(tokens)} tokens)")

        # 2. Write default feature flags if missing
        flags_written = {}
        for flag, val in _DEFAULT_FEATURE_FLAGS.items():
            if not self._get_env_value(flag):
                flags_written[flag] = val
        if flags_written:
            self._write_env_values(flags_written, "Feature Flags")
            generated.append(f"Feature flags configured ({len(flags_written)} flags)")

        # 3. Ensure LANCELOT_PROVIDER is set
        if not self._get_env_value("LANCELOT_PROVIDER"):
            provider = self._infer_provider_from_keys()
            if provider:
                self._write_env_values({"LANCELOT_PROVIDER": provider})
                generated.append(f"Provider set to {provider}")

        # Build summary
        provider = self._get_env_value("LANCELOT_PROVIDER") or "unknown"
        provider_name = PROVIDERS.get(provider, {}).get("name", provider)
        comms = self._get_env_value("LANCELOT_COMMS_TYPE") or "none"
        comms_display = COMMS_CONNECTORS.get(comms, {}).get("name", comms)

        provider_mode = self._get_env_value("LANCELOT_PROVIDER_MODE") or "sdk"

        msg = "**Final Configuration Complete**\n\n"
        msg += "**System Summary:**\n"
        msg += f"- LLM Provider: {provider_name} ({provider_mode.upper()} mode)\n"
        msg += f"- Local Model: Verified\n"
        msg += f"- Communications: {comms_display}\n"
        msg += f"- Security: Tokens configured\n"
        msg += f"- Feature Flags: Set\n"

        if generated:
            msg += f"\n*Auto-configured: {', '.join(generated)}*\n"

        self._complete_onboarding()

        msg += ("\n**Lancelot is now operational.** How may I serve you, Commander?\n\n"
                "*Note: A restart may be required to activate all settings.*")

        flags_dir = os.path.join(self.data_dir, "FLAGS")
        os.makedirs(flags_dir, exist_ok=True)
        with open(os.path.join(flags_dir, "RESTART_REQUIRED"), "w") as f:
            f.write("ONBOARDING_COMPLETE")

        return msg

    def _complete_onboarding(self):
        """Marks onboarding as complete in USER.md."""
        try:
            with open(self.user_file, "a") as f:
                f.write("\n- OnboardingComplete: True")
            self.state = "READY"
        except Exception as e:
            print(f"Error marking complete: {e}")

    # ------------------------------------------------------------------
    # Main state machine
    # ------------------------------------------------------------------

    def process(self, user: str, text: str) -> str:
        """Main state machine processor."""
        # --- Global recovery commands (v4: STATUS, BACK, etc.) ---
        recovery_response = recovery_commands.try_handle(text, self.snapshot)
        if recovery_response is not None:
            return recovery_response

        if self.state == "COOLDOWN":
            remaining = self.snapshot.cooldown_remaining()
            if remaining > 0:
                mins, secs = divmod(int(remaining), 60)
                return (f"System is in cooldown. {mins}m {secs}s remaining. "
                        "Use `STATUS` to check progress.")
            else:
                self.state = self._determine_state()

        if self.state == "WELCOME":
            return self._bond_identity(user)

        elif self.state == "FLAGSHIP_SELECTION":
            return self._handle_flagship_selection(text)

        elif self.state == "HANDSHAKE":
            return self._handle_auth_options(text)

        elif self.state == "PROVIDER_MODE_SELECTION":
            return self._handle_provider_mode(text)

        elif self.state == "LOCAL_UTILITY_SETUP":
            return handle_local_utility_setup(text, self.snapshot)

        elif self.state == "COMMS_SELECTION":
            return self._handle_comms_selection(text)

        elif self.state == "COMMS_GUIDED_SETUP":
            return self._handle_guided_setup(text)

        elif self.state == "COMMS_CHAT_SCAN":
            return self._handle_chat_scan(text)

        elif self.state == "COMMS_CHAT_SELECT":
            return self._handle_chat_select(text)

        elif self.state == "COMMS_TELEGRAM_TOKEN":
            return self._handle_telegram_token(text)

        elif self.state == "COMMS_TELEGRAM_CHAT":
            return self._handle_telegram_chat(text)

        elif self.state == "COMMS_VERIFY":
            return self._verify_handshake(text)

        elif self.state == "FINAL_CHECKS":
            return self._handle_final_checks()

        return "Lancelot is ready. How may I serve you?"

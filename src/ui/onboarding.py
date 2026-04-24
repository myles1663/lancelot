import os
import json
import secrets
import logging
from pathlib import Path

from src.core.onboarding_snapshot import OnboardingSnapshot, OnboardingState
from src.core import recovery_commands
from src.core.outbound_http import OutboundNetworkError, assert_url_allowed
from src.core.local_utility_setup import handle_local_utility_setup


logger = logging.getLogger(__name__)


def _has_codex_cli_auth() -> bool:
    """Return True when mounted Codex CLI auth is available during onboarding."""
    try:
        from src.core.providers.codex_cli_client import has_codex_cli_auth
        return has_codex_cli_auth()
    except Exception as exc:
        logger.warning("Onboarding failed to inspect Codex CLI auth: %s", exc)
        return False


def _load_persisted_provider() -> str:
    """Return the durable active provider when provider persistence is available."""
    candidates = []
    configured_data_dir = os.getenv("LANCELOT_DATA_DIR", "").strip()
    if configured_data_dir:
        candidates.append(Path(configured_data_dir) / "provider_config.json")

    candidates.append(Path("/home/lancelot/data/provider_config.json"))
    candidates.append(Path("lancelot_data/provider_config.json"))

    seen = set()
    for path in candidates:
        normalized = str(path)
        if normalized in seen:
            continue
        seen.add(normalized)
        try:
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                provider = (data.get("active_provider") or "").strip()
                if provider:
                    return provider
        except Exception as exc:
            logger.warning("Onboarding failed to read persisted provider from %s: %s", path, exc)

    return ""

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
    "nvidia": {
        "name": "NVIDIA Nemotron",
        "env_var": "NVIDIA_API_KEY",
        "env_provider": "nvidia",
        "prefix": "nvapi-",
        "signup": "https://build.nvidia.com/",
        "description": "Nemotron models via NIM, free tier available",
    },
    "openai-codex": {
        "name": "OpenAI (Codex/Pro)",
        "env_var": None,
        "env_provider": "openai-codex",
        "prefix": None,
        "signup": "https://chatgpt.com/",
        "description": "ChatGPT Plus/Pro subscription via OAuth — flat rate, no per-token billing",
        "oauth_only": True,
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
            "ANTHROPIC_OAUTH_WAITING": OnboardingState.CREDENTIALS_CAPTURE,  # V28: OAuth browser flow
            "OPENAI_CODEX_OAUTH_WAITING": OnboardingState.CREDENTIALS_CAPTURE,  # Codex OAuth browser flow
            "PROVIDER_MODE_SELECTION": OnboardingState.CREDENTIALS_CAPTURE,  # V27: shares credential phase
            "LOCAL_UTILITY_SETUP": OnboardingState.LOCAL_UTILITY_SETUP,
            "COMMS_SELECTION": OnboardingState.COMMS_SELECTION,
            "AUTH_MODEL_SELECTION": OnboardingState.CREDENTIALS_CAPTURE,
            "LOCAL_AUTH_SETUP": OnboardingState.CREDENTIALS_CAPTURE,
            "ENTERPRISE_AUTH_SETUP": OnboardingState.CREDENTIALS_CAPTURE,
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
                  -> AUTH_MODEL_SELECTION -> LOCAL_AUTH_SETUP|ENTERPRISE_AUTH_SETUP
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
        provider = self._get_selected_provider()
        if not provider:
            return "FLAGSHIP_SELECTION"

        # Step 3: API key (or ADC for Gemini) must exist for selected provider
        provider_info = PROVIDERS.get(provider, {})
        env_var = provider_info.get("env_var", "")
        api_key = self._get_env_value(env_var) if env_var else None
        if not api_key and env_var:
            api_key = os.getenv(env_var)
        # Also check Vault-backed secret_cache (key may only be in Vault)
        if not api_key and env_var:
            try:
                import secret_cache
                if secret_cache.is_bootstrapped():
                    api_key = secret_cache.get(env_var, "")
            except Exception as exc:
                logger.warning("Onboarding failed to read provider credential %s from secret cache: %s", env_var, exc)

        adc_exists = False
        if provider == "gemini":
            adc_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
            adc_exists = adc_path and os.path.exists(adc_path)
            if not adc_exists:
                default_adc = "/home/lancelot/.config/gcloud/application_default_credentials.json"
                if os.path.exists(default_adc):
                    adc_exists = True

        # OAuth or mounted CLI auth can satisfy credential capture for some providers.
        credential_source_ready = False
        if provider == "anthropic":
            try:
                from oauth_token_manager import get_oauth_manager
                mgr = get_oauth_manager()
                if mgr and mgr.get_token_status().get("configured"):
                    credential_source_ready = True
            except Exception as exc:
                logger.warning("Onboarding failed to inspect Anthropic OAuth status: %s", exc)
        elif provider == "openai-codex":
            credential_source_ready = _has_codex_cli_auth()
            if not credential_source_ready:
                try:
                    from openai_codex_oauth_manager import get_openai_codex_manager
                    mgr = get_openai_codex_manager()
                    if mgr and mgr.get_token_status().get("configured"):
                        credential_source_ready = True
                except Exception as exc:
                    logger.warning("Onboarding failed to inspect Codex OAuth status: %s", exc)

        # If snapshot already records credentials as verified, trust it
        # (key may be in Vault which isn't bootstrapped during __init__)
        snapshot_verified = self.snapshot.credential_status == "verified"
        if not api_key and not adc_exists and not credential_source_ready and not snapshot_verified:
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

        # Step 6: War Room / enterprise auth must be configured
        auth_provider = self._infer_auth_provider()
        if not auth_provider:
            return "AUTH_MODEL_SELECTION"

        if auth_provider == "local":
            if not self._get_env_value("WARROOM_USERNAME") or not self._get_env_value("WARROOM_PASSWORD"):
                return "LOCAL_AUTH_SETUP"
        elif auth_provider == "oidc":
            oidc_required = [
                "OIDC_ISSUER_URL",
                "OIDC_CLIENT_ID",
                "OIDC_CLIENT_SECRET",
            ]
            if any(not self._get_env_value(key) for key in oidc_required):
                return "ENTERPRISE_AUTH_SETUP"
        else:
            return "AUTH_MODEL_SELECTION"

        # Step 7: V16 — Security tokens must exist
        if not self._has_security_tokens():
            return "FINAL_CHECKS"

        return "READY"

    # ------------------------------------------------------------------
    # Env file helpers
    # ------------------------------------------------------------------

    def _get_env_value(self, key):
        """Read a value from the durable runtime configuration sources."""
        if not key:
            return None
        try:
            import secret_cache
            if secret_cache.is_bootstrapped():
                cached = secret_cache.get(key, "")
                if cached:
                    return cached
        except Exception as exc:
            logger.warning("Onboarding failed to read %s from secret cache: %s", key, exc)

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
            except Exception as exc:
                logger.warning("Onboarding failed to read %s from %s: %s", key, self.env_file, exc)
        return None

    def _infer_provider_from_keys(self):
        """Backward compat: infer provider from which API key exists."""
        for provider_id, info in PROVIDERS.items():
            env_var = info.get("env_var")
            if env_var and self._get_env_value(env_var):
                return provider_id
        adc_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        if adc_path and os.path.exists(adc_path):
            return "gemini"
        default_adc = "/home/lancelot/.config/gcloud/application_default_credentials.json"
        if os.path.exists(default_adc):
            return "gemini"
        return None

    def _infer_auth_provider(self):
        """Backward compat: infer auth mode for upgraded installs."""
        explicit = (self._get_env_value("LANCELOT_AUTH_PROVIDER") or "").strip().lower()
        if explicit in {"local", "oidc"}:
            return explicit

        if self._get_env_value("OIDC_ISSUER_URL") and self._get_env_value("OIDC_CLIENT_ID"):
            return "oidc"

        if self._get_env_value("WARROOM_USERNAME") and self._get_env_value("WARROOM_PASSWORD"):
            return "local"

        return ""

    def _get_selected_provider(self) -> str:
        """Resolve the active provider with durable runtime state preferred over stale env."""
        persisted_provider = _load_persisted_provider()
        if persisted_provider:
            return persisted_provider

        snapshot_provider = (self.snapshot.flagship_provider or "").strip()
        if snapshot_provider:
            return snapshot_provider

        env_provider = self._get_env_value("LANCELOT_PROVIDER")
        if env_provider:
            return env_provider

        return self._infer_provider_from_keys()

    def _has_vault_key_configured(self) -> bool:
        """Return True when the connector vault key is available from env or Docker secret."""
        if self._get_env_value("LANCELOT_VAULT_KEY"):
            return True

        try:
            from src.connectors.vault import CredentialVault

            config = CredentialVault._load_config("config/vault.yaml")
            enc = config.get("encryption", {})
            key_env_var = enc.get("key_env_var", "LANCELOT_VAULT_KEY")
            docker_secret_name = enc.get("docker_secret", "lancelot_vault_key")
            key, _origin = CredentialVault._resolve_key_with_origin(key_env_var, docker_secret_name)
            return bool(key)
        except Exception as exc:
            logger.warning("Onboarding failed to inspect vault-key configuration: %s", exc)
            return False

    def _has_security_tokens(self):
        """Check if management secrets and the vault key exist in a durable source."""
        for key in ("LANCELOT_OWNER_TOKEN", "LANCELOT_API_TOKEN"):
            # Check secret_cache first (vault-backed), then env
            found = False
            try:
                import secret_cache
                if secret_cache.is_bootstrapped() and secret_cache.get(key, ""):
                    found = True
            except Exception as exc:
                logger.warning("Onboarding failed to read security token %s from secret cache: %s", key, exc)
            if not found and not self._get_env_value(key):
                return False
        return self._has_vault_key_configured()

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
        except Exception as exc:
            logger.warning("Onboarding failed to write .env values: %s", exc)

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
            "[5] NVIDIA Nemotron — Nemotron models via NIM, free tier available\n"
            "    Get a key at: https://build.nvidia.com/\n\n"
            "[6] OpenAI Codex (Pro) — ChatGPT Plus/Pro subscription via Codex CLI auth\n"
            "    Preferred: sign in on the host so ~/.codex/auth.json is mounted; browser OAuth is fallback only\n\n"
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
            "5": "nvidia", "nvidia": "nvidia", "nemotron": "nvidia",
            "6": "openai-codex", "codex": "openai-codex", "openai-codex": "openai-codex",
        }

        provider_id = provider_map.get(choice.lower())
        if not provider_id:
            return "Invalid selection.\n\n" + self._flagship_selection_prompt()

        self.temp_data["provider"] = provider_id
        provider = PROVIDERS[provider_id]

        # OpenAI Codex prefers mounted CLI auth and only falls back to browser OAuth.
        if provider.get("oauth_only"):
            return self._initiate_openai_codex_setup()

        self.state = "HANDSHAKE"

        msg = f"**{provider['name']} Selected.**\n\n"
        msg += "**API Key Required**\n"
        msg += f"Get your key at: [{provider['name']}]({provider['signup']})\n\n"
        msg += f"Paste your API key below (starts with `{provider['prefix']}...`)."

        if provider_id == "gemini":
            msg += ("\n\nAlternatively, type **'scan'** to detect Google Cloud "
                    "Application Default Credentials (advanced).")

        # V28: OAuth option for Anthropic
        if provider_id == "anthropic":
            msg += ("\n\nAlternatively, type **'oauth'** to authenticate via browser "
                    "(uses your claude.ai subscription — no API key needed).")

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

        # V28: Anthropic OAuth browser flow
        if stripped.lower() == "oauth" and provider_id == "anthropic":
            return self._initiate_anthropic_oauth()

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

    # ------------------------------------------------------------------
    # V28: Anthropic OAuth browser flow
    # ------------------------------------------------------------------

    def _initiate_anthropic_oauth(self) -> str:
        """Start Anthropic OAuth PKCE flow — generate auth URL for browser."""
        try:
            from oauth_token_manager import get_oauth_manager
            manager = get_oauth_manager()
            if manager is None:
                return ("OAuth manager not available. Please use an API key instead.\n\n"
                        "Paste your API key (starts with `sk-ant-...`):")

            auth_url, state = manager.generate_auth_url()
            self.temp_data["oauth_state"] = state
            self.state = "ANTHROPIC_OAUTH_WAITING"

            return (
                "**Anthropic OAuth Setup**\n\n"
                "Please open this URL in your browser to authorize Lancelot:\n\n"
                f"{auth_url}\n\n"
                "After you authorize, the browser will redirect back to Lancelot.\n"
                "Type **'done'** once you see the success page, or **'cancel'** to use an API key instead."
            )
        except Exception as e:
            return f"OAuth initialization failed: {e}\n\nPlease paste your API key instead:"

    def _handle_anthropic_oauth_waiting(self, text: str) -> str:
        """Handle user input while waiting for OAuth browser completion."""
        cmd = text.strip().lower()

        if cmd == "cancel":
            self.state = "HANDSHAKE"
            provider = PROVIDERS.get("anthropic", PROVIDERS["gemini"])
            return (f"OAuth cancelled.\n\nPaste your API key "
                    f"(starts with `{provider['prefix']}...`):")

        if cmd == "done":
            try:
                from oauth_token_manager import get_oauth_manager
                manager = get_oauth_manager()
                if manager and manager.get_token_status().get("configured"):
                    self._write_env_values({
                        "LANCELOT_AUTH_MODE": "OAUTH",
                        "LANCELOT_PROVIDER": "anthropic",
                    }, section_comment="Anthropic OAuth (V28)")
                    self.fail_count = 0
                    self.state = "PROVIDER_MODE_SELECTION"
                    return (
                        "**OAuth Authorized.** (Anthropic)\n\n"
                        "Your Lancelot instance is connected to your claude.ai account.\n\n"
                        + self._provider_mode_prompt()
                    )
                else:
                    return (
                        "OAuth tokens not detected yet. Make sure you completed "
                        "the browser authorization.\n\n"
                        "Type **'done'** to check again, or **'cancel'** to use "
                        "an API key instead."
                    )
            except Exception as e:
                return f"Error checking OAuth status: {e}\n\nType **'done'** or **'cancel'**."

        return ("Please type **'done'** after completing browser authorization, "
                "or **'cancel'** to use an API key instead.")

    # ------------------------------------------------------------------
    # OpenAI Codex OAuth flow
    # ------------------------------------------------------------------

    def _complete_openai_codex_cli_setup(self) -> str:
        """Persist provider selection when mounted Codex CLI auth is already available."""
        self._write_env_values({
            "LANCELOT_AUTH_MODE": "OAUTH",
            "LANCELOT_PROVIDER": "openai-codex",
        }, section_comment="OpenAI Codex CLI Auth")
        self.fail_count = 0
        self.snapshot.credential_status = "verified"
        self.snapshot.flagship_provider = "openai-codex"
        self.snapshot.save()
        self.state = "PROVIDER_MODE_SELECTION"
        return (
            "**Codex CLI Auth Detected.** (OpenAI Codex)\n\n"
            "Lancelot found mounted host auth at `~/.codex/auth.json` and will use your ChatGPT Plus/Pro "
            "subscription through the official Codex CLI.\n"
            "No browser OAuth is required on this machine.\n\n"
            + self._provider_mode_prompt()
        )

    def _initiate_openai_codex_setup(self) -> str:
        """Prefer mounted Codex CLI auth before starting browser OAuth."""
        if _has_codex_cli_auth():
            return self._complete_openai_codex_cli_setup()
        return self._initiate_openai_codex_oauth()

    def _initiate_openai_codex_oauth(self) -> str:
        """Start OpenAI Codex OAuth PKCE flow — generate auth URL for browser."""
        try:
            from openai_codex_oauth_manager import get_openai_codex_manager
            manager = get_openai_codex_manager()
            if manager is None:
                self.state = "FLAGSHIP_SELECTION"
                return ("Codex OAuth manager not available. Please choose a different provider.\n\n"
                        + self._flagship_selection_prompt())

            auth_url, state = manager.generate_auth_url()
            self.temp_data["oauth_state"] = state
            self.state = "OPENAI_CODEX_OAUTH_WAITING"

            return (
                "**OpenAI Codex OAuth Setup**\n\n"
                "Preferred enterprise path: sign in on the host first so `~/.codex/auth.json` is mounted into the container.\n"
                "Use browser OAuth only when mounted Codex CLI auth is not available.\n\n"
                "Please open this URL in your browser to sign in with your ChatGPT account:\n\n"
                f"{auth_url}\n\n"
                "After you authorize, the browser will redirect back to Lancelot.\n"
                "Your ChatGPT Plus/Pro subscription will be used for API access (no per-token billing).\n\n"
                "Type **'done'** once you see the success page, or **'cancel'** to choose a different provider."
            )
        except Exception as e:
            self.state = "FLAGSHIP_SELECTION"
            return f"Codex OAuth initialization failed: {e}\n\n" + self._flagship_selection_prompt()

    def _handle_openai_codex_oauth_waiting(self, text: str) -> str:
        """Handle user input while waiting for Codex OAuth browser completion."""
        cmd = text.strip().lower()

        if cmd == "cancel":
            self.state = "FLAGSHIP_SELECTION"
            return ("Codex OAuth cancelled.\n\n" + self._flagship_selection_prompt())

        if cmd == "done":
            try:
                if _has_codex_cli_auth():
                    return self._complete_openai_codex_cli_setup()
                from openai_codex_oauth_manager import get_openai_codex_manager
                manager = get_openai_codex_manager()
                if manager and manager.get_token_status().get("configured"):
                    self._write_env_values({
                        "LANCELOT_AUTH_MODE": "OAUTH",
                        "LANCELOT_PROVIDER": "openai-codex",
                    }, section_comment="OpenAI Codex OAuth")
                    self.fail_count = 0
                    self.state = "PROVIDER_MODE_SELECTION"
                    return (
                        "**OAuth Authorized.** (OpenAI Codex)\n\n"
                        "Your Lancelot instance is connected to your ChatGPT account.\n"
                        "API calls will use your subscription at flat rate.\n\n"
                        + self._provider_mode_prompt()
                    )
                else:
                    return (
                        "Codex credentials not detected yet. Either complete the browser authorization "
                        "or sign in on the host so `~/.codex/auth.json` is mounted.\n\n"
                        "Type **'done'** to check again, or **'cancel'** to choose "
                        "a different provider."
                    )
            except Exception as e:
                return f"Error checking Codex OAuth status: {e}\n\nType **'done'** or **'cancel'**."

        return ("Please type **'done'** after completing browser authorization, "
                "or **'cancel'** to choose a different provider.")

    def _validate_api_key_live(self, provider: str, key: str) -> dict:
        """Live HTTP probe to validate API key. Non-blocking on network errors."""
        import requests
        try:
            if provider == "gemini":
                url = assert_url_allowed(
                    f"https://generativelanguage.googleapis.com/v1beta/models?key={key}",
                    component="Onboarding Gemini API key validation",
                )
                r = requests.get(
                    url,
                    timeout=10,
                )
                if r.ok:
                    return {"valid": True}
                if r.status_code in (400, 403):
                    return {"valid": False, "error": "Invalid API key — rejected by Google"}
                return {"valid": False, "error": f"Unexpected response (HTTP {r.status_code})"}

            elif provider == "openai":
                url = assert_url_allowed(
                    "https://api.openai.com/v1/models",
                    component="Onboarding OpenAI API key validation",
                )
                r = requests.get(
                    url,
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=10,
                )
                if r.ok:
                    return {"valid": True}
                if r.status_code == 401:
                    return {"valid": False, "error": "Invalid API key — rejected by OpenAI"}
                return {"valid": False, "error": f"Unexpected response (HTTP {r.status_code})"}

            elif provider == "anthropic":
                url = assert_url_allowed(
                    "https://api.anthropic.com/v1/messages",
                    component="Onboarding Anthropic API key validation",
                )
                r = requests.post(
                    url,
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
                url = assert_url_allowed(
                    "https://api.x.ai/v1/models",
                    component="Onboarding xAI API key validation",
                )
                r = requests.get(
                    url,
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=10,
                )
                if r.ok:
                    return {"valid": True}
                if r.status_code == 401:
                    return {"valid": False, "error": "Invalid API key — rejected by xAI"}
                return {"valid": False, "error": f"Unexpected response (HTTP {r.status_code})"}

            elif provider == "nvidia":
                url = assert_url_allowed(
                    "https://integrate.api.nvidia.com/v1/models",
                    component="Onboarding NVIDIA API key validation",
                )
                r = requests.get(
                    url,
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=10,
                )
                if r.ok:
                    return {"valid": True}
                if r.status_code == 401:
                    return {"valid": False, "error": "Invalid API key — rejected by NVIDIA"}
                return {"valid": False, "error": f"Unexpected response (HTTP {r.status_code})"}

            return {"valid": False, "error": f"Unknown provider: {provider}"}

        except OutboundNetworkError as exc:
            return {"valid": False, "error": str(exc)}
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
        # Skip already-completed steps (e.g. local models already verified)
        self.state = self._determine_state()
        self._sync_snapshot()
        if self.state == "READY":
            return (
                f"**{mode.upper()} mode selected.**\n\n"
                "All setup steps already complete. Lancelot is ready."
            )
        return (
            f"**{mode.upper()} mode selected.**\n\n"
            "Proceeding to next setup step..."
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
        """Render the comms selection menu for supported runtime backends."""
        return (
            "**Secure Comms Link**\n"
            "Select your communication channel:\n\n"
            "**Messaging (Bidirectional)**\n"
            "[1] Telegram (Recommended) - Simple setup via BotFather\n"
            "[2] Google Chat - Requires Google Cloud project\n"
            "\n"
            "[3] Skip (Configure later in the War Room)\n\n"
            "Additional channels remain connector-level capabilities until their "
            "bidirectional runtime adapters are implemented."
        )

    def _handle_comms_selection(self, text: str) -> str:
        """Handles comms connector selection for supported runtime backends."""
        choice = text.strip().lower()

        unsupported_choices = {
            "4", "5", "6", "7", "8",
            "slack", "discord", "teams", "microsoft teams", "ms teams",
            "whatsapp", "email", "smtp", "sms", "twilio",
        }
        if choice in unsupported_choices:
            return (
                "That channel is not yet available as a bidirectional runtime backend. "
                "Use Telegram or Google Chat for bonded comms, or configure the service "
                "later through governed connectors.\n\n"
                + self._comms_selection_prompt()
            )

        # Map user input to connector ID
        selection_map = {
            "1": "telegram", "telegram": "telegram",
            "2": "google_chat", "google chat": "google_chat", "google": "google_chat", "gchat": "google_chat",
            "3": "skip", "9": "skip", "skip": "skip",
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
                url = assert_url_allowed(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    component="Onboarding Telegram handshake",
                )
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

        except OutboundNetworkError as exc:
            return f"**Connection Blocked:** {exc}"
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
    # Auth model setup
    # ------------------------------------------------------------------

    def _auth_model_prompt(self) -> str:
        return (
            "**War Room Authentication**\n\n"
            "Choose how operators will sign in:\n\n"
            "[1] Local Account (Recommended for personal and small-team deployments)\n"
            "    Create a War Room username/password on this Lancelot instance.\n\n"
            "[2] Enterprise SSO (OIDC)\n"
            "    Sign in with your existing identity provider such as Entra, Okta, or Keycloak.\n\n"
            "Enter your choice:"
        )

    def _handle_auth_model_selection(self, text: str) -> str:
        choice = text.strip().lower()
        selection_map = {
            "1": "local", "local": "local",
            "2": "oidc", "enterprise": "oidc", "oidc": "oidc", "sso": "oidc",
        }
        provider = selection_map.get(choice)
        if not provider:
            return "Invalid selection.\n\n" + self._auth_model_prompt()

        self._write_env_values(
            {"LANCELOT_AUTH_PROVIDER": provider},
            "War Room Authentication",
        )

        if provider == "local":
            self.temp_data["local_auth_stage"] = "username"
            self.state = "LOCAL_AUTH_SETUP"
            return (
                "**Local authentication selected.**\n\n"
                "Choose your War Room username:"
            )

        self.temp_data["enterprise_auth_stage"] = "issuer"
        self.state = "ENTERPRISE_AUTH_SETUP"
        return (
            "**Enterprise SSO selected.**\n\n"
            "Enter your OIDC issuer URL.\n"
            "Example: `https://login.microsoftonline.com/<tenant>/v2.0` or `https://your-okta-domain/oauth2/default`"
        )

    def _handle_local_auth_setup(self, text: str) -> str:
        stage = self.temp_data.get("local_auth_stage", "username")
        value = text.strip()

        if stage == "username":
            if len(value) < 2 or not all(ch.isalnum() or ch in "._-" for ch in value):
                return (
                    "Username must be at least 2 characters and only use letters, numbers, dots, dashes, or underscores.\n\n"
                    "Choose your War Room username:"
                )
            self.temp_data["warroom_username"] = value
            self.temp_data["local_auth_stage"] = "password"
            return "Enter your War Room password (minimum 8 characters):"

        if stage == "password":
            if len(value) < 8:
                return "Password must be at least 8 characters.\n\nEnter your War Room password:"
            self.temp_data["warroom_password"] = value
            self.temp_data["local_auth_stage"] = "confirm_password"
            return "Confirm your War Room password:"

        if stage == "confirm_password":
            if value != self.temp_data.get("warroom_password", ""):
                self.temp_data["local_auth_stage"] = "password"
                return (
                    "Passwords do not match.\n\n"
                    "Enter your War Room password again:"
                )

            self._write_env_values(
                {
                    "LANCELOT_AUTH_PROVIDER": "local",
                    "WARROOM_USERNAME": self.temp_data.get("warroom_username", "admin"),
                    "WARROOM_PASSWORD": self.temp_data.get("warroom_password", ""),
                },
                "War Room Local Authentication",
            )
            self.state = "FINAL_CHECKS"
            return (
                "**Local authentication configured.**\n\n"
                + self._handle_final_checks()
            )

        self.temp_data["local_auth_stage"] = "username"
        return self._handle_auth_model_selection("local")

    def _handle_enterprise_auth_setup(self, text: str) -> str:
        stage = self.temp_data.get("enterprise_auth_stage", "issuer")
        value = text.strip()

        if stage == "issuer":
            if not value.startswith("http://") and not value.startswith("https://"):
                return "Issuer URL must start with `http://` or `https://`.\n\nEnter your OIDC issuer URL:"
            self.temp_data["oidc_issuer_url"] = value.rstrip("/")
            self.temp_data["enterprise_auth_stage"] = "client_id"
            return "Enter your OIDC client ID:"

        if stage == "client_id":
            if not value:
                return "Client ID is required.\n\nEnter your OIDC client ID:"
            self.temp_data["oidc_client_id"] = value
            self.temp_data["enterprise_auth_stage"] = "client_secret"
            return "Enter your OIDC client secret:"

        if stage == "client_secret":
            if not value:
                return "Client secret is required.\n\nEnter your OIDC client secret:"
            self.temp_data["oidc_client_secret"] = value
            self.temp_data["enterprise_auth_stage"] = "allowed_groups"
            return (
                "Enter allowed OIDC groups separated by commas, or type `open` to explicitly allow any authenticated enterprise user:"
            )

        if stage == "allowed_groups":
            if not value:
                return (
                    "At least one allowed OIDC group is required unless you explicitly type `open`.\n\n"
                    "Enter allowed OIDC groups separated by commas, or type `open` to explicitly allow any authenticated enterprise user:"
                )
            allow_any_authenticated = "true" if value.lower() in {"open", "skip"} else "false"
            allowed_groups = "" if allow_any_authenticated == "true" else value
            self._write_env_values(
                {
                    "LANCELOT_AUTH_PROVIDER": "oidc",
                    "OIDC_ISSUER_URL": self.temp_data.get("oidc_issuer_url", ""),
                    "OIDC_CLIENT_ID": self.temp_data.get("oidc_client_id", ""),
                    "OIDC_CLIENT_SECRET": self.temp_data.get("oidc_client_secret", ""),
                    "OIDC_ALLOWED_GROUPS": allowed_groups,
                    "OIDC_ALLOW_ANY_AUTHENTICATED": allow_any_authenticated,
                },
                "War Room Enterprise Authentication",
            )
            self.state = "FINAL_CHECKS"
            return (
                "**Enterprise SSO configured.**\n\n"
                + self._handle_final_checks()
            )

        self.temp_data["enterprise_auth_stage"] = "issuer"
        return self._handle_auth_model_selection("oidc")

    # ------------------------------------------------------------------
    # V16: FINAL_CHECKS state
    # ------------------------------------------------------------------

    def _handle_final_checks(self) -> str:
        """Auto-generate missing config, display summary, advance to READY."""
        auth_provider = self._infer_auth_provider()
        if not auth_provider:
            self.state = "AUTH_MODEL_SELECTION"
            return self._auth_model_prompt()
        if auth_provider == "local" and (
            not self._get_env_value("WARROOM_USERNAME") or not self._get_env_value("WARROOM_PASSWORD")
        ):
            self.temp_data["local_auth_stage"] = "username"
            self.state = "LOCAL_AUTH_SETUP"
            return "War Room local authentication is not configured yet.\n\nChoose your War Room username:"
        if auth_provider == "oidc":
            oidc_required = ["OIDC_ISSUER_URL", "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET"]
            if any(not self._get_env_value(key) for key in oidc_required):
                self.temp_data["enterprise_auth_stage"] = "issuer"
                self.state = "ENTERPRISE_AUTH_SETUP"
                return "Enterprise SSO is not configured yet.\n\nEnter your OIDC issuer URL:"

        generated = []

        # 1. Generate security tokens if missing
        tokens = {}
        for token_name in ("LANCELOT_OWNER_TOKEN", "LANCELOT_API_TOKEN", "LANCELOT_VAULT_KEY"):
            if not self._get_env_value(token_name):
                tokens[token_name] = secrets.token_urlsafe(32)
        if tokens:
            self._write_env_values(tokens, "Security Tokens (auto-generated — keep secret)")
            generated.append(f"Security tokens generated ({len(tokens)} tokens)")
            # Also store in vault if available
            try:
                import secret_cache
                if secret_cache.is_bootstrapped():
                    _vault_map = {
                        "LANCELOT_API_TOKEN": "system.api_token",
                        "LANCELOT_OWNER_TOKEN": "system.owner_token",
                    }
                    from connectors.vault import CredentialVault as _OnbVault
                    _onb_vault = _OnbVault(config_path="config/vault.yaml")
                    for _tk, _tv in tokens.items():
                        if _tk in _vault_map:
                            _onb_vault.store(_vault_map[_tk], _tv, type="system_secret")
                    secret_cache.bootstrap(_onb_vault)
            except Exception:
                logger.warning("Onboarding failed to bootstrap generated security tokens into secret cache", exc_info=True)

        # 2. Generate local-auth recovery code if missing
        warroom_username = self._get_env_value("WARROOM_USERNAME")
        password_reset_code = None
        if auth_provider == "local" and not self._get_env_value("WARROOM_PASSWORD_RESET_CODE"):
            password_reset_code = secrets.token_urlsafe(16)
            self._write_env_values(
                {"WARROOM_PASSWORD_RESET_CODE": password_reset_code},
                "War Room Password Recovery",
            )
            generated.append("War Room password reset code generated")

        # 3. Write default feature flags if missing
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

        # 4. Check UAB daemon reachability (non-blocking)
        uab_running = False
        try:
            import urllib.request
            uab_url = os.environ.get("UAB_DAEMON_URL", "http://host.docker.internal:7900")
            payload = json.dumps({
                "jsonrpc": "2.0", "method": "getStatus", "params": {}, "id": 1,
            }).encode("utf-8")
            req = urllib.request.Request(
                uab_url, data=payload,
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                uab_data = json.loads(resp.read().decode("utf-8"))
            uab_running = "result" in uab_data
        except Exception as exc:
            logger.debug("Onboarding could not reach UAB daemon for final summary: %s", exc)

        msg = "**Final Configuration Complete**\n\n"
        msg += "**System Summary:**\n"
        msg += f"- LLM Provider: {provider_name} ({provider_mode.upper()} mode)\n"
        msg += f"- Local Model: Verified\n"
        msg += f"- Communications: {comms_display}\n"
        msg += f"- Security: Tokens configured\n"
        msg += f"- Feature Flags: Set\n"
        msg += f"- War Room Auth: {'Enterprise SSO (OIDC)' if auth_provider == 'oidc' else 'Local Account'}\n"
        if uab_running:
            msg += f"- UAB Daemon: Running\n"
        else:
            msg += f"- UAB Daemon: Not running — run `scripts\\install-uab.bat` on the host for auto-start\n"

        if auth_provider == "local":
            msg += f"\n**War Room Login:**\n"
            msg += f"- Username: `{warroom_username or 'admin'}`\n"
            msg += "*Use the password you created during setup.*\n"
            if password_reset_code:
                msg += f"- Password reset code: `{password_reset_code}`\n"
                msg += "*Store this reset code securely. It is required for local password recovery from the login screen.*\n"
        else:
            msg += (
                "\n**War Room Login:**\n"
                "- Enterprise SSO is enabled.\n"
                "*Users will sign in through your configured OIDC identity provider.*\n"
            )

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
        except Exception as exc:
            logger.warning("Onboarding failed to mark onboarding complete: %s", exc)

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

        elif self.state == "ANTHROPIC_OAUTH_WAITING":
            return self._handle_anthropic_oauth_waiting(text)

        elif self.state == "OPENAI_CODEX_OAUTH_WAITING":
            return self._handle_openai_codex_oauth_waiting(text)

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

        elif self.state == "AUTH_MODEL_SELECTION":
            return self._handle_auth_model_selection(text)

        elif self.state == "LOCAL_AUTH_SETUP":
            return self._handle_local_auth_setup(text)

        elif self.state == "ENTERPRISE_AUTH_SETUP":
            return self._handle_enterprise_auth_setup(text)

        elif self.state == "FINAL_CHECKS":
            return self._handle_final_checks()

        return "Lancelot is ready. How may I serve you?"

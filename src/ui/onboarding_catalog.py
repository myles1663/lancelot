"""Static provider and connector options for the onboarding state machine."""

# ---------------------------------------------------------------------------
# Provider configuration mirrors installer/src/constants.mjs
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
    "deepseek": {
        "name": "DeepSeek",
        "env_var": "DEEPSEEK_API_KEY",
        "env_provider": "deepseek",
        "prefix": "",
        "signup": "https://platform.deepseek.com/",
        "description": "DeepSeek V4 via OpenAI-compatible API",
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
# Comms connector definitions for supported messaging platforms
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

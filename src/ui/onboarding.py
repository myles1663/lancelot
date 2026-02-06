import os
import json

from src.core.onboarding_snapshot import OnboardingSnapshot, OnboardingState
from src.core import recovery_commands
from src.core.local_utility_setup import handle_local_utility_setup

class OnboardingOrchestrator:
    def __init__(self, data_dir="/home/lancelot/data"):
        self.data_dir = data_dir
        self.user_file = os.path.join(data_dir, "USER.md")
        self.env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
        self.fail_count = 0
        self.temp_data = {} # Store transient data like webhook url before verification
        self.snapshot = OnboardingSnapshot(data_dir)
        self.state = self._determine_state()

    def _determine_state(self):
        """Determines current state based on snapshot and file existence."""
        # v4: COOLDOWN replaces permanent LOCKDOWN — check snapshot first
        if self.snapshot.state == OnboardingState.COOLDOWN:
            if self.snapshot.is_in_cooldown():
                return "COOLDOWN"
            # Cooldown expired — resume from before cooldown
            # (snapshot.transition guards already handle this)

        if not os.path.exists(self.user_file):
            return "WELCOME"
        
        # Check for ACTUAL credential presence, not just flags
        api_key = os.getenv("GEMINI_API_KEY")
        
        # Check if API key is in .env but not loaded
        if not api_key and os.path.exists(self.env_file):
            with open(self.env_file, "r") as f:
                for line in f:
                    if line.startswith("GEMINI_API_KEY="):
                        api_key = line.split("=", 1)[1].strip()
                        break
        
        # Check for ACTUAL ADC file presence (not just flag)
        adc_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        adc_exists = adc_path and os.path.exists(adc_path)
        
        # Fallback: Check default ADC location in Docker
        if not adc_exists:
            default_adc = "/home/lancelot/.config/gcloud/application_default_credentials.json"
            if os.path.exists(default_adc):
                adc_exists = True
        
        # No valid auth at all -> Handshake
        if not api_key and not adc_exists:
            return "HANDSHAKE"
        
        # Auth exists but Comms not configured
        comms_type = os.getenv("LANCELOT_COMMS_TYPE")
        if not comms_type and os.path.exists(self.env_file):
            with open(self.env_file, "r") as f:
                if "LANCELOT_COMMS_TYPE=" in f.read():
                    comms_type = True
        
        if not comms_type:
            return "COMMS_SELECTION"
            
        return "READY"

    def _enter_cooldown(self, seconds: int = 300, reason: str = "Too many failures"):
        """Enter time-based cooldown (v4: replaces permanent LOCKDOWN)."""
        self.snapshot.enter_cooldown(seconds, reason)
        self.state = "COOLDOWN"

    def _complete_onboarding(self):
        """Marks onboarding as complete in USER.md."""
        try:
            with open(self.user_file, "a") as f:
                f.write("\n- OnboardingComplete: True")
            self.state = "READY"
        except Exception as e:
            print(f"Error marking complete: {e}")

    def _bond_identity(self, user: str) -> str:
        """Creates USER.md and bonds identity."""
        try:
            with open(self.user_file, "w") as f:
                f.write(f"# User Profile\n- Name: {user}\n- Role: Commander\n- Bonded: True")

            self.state = "HANDSHAKE"
            return (f"Welcome, {user}. I've bonded to your identity.\n\n"
                    "**LLM Authentication Required**\n"
                    "I need an LLM connection to function. Choose one:\n\n"
                    "**Option A: Gemini API Key (Recommended)**\n"
                    "Get a free key at: [Google AI Studio](https://aistudio.google.com/app/apikey)\n"
                    "Then paste your API Key below.\n\n"
                    "**Option B: Google Cloud ADC (Advanced)**\n"
                    "If you already have `gcloud` configured, type **'scan'** to detect credentials.")
        except Exception as e:
            return f"Error bonding identity: {e}"

    def _handle_auth_options(self, text: str) -> str:
        """Handles HANDSHAKE state — user provides API key or types 'scan' for ADC."""
        stripped = text.strip()
        if stripped.lower() == "scan":
            result = self._verify_oauth_creds()
            if self.state == "COMMS_CHAT_SCAN":
                # ADC found — move to comms selection instead of chat scan
                self.state = "COMMS_SELECTION"
                return ("**Identity Verified.** ✅ (Google ADC detected)\n\n"
                        "**Secure Comms Link**\n"
                        "Select your communication channel:\n"
                        "[1] Telegram (Simple setup via BotFather)\n"
                        "[2] Google Chat (Requires Google Cloud project)\n"
                        "[3] Skip (Configure later)")
            return result
        elif stripped.startswith("AIza") or stripped.startswith("AI"):
            return self._verify_api_key(stripped)
        else:
            return ("Please provide your authentication:\n\n"
                    "**Option A: Gemini API Key** — Paste your key (starts with 'AI...')\n"
                    "[Get a free key](https://aistudio.google.com/app/apikey)\n\n"
                    "**Option B: Google Cloud ADC** — Type **'scan'** to detect credentials.")

    def _verify_api_key(self, text: str) -> str:
        """Verifies and saves API Key."""
        key = text.strip()
        if not key.startswith("AIza") and not key.startswith("AI"):
             return "Invalid API Key format. Please try again."
        
        try:
            with open(self.env_file, "a") as f:
                f.write(f"\nGEMINI_API_KEY={key}\n")
            
            # Set in current env
            os.environ["GEMINI_API_KEY"] = key
            
            # If they give an API key, we still might need Comms.
            # But for now, mark as Readyish?
            # Let's send them to Comms Selection
            self.state = "COMMS_SELECTION"
            return ("**API Key Accepted.** ✅\n\n"
                    "**Secure Comms Link**\n"
                    "I can link to your preferred communication channel for remote command.\n"
                    "Select Channel:\n"
                    "[1] Telegram (Simple setup via BotFather)\n"
                    "[2] Google Chat (Requires Google Cloud project)\n"
                    "[3] Skip (Configure later)")
        except Exception as e:
            return f"Error saving API Key: {e}"

    def _verify_oauth_creds(self) -> str:
        """Checks for ADC file presence and provides complete setup walkthrough."""
        adc_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        
        # Check default Docker path if env var not set
        default_adc = "/home/lancelot/.config/gcloud/application_default_credentials.json"
        adc_exists = (adc_path and os.path.exists(adc_path)) or os.path.exists(default_adc)
        
        if adc_exists:
            try:
                # Save OAuth Mode
                with open(self.env_file, "a") as f:
                    f.write("\nLANCELOT_AUTH_MODE=OAUTH\n")
                
                os.environ["LANCELOT_AUTH_MODE"] = "OAUTH"
                self.state = "COMMS_CHAT_SCAN"
                
                # Complete Walkthrough Instructions
                instructions = (
                    "**Identity Verified.** ✅ (Google ADC detected)\n\n"
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
                return instructions
            except Exception as e:
                return f"Error saving auth mode: {e}"
        else:
            # No ADC found - provide complete setup from scratch
            return (
                "**Google Credentials Not Found** ❌\n\n"
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
                "2. Click '+' → 'Create a space'\n"
                "3. Name it 'Lancelot'\n\n"
                "### Step 6: Restart Lancelot\n"
                "Close and reopen Lancelot to pick up the new credentials.\n\n"
                "---\n"
                "After completing these steps, restart Lancelot and select Google Chat again."
            )

    def _calibrate(self) -> str:
        """Mock calibration step."""
        pass

    def _handle_comms_selection(self, text: str) -> str:
        """Handles provider selection."""
        choice = text.strip()
        if "1" in choice or "telegram" in choice.lower():
            self.temp_data["comms_type"] = "telegram"
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
        elif "2" in choice or "google" in choice.lower():
            self.temp_data["comms_type"] = "google_chat"
            self.state = "COMMS_ADC_CHECK"
            # Trigger ADC check immediately
            return self._verify_oauth_creds()
        elif "3" in choice or "skip" in choice.lower():
            self.state = "READY"
            self._complete_onboarding()
            return ("**Comms Setup Skipped.**\n\n"
                    "**Lancelot is now operational.** How may I serve you, Commander?")
        else:
            return "Invalid selection. Please choose [1] Telegram, [2] Google Chat, or [3] Skip."

    def _handle_chat_scan(self, text: str) -> str:
        """Scans for Google Chat spaces."""
        # Handle retry/skip commands
        cmd = text.strip().lower()
        if cmd == "skip":
            self.state = "READY"
            self._complete_onboarding()
            return ("**Google Chat Setup Skipped.**\n\n"
                    "You can configure this later. **Lancelot is now operational.**")
        
        from chat_poller import ChatPoller
        
        # Initialize temp poller just for scanning
        poller = ChatPoller(self.data_dir)
        spaces = poller.list_spaces()
        
        if not spaces:
            return (
                "**No Spaces Found.** ❌\n\n"
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
            # Space name is usually "spaces/AAAA..."
            # Display name is "Lancelot DM" or similar
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
                space_name = selected_space['name'] # resources/spaces/xxx
                display_name = selected_space.get('displayName')
                
                self.temp_data["chat_space_name"] = space_name
                self.temp_data["chat_display_name"] = display_name
                
                return self._initiate_handshake("google_chat")
            else:
                return "Invalid number. Try again."
        except ValueError:
            return "Please enter a number."

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

    def _initiate_handshake(self, provider: str) -> str:
        """Sends code via provider."""
        import random
        import string
        import requests
        from chat_poller import ChatPoller
        
        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        self.temp_data["verification_code"] = code
        
        msg = f"🛡️ *Lancelot Handshake Check*\n\nYour verification code is: `{code}`"
        
        try:
            if provider == "google_chat":
                # Use Poller to send
                poller = ChatPoller(self.data_dir)
                poller.send_message(msg, self.temp_data["chat_space_name"])
                # We assume success if no exception, Poller logs errors
                
            elif provider == "telegram":
                token = self.temp_data["telegram_token"]
                chat_id = self.temp_data["telegram_chat_id"]
                url = f"https://api.telegram.org/bot{token}/sendMessage"
                resp = requests.post(url, json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"}, timeout=10)
                
                # Check response
                if resp.status_code != 200:
                    result = resp.json() if resp.text else {}
                    error_desc = result.get("description", "Unknown error")
                    return (f"**Telegram Send Failed** ❌\n\n"
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
            return "**Connection Timeout** - Could not reach Telegram. Check internet connection."
        except Exception as e:
            return f"**Connection Failed:** {e}"

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
                # Cooldown expired — re-determine state and continue
                self.state = self._determine_state()

        if self.state == "WELCOME":
            return self._bond_identity(user)

        elif self.state == "HANDSHAKE":
            return self._handle_auth_options(text)

        elif self.state == "LOCAL_UTILITY_SETUP":
            return handle_local_utility_setup(text, self.snapshot)

        elif self.state == "COMMS_SELECTION":
            return self._handle_comms_selection(text)
            
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

        return "Lancelot is ready. How may I serve you?"

    def _verify_handshake(self, text: str) -> str:
        """Verifies the code entered by the user."""
        input_code = text.strip().upper()
        expected_code = self.temp_data.get("verification_code")
        
        if input_code == expected_code:
            # SAVE EVERYTHING
            try:
                with open(self.env_file, "a") as f:
                    f.write(f"\nLANCELOT_COMMS_TYPE={self.temp_data['comms_type']}\n")
                    if self.temp_data['comms_type'] == 'google_chat':
                        f.write(f"LANCELOT_CHAT_SPACE_NAME={self.temp_data['chat_space_name']}\n")
                    elif self.temp_data['comms_type'] == 'telegram':
                        f.write(f"LANCELOT_TELEGRAM_TOKEN={self.temp_data['telegram_token']}\n")
                        f.write(f"LANCELOT_TELEGRAM_CHAT_ID={self.temp_data['telegram_chat_id']}\n")
                
                # Create Restart Flag
                flags_dir = os.path.join(self.data_dir, "FLAGS")
                os.makedirs(flags_dir, exist_ok=True)
                with open(os.path.join(flags_dir, "RESTART_REQUIRED"), "w") as f:
                    f.write("CONFIG_UPDATED")
                
                self.state = "READY"
                return ("**Handshake Verified.** 🤝\n"
                        "Secure Comms Link established.\n\n"
                        "I am restarting my nervous system to bond with these new settings... Please wait.")
            except Exception as e:
                return f"Error saving configuration: {e}"
        else:
            return "❌ Verification Failed. Code does not match. Try again."


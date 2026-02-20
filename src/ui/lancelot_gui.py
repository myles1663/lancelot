"""
Lancelot Launcher
-----------------
The desktop entry point for the Lancelot platform.
"""

import webview
import threading
import time
import requests
import subprocess
import os
import webbrowser

# Configuration
DOCKER_CHECK_CMD = "docker info"
COMPOSE_UP_CMD = "docker-compose up -d"
GUI_TITLE = "🛡️ Lancelot - The Paladin's Visage"
WAR_ROOM_URL = "http://localhost:8000"

class LancelotLauncher:
    def __init__(self):
        self.process = None
        self.window = None
        self.first_run = False
        
        # Check for onboarding requirement (missing USER.md)
        user_profile = os.path.join("lancelot_data", "USER.md")
        # Check for onboarding requirement (missing USER.md or incomplete)
        user_profile = os.path.join("lancelot_data", "USER.md")
        self.first_run = True # Default to true
        
        if os.path.exists(user_profile):
            # Check if actually complete
            with open(user_profile, "r") as f:
                if "OnboardingComplete: True" in f.read():
                    self.first_run = False
        
        if self.first_run:
            print("User missing or incomplete. Engaging Onboarding Protocols.")
            
        # Legacy flag cleanup
        if os.path.exists("first_run.flag"):
            try:
                os.remove("first_run.flag")
            except:
                pass

    def check_docker(self):
        """Verifies Docker Desktop is running."""
        try:
            subprocess.run(DOCKER_CHECK_CMD, shell=True, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except subprocess.CalledProcessError:
            return False
        except FileNotFoundError:
            return False

    def start_engine(self):
        """Starts the backend via Docker Compose."""
        print("Igniting Lancelot Engine...")
        try:
            self.process = subprocess.Popen(COMPOSE_UP_CMD, shell=True)
            # We don't wait for it to finish, it runs in background (-d)
        except Exception as e:
            print(f"Failed to start engine: {e}")

    def monitor_health(self):
        """Polls the Gateway until it's ready, then loads the UI."""
        max_retries = 30
        
        # [NEW] Check for Restart Flag (Hot Reload)
        flags_path = os.path.join("lancelot_data", "FLAGS", "RESTART_REQUIRED")
        if os.path.exists(flags_path):
            print("Restart Signal Detected. Rebooting Fortress...")
            try:
                os.remove(flags_path)
                subprocess.run("docker-compose restart", shell=True)
                time.sleep(5) # Allow shutdown
            except Exception as e:
                print(f"Restart failed: {e}")

        for _ in range(max_retries):
            try:
                requests.get(f"{WAR_ROOM_URL}/health", timeout=2)
                # Engine is up!
                time.sleep(1) # Give Streamlit a breath
                
                final_url = WAR_ROOM_URL
                if self.first_run:
                    final_url += "?onboarding=true"
                    
                if self.window:
                    self.window.load_url(final_url)
                
                # [NEW] Continuous Monitoring Loop for future restarts
                while True:
                    time.sleep(2)
                    if os.path.exists(flags_path):
                        print("Runtime Restart Signal. Rebooting...")
                        os.remove(flags_path)
                        if self.window:
                            # Use load_html instead of data URL for Windows compatibility
                            self.window.load_html("<html><body style='background:#1a1a2e;display:flex;justify-content:center;align-items:center;height:100vh'><h1 style='color:#007bff;font-family:sans-serif;'>Rebooting System...</h1></body></html>")
                        subprocess.run("docker-compose restart", shell=True)
                        break # Break loop to re-enter monitor_health (wait for up)
                        
                return
            except:
                time.sleep(2)
        
        # If we time out
        if self.window:
            self.window.load_html("<html><body style='background:#1a1a2e;padding:40px'><h1 style='color:#ef4444;font-family:sans-serif;'>Connection Failed</h1><p style='color:white;font-family:sans-serif;'>Lancelot Core failed to initialize. Please ensure Docker is running.</p></body></html>")

class JS_API:
    def __init__(self, launcher):
        self._launcher = launcher
    
    def retry_connection(self):
        threading.Thread(target=self._launcher.monitor_health, daemon=True).start()

    def open_external(self, url):
        webbrowser.open(url)

def start_launcher():
    launcher = LancelotLauncher()
    api = JS_API(launcher)
    
    # 1. Environment Check
    if not launcher.check_docker():
        webview.create_window(
            GUI_TITLE,
            html="""
                <body style='background:#0f172a; color:#f1f5f9; font-family:sans-serif; text-align:center; padding-top:50px;'>
                    <h1 style='color:#ef4444;'>Docker Required</h1>
                    <p>Lancelot requires Docker Desktop to function.</p>
                    <p>Please start Docker Desktop and restart Lancelot.</p>
                    <button onclick='pywebview.api.open_external("https://docs.docker.com/desktop/install/windows-install/")' 
                            style='padding:10px 20px; background:#007bff; color:white; border:none; border-radius:5px; cursor:pointer;'>
                        Install Docker
                    </button>
                </body>
            """,
            width=600,
            height=400,
            js_api=api
        )
        webview.start()
        return

    # 2. Start Engine
    launcher.start_engine()

    # 3. Create Loading Window
    launcher.window = webview.create_window(
        GUI_TITLE,
        html="""
            <body style='background:#0f172a; color:#f1f5f9; font-family:sans-serif; text-align:center; display:flex; flex-direction:column; justify-content:center; height:100vh; margin:0;'>
                <h1 style='color:#007bff;'>Initializing Fortress...</h1>
                <p>Spinning up containerized services.</p>
                <div style='margin-top:20px;'><div class='loader'></div></div>
                <style>
                .loader {border: 4px solid #f3f3f3; border-top: 4px solid #3498db; border-radius: 50%; width: 40px; height: 40px; animation: spin 2s linear infinite; margin: 0 auto;}
                @keyframes spin {0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); }}
                </style>
            </body>
        """,
        width=1280,
        height=800,
        resizable=True,
        js_api=api
    )

    # 4. Start Monitoring Thread
    threading.Thread(target=launcher.monitor_health, daemon=True).start()

    webview.start(debug=False)

if __name__ == "__main__":
    start_launcher()

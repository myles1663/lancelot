import streamlit as st
import os
import time
import datetime
from orchestrator import LancelotOrchestrator
from onboarding import OnboardingOrchestrator
from crusader import CrusaderMode, CrusaderAdapter
from receipts import get_receipt_service, ReceiptStatus, ActionType
from panels.tools_panel import get_tools_panel, render_tools_panel

# Page Config
st.set_page_config(
    page_title="Lancelot War Room",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Paths
DATA_DIR = "/home/lancelot/data"
LOGS_DIR = os.path.join(DATA_DIR, "logs")
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# Load Custom CSS
def local_css(file_name):
    with open(file_name) as f:
        st.markdown(f'<style>{f.read()}</style>', unsafe_allow_html=True)

if os.path.exists(os.path.join(STATIC_DIR, "style.css")):
    local_css(os.path.join(STATIC_DIR, "style.css"))

# Initialize Orchestrator
if "orchestrator" not in st.session_state:
    st.session_state.orchestrator = LancelotOrchestrator(data_dir=DATA_DIR)

# Chat History
if "messages" not in st.session_state:
    st.session_state.messages = []

# Crusader Mode session state
if "crusader_session" not in st.session_state:
    st.session_state.crusader_session = CrusaderMode()
    st.session_state.crusader_adapter = CrusaderAdapter()
    st.session_state.crusader_mode = False

# Initialize Onboarding Orchestrator
if "onboarding_orchestrator" not in st.session_state:
    st.session_state.onboarding_orchestrator = OnboardingOrchestrator(data_dir=DATA_DIR)

# [NEW] Check for Onboarding Trigger from Launcher or State
onboard = st.session_state.onboarding_orchestrator
if len(st.session_state.messages) == 0:
    # Auto-detect start state
    initial_msg = None
    
    if onboard.state == "WELCOME":
        initial_msg = "**Squire's Journey Detected**\n\nWelcome, Initiate. I am Lancelot. Let us begin by bonding your identity to this Fortress.\n\n*Please state your name.*"
    elif onboard.state == "HANDSHAKE_API_KEY":
        initial_msg = "**Neural Link Required (LLM)**\n\nPlease provide your **Gemini API Key** to activate my core logic.\n[Get API Key](https://aistudio.google.com/app/apikey)"
    elif onboard.state == "COMMS_SELECTION":
        initial_msg = "Resuming setup. Please select your Communication Channel:\n[1] Google Chat\n[2] Telegram\n[3] Skip"
    elif onboard.state == "HANDSHAKE":
        initial_msg = ("**Authentication Required**\n\n"
                       "I need a valid Google Identity to function.\n\n"
                       "**Option A: Google Cloud ADC (Recommended for PRO)**\n"
                       "Run:\n"
                       "`gcloud auth application-default login --scopes=...`\n"
                       "Then type **'scan'** to detect credentials.\n\n"
                       "**Option B: Gemini API Key**\n"
                       "Paste your API Key below.")
    elif onboard.state in ["COMMS_WEBHOOK_INPUT", "COMMS_TELEGRAM_TOKEN", "COMMS_TELEGRAM_CHAT", "COMMS_VERIFY", "COMMS_ADC_CHECK", "COMMS_CHAT_SCAN"]:
        initial_msg = "Resuming setup. Please complete the communications configuration."
    elif "onboarding" in st.query_params and st.query_params["onboarding"] == "true":
        initial_msg = "Lancelot Redux. Resuming onboarding sequence."
        
    if initial_msg:
        st.session_state.messages.append({"role": "assistant", "content": initial_msg})
        # Clear the param so it doesn't re-trigger on reload
        if "onboarding" in st.query_params:
            st.query_params.clear()

# Sidebar: Live Logs
with st.sidebar:
    # [NEW] Branding Logo
    logo_path = os.path.join(STATIC_DIR, "logo.jpeg")
    if os.path.exists(logo_path):
        st.image(logo_path, use_container_width=True)

    st.title("🛡️ LANCELOT OS")
    
    # [NEW] Restart Indicator
    if os.path.exists(os.path.join(DATA_DIR, "FLAGS", "RESTART_REQUIRED")):
        st.warning("⚠️ System Restart Initiated...")
        st.stop()
        
    st.divider()
    
    st.subheader("System Logs")
    log_files = [f for f in os.listdir(LOGS_DIR) if f.endswith(".txt")] if os.path.exists(LOGS_DIR) else []
    selected_log = st.selectbox("Select Log Stream", log_files, index=0 if log_files else None)

    if selected_log:
        with open(os.path.join(LOGS_DIR, selected_log), "r") as f:
            content = f.read()
            st.text_area("Log Content", content[-3000:], height=400)
    
    st.divider()
    
    # Quick Commands
    st.subheader("Quick Commands")
    if st.button("Check Health", use_container_width=True):
        st.session_state.messages.append({"role": "user", "content": "/health"})
        health_resp = str(st.session_state.orchestrator.execute_command("echo 'System Health: Nominal'")) # Simplified for UI demo
        st.session_state.messages.append({"role": "assistant", "content": health_resp})
        st.rerun()

# Top Bar Status Monitor
st.write('<div style="margin-top: -50px;"></div>', unsafe_allow_html=True) # Adjust Streamlit default padding
cols = st.columns(4)

with cols[0]:
    st.markdown('<div class="status-card"><span class="status-label">Identity Bonded</span><span class="status-value">YES (100%)</span></div>', unsafe_allow_html=True)
with cols[1]:
    st.markdown('<div class="status-card"><span class="status-label">Armor Integrity</span><span class="status-value">98.4%</span></div>', unsafe_allow_html=True)
with cols[2]:
    st.markdown('<div class="status-card"><span class="status-label">Connection</span><span class="status-value scanning-text">ACTIVE</span></div>', unsafe_allow_html=True)
with cols[3]:
    mode_label = "CRUSADER" if st.session_state.crusader_mode else "NORMAL"
    mode_color = "var(--crusader-red)" if st.session_state.crusader_mode else "var(--primary-blue)"
    st.markdown(f'<div class="status-card"><span class="status-label">Defense Posture</span><span class="status-value" style="color: {mode_color}">{mode_label}</span></div>', unsafe_allow_html=True)

st.divider()

# Tabs for Main Interface
tab_command, tab_recovery, tab_audit, tab_tools = st.tabs(["Command Center", "Setup & Recovery", "Neural Audit", "Tool Fabric"])

with tab_command:
    # Main Header
    if st.session_state.crusader_mode:
        st.markdown('<div class="crusader-title"><h1>Lancelot War Room // CRUSADER</h1></div>', unsafe_allow_html=True)
    else:
        st.title("Lancelot War Room")

    # Command Center Panel
    c1, c2 = st.columns([4, 1])

    with c2:
        st.write("### Controls")
        if st.session_state.crusader_mode:
            if st.button("STAND DOWN", use_container_width=True, key="stand_down_btn", type="primary", help="Exit Crusader Mode"):
                response = st.session_state.crusader_session.deactivate()
                st.session_state.crusader_mode = False
                st.session_state.messages.append({"role": "assistant", "content": response})
                st.rerun()
        else:
            st.markdown('<div class="crusader-btn">', unsafe_allow_html=True)
            if st.button("ENGAGE CRUSADER", use_container_width=True, key="engage_btn", help="Enter Decisive Execution Mode"):
                response = st.session_state.crusader_session.activate()
                st.session_state.crusader_mode = True
                st.session_state.messages.append({"role": "assistant", "content": response})
                st.rerun()
            st.markdown('</div>', unsafe_allow_html=True)

    with c1:
        # Display Chat
        chat_container = st.container(height=500)
        with chat_container:
            for msg in st.session_state.messages:
                with st.chat_message(msg["role"]):
                    st.markdown(msg["content"])
                    if "is_draft" in msg:
                        st.warning("Awaiting Confirmation...")

        # Chat Input
        if prompt := st.chat_input("Issue command to Lancelot..."):
            st.session_state.messages.append({"role": "user", "content": prompt})
            
            with chat_container:
                with st.chat_message("user"):
                    st.markdown(prompt)

                with st.chat_message("assistant"):
                    # Check if onboarding is complete first
                    onboard = st.session_state.onboarding_orchestrator
                    
                    if onboard.state != "READY":
                        # Route through onboarding orchestrator
                        response = onboard.process(user="Commander", text=prompt)
                        st.markdown(response)
                        st.session_state.messages.append({"role": "assistant", "content": response})
                        
                        # Check if onboarding just completed
                        if onboard.state == "READY":
                            # Re-initialize the main orchestrator now that we have API key
                            st.session_state.orchestrator = LancelotOrchestrator(data_dir=DATA_DIR)
                            st.success("Setup complete! Lancelot is now operational.")
                        st.rerun()
                    
                    else:
                        # Normal operation - check crusader triggers first
                        crusader_session = st.session_state.crusader_session
                        is_trigger, action = crusader_session.should_intercept(prompt)

                        if is_trigger:
                            if action == "activate":
                                response = crusader_session.activate()
                                st.session_state.crusader_mode = True
                            else:
                                response = crusader_session.deactivate()
                                st.session_state.crusader_mode = False
                            st.markdown(response)
                            st.session_state.messages.append({"role": "assistant", "content": response})
                            st.rerun()

                        elif st.session_state.crusader_mode:
                            adapter = st.session_state.crusader_adapter
                            if adapter.check_auto_pause(prompt):
                                response = (
                                    "Authority required.\n"
                                    "This operation is restricted even in Crusader Mode."
                                )
                                st.error(response)
                            else:
                                raw_response = st.session_state.orchestrator.chat(
                                    prompt, crusader_mode=True
                                )
                                response = adapter.format_response(raw_response)
                                st.markdown(response)
                            st.session_state.messages.append({"role": "assistant", "content": response})

                        else:
                            # Normal mode
                            response = st.session_state.orchestrator.chat(prompt)

                            if response.startswith("DRAFT:"):
                                st.warning("System Learning Detected (Confidence 70-90%)")
                                st.markdown(response)
                                st.session_state.messages.append({"role": "assistant", "content": response, "is_draft": True})
                            else:
                                st.markdown(response)
                                st.session_state.messages.append({"role": "assistant", "content": response})

with tab_recovery:
    from recovery_panel import render_recovery_panel
    render_recovery_panel(DATA_DIR)

with tab_audit:
    st.header("Neural Audit Trail")
    receipt_service = get_receipt_service(DATA_DIR)
    
    # KPIs
    stats = receipt_service.get_stats()
    kpx, kpy, kpz = st.columns(3)
    kpx.metric("Total Actions", stats["total_receipts"])
    kpy.metric("Tokens Processed", stats["tokens"]["total"])
    kpz.metric("Avg Duration", f"{stats['duration_ms']['average']}ms")
    
    st.divider()
    
    # Search
    search_q = st.text_input("Search Receipt History", placeholder="Filter by action, input, or output...")
    
    # List
    if search_q:
        receipts = receipt_service.search(search_q, limit=20)
    else:
        receipts = receipt_service.list(limit=20)
        
    for r in receipts:
        status_icon = "✅" if r.status == "success" else "❌" if r.status == "failure" else "⏳"
        with st.expander(f"{status_icon} [{r.timestamp[:19]}] {r.action_name} ({r.action_type})"):
            st.json(r.to_dict())

with tab_tools:
    # Initialize tools panel in session state if needed
    if "tools_panel" not in st.session_state:
        st.session_state.tools_panel = get_tools_panel()

    render_tools_panel(
        panel=st.session_state.tools_panel,
        streamlit_module=st,
    )

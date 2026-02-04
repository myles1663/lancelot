from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from onboarding import OnboardingOrchestrator
from orchestrator import LancelotOrchestrator
# [NEW] Production Modules
from librarian_v2 import LibrarianV2
from antigravity_engine import AntigravityEngine
from security_bridge import MFAListener, WebhookAuthenticator
from mcp_sentry import MCPSentry
from vault import SecretVault
from sandbox import SandboxExecutor
from api_discovery import APIDiscoveryEngine
from api_discovery import APIDiscoveryEngine
from post_dispatcher import PostDispatcher
from chat_poller import ChatPoller
from crusader import CrusaderMode, CrusaderAdapter
import threading
import time
import uuid
import os
import logging

# F1: Configurable log level
LOG_LEVEL = os.getenv("LANCELOT_LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO))
logger = logging.getLogger("lancelot.gateway")

# S11: Request size limit (1 MB)
MAX_REQUEST_SIZE = 1_048_576

# F8: Startup timestamp for uptime tracking
_startup_time = None


# F2: Structured error response helper
def error_response(status_code: int, message: str, detail: str = None, request_id: str = None) -> JSONResponse:
    content = {"error": message, "status": status_code}
    if detail:
        content["detail"] = detail
    if request_id:
        content["request_id"] = request_id
    return JSONResponse(status_code=status_code, content=content)


class RateLimiter:
    """S11: Sliding-window rate limiter per IP address."""

    def __init__(self, max_requests=60, window_seconds=60):
        self.max_requests = max_requests
        self.window = window_seconds
        self._requests = {}  # ip -> [timestamps]

    def check(self, ip: str) -> bool:
        """Returns True if request is allowed."""
        now = time.time()
        if ip not in self._requests:
            self._requests[ip] = []
        self._requests[ip] = [t for t in self._requests[ip] if t > now - self.window]
        if len(self._requests[ip]) >= self.max_requests:
            return False
        self._requests[ip].append(now)
        return True


app = FastAPI()

# S11: CORS middleware
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:8501").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- API Authentication ---
API_TOKEN = os.getenv("LANCELOT_API_TOKEN")


def verify_token(request: Request) -> bool:
    """Validates Bearer token from Authorization header."""
    if not API_TOKEN:
        # No token configured — allow access (dev mode)
        return True
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:] == API_TOKEN
    return False


# F7: Generate unique request ID
def make_request_id() -> str:
    return str(uuid.uuid4())


# S11: Rate limiter instance
rate_limiter = RateLimiter()

# [NEW] Initialize Production Modules
main_orchestrator = LancelotOrchestrator(data_dir="/home/lancelot/data")
onboarding_orch = OnboardingOrchestrator(data_dir="/home/lancelot/data")
librarian = LibrarianV2(data_dir="/home/lancelot/data")
antigravity = AntigravityEngine(data_dir="/home/lancelot/data")
mfa_guard = MFAListener()
webhook_auth = WebhookAuthenticator()

sentry = MCPSentry(data_dir="/home/lancelot/data")

# Crusader Mode: session-scoped, non-persistent
crusader_mode = CrusaderMode()
crusader_adapter = CrusaderAdapter()

# Forge of Innovation modules
forge_vault = SecretVault(data_dir="/home/lancelot/data")
forge_sandbox = SandboxExecutor()
forge_discovery = APIDiscoveryEngine(orchestrator=main_orchestrator)
forge_dispatcher = PostDispatcher(vault=forge_vault)
chat_poller = ChatPoller(data_dir="/home/lancelot/data", orchestrator=main_orchestrator)


@app.on_event("startup")
async def startup_event():
    global _startup_time
    _startup_time = time.time()

    # F8: Validate environment on startup
    if not os.getenv("GEMINI_API_KEY") and not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        logger.warning("No GEMINI_API_KEY or GOOGLE_APPLICATION_CREDENTIALS set. LLM features may be unavailable.")
    if not API_TOKEN:
        logger.warning("LANCELOT_API_TOKEN not set. Running in dev mode (no auth required).")

    # [NEW] Start Production Services
    librarian.start()
    await antigravity.start()

    # Inject Sentry into Orchestrator (Dependency Injection pattern)
    main_orchestrator.sentry = sentry
    # [NEW] Inject MFA Guard and Antigravity into Orchestrator (Monkey Patching for now)
    main_orchestrator.mfa_guard = mfa_guard
    main_orchestrator.antigravity = antigravity
    
    # Start Chat Polling
    chat_poller.start_polling()
    
    # Register Google Chat for Dispatcher
    forge_dispatcher.register_platform(
        name="google_chat",
        handler=lambda content: chat_poller.send_message(content),
        mode="local"
    )
    
    logger.info("Lancelot Gateway started.")


@app.on_event("shutdown")
async def shutdown_event():
    """F8: Graceful shutdown."""
    logger.info("Lancelot Gateway shutting down.")
    try:
        librarian.stop()
        await antigravity.stop()
        chat_poller.stop_polling()
        main_orchestrator.audit_logger.log_event("GATEWAY_SHUTDOWN", "Graceful shutdown initiated")
    except Exception as e:
        logger.error(f"Shutdown error: {e}")


class ChatMessage(BaseModel):
    text: str
    user: str = "Unknown"


@app.post("/chat")
async def chat_webhook(request: Request):
    """
    Receives JSON payload from Google Chat.
    Routes to Onboarding if identity not bonded or keys missing.
    Intercepts Crusader Mode triggers before routing to orchestrator.
    """
    request_id = make_request_id()

    if not verify_token(request):
        return error_response(401, "Unauthorized", request_id=request_id)

    # S11: Rate limit check
    client_ip = request.client.host if request.client else "unknown"
    if not rate_limiter.check(client_ip):
        return error_response(429, "Rate limit exceeded. Try again later.", request_id=request_id)

    # S11: Request size check
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_REQUEST_SIZE:
        return error_response(413, "Request body too large.", request_id=request_id)

    try:
        data = await request.json()
        message = data.get("text", "")
        user = data.get("user", "Unknown")

        logger.info(f"[{request_id}] Message from {user}: {message[:50]}...")

        # Check Onboarding State
        onboarding_orch.state = onboarding_orch._determine_state()

        if onboarding_orch.state != "READY":
            response_text = onboarding_orch.process(user, message)
        else:
            # --- CRUSADER MODE INTERCEPT ---
            is_trigger, action = crusader_mode.should_intercept(message)

            if is_trigger:
                if action == "activate":
                    response_text = crusader_mode.activate()
                    main_orchestrator.audit_logger.log_event(
                        "CRUSADER_MODE_ACTIVATED",
                        "User activated Crusader Mode",
                        user
                    )
                else:
                    response_text = crusader_mode.deactivate()
                    main_orchestrator.audit_logger.log_event(
                        "CRUSADER_MODE_DEACTIVATED",
                        "User deactivated Crusader Mode",
                        user
                    )
            elif crusader_mode.is_active:
                if crusader_adapter.check_auto_pause(message):
                    response_text = (
                        "Authority required.\n"
                        "This operation is restricted even in Crusader Mode."
                    )
                    main_orchestrator.audit_logger.log_event(
                        "CRUSADER_AUTO_PAUSE",
                        f"Blocked: {message}",
                        user
                    )
                else:
                    response_text = main_orchestrator.chat(
                        message, crusader_mode=True
                    )
                    response_text = crusader_adapter.format_response(
                        response_text
                    )
            else:
                # Standard mode
                response_text = main_orchestrator.chat(message)

        return {
            "response": response_text,
            "crusader_mode": crusader_mode.is_active,
            "request_id": request_id,
        }
    except Exception as e:
        logger.error(f"[{request_id}] Chat error: {e}")
        return error_response(500, "Internal server error", request_id=request_id)


@app.post("/mfa_submit")
async def mfa_submit(request: Request):
    """
    Receives MFA code and unblocks the security bridge.
    Payload: {"code": "123456", "task_id": "..."}
    """
    request_id = make_request_id()
    if not verify_token(request):
        return error_response(401, "Unauthorized", request_id=request_id)
    try:
        data = await request.json()
        code = data.get("code")
        task_id = data.get("task_id", "default")
        
        logger.info(f"[{request_id}] MFA Code Received for Task {task_id}")
        
        success = mfa_guard.submit_code(task_id, code)
        
        if success:
            return {"status": "Code Accepted. Bridge Released.", "request_id": request_id}
        else:
            return error_response(404, "Unknown Task ID or no pending challenge.", request_id=request_id)
            
    except Exception as e:
        return error_response(500, "Internal server error", request_id=request_id)


@app.get("/health")
def health_check():
    """F6: Enhanced health check with component status."""
    try:
        components = {
            "gateway": "ok",
            "orchestrator": "ok" if main_orchestrator.client else "degraded",
            "sentry": "ok",
            "vault": "ok",
            "chromadb": "ok" if main_orchestrator.memory_collection else "unavailable",
        }
        uptime = round(time.time() - _startup_time, 1) if _startup_time else 0
        return {
            "status": "online",
            "version": "3.0",
            "components": components,
            "crusader_mode": crusader_mode.is_active,
            "uptime_seconds": uptime,
        }
    except Exception as exc:
        logger.error("Health check error: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"status": "error", "error": "Health check failed"},
        )


@app.get("/ready")
def readiness_check():
    """F8: Readiness probe — checks all components are initialized."""
    ready = _startup_time is not None
    components = {
        "gateway": "ok",
        "orchestrator": "ok" if main_orchestrator.client else "degraded",
        "sentry": "ok",
        "chromadb": "ok" if main_orchestrator.memory_collection else "unavailable",
    }
    all_ok = all(v == "ok" for v in components.values())
    status_code = 200 if (ready and all_ok) else 503
    return JSONResponse(
        status_code=status_code,
        content={"ready": ready and all_ok, "components": components},
    )


@app.get("/crusader_status")
def crusader_status(request: Request):
    if not verify_token(request):
        return error_response(401, "Unauthorized")
    return {"crusader_mode": crusader_mode.is_active}


# Visual Receipt Helper (Simulated)
from receipt_service import ReceiptService
receipt_svc = ReceiptService()


@app.get("/receipt/{task_id}")
def get_receipt(task_id: str, request: Request):
    request_id = make_request_id()
    if not verify_token(request):
        return error_response(401, "Unauthorized", request_id=request_id)
    try:
        path = receipt_svc.generate_receipt(task_id)
        return {"receipt_path": path, "message": "Receipt generated & sent to Chat.", "request_id": request_id}
    except Exception as e:
        return error_response(500, "Internal server error", request_id=request_id)


@app.post("/mcp_callback")
async def mcp_callback(request: Request):
    """
    Receives 'Approve' click from Google Chat Card.
    Payload: {"request_id": "...", "action": "APPROVE"}
    """
    req_request_id = make_request_id()
    if not verify_token(request):
        return error_response(401, "Unauthorized", request_id=req_request_id)
    try:
        data = await request.json()
        req_id = data.get("request_id")
        action = data.get("action")

        if action == "APPROVE":
            success = sentry.approve_request(req_id)
            if success:
                return {"status": "Request Approved. Agent resuming...", "request_id": req_request_id}
            else:
                return error_response(400, "Request ID not found or invalid.", request_id=req_request_id)
        return {"status": "Action ignored.", "request_id": req_request_id}
    except Exception as e:
        return error_response(500, "Internal server error", request_id=req_request_id)


# --- Forge of Innovation Endpoints ---

@app.post("/forge/discover")
async def forge_discover(request: Request):
    """
    Scrapes API documentation and generates a manifest + wrapper script.
    Payload: {"url": "https://... or raw doc text"}
    """
    request_id = make_request_id()
    if not verify_token(request):
        return error_response(401, "Unauthorized", request_id=request_id)
    try:
        data = await request.json()
        url_or_text = data.get("url", "")
        if not url_or_text:
            return error_response(400, "Missing 'url' field", request_id=request_id)

        doc_text = forge_discovery.scrape_docs(url_or_text)
        manifest = forge_discovery.generate_manifest(doc_text)
        script = forge_discovery.generate_wrapper_script(manifest)

        return {
            "manifest": manifest,
            "generated_script": script,
            "endpoint_count": len(manifest.get("endpoints", [])),
            "request_id": request_id,
        }
    except Exception as e:
        return error_response(500, "Internal server error", request_id=request_id)


@app.post("/forge/dispatch")
async def forge_dispatch(request: Request):
    """
    Dispatches content to platforms based on tags in the prompt.
    Payload: {"content": "...", "prompt": "Post this [twitter:local:post]"}
    """
    request_id = make_request_id()
    if not verify_token(request):
        return error_response(401, "Unauthorized", request_id=request_id)
    try:
        data = await request.json()
        content = data.get("content", "")
        prompt = data.get("prompt", "")
        if not content:
            return error_response(400, "Missing 'content' field", request_id=request_id)

        results = forge_dispatcher.dispatch_from_prompt(prompt, content)
        return {"results": results, "dispatched_count": len(results), "request_id": request_id}
    except Exception as e:
        return error_response(500, "Internal server error", request_id=request_id)


# --- Live API (Real-Time Streaming) ---

from live_session import LiveSessionManager


@app.websocket("/live")
async def live_stream(websocket: WebSocket):
    """
    WebSocket endpoint for real-time streaming via Gemini Live API.
    Auth via query param: ws://host:8000/live?token=<LANCELOT_API_TOKEN>
    """
    await websocket.accept()

    # Auth check via query param
    token = websocket.query_params.get("token", "")
    if API_TOKEN and token != API_TOKEN:
        await websocket.close(code=4001, reason="Unauthorized")
        return

    if not main_orchestrator.client:
        await websocket.send_text("Error: Gemini client not initialized.")
        await websocket.close(code=4002, reason="Service unavailable")
        return

    session_mgr = LiveSessionManager(
        client=main_orchestrator.client,
        model_name=main_orchestrator.model_name,
        system_instruction=main_orchestrator._build_system_instruction(),
    )

    try:
        await session_mgr.connect()
        logger.info("Live API session connected.")
        while True:
            data = await websocket.receive_text()
            async for chunk in session_mgr.send_text(data):
                await websocket.send_text(chunk)
    except WebSocketDisconnect:
        logger.info("Live API session disconnected.")
    except Exception as e:
        logger.error(f"Live API error: {e}")
        try:
            await websocket.send_text("Error: internal server error")
        except Exception:
            pass
    finally:
        await session_mgr.close()


# --- UCP (Universal Commerce Protocol) ---

from ucp_connector import UCPConnector

ucp_connector = UCPConnector(audit_logger=main_orchestrator.audit_logger)


@app.post("/ucp/discover")
async def ucp_discover(request: Request):
    """Discovers UCP capabilities from a merchant URL."""
    request_id = make_request_id()
    if not verify_token(request):
        return error_response(401, "Unauthorized", request_id=request_id)
    try:
        data = await request.json()
        merchant_url = data.get("merchant_url", "")
        if not merchant_url:
            return error_response(400, "Missing 'merchant_url' field", request_id=request_id)

        manifest = ucp_connector.discover_merchant(merchant_url)
        return {"manifest": manifest, "request_id": request_id}
    except Exception as e:
        return error_response(500, "Internal server error", request_id=request_id)


@app.post("/ucp/search")
async def ucp_search(request: Request):
    """Searches products via a UCP-enabled merchant."""
    request_id = make_request_id()
    if not verify_token(request):
        return error_response(401, "Unauthorized", request_id=request_id)
    try:
        data = await request.json()
        merchant_url = data.get("merchant_url", "")
        query = data.get("query", "")
        if not merchant_url or not query:
            return error_response(400, "Missing 'merchant_url' or 'query' field", request_id=request_id)

        results = ucp_connector.search_products(merchant_url, query)
        return {"results": results, "result_count": len(results), "request_id": request_id}
    except Exception as e:
        return error_response(500, "Internal server error", request_id=request_id)


@app.post("/ucp/transact")
async def ucp_transact(request: Request):
    """Initiates a commerce transaction (requires Sentry approval)."""
    request_id = make_request_id()
    if not verify_token(request):
        return error_response(401, "Unauthorized", request_id=request_id)
    try:
        data = await request.json()
        merchant_url = data.get("merchant_url", "")
        product_id = data.get("product_id", "")
        params = data.get("params", {})
        if not merchant_url or not product_id:
            return error_response(400, "Missing 'merchant_url' or 'product_id' field", request_id=request_id)

        # Sentry permission check for UCP transactions (HIGH risk)
        perm = sentry.check_permission("ucp_transaction", {
            "merchant_url": merchant_url,
            "product_id": product_id,
        })
        if perm["status"] == "PENDING":
            return {
                "status": "pending_approval",
                "message": perm["message"],
                "sentry_request_id": perm["request_id"],
                "request_id": request_id,
            }
        elif perm["status"] == "DENIED":
            return error_response(403, perm["message"], request_id=request_id)

        result = ucp_connector.initiate_transaction(merchant_url, product_id, params)
        return {"transaction": result, "request_id": request_id}
    except Exception as e:
        return error_response(500, "Internal server error", request_id=request_id)


@app.post("/ucp/confirm")
async def ucp_confirm(request: Request):
    """Confirms a pending UCP transaction after user approval."""
    request_id = make_request_id()
    if not verify_token(request):
        return error_response(401, "Unauthorized", request_id=request_id)
    try:
        data = await request.json()
        transaction_id = data.get("transaction_id", "")
        if not transaction_id:
            return error_response(400, "Missing 'transaction_id' field", request_id=request_id)

        result = ucp_connector.confirm_transaction(transaction_id)
        return {"result": result, "request_id": request_id}
    except Exception as e:
        return error_response(500, "Internal server error", request_id=request_id)

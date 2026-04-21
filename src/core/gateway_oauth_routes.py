from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse

logger = logging.getLogger("lancelot.gateway.routes")
router = APIRouter()

def bind_gateway_globals(**kwargs):
    globals().update(kwargs)

# --- Anthropic OAuth callback (browser redirect) ---

@router.get("/callback")
async def oauth_anthropic_callback(request: Request):
    """Receive OAuth authorization code from browser redirect after user authorises."""
    error = request.query_params.get("error")
    if error:
        desc = request.query_params.get("error_description", error)
        return render_callback_page("Authorization Failed", desc, status_code=400)

    code = request.query_params.get("code")
    state = request.query_params.get("state")
    if not code or not state:
        return render_callback_page(
            "Missing Parameters",
            "No authorization code received.",
            status_code=400,
        )

    try:
        from oauth_token_manager import get_oauth_manager
        manager = get_oauth_manager()
        if manager is None:
            raise RuntimeError("OAuth manager not initialized")
        success = manager.exchange_code(code, state)
        if success:
            # Recover provider wiring if startup ran before OAuth was available.
            if main_orchestrator.provider is None:
                main_orchestrator._init_provider()
                if main_orchestrator.provider:
                    logger.info("Provider hot-initialized via OAuth callback.")

            # Refresh model discovery so War Room lane metadata reflects the new provider.
            _bootstrap_model_discovery()

            # Advance onboarding state once OAuth completes successfully.
            try:
                from onboarding_snapshot import OnboardingState
                snap = onboarding_orch.snapshot
                if snap.credential_status in ("oauth_pending", "none"):
                    snap.credential_status = "verified"
                    if snap.state != OnboardingState.READY:
                        snap.state = OnboardingState.READY
                    snap.save()
                    logger.info("Onboarding credential_status updated to verified (OAuth complete).")
            except Exception as _e:
                logger.warning("Could not update onboarding after OAuth: %s", _e)

            return HTMLResponse(
                "<html><body style='font-family:sans-serif;text-align:center;padding:60px'>"
                "<h2 style='color:#22c55e'>Authorization Successful</h2>"
                "<p>Lancelot is now connected to your Anthropic account via OAuth.</p>"
                "<p style='color:#888'>You may close this tab.</p>"
                "<script>setTimeout(function(){window.close()},3000)</script>"
                "</body></html>"
            )
        else:
            return render_callback_page(
                "Authorization Failed",
                "Invalid or expired authorization state. Please try again from Lancelot.",
                status_code=400,
            )
    except Exception as e:
        logger.error("OAuth callback error: %s", e)
        return render_callback_exception_page("OAuth")


# --- OpenAI Codex OAuth Callback (unauthenticated â€” browser redirect) ---

@router.get("/auth/callback")
async def oauth_codex_callback(request: Request):
    """Receive OAuth authorization code from browser redirect after user authorises with ChatGPT.

    Path must be /auth/callback to match OpenAI's registered redirect URI for the Codex public client.
    """
    error = request.query_params.get("error")
    if error:
        desc = request.query_params.get("error_description", error)
        return render_callback_page("Authorization Failed", desc, status_code=400)

    code = request.query_params.get("code")
    state = request.query_params.get("state")
    if not code or not state:
        return render_callback_page(
            "Missing Parameters",
            "No authorization code received.",
            status_code=400,
        )

    try:
        from openai_codex_oauth_manager import get_openai_codex_manager
        manager = get_openai_codex_manager()
        if manager is None:
            raise RuntimeError("Codex OAuth manager not initialized")
        success = manager.exchange_code(code, state)
        if success:
            # Re-init provider if it wasn't initialized at startup
            if main_orchestrator.provider is None or main_orchestrator._provider_name != "openai-codex":
                # Switch to openai-codex provider
                os.environ["LANCELOT_PROVIDER"] = "openai-codex"
                os.environ["LANCELOT_AUTH_MODE"] = "OAUTH"
                main_orchestrator._init_provider()
                if main_orchestrator.provider:
                    logger.info("Provider hot-initialized via Codex OAuth callback.")

            # Persist provider selection so restarts recover onto Codex OAuth.
            try:
                from providers.api import _read_current_config, _save_config, _update_env_file

                _update_env_file("LANCELOT_PROVIDER", "openai-codex")
                _update_env_file("LANCELOT_AUTH_MODE", "OAUTH")

                _provider_cfg = _read_current_config()
                _provider_cfg["active_provider"] = "openai-codex"
                _save_config(_provider_cfg)
            except Exception as _e:
                logger.warning("Could not persist Codex OAuth provider selection: %s", _e)

            # Bootstrap model discovery so lanes appear in War Room
            _bootstrap_model_discovery()

            # Update onboarding snapshot
            try:
                from onboarding_snapshot import OnboardingState
                snap = onboarding_orch.snapshot
                if snap.credential_status in ("oauth_pending", "none"):
                    snap.credential_status = "verified"
                    if snap.state != OnboardingState.READY:
                        snap.state = OnboardingState.READY
                    snap.save()
                    logger.info("Onboarding credential_status updated to verified (Codex OAuth complete).")
            except Exception as _e:
                logger.warning("Could not update onboarding after Codex OAuth: %s", _e)

            return HTMLResponse(
                "<html><body style='font-family:sans-serif;text-align:center;padding:60px'>"
                "<h2 style='color:#22c55e'>Authorization Successful</h2>"
                "<p>Lancelot is now connected to your ChatGPT account via Codex OAuth.</p>"
                "<p style='color:#888'>Your Pro subscription will be used for API access. You may close this tab.</p>"
                "<script>setTimeout(function(){window.close()},3000)</script>"
                "</body></html>"
            )
        else:
            return render_callback_page(
                "Authorization Failed",
                "Invalid or expired authorization state. Please try again from Lancelot.",
                status_code=400,
            )
    except Exception as e:
        logger.error("Codex OAuth callback error: %s", e)
        return render_callback_exception_page("Codex OAuth")


# --- Google OAuth 2.0 endpoints ---

@router.get("/google/callback")
async def google_oauth_callback(request: Request):
    """Receive Google OAuth authorization code from browser redirect.

    This endpoint is intentionally unauthenticated because Google redirects the
    browser here after consent. State nonce validation happens in exchange_code().
    """
    error = request.query_params.get("error")
    if error:
        desc = request.query_params.get("error_description", error)
        return render_callback_page("Authorization Failed", desc, status_code=400)

    code = request.query_params.get("code")
    state = request.query_params.get("state")
    if not code or not state:
        return render_callback_page(
            "Missing Parameters",
            "No authorization code received.",
            status_code=400,
        )

    try:
        from google_oauth_manager import get_google_oauth_manager
        manager = get_google_oauth_manager()
        if manager is None:
            raise RuntimeError("Google OAuth manager not initialized")
        success = manager.exchange_code(code, state)
        if success:
            return HTMLResponse(
                "<html><body style='font-family:sans-serif;text-align:center;padding:60px'>"
                "<h2 style='color:#22c55e'>Google Authorization Successful</h2>"
                "<p>Lancelot is now connected to your Google account.</p>"
                "<p>Gmail and Calendar access is now active.</p>"
                "<p style='color:#888'>You may close this tab.</p>"
                "<script>setTimeout(function(){window.close()},3000)</script>"
                "</body></html>"
            )
        else:
            return render_callback_page(
                "Authorization Failed",
                "Invalid or expired authorization state. Please try again from Lancelot.",
                status_code=400,
            )
    except Exception as e:
        logger.error("Google OAuth callback error: %s", e)
        return render_callback_exception_page("Google OAuth")


@router.get("/api/google-oauth/status")
async def google_oauth_status(request: Request):
    """Return current Google OAuth token health."""
    request_id = str(uuid.uuid4())[:8]
    authz_error = _require_request_capability(
        request, "connectors.admin", request_id=request_id
    )
    if authz_error is not None:
        return authz_error

    try:
        from google_oauth_manager import get_google_oauth_manager
        from feature_flags import FEATURE_GOOGLE_OAUTH
        manager = get_google_oauth_manager()
        if not manager:
            return {
                "status": "not_configured",
                "feature_enabled": FEATURE_GOOGLE_OAUTH,
                "request_id": request_id,
            }
        status = manager.get_status()
        status["feature_enabled"] = FEATURE_GOOGLE_OAUTH
        status["request_id"] = request_id
        return status
    except Exception as e:
        logger.error("[%s] Google OAuth status error: %s", request_id, e)
        return error_response(500, "Internal server error", request_id=request_id)


@router.post("/api/google-oauth/revoke")
async def google_oauth_revoke(request: Request):
    """Revoke Google OAuth tokens and clear stored credentials."""
    request_id = str(uuid.uuid4())[:8]
    authz_error = _require_request_capability(
        request, "connectors.admin", request_id=request_id
    )
    if authz_error is not None:
        return authz_error

    try:
        from google_oauth_manager import get_google_oauth_manager
        manager = get_google_oauth_manager()
        if not manager:
            return error_response(500, "Google OAuth manager not initialized", request_id=request_id)

        manager.revoke()
        return {
            "status": "revoked",
            "message": "All Google OAuth tokens have been cleared.",
            "request_id": request_id,
        }
    except Exception as e:
        logger.error("[%s] Google OAuth revoke error: %s", request_id, e)
        return error_response(500, "Internal server error", request_id=request_id)


# --- Workspace file download endpoint ---
# Serve generated workspace artifacts so chat responses can reference downloads.

_WORKSPACE_ROOT = Path(os.getenv("LANCELOT_WORKSPACE", "/home/lancelot/workspace"))

@router.get("/api/files/{file_path:path}")
async def serve_workspace_file(file_path: str, request: Request):
    """Serve a file from the workspace for download.

    Enables chat responses to include clickable download links for generated
    documents while blocking path traversal outside the workspace directory.
    """
    if not verify_token(request):
        return error_response(401, "Unauthorized")

    # Resolve and validate the path before serving any workspace content.
    try:
        target = (_WORKSPACE_ROOT / file_path).resolve()
        if not str(target).startswith(str(_WORKSPACE_ROOT.resolve())):
            return error_response(403, "Path traversal blocked")
    except Exception:
        return error_response(400, "Invalid file path")

    if not target.is_file():
        return error_response(404, f"File not found: {file_path}")

    # Render common document/image types inline and download everything else.
    suffix = target.suffix.lower()
    inline_types = {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".txt", ".md", ".csv", ".html"}
    disposition = "inline" if suffix in inline_types else "attachment"

    return FileResponse(
        path=str(target),
        filename=target.name,
        content_disposition_type=disposition,
    )

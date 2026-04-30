import uuid
from typing import Any, Callable

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from gateway_request_models import (
    ForgeDiscoverRequest,
    ForgeDispatchRequest,
    GoogleOauthStartRequest,
    McpCallbackRequest,
    UcpConfirmRequest,
    UcpDiscoverRequest,
    UcpSearchRequest,
    UcpTransactRequest,
    parse_request_model_or_error,
)


def create_gateway_admin_router(
    *,
    error_response: Callable[..., JSONResponse],
    require_request_capability: Callable[..., JSONResponse | None],
    make_request_id: Callable[[], str],
    webhook_auth: Any,
    sentry: Any,
    forge_discovery: Any,
    forge_dispatcher: Any,
    ucp_connector: Any,
    logger: Any,
) -> APIRouter:
    router = APIRouter()

    @router.post("/mcp_callback")
    async def mcp_callback(request: Request):
        """
        Receives 'Approve' click from Google Chat Card.
        Payload: {"request_id": "...", "action": "APPROVE"}
        """
        req_request_id = make_request_id()
        auth_header = request.headers.get("authorization", "")
        webhook_authorized = webhook_auth.verify_remote_header(auth_header)
        if not webhook_authorized:
            authz_error = require_request_capability(
                request, "governance.admin", request_id=req_request_id
            )
            if authz_error is not None:
                return authz_error
        try:
            body = await parse_request_model_or_error(
                request,
                McpCallbackRequest,
                req_request_id,
                error_response=error_response,
            )
            if isinstance(body, JSONResponse):
                return body
            req_id = body.request_id
            action = body.action

            if action == "APPROVE":
                success = sentry.approve_request(req_id)
                if success:
                    return {
                        "status": "Request Approved. Agent resuming...",
                        "request_id": req_request_id,
                    }
                return error_response(
                    400,
                    "Request ID not found or invalid.",
                    request_id=req_request_id,
                )
            return {"status": "Action ignored.", "request_id": req_request_id}
        except Exception:
            return error_response(500, "Internal server error", request_id=req_request_id)

    @router.post("/forge/discover")
    async def forge_discover(request: Request):
        """
        Scrapes API documentation and generates a manifest + wrapper script.
        Payload: {"url": "https://... or raw doc text"}
        """
        request_id = make_request_id()
        authz_error = require_request_capability(
            request, "platform.admin", request_id=request_id
        )
        if authz_error is not None:
            return authz_error
        try:
            body = await parse_request_model_or_error(
                request,
                ForgeDiscoverRequest,
                request_id,
                error_response=error_response,
            )
            if isinstance(body, JSONResponse):
                return body
            url_or_text = body.url
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
        except Exception:
            return error_response(500, "Internal server error", request_id=request_id)

    @router.post("/forge/dispatch")
    async def forge_dispatch(request: Request):
        """
        Dispatches content to platforms based on tags in the prompt.
        Payload: {"content": "...", "prompt": "Post this [twitter:local:post]"}
        """
        request_id = make_request_id()
        authz_error = require_request_capability(
            request, "platform.admin", request_id=request_id
        )
        if authz_error is not None:
            return authz_error
        try:
            body = await parse_request_model_or_error(
                request,
                ForgeDispatchRequest,
                request_id,
                error_response=error_response,
            )
            if isinstance(body, JSONResponse):
                return body
            content = body.content
            prompt = body.prompt
            if not content:
                return error_response(400, "Missing 'content' field", request_id=request_id)

            results = forge_dispatcher.dispatch_from_prompt(prompt, content)
            return {
                "results": results,
                "dispatched_count": len(results),
                "request_id": request_id,
            }
        except Exception:
            return error_response(500, "Internal server error", request_id=request_id)

    @router.post("/ucp/discover")
    async def ucp_discover(request: Request):
        """Discovers UCP capabilities from a merchant URL."""
        request_id = make_request_id()
        authz_error = require_request_capability(
            request, "platform.admin", request_id=request_id
        )
        if authz_error is not None:
            return authz_error
        try:
            body = await parse_request_model_or_error(
                request,
                UcpDiscoverRequest,
                request_id,
                error_response=error_response,
            )
            if isinstance(body, JSONResponse):
                return body
            merchant_url = body.merchant_url
            if not merchant_url:
                return error_response(
                    400,
                    "Missing 'merchant_url' field",
                    request_id=request_id,
                )

            manifest = ucp_connector.discover_merchant(merchant_url)
            return {"manifest": manifest, "request_id": request_id}
        except Exception:
            return error_response(500, "Internal server error", request_id=request_id)

    @router.post("/ucp/search")
    async def ucp_search(request: Request):
        """Searches products via a UCP-enabled merchant."""
        request_id = make_request_id()
        authz_error = require_request_capability(
            request, "platform.admin", request_id=request_id
        )
        if authz_error is not None:
            return authz_error
        try:
            body = await parse_request_model_or_error(
                request,
                UcpSearchRequest,
                request_id,
                error_response=error_response,
            )
            if isinstance(body, JSONResponse):
                return body
            merchant_url = body.merchant_url
            query = body.query
            if not merchant_url or not query:
                return error_response(
                    400,
                    "Missing 'merchant_url' or 'query' field",
                    request_id=request_id,
                )

            results = ucp_connector.search_products(merchant_url, query)
            return {
                "results": results,
                "result_count": len(results),
                "request_id": request_id,
            }
        except Exception:
            return error_response(500, "Internal server error", request_id=request_id)

    @router.post("/ucp/transact")
    async def ucp_transact(request: Request):
        """Initiates a commerce transaction (requires Sentry approval)."""
        request_id = make_request_id()
        authz_error = require_request_capability(
            request, "platform.admin", request_id=request_id
        )
        if authz_error is not None:
            return authz_error
        try:
            body = await parse_request_model_or_error(
                request,
                UcpTransactRequest,
                request_id,
                error_response=error_response,
            )
            if isinstance(body, JSONResponse):
                return body
            merchant_url = body.merchant_url
            product_id = body.product_id
            params = body.params
            if not merchant_url or not product_id:
                return error_response(
                    400,
                    "Missing 'merchant_url' or 'product_id' field",
                    request_id=request_id,
                )

            perm = sentry.check_permission(
                "ucp_transaction",
                {
                    "merchant_url": merchant_url,
                    "product_id": product_id,
                },
            )
            if perm["status"] == "PENDING":
                return {
                    "status": "pending_approval",
                    "message": perm["message"],
                    "sentry_request_id": perm["request_id"],
                    "request_id": request_id,
                }
            if perm["status"] == "DENIED":
                return error_response(403, perm["message"], request_id=request_id)

            result = ucp_connector.initiate_transaction(merchant_url, product_id, params)
            return {"transaction": result, "request_id": request_id}
        except Exception:
            return error_response(500, "Internal server error", request_id=request_id)

    @router.post("/ucp/confirm")
    async def ucp_confirm(request: Request):
        """Confirms a pending UCP transaction after user approval."""
        request_id = make_request_id()
        authz_error = require_request_capability(
            request, "platform.admin", request_id=request_id
        )
        if authz_error is not None:
            return authz_error
        try:
            body = await parse_request_model_or_error(
                request,
                UcpConfirmRequest,
                request_id,
                error_response=error_response,
            )
            if isinstance(body, JSONResponse):
                return body
            transaction_id = body.transaction_id
            if not transaction_id:
                return error_response(
                    400,
                    "Missing 'transaction_id' field",
                    request_id=request_id,
                )

            result = ucp_connector.confirm_transaction(transaction_id)
            return {"result": result, "request_id": request_id}
        except Exception:
            return error_response(500, "Internal server error", request_id=request_id)

    @router.post("/api/google-oauth/start")
    async def google_oauth_start(request: Request):
        """Accept client_id + client_secret, store in vault, return Google consent URL."""
        request_id = str(uuid.uuid4())[:8]
        authz_error = require_request_capability(
            request, "connectors.admin", request_id=request_id
        )
        if authz_error is not None:
            return authz_error

        from feature_flags import FEATURE_GOOGLE_OAUTH

        if not FEATURE_GOOGLE_OAUTH:
            return error_response(
                403,
                "Google OAuth is disabled. Set FEATURE_GOOGLE_OAUTH=true.",
                request_id=request_id,
            )

        try:
            from google_oauth_manager import (
                GoogleOAuthManager,
                get_google_oauth_manager,
                set_google_oauth_manager,
            )

            manager = get_google_oauth_manager()
            if not manager:
                try:
                    from credential_api import _vault as _lazy_vault

                    if _lazy_vault:
                        manager = GoogleOAuthManager(vault=_lazy_vault)
                        set_google_oauth_manager(manager)
                        logger.info(
                            "Google OAuth manager lazy-initialized (flag toggled at runtime)"
                        )
                except Exception as exc:
                    logger.warning("Google OAuth lazy-init failed: %s", exc)
            if not manager:
                return error_response(
                    500,
                    "Google OAuth manager not initialized",
                    request_id=request_id,
                )

            body = await parse_request_model_or_error(
                request,
                GoogleOauthStartRequest,
                request_id,
                error_response=error_response,
            )
            if isinstance(body, JSONResponse):
                return body
            client_id = body.client_id.strip()
            client_secret = body.client_secret.strip()

            if not client_id or not client_secret:
                return error_response(
                    400,
                    "Both client_id and client_secret are required",
                    request_id=request_id,
                )

            auth_url = manager.generate_auth_url(client_id, client_secret)
            return {
                "auth_url": auth_url,
                "message": (
                    "Open this URL in your browser to authorize Gmail and Calendar access."
                ),
                "request_id": request_id,
            }
        except Exception as exc:
            logger.error("[%s] Google OAuth start error: %s", request_id, exc)
            return error_response(500, "Internal server error", request_id=request_id)

    return router

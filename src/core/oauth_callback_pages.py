"""Safe HTML renderers for OAuth browser callback pages."""

from __future__ import annotations

import html

from fastapi.responses import HTMLResponse


def render_callback_page(
    title: str,
    message: str,
    *,
    status_code: int = 200,
    success: bool = False,
    auto_close: bool = False,
) -> HTMLResponse:
    """Render a minimal OAuth callback page with escaped content."""
    safe_title = html.escape(title, quote=True)
    safe_message = html.escape(message, quote=True)
    title_style = " style='color:#22c55e'" if success else ""
    close_script = "<script>setTimeout(function(){window.close()},3000)</script>" if auto_close else ""
    body = (
        "<html><body style='font-family:sans-serif;text-align:center;padding:60px'>"
        f"<h2{title_style}>{safe_title}</h2><p>{safe_message}</p>"
        f"{close_script}</body></html>"
    )
    return HTMLResponse(body, status_code=status_code)


def render_callback_exception_page(provider_name: str) -> HTMLResponse:
    """Render a generic callback failure page without leaking exception text."""
    return render_callback_page(
        "Authorization Error",
        f"Lancelot could not complete the {provider_name} callback. Please retry from the War Room.",
        status_code=500,
    )

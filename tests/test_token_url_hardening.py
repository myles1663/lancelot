from pathlib import Path

from src.core.orch_helpers.response_helpers import append_download_links


def test_append_download_links_does_not_embed_api_token(monkeypatch):
    monkeypatch.setenv("LANCELOT_WORKSPACE", "/tmp/workspace")
    monkeypatch.setenv("LANCELOT_API_TOKEN", "secret-token")

    result = append_download_links(
        "Done",
        ["/tmp/workspace/reports/summary.pdf"],
    )

    assert "/api/files/reports/summary.pdf" in result
    assert "?token=" not in result
    assert "secret-token" not in result


def test_warroom_ws_source_does_not_accept_query_param_tokens():
    source = Path("src/core/warroom_ws.py").read_text(encoding="utf-8")

    assert 'query_params.get("token"' not in source


def test_gateway_source_removes_query_token_auth_from_live_and_file_routes():
    source = Path("src/core/gateway.py").read_text(encoding="utf-8")

    live_idx = source.find('async def live_stream')
    files_idx = source.find('async def serve_workspace_file')
    assert live_idx != -1
    assert files_idx != -1

    live_body = source[live_idx:files_idx]
    files_body = source[files_idx:files_idx + 900]

    assert 'query_params.get("token"' not in live_body
    assert 'token_param' not in files_body
    assert 'auth != f"Bearer {API_TOKEN}"' in files_body


def test_chat_message_source_uses_authorized_fetch_for_workspace_downloads():
    source = Path("src/warroom/src/pages/command/ChatMessage.tsx").read_text(encoding="utf-8")

    assert "fetch(url.toString(), { headers })" in source
    assert "Authorization = `Bearer ${token}`" in source
    assert "url.pathname.startsWith('/api/files/')" in source

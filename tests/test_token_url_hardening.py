import json
from pathlib import Path

import gateway
import gateway_oauth_routes
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

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


def test_live_websocket_rejects_query_param_tokens(monkeypatch):
    monkeypatch.setattr(gateway, "API_TOKEN", "secret-token")

    client = TestClient(gateway.app)
    with client.websocket_connect("/live?token=secret-token") as websocket:
        websocket.send_text(json.dumps({"type": "ping"}))
        with pytest.raises(WebSocketDisconnect) as excinfo:
            websocket.receive_text()

    assert excinfo.value.code == 4001


def test_workspace_file_route_requires_header_auth_not_query_token(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    report_dir = workspace / "reports"
    report_dir.mkdir(parents=True)
    report = report_dir / "summary.pdf"
    report.write_text("pdf-bytes", encoding="utf-8")

    monkeypatch.setattr(gateway, "API_TOKEN", "secret-token")
    monkeypatch.setattr(gateway_oauth_routes, "_WORKSPACE_ROOT", workspace)

    client = TestClient(gateway.app)
    unauthorized = client.get("/api/files/reports/summary.pdf?token=secret-token")
    assert unauthorized.status_code == 401

    authorized = client.get(
        "/api/files/reports/summary.pdf",
        headers={"Authorization": "Bearer secret-token"},
    )
    assert authorized.status_code == 200
    assert authorized.content == b"pdf-bytes"


def test_chat_message_source_uses_authorized_fetch_for_workspace_downloads():
    source = Path("src/warroom/src/pages/command/ChatMessage.tsx").read_text(encoding="utf-8")

    assert "fetch(url.toString(), { credentials: 'include' })" in source
    assert "url.pathname.startsWith('/api/files/')" in source

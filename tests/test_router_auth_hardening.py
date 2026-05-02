import importlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core import api_auth


ROUTE_CASES = [
    ("src.core.governance_api", "/api/governance/approvals"),
    ("src.core.trust_api", "/api/trust/records"),
    ("src.core.apl_api", "/api/apl/rules"),
    ("src.core.tools_api", "/api/tools/health"),
    ("src.core.skills_api", "/api/skills"),
    ("src.core.receipts_api", "/api/receipts"),
    ("src.core.connectors_api", "/api/connectors"),
    ("src.connectors.credential_api", "/connectors/email/credentials/status"),
    ("src.core.flags_api", "/api/flags"),
    ("src.core.providers.api", "/api/v1/providers/stack"),
    ("src.core.memory.api", "/memory/stats"),
    ("src.core.soul.api", "/soul/status"),
    ("src.core.soul.template_api", "/soul/templates"),
    ("src.core.scheduler_api", "/api/scheduler/jobs"),
    ("src.observability.api", "/api/observability/config"),
    ("src.observability.metrics_api", "/api/metrics/summary"),
    ("src.timetravel.api", "/api/timetravel/status"),
    ("src.a2a.api", "/api/a2a/status"),
    ("src.incidents.api", "/api/incidents/stats"),
    ("src.incidents.playbook_api", "/api/playbooks"),
    ("src.mcp.api", "/api/mcp/servers"),
    ("src.compliance.api", "/api/compliance/history"),
    ("src.core.update_api", "/api/updates/status"),
    ("src.core.actioncard_api", "/api/actioncards/"),
    ("src.hive.api", "/api/hive/status"),
    ("src.federation.api", "/api/federation/status"),
    ("src.federation.graph_api", "/api/federation/graph/topologies/active"),
]


def _build_client(module_name: str, verify_request):
    module = importlib.import_module(module_name)
    app = FastAPI()
    api_auth.init_api_auth(verify_request)
    router = getattr(module, "router", None) or getattr(module, "graph_router", None)
    app.include_router(router)
    return TestClient(app)


@pytest.mark.parametrize(("module_name", "path"), ROUTE_CASES)
def test_hardened_routers_reject_unauthenticated_requests(module_name, path):
    client = _build_client(module_name, verify_request=lambda request: False)

    response = client.get(path)

    assert response.status_code == 401
    assert response.json()["detail"] == "Unauthorized"


def test_hardened_routers_fail_closed_when_auth_not_initialized():
    client = _build_client("src.core.receipts_api", verify_request=None)

    response = client.get("/api/receipts")

    assert response.status_code == 503
    assert response.json()["detail"] == "API auth not configured"


@pytest.mark.parametrize(
    ("module_name", "path"),
    [
        ("src.core.receipts_api", "/api/receipts"),
        ("src.core.providers.api", "/api/v1/providers/stack"),
        ("src.core.flags_api", "/api/flags"),
    ],
)
def test_hardened_routers_allow_authenticated_requests_to_reach_handler(module_name, path):
    client = _build_client(
        module_name,
        verify_request=lambda request: request.headers.get("authorization") == "Bearer good-token",
    )

    response = client.get(path, headers={"Authorization": "Bearer good-token"})

    assert response.status_code != 401
    assert response.status_code != 503

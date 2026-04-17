from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import orchestrator as orch_mod
from src.core.operator_identity import OperatorIdentity


def _build_minimal_orchestrator():
    orch = orch_mod.LancelotOrchestrator.__new__(orch_mod.LancelotOrchestrator)
    orch.wake_up = lambda *_args, **_kwargs: None
    orch.governor = SimpleNamespace(check_limit=lambda *_args, **_kwargs: True)
    orch.sanitizer = SimpleNamespace(sanitize=lambda text: text)
    orch._check_name_update = lambda *_args, **_kwargs: None
    orch.context_env = SimpleNamespace(add_history=lambda *_args, **_kwargs: None, _current_quest_id=None)
    orch._verify_intent_with_llm = lambda _message, intent: intent
    orch._route_model = lambda _message: "test-model"
    orch.receipt_service = MagicMock()
    orch.provider = None
    orch.task_store = None
    orch._last_plan_artifact = None
    return orch


class TestOrchestratorChatProvenance:
    def test_chat_preserves_supplied_identity_and_quest(self):
        orch = _build_minimal_orchestrator()

        with patch.object(orch_mod, "classify_intent", return_value=orch_mod.IntentType.KNOWLEDGE_REQUEST), \
             patch.object(orch_mod, "create_receipt", return_value=MagicMock()) as create_receipt:
            response = orch.chat(
                "hello",
                channel="warroom",
                session_id="session-123",
                operator_id="operator-456",
                operator_name="Myles",
                quest_id="quest-789",
            )

        assert response == "Error: LLM provider not initialized (Missing API Key)."
        assert orch._current_session_id == "session-123"
        assert orch._current_operator_id == "operator-456"
        assert orch._current_operator_name == "Myles"
        assert orch._current_quest_id == "quest-789"
        assert orch.context_env._current_quest_id == "quest-789"

        create_receipt.assert_called_once()
        assert create_receipt.call_args.kwargs["quest_id"] == "quest-789"
        assert create_receipt.call_args.kwargs["metadata"]["session_id"] == "session-123"
        assert create_receipt.call_args.kwargs["metadata"]["operator_id"] == "operator-456"
        assert create_receipt.call_args.kwargs["metadata"]["operator_name"] == "Myles"


class TestGatewayChatIdentityForwarding:
    def test_chat_route_forwards_authenticated_identity(self):
        from fastapi.testclient import TestClient
        import gateway

        identity = OperatorIdentity(
            operator_id="operator-123",
            display_name="Arthur",
            session_id="session-abc",
            session_started_at="2026-01-01T00:00:00Z",
            auth_method="local",
            ip_address="127.0.0.1",
        )

        with patch.dict("os.environ", {"LANCELOT_DEV_MODE": "true"}, clear=False):
            gateway.DEV_MODE = True
            gateway.onboarding_orch.state = "READY"
            with patch.object(gateway.onboarding_orch, "_determine_state", return_value="READY"), \
                 patch("src.core.auth_api.resolve_authenticated_identity", return_value=identity), \
                 patch.object(gateway.main_orchestrator, "chat", return_value="ok") as chat_mock:
                client = TestClient(gateway.app)
                response = client.post("/chat", json={"text": "status", "user": "ignored", "channel": "warroom"})

        assert response.status_code == 200
        assert response.json()["response"] == "ok"
        chat_mock.assert_called_once()
        assert chat_mock.call_args.kwargs["session_id"] == "session-abc"
        assert chat_mock.call_args.kwargs["operator_id"] == "operator-123"
        assert chat_mock.call_args.kwargs["operator_name"] == "Arthur"
        assert chat_mock.call_args.kwargs["channel"] == "warroom"

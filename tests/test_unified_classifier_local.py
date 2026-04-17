from types import SimpleNamespace
from unittest.mock import MagicMock

from src.core.unified_classifier import UnifiedClassifier


def test_local_router_handles_obvious_question_without_frontier_call():
    provider = MagicMock()
    router = MagicMock()
    router.route.return_value = SimpleNamespace(executed=True, output="question")

    classifier = UnifiedClassifier(provider, model_router=router)
    result = classifier.classify("What is the deployment status?")

    assert result.intent == "question"
    provider.generate.assert_not_called()


def test_local_router_handles_greeting_without_frontier_call():
    provider = MagicMock()
    router = MagicMock()
    router.route.return_value = SimpleNamespace(executed=True, output="greeting")

    classifier = UnifiedClassifier(provider, model_router=router)
    result = classifier.classify("hello")

    assert result.intent == "conversational"
    provider.generate.assert_not_called()


def test_local_router_command_escalates_to_keyword_fallback_not_frontier():
    provider = MagicMock()
    router = MagicMock()
    router.route.return_value = SimpleNamespace(executed=True, output="command")

    classifier = UnifiedClassifier(provider, model_router=router)
    result = classifier.classify("delete the old deployment")

    assert "Local utility classifier returned 'command'" in result.reasoning
    provider.generate.assert_not_called()

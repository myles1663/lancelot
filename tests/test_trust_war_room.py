"""
Tests for Prompt 46: Trust War Room Panel.
"""

import pytest
from src.core.governance.models import RiskTier
from src.core.governance.trust_models import (
    GraduationProposal,
    TrustGraduationConfig,
    TrustGraduationThresholds,
    TrustRevocationConfig,
)
from src.core.governance.trust_ledger import TrustLedger
from src.core.governance.war_room_panel import (
    render_governance_panel,
    render_trust_panel,
    format_graduation_proposal,
)


@pytest.fixture
def config():
    return TrustGraduationConfig(
        thresholds=TrustGraduationThresholds(T3_to_T2=50, T2_to_T1=100, T1_to_T0=200),
        revocation=TrustRevocationConfig(),
    )


@pytest.fixture
def ledger(config):
    return TrustLedger(config)


class TestRenderTrustPanel:
    def test_empty_ledger_zero_counts(self):
        result = render_trust_panel(None)
        assert result["summary"]["total_records"] == 0
        assert result["summary"]["graduated_records"] == 0
        assert result["summary"]["pending_proposals"] == 0
        assert result["summary"]["avg_success_rate"] == 0.0

    def test_with_records_correct_summary(self, ledger):
        ledger.get_or_create_record("connector.slack.post", "s", RiskTier.T3_IRREVERSIBLE)
        ledger.get_or_create_record("connector.slack.read", "s", RiskTier.T0_INERT)
        # Add some successes
        for _ in range(10):
            ledger.record_success("connector.slack.post", "s")
            ledger.record_success("connector.slack.read", "s")
        result = render_trust_panel(ledger)
        assert result["summary"]["total_records"] == 2
        assert result["summary"]["avg_success_rate"] == 1.0

    def test_graduated_records_counted(self, ledger):
        rec = ledger.get_or_create_record("connector.slack.post", "s", RiskTier.T3_IRREVERSIBLE)
        rec.current_tier = RiskTier.T2_CONTROLLED  # Manually graduate
        result = render_trust_panel(ledger)
        assert result["summary"]["graduated_records"] == 1

    def test_proposals_included(self, ledger):
        ledger.get_or_create_record("connector.slack.post", "s", RiskTier.T3_IRREVERSIBLE)
        for _ in range(50):
            ledger.record_success("connector.slack.post", "s")
        result = render_trust_panel(ledger)
        assert result["summary"]["pending_proposals"] == 1
        assert len(result["proposals"]) == 1
        assert result["proposals"][0]["capability"] == "connector.slack.post"

    def test_per_connector_breakdown(self, ledger):
        ledger.get_or_create_record("connector.slack.post", "s", RiskTier.T2_CONTROLLED)
        ledger.get_or_create_record("connector.email.send", "s", RiskTier.T3_IRREVERSIBLE)
        result = render_trust_panel(ledger)
        connector_ids = {c["connector_id"] for c in result["per_connector"]}
        assert "slack" in connector_ids
        assert "email" in connector_ids


class TestFormatGraduationProposal:
    def test_includes_capability_and_transition(self):
        proposal = GraduationProposal(
            capability="connector.slack.post_message",
            scope="channel:#general",
            current_tier=RiskTier.T2_CONTROLLED,
            proposed_tier=RiskTier.T1_REVERSIBLE,
            consecutive_successes=100,
        )
        text = format_graduation_proposal(proposal)
        assert "connector.slack.post_message" in text
        assert "100" in text
        assert "T2_CONTROLLED" in text
        assert "T1_REVERSIBLE" in text
        assert "Approve?" in text


class _Column:
    def __init__(self, calls):
        self.calls = calls

    def metric(self, label, value):
        self.calls.append(("metric", label, value))


class _Streamlit:
    def __init__(self):
        self.calls = []

    def header(self, text):
        self.calls.append(("header", text))

    def subheader(self, text):
        self.calls.append(("subheader", text))

    def caption(self, text):
        self.calls.append(("caption", text))

    def info(self, text):
        self.calls.append(("info", text))

    def warning(self, text):
        self.calls.append(("warning", text))

    def divider(self):
        self.calls.append(("divider",))

    def text(self, text):
        self.calls.append(("text", text))

    def columns(self, count):
        self.calls.append(("columns", count))
        return [_Column(self.calls) for _ in range(count)]


class _Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class TestRenderGovernancePanel:
    def test_no_streamlit_is_noop(self):
        assert render_governance_panel(None) is None

    def test_disabled_flag_shows_info(self, monkeypatch):
        st = _Streamlit()
        monkeypatch.setitem(__import__("sys").modules, "feature_flags", type("Flags", (), {"FEATURE_RISK_TIERED_GOVERNANCE": False}))

        render_governance_panel(st)

        assert ("header", "Governance Performance") in st.calls
        assert any(call[0] == "info" and "disabled" in call[1] for call in st.calls)

    def test_successful_stats_render_metrics_and_templates(self, monkeypatch):
        import sys
        import types

        st = _Streamlit()
        payload = {
            "policy_cache": {
                "total_entries": 12,
                "hit_rate": 0.75,
                "soul_version": "soul-v1",
            },
            "async_queue": {"depth": 3, "pending": 2},
            "intent_templates": {
                "total": 2,
                "active": 1,
                "templates": [
                    {
                        "intent_pattern": "send report",
                        "active": True,
                        "success_count": 4,
                        "failure_count": 1,
                    }
                ],
            },
        }
        requests = types.SimpleNamespace(get=lambda url, timeout: _Response(payload=payload))
        monkeypatch.setitem(sys.modules, "feature_flags", types.SimpleNamespace(FEATURE_RISK_TIERED_GOVERNANCE=True))
        monkeypatch.setitem(sys.modules, "requests", requests)

        render_governance_panel(st, gateway_url="http://gateway")

        assert ("metric", "Cache Entries", 12) in st.calls
        assert ("metric", "Hit Rate", "75.0%") in st.calls
        assert ("metric", "Queue Depth", 3) in st.calls
        assert ("metric", "Total Templates", 2) in st.calls
        assert any(call == ("text", "send report | Active | S:4 F:1") for call in st.calls)

    def test_gateway_status_and_request_failures_are_displayed(self, monkeypatch):
        import sys
        import types

        st = _Streamlit()
        responses = iter([
            _Response(status_code=503),
            _Response(status_code=503),
            RuntimeError("network down"),
        ])

        def fake_get(url, timeout):
            response = next(responses)
            if isinstance(response, Exception):
                raise response
            return response

        monkeypatch.setitem(sys.modules, "feature_flags", types.SimpleNamespace(FEATURE_RISK_TIERED_GOVERNANCE=True))
        monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(get=fake_get))

        render_governance_panel(st)

        assert ("warning", "Could not fetch governance stats from gateway.") in st.calls
        assert ("caption", "Stats unavailable") in st.calls
        assert ("caption", "Templates disabled or not initialized") in st.calls

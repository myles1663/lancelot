"""
Tests for Prompt 39: CalendarConnector.
"""

import pytest
from src.connectors.connectors.calendar import CalendarConnector
from src.connectors.models import HTTPMethod
from src.core.governance.models import RiskTier


@pytest.fixture
def cal():
    return CalendarConnector()


class TestCalendarConnector:
    def test_six_operations(self, cal):
        assert len(cal.get_operations()) == 6

    def test_correct_tiers(self, cal):
        ops = {o.id: o for o in cal.get_operations()}
        assert ops["read_events"].default_tier == RiskTier.T0_INERT
        assert ops["read_availability"].default_tier == RiskTier.T0_INERT
        assert ops["create_event"].default_tier == RiskTier.T2_CONTROLLED
        assert ops["update_event"].default_tier == RiskTier.T2_CONTROLLED
        assert ops["delete_event"].default_tier == RiskTier.T3_IRREVERSIBLE
        assert ops["send_invite"].default_tier == RiskTier.T3_IRREVERSIBLE

    def test_read_events_url(self, cal):
        result = cal.execute("read_events", {})
        assert "/calendars/primary/events" in result.url
        assert result.method == HTTPMethod.GET

    def test_read_events_with_time_range(self, cal):
        result = cal.execute("read_events", {
            "time_min": "2026-01-01T00:00:00Z",
            "time_max": "2026-01-31T00:00:00Z",
        })
        assert "timeMin=" in result.url
        assert "timeMax=" in result.url

    def test_read_availability_post(self, cal):
        result = cal.execute("read_availability", {
            "time_min": "2026-01-01T00:00:00Z",
            "time_max": "2026-01-02T00:00:00Z",
        })
        assert "freeBusy" in result.url
        assert result.method == HTTPMethod.POST

    def test_create_event_post(self, cal):
        result = cal.execute("create_event", {
            "summary": "Meeting",
            "start": "2026-01-15T10:00:00Z",
            "end": "2026-01-15T11:00:00Z",
        })
        assert "/calendars/primary/events" in result.url
        assert result.method == HTTPMethod.POST
        assert result.body["summary"] == "Meeting"

    def test_update_event_put(self, cal):
        result = cal.execute("update_event", {
            "event_id": "evt1",
            "summary": "Updated Meeting",
        })
        assert "/events/evt1" in result.url
        assert result.method == HTTPMethod.PUT

    def test_delete_event_delete(self, cal):
        result = cal.execute("delete_event", {"event_id": "evt1"})
        assert "/events/evt1" in result.url
        assert result.method == HTTPMethod.DELETE

    def test_send_invite_with_send_updates(self, cal):
        result = cal.execute("send_invite", {
            "summary": "Party",
            "start": "2026-02-01T18:00:00Z",
            "end": "2026-02-01T22:00:00Z",
            "attendees": ["alice@example.com"],
        })
        assert "sendUpdates=all" in result.url
        assert result.body["attendees"][0]["email"] == "alice@example.com"

    def test_create_event_reversible(self, cal):
        ops = {o.id: o for o in cal.get_operations()}
        assert ops["create_event"].reversible is True
        assert ops["create_event"].rollback_operation_id == "delete_event"

    def test_delete_event_not_reversible(self, cal):
        ops = {o.id: o for o in cal.get_operations()}
        assert ops["delete_event"].reversible is False

    def test_all_have_credential_key(self, cal):
        for op_id in ("read_events", "read_availability", "create_event", "delete_event"):
            params = {"event_id": "e1", "time_min": "a", "time_max": "b", "summary": "s", "start": "s", "end": "e"}
            result = cal.execute(op_id, params)
            assert result.credential_vault_key == "calendar.google_token"

"""
Tests for Prompt 39: CalendarConnector.
"""

import os
import pytest
from src.connectors.connectors.calendar import CalendarConnector
from src.connectors.models import HTTPMethod
from src.core import feature_flags
from src.core.governance.models import RiskTier


@pytest.fixture
def cal():
    return CalendarConnector()


@pytest.fixture(autouse=True)
def enable_google_oauth(monkeypatch):
    old = os.environ.get("FEATURE_GOOGLE_OAUTH")
    monkeypatch.setenv("FEATURE_GOOGLE_OAUTH", "true")
    feature_flags.reload_flags()
    yield
    if old is None:
        monkeypatch.delenv("FEATURE_GOOGLE_OAUTH", raising=False)
    else:
        monkeypatch.setenv("FEATURE_GOOGLE_OAUTH", old)
    feature_flags.reload_flags()


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

    def test_calendar_disabled_when_google_oauth_flag_off(self, monkeypatch):
        old = os.environ.get("FEATURE_GOOGLE_OAUTH")
        monkeypatch.setenv("FEATURE_GOOGLE_OAUTH", "false")
        feature_flags.reload_flags()

        class _Vault:
            def exists(self, key):
                return key == "calendar.google_token"

        try:
            cal = CalendarConnector(vault=_Vault())
            assert cal.validate_credentials() is False
            with pytest.raises(RuntimeError, match="Google OAuth is disabled"):
                cal.execute("read_events", {})
        finally:
            if old is None:
                monkeypatch.delenv("FEATURE_GOOGLE_OAUTH", raising=False)
            else:
                monkeypatch.setenv("FEATURE_GOOGLE_OAUTH", old)
            feature_flags.reload_flags()


class _Vault:
    def __init__(self, keys: set[str]) -> None:
        self._keys = keys

    def exists(self, key):
        return key in self._keys


class TestCalendarBackends:
    def test_invalid_backend_raises(self):
        with pytest.raises(ValueError, match="Unknown calendar backend"):
            CalendarConnector(backend="invalid")

    def test_outlook_backend_manifest(self):
        cal = CalendarConnector(backend="outlook")

        assert cal.backend == "outlook"
        assert cal.manifest.target_domains == ["graph.microsoft.com"]
        assert cal.manifest.required_credentials[0].vault_key == "calendar.outlook_token"
        assert "Calendars.ReadWrite" in cal.manifest.required_credentials[0].scopes

    def test_outlook_read_events_uses_graph(self):
        result = CalendarConnector(backend="outlook").execute("read_events", {"max_results": 10})

        assert result.method == HTTPMethod.GET
        assert result.url.startswith("https://graph.microsoft.com/v1.0/me/calendar/events")
        assert "%24top=10" in result.url
        assert result.credential_vault_key == "calendar.outlook_token"

    def test_outlook_availability_uses_get_schedule(self):
        result = CalendarConnector(backend="outlook").execute(
            "read_availability",
            {
                "calendar_id": "user@example.com",
                "time_min": "2026-01-01T09:00:00",
                "time_max": "2026-01-01T10:00:00",
            },
        )

        assert result.method == HTTPMethod.POST
        assert result.url.endswith("/me/calendar/getSchedule")
        assert result.body["schedules"] == ["user@example.com"]

    def test_outlook_create_update_delete_specs(self):
        cal = CalendarConnector(backend="outlook")
        create = cal.execute("create_event", {
            "summary": "Briefing",
            "start": "2026-01-01T09:00:00",
            "end": "2026-01-01T10:00:00",
            "attendees": ["a@example.com"],
        })
        update = cal.execute("update_event", {"event_id": "evt1", "summary": "Updated"})
        delete = cal.execute("delete_event", {"event_id": "evt1"})

        assert create.method == HTTPMethod.POST
        assert create.body["subject"] == "Briefing"
        assert create.body["attendees"][0]["emailAddress"]["address"] == "a@example.com"
        assert update.method == HTTPMethod.PATCH
        assert "/events/evt1" in update.url
        assert delete.method == HTTPMethod.DELETE

    def test_caldav_backend_produces_protocol_spec(self):
        result = CalendarConnector(backend="caldav").execute(
            "create_event",
            {
                "summary": "Planning",
                "start": "2026-01-01T09:00:00",
                "end": "2026-01-01T10:00:00",
            },
        )

        assert result.method == HTTPMethod.POST
        assert result.url == "protocol://caldav"
        assert result.body["protocol"] == "caldav"
        assert result.body["action"] == "create"
        assert result.metadata["protocol_adapter"] is True

    def test_calendar_backend_credential_validation(self):
        assert CalendarConnector(
            backend="outlook",
            vault=_Vault({"calendar.outlook_token"}),
        ).validate_credentials() is True
        assert CalendarConnector(
            backend="caldav",
            vault=_Vault({"calendar.caldav_url", "calendar.caldav_username", "calendar.caldav_password"}),
        ).validate_credentials() is True
        assert CalendarConnector(
            backend="caldav",
            vault=_Vault({"calendar.caldav_url"}),
        ).validate_credentials() is False

    def test_non_google_calendar_backend_available_when_google_flag_off(self, monkeypatch):
        old = os.environ.get("FEATURE_GOOGLE_OAUTH")
        monkeypatch.setenv("FEATURE_GOOGLE_OAUTH", "false")
        feature_flags.reload_flags()

        try:
            result = CalendarConnector(backend="outlook").execute("read_events", {})
            assert result.url.startswith("https://graph.microsoft.com")
        finally:
            if old is None:
                monkeypatch.delenv("FEATURE_GOOGLE_OAUTH", raising=False)
            else:
                monkeypatch.setenv("FEATURE_GOOGLE_OAUTH", old)
            feature_flags.reload_flags()

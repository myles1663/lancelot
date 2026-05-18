"""
Calendar Connector - multi-backend calendar integration.

Supports Google Calendar, Microsoft Graph calendar, and CalDAV protocol specs.
Never makes network calls directly.
"""

from __future__ import annotations

from typing import Any, Dict, List
from urllib.parse import quote, urlencode

from src.connectors.base import ConnectorBase, ConnectorManifest, CredentialSpec
from src.connectors.google_feature_gate import (
    google_connector_disabled_reason,
    is_google_connector_enabled,
)
from src.connectors.models import (
    ConnectorOperation,
    ConnectorResult,
    HTTPMethod,
    ParameterSpec,
)
from src.core.governance.models import RiskTier


_BACKEND_CONFIG = {
    "google": {
        "api_base": "https://www.googleapis.com/calendar/v3",
        "target_domains": ["www.googleapis.com"],
        "description": "Google Calendar API for events and scheduling",
        "credentials": [
            CredentialSpec(
                name="google_calendar_token",
                type="oauth_token",
                vault_key="calendar.google_token",
                scopes=["calendar.readonly", "calendar.events"],
            ),
        ],
        "does_not_access": ["Other users' calendars", "Calendar settings"],
    },
    "outlook": {
        "api_base": "https://graph.microsoft.com/v1.0",
        "target_domains": ["graph.microsoft.com"],
        "description": "Microsoft Graph calendar for Outlook and Microsoft 365 scheduling",
        "credentials": [
            CredentialSpec(
                name="outlook_calendar_token",
                type="oauth_token",
                vault_key="calendar.outlook_token",
                scopes=["Calendars.Read", "Calendars.ReadWrite"],
            ),
        ],
        "does_not_access": ["Email", "Teams messages", "OneDrive files"],
    },
    "caldav": {
        "api_base": "protocol://caldav",
        "target_domains": ["protocol.caldav"],
        "description": "CalDAV calendar integration for standards-based providers",
        "credentials": [
            CredentialSpec(
                name="caldav_url",
                type="config",
                vault_key="calendar.caldav_url",
                required=True,
            ),
            CredentialSpec(
                name="caldav_username",
                type="config",
                vault_key="calendar.caldav_username",
                required=True,
            ),
            CredentialSpec(
                name="caldav_password",
                type="api_key",
                vault_key="calendar.caldav_password",
                required=True,
            ),
        ],
        "does_not_access": ["Email", "Contacts", "Files"],
    },
}


class CalendarConnector(ConnectorBase):
    """Multi-backend calendar connector with governed operations."""

    GCAL_API_BASE = "https://www.googleapis.com/calendar/v3"
    GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"

    def __init__(self, backend: str = "google", vault=None) -> None:
        if backend not in _BACKEND_CONFIG:
            raise ValueError(
                f"Unknown calendar backend: {backend!r}. "
                f"Supported: {list(_BACKEND_CONFIG.keys())}"
            )
        self._backend = backend
        cfg = _BACKEND_CONFIG[backend]
        manifest = ConnectorManifest(
            id="calendar",
            name="Calendar Integration",
            version="1.0.0",
            author="lancelot",
            source="first-party",
            description=cfg["description"],
            target_domains=cfg["target_domains"],
            required_credentials=cfg["credentials"],
            data_reads=["Calendar events (title, time, attendees)"],
            data_writes=["New events, event updates, invitations"],
            does_not_access=cfg["does_not_access"],
        )
        super().__init__(manifest)
        self._vault = vault
        self._api_base = cfg["api_base"]
        self._cred_key = cfg["credentials"][-1].vault_key

    @property
    def backend(self) -> str:
        return self._backend

    def get_operations(self) -> List[ConnectorOperation]:
        cid = "calendar"
        return [
            ConnectorOperation(
                id="read_events",
                connector_id=cid,
                capability="connector.read",
                name="Read Events",
                description="List events from a calendar",
                default_tier=RiskTier.T0_INERT,
                idempotent=True,
                parameters=[
                    ParameterSpec(name="calendar_id", type="str", required=False, default="primary"),
                    ParameterSpec(name="time_min", type="str", required=False),
                    ParameterSpec(name="time_max", type="str", required=False),
                    ParameterSpec(name="max_results", type="int", required=False, default=50),
                ],
            ),
            ConnectorOperation(
                id="read_availability",
                connector_id=cid,
                capability="connector.read",
                name="Read Availability",
                description="Check free/busy status",
                default_tier=RiskTier.T0_INERT,
                idempotent=True,
                parameters=[
                    ParameterSpec(name="calendar_id", type="str", required=False, default="primary"),
                    ParameterSpec(name="time_min", type="str", required=True),
                    ParameterSpec(name="time_max", type="str", required=True),
                    ParameterSpec(name="timezone", type="str", required=False, default="UTC"),
                ],
            ),
            ConnectorOperation(
                id="create_event",
                connector_id=cid,
                capability="connector.write",
                name="Create Event",
                description="Create a new calendar event",
                default_tier=RiskTier.T2_CONTROLLED,
                reversible=True,
                rollback_operation_id="delete_event",
                parameters=[
                    ParameterSpec(name="calendar_id", type="str", required=False, default="primary"),
                    ParameterSpec(name="summary", type="str", required=True),
                    ParameterSpec(name="start", type="str", required=True),
                    ParameterSpec(name="end", type="str", required=True),
                    ParameterSpec(name="timezone", type="str", required=False, default="UTC"),
                    ParameterSpec(name="description", type="str", required=False, default=""),
                    ParameterSpec(name="attendees", type="list[str]", required=False),
                ],
            ),
            ConnectorOperation(
                id="update_event",
                connector_id=cid,
                capability="connector.write",
                name="Update Event",
                description="Update an existing calendar event",
                default_tier=RiskTier.T2_CONTROLLED,
                idempotent=True,
                reversible=True,
                parameters=[
                    ParameterSpec(name="calendar_id", type="str", required=False, default="primary"),
                    ParameterSpec(name="event_id", type="str", required=True),
                    ParameterSpec(name="summary", type="str", required=False),
                    ParameterSpec(name="start", type="str", required=False),
                    ParameterSpec(name="end", type="str", required=False),
                    ParameterSpec(name="timezone", type="str", required=False, default="UTC"),
                    ParameterSpec(name="description", type="str", required=False),
                ],
            ),
            ConnectorOperation(
                id="delete_event",
                connector_id=cid,
                capability="connector.delete",
                name="Delete Event",
                description="Delete a calendar event",
                default_tier=RiskTier.T3_IRREVERSIBLE,
                idempotent=True,
                reversible=False,
                parameters=[
                    ParameterSpec(name="calendar_id", type="str", required=False, default="primary"),
                    ParameterSpec(name="event_id", type="str", required=True),
                ],
            ),
            ConnectorOperation(
                id="send_invite",
                connector_id=cid,
                capability="connector.write",
                name="Send Invite",
                description="Create event with attendee notifications",
                default_tier=RiskTier.T3_IRREVERSIBLE,
                reversible=False,
                parameters=[
                    ParameterSpec(name="calendar_id", type="str", required=False, default="primary"),
                    ParameterSpec(name="summary", type="str", required=True),
                    ParameterSpec(name="start", type="str", required=True),
                    ParameterSpec(name="end", type="str", required=True),
                    ParameterSpec(name="timezone", type="str", required=False, default="UTC"),
                    ParameterSpec(name="attendees", type="list[str]", required=True),
                    ParameterSpec(name="description", type="str", required=False, default=""),
                ],
            ),
        ]

    def execute(self, operation_id: str, params: dict) -> ConnectorResult:
        if self._backend == "google" and not is_google_connector_enabled("calendar", self._backend):
            raise RuntimeError(google_connector_disabled_reason("calendar", self._backend))
        if self._backend == "google":
            return self._execute_google(operation_id, params)
        if self._backend == "outlook":
            return self._execute_outlook(operation_id, params)
        if self._backend == "caldav":
            return self._execute_caldav(operation_id, params)
        raise ValueError(f"Unknown backend: {self._backend}")

    def _execute_google(self, operation_id: str, params: dict) -> ConnectorResult:
        base = self.GCAL_API_BASE
        cred_key = "calendar.google_token"
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        cal_id = params.get("calendar_id", "primary")

        if operation_id == "read_events":
            qp = {"maxResults": params.get("max_results", 50)}
            if params.get("time_min"):
                qp["timeMin"] = params["time_min"]
            if params.get("time_max"):
                qp["timeMax"] = params["time_max"]
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="calendar",
                method=HTTPMethod.GET,
                url=f"{base}/calendars/{cal_id}/events?{urlencode(qp)}",
                headers=headers,
                credential_vault_key=cred_key,
            )

        if operation_id == "read_availability":
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="calendar",
                method=HTTPMethod.POST,
                url=f"{base}/freeBusy",
                headers=headers,
                body={
                    "timeMin": params["time_min"],
                    "timeMax": params["time_max"],
                    "items": [{"id": cal_id}],
                },
                credential_vault_key=cred_key,
            )

        if operation_id == "create_event":
            body: Dict[str, Any] = {
                "summary": params["summary"],
                "start": {"dateTime": params["start"]},
                "end": {"dateTime": params["end"]},
            }
            if params.get("description"):
                body["description"] = params["description"]
            if params.get("attendees"):
                body["attendees"] = [{"email": a} for a in params["attendees"]]
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="calendar",
                method=HTTPMethod.POST,
                url=f"{base}/calendars/{cal_id}/events",
                headers=headers,
                body=body,
                credential_vault_key=cred_key,
            )

        if operation_id == "update_event":
            event_id = params["event_id"]
            body = {}
            if params.get("summary"):
                body["summary"] = params["summary"]
            if params.get("start"):
                body["start"] = {"dateTime": params["start"]}
            if params.get("end"):
                body["end"] = {"dateTime": params["end"]}
            if params.get("description"):
                body["description"] = params["description"]
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="calendar",
                method=HTTPMethod.PUT,
                url=f"{base}/calendars/{cal_id}/events/{event_id}",
                headers=headers,
                body=body,
                credential_vault_key=cred_key,
            )

        if operation_id == "delete_event":
            event_id = params["event_id"]
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="calendar",
                method=HTTPMethod.DELETE,
                url=f"{base}/calendars/{cal_id}/events/{event_id}",
                headers=headers,
                credential_vault_key=cred_key,
            )

        if operation_id == "send_invite":
            body = {
                "summary": params["summary"],
                "start": {"dateTime": params["start"]},
                "end": {"dateTime": params["end"]},
                "attendees": [{"email": a} for a in params["attendees"]],
            }
            if params.get("description"):
                body["description"] = params["description"]
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="calendar",
                method=HTTPMethod.POST,
                url=f"{base}/calendars/{cal_id}/events?sendUpdates=all",
                headers=headers,
                body=body,
                credential_vault_key=cred_key,
            )

        raise KeyError(f"Unknown operation: {operation_id}")

    def _execute_outlook(self, operation_id: str, params: dict) -> ConnectorResult:
        base = self.GRAPH_API_BASE
        cred_key = "calendar.outlook_token"
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        cal_path = self._outlook_calendar_path(params.get("calendar_id", "primary"))

        if operation_id == "read_events":
            qp = {"$top": params.get("max_results", 50)}
            if params.get("time_min") or params.get("time_max"):
                filters = []
                if params.get("time_min"):
                    filters.append(f"start/dateTime ge '{params['time_min']}'")
                if params.get("time_max"):
                    filters.append(f"end/dateTime le '{params['time_max']}'")
                qp["$filter"] = " and ".join(filters)
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="calendar",
                method=HTTPMethod.GET,
                url=f"{base}{cal_path}/events?{urlencode(qp)}",
                headers={"Accept": "application/json"},
                credential_vault_key=cred_key,
            )

        if operation_id == "read_availability":
            timezone = params.get("timezone", "UTC")
            schedule = params.get("schedule") or params.get("calendar_id") or "me"
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="calendar",
                method=HTTPMethod.POST,
                url=f"{base}/me/calendar/getSchedule",
                headers=headers,
                body={
                    "schedules": [schedule],
                    "startTime": {"dateTime": params["time_min"], "timeZone": timezone},
                    "endTime": {"dateTime": params["time_max"], "timeZone": timezone},
                    "availabilityViewInterval": params.get("availability_view_interval", 30),
                },
                credential_vault_key=cred_key,
            )

        if operation_id == "create_event":
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="calendar",
                method=HTTPMethod.POST,
                url=f"{base}{cal_path}/events",
                headers=headers,
                body=self._outlook_event_body(params),
                credential_vault_key=cred_key,
            )

        if operation_id == "update_event":
            event_id = quote(params["event_id"], safe="")
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="calendar",
                method=HTTPMethod.PATCH,
                url=f"{base}{cal_path}/events/{event_id}",
                headers=headers,
                body=self._outlook_event_body(params, partial=True),
                credential_vault_key=cred_key,
            )

        if operation_id == "delete_event":
            event_id = quote(params["event_id"], safe="")
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="calendar",
                method=HTTPMethod.DELETE,
                url=f"{base}{cal_path}/events/{event_id}",
                headers={"Accept": "application/json"},
                credential_vault_key=cred_key,
            )

        if operation_id == "send_invite":
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="calendar",
                method=HTTPMethod.POST,
                url=f"{base}{cal_path}/events",
                headers=headers,
                body=self._outlook_event_body(params),
                credential_vault_key=cred_key,
                metadata={"attendee_notifications": "provider_managed"},
            )

        raise KeyError(f"Unknown operation: {operation_id}")

    def _execute_caldav(self, operation_id: str, params: dict) -> ConnectorResult:
        body = {"protocol": "caldav", "calendar_id": params.get("calendar_id", "default")}

        if operation_id == "read_events":
            body.update({
                "action": "list",
                "time_min": params.get("time_min"),
                "time_max": params.get("time_max"),
                "max_results": params.get("max_results", 50),
            })
        elif operation_id == "read_availability":
            body.update({
                "action": "free_busy",
                "time_min": params["time_min"],
                "time_max": params["time_max"],
            })
        elif operation_id == "create_event":
            body.update({
                "action": "create",
                "summary": params["summary"],
                "start": params["start"],
                "end": params["end"],
                "description": params.get("description", ""),
                "attendees": params.get("attendees", []),
            })
        elif operation_id == "update_event":
            body.update({
                "action": "update",
                "event_id": params["event_id"],
                "summary": params.get("summary"),
                "start": params.get("start"),
                "end": params.get("end"),
                "description": params.get("description"),
            })
        elif operation_id == "delete_event":
            body.update({"action": "delete", "event_id": params["event_id"]})
        elif operation_id == "send_invite":
            body.update({
                "action": "invite",
                "summary": params["summary"],
                "start": params["start"],
                "end": params["end"],
                "attendees": params["attendees"],
                "description": params.get("description", ""),
            })
        else:
            raise KeyError(f"Unknown operation: {operation_id}")

        return ConnectorResult(
            operation_id=operation_id,
            connector_id="calendar",
            method=HTTPMethod.POST,
            url="protocol://caldav",
            body=body,
            credential_vault_key="calendar.caldav_password",
            metadata={"protocol_adapter": True},
        )

    @staticmethod
    def _outlook_calendar_path(calendar_id: str) -> str:
        if calendar_id in ("", "primary", "me"):
            return "/me/calendar"
        return f"/me/calendars/{quote(calendar_id, safe='')}"

    @staticmethod
    def _outlook_event_body(params: dict, *, partial: bool = False) -> Dict[str, Any]:
        body: Dict[str, Any] = {}
        timezone = params.get("timezone", "UTC")
        if not partial or params.get("summary") is not None:
            body["subject"] = params.get("summary", "")
        if not partial or params.get("start") is not None:
            body["start"] = {"dateTime": params.get("start"), "timeZone": timezone}
        if not partial or params.get("end") is not None:
            body["end"] = {"dateTime": params.get("end"), "timeZone": timezone}
        if params.get("description"):
            body["body"] = {"contentType": "Text", "content": params["description"]}
        if params.get("attendees"):
            body["attendees"] = [
                {"emailAddress": {"address": attendee}, "type": "required"}
                for attendee in params["attendees"]
            ]
        return body

    def validate_credentials(self) -> bool:
        if self._vault is None:
            return False
        if self._backend == "google" and not is_google_connector_enabled("calendar", self._backend):
            return False
        return all(
            self._vault.exists(spec.vault_key)
            for spec in self.manifest.required_credentials
            if spec.required
        )

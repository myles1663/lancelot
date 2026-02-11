"""
Calendar Connector — Google Calendar API integration.

Produces HTTP request specs for Google Calendar operations.
Never makes network calls directly.
"""

from __future__ import annotations

from typing import Any, Dict, List
from urllib.parse import urlencode

from src.connectors.base import ConnectorBase, ConnectorManifest, CredentialSpec
from src.connectors.models import (
    ConnectorOperation,
    ConnectorResult,
    HTTPMethod,
    ParameterSpec,
)
from src.core.governance.models import RiskTier


class CalendarConnector(ConnectorBase):
    """Google Calendar API connector."""

    GCAL_API_BASE = "https://www.googleapis.com/calendar/v3"

    def __init__(self, vault=None) -> None:
        manifest = ConnectorManifest(
            id="calendar",
            name="Calendar Integration",
            version="1.0.0",
            author="lancelot",
            source="first-party",
            description="Google Calendar API for events and scheduling",
            target_domains=["www.googleapis.com"],
            required_credentials=[
                CredentialSpec(
                    name="google_calendar_token",
                    type="oauth_token",
                    vault_key="calendar.google_token",
                    scopes=["calendar.readonly", "calendar.events"],
                ),
            ],
            data_reads=["Calendar events (title, time, attendees)"],
            data_writes=["New events, event updates, invitations"],
            does_not_access=["Other users' calendars", "Calendar settings"],
        )
        super().__init__(manifest)
        self._vault = vault

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
                    ParameterSpec(name="attendees", type="list[str]", required=True),
                    ParameterSpec(name="description", type="str", required=False, default=""),
                ],
            ),
        ]

    def execute(self, operation_id: str, params: dict) -> ConnectorResult:
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
            qs = urlencode(qp)
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="calendar",
                method=HTTPMethod.GET,
                url=f"{base}/calendars/{cal_id}/events?{qs}",
                headers=headers,
                credential_vault_key=cred_key,
            )

        elif operation_id == "read_availability":
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

        elif operation_id == "create_event":
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

        elif operation_id == "update_event":
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

        elif operation_id == "delete_event":
            event_id = params["event_id"]
            return ConnectorResult(
                operation_id=operation_id,
                connector_id="calendar",
                method=HTTPMethod.DELETE,
                url=f"{base}/calendars/{cal_id}/events/{event_id}",
                headers=headers,
                credential_vault_key=cred_key,
            )

        elif operation_id == "send_invite":
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

        else:
            raise KeyError(f"Unknown operation: {operation_id}")

    def validate_credentials(self) -> bool:
        if self._vault is None:
            return False
        return self._vault.exists("calendar.google_token")

from pathlib import Path

from src.incidents.models import IncidentCategory, IncidentRecord, IncidentSeverity, IncidentStatus
from src.incidents import report_generator


def _incident(**overrides) -> IncidentRecord:
    incident = IncidentRecord.create(
        trigger_receipt_id="receipt-1",
        category=IncidentCategory.GOVERNANCE_BREACH,
        severity=IncidentSeverity.HIGH,
        playbook_name="governance-breach-kill-switch",
        dedup_key="kill_switch_activated:FEATURE_MCP",
    )
    incident.status = overrides.get("status", IncidentStatus.CLOSED.value)
    incident.opened_at = overrides.get("opened_at", "2026-04-19T10:00:00+00:00")
    incident.closed_at = overrides.get("closed_at", "2026-04-19T11:45:00+00:00")
    incident.closed_by = overrides.get("closed_by", "op-arthur")
    incident.root_cause = overrides.get("root_cause", "Misconfigured kill-switch policy")
    incident.board_report_generated = overrides.get("board_report_generated", True)
    incident.timeline = overrides.get(
        "timeline",
        [
            {
                "timestamp": "2026-04-19T10:05:00+00:00",
                "entry_type": "status_change",
                "actor": "Arthur",
                "detail": "Acknowledged incident and paged responders",
            },
            {
                "timestamp": "2026-04-19T10:30:00+00:00",
                "entry_type": "containment",
                "actor": "SYSTEM",
                "detail": "Kill switch issued to contain impact",
            },
        ],
    )
    incident.remediation_receipts = overrides.get("remediation_receipts", ["receipt-remediate-1"])
    return incident


def test_generate_incident_report_returns_pdf_bytes_and_writes_output(tmp_path):
    incident = _incident()
    receipts = [
        {
            "id": "receipt-contain-1",
            "timestamp": "2026-04-19T10:20:00+00:00",
            "action_type": "kill_switch_issued",
            "description": "Issued FEATURE_MCP kill switch",
        },
        {
            "id": "receipt-remediate-1",
            "timestamp": "2026-04-19T11:00:00+00:00",
            "action_type": "connector_disabled",
            "description": "Disabled outbound connector",
        },
    ]

    pdf_bytes = report_generator.generate_incident_report(
        incident,
        receipts=receipts,
        output_dir=str(tmp_path),
    )

    assert pdf_bytes.startswith(b"%PDF")
    saved = tmp_path / f"{incident.incident_id}.pdf"
    assert saved.exists()
    assert saved.read_bytes().startswith(b"%PDF")


def test_generate_incident_report_handles_open_incident_and_missing_optional_sections():
    incident = _incident(
        status=IncidentStatus.OPEN.value,
        closed_at=None,
        closed_by=None,
        root_cause=None,
        board_report_generated=False,
        timeline=[],
        remediation_receipts=[],
    )

    pdf_bytes = report_generator.generate_incident_report(incident, receipts=[])

    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 1000


def test_format_ts_and_compute_duration_cover_edge_cases():
    assert report_generator._format_ts("2026-04-19T10:00:00+00:00") == "2026-04-19 10:00:00 UTC"
    assert report_generator._format_ts("not-a-date") == "not-a-date"
    assert report_generator._format_ts(None) == ""

    assert report_generator._compute_duration("2026-04-19T10:00:00+00:00", "2026-04-19T10:00:45+00:00") == "45 seconds"
    assert report_generator._compute_duration("2026-04-19T10:00:00+00:00", "2026-04-19T10:15:00+00:00") == "15 minutes"
    assert report_generator._compute_duration("2026-04-19T10:00:00+00:00", "2026-04-19T12:30:00+00:00") == "2h 30m"
    assert report_generator._compute_duration("2026-04-19T10:00:00+00:00", "2026-04-21T13:00:00+00:00") == "2d 3h"
    assert report_generator._compute_duration("bad", "2026-04-21T13:00:00+00:00") == "unknown duration"
    assert report_generator._compute_duration(None, None) == "unknown duration"

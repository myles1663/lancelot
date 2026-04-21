from __future__ import annotations

from fastapi.responses import JSONResponse

import feature_flags as _ff


SUBSYSTEM_GATES = (
    ("/memory", "FEATURE_MEMORY_VNEXT"),
    ("/soul", "FEATURE_SOUL"),
    ("/api/scheduler", "FEATURE_SCHEDULER"),
    ("/api/v1/clients", "FEATURE_BAL"),
    ("/api/hive", "FEATURE_HIVE"),
    ("/api/federation", "FEATURE_FEDERATION"),
    ("/api/mcp", "FEATURE_MCP"),
    ("/api/observability", "FEATURE_OBSERVABILITY"),
    ("/api/metrics", "FEATURE_OBSERVABILITY"),
    ("/api/timetravel", "FEATURE_TIME_TRAVEL"),
    ("/api/a2a", "FEATURE_A2A"),
    ("/a2a", "FEATURE_A2A"),
    ("/.well-known/agent.json", "FEATURE_A2A"),
    ("/api/incidents", "FEATURE_INCIDENT_RESPONSE"),
    ("/api/playbooks", "FEATURE_INCIDENT_RESPONSE"),
    ("/api/actioncards", "FEATURE_ACTION_CARDS"),
)


async def subsystem_gate_middleware(request, call_next):
    path = request.url.path
    for prefix, flag_name in SUBSYSTEM_GATES:
        if path == prefix or path.startswith(prefix + "/"):
            if not getattr(_ff, flag_name, False):
                return JSONResponse(
                    status_code=503,
                    content={
                        "error": "subsystem_disabled",
                        "flag": flag_name,
                        "message": f"Enable {flag_name} to use this endpoint",
                    },
                )
    return await call_next(request)

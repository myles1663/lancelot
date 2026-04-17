#!/usr/bin/env python3
"""
================================================================================
  DEMO DATA ONLY — DO NOT COMMIT — LOCAL USE ONLY
================================================================================

  Populates Lancelot data stores with realistic fictional demo data
  for War Room demonstration purposes.

  HOW TO RUN (from Git Bash on host):
  -----------------------------------
    # One-liner: copy into container and execute
    docker cp scripts/seed-demo.py lancelot_core:/tmp/seed-demo.py && \
    MSYS_NO_PATHCONV=1 docker exec lancelot_core python /tmp/seed-demo.py

    # With --clear flag to wipe demo data first:
    docker cp scripts/seed-demo.py lancelot_core:/tmp/seed-demo.py && \
    MSYS_NO_PATHCONV=1 docker exec lancelot_core python /tmp/seed-demo.py --clear

  WHAT IT POPULATES:
  ------------------
    - receipts.db        — 50 receipts across 7 days (mixed tiers, subsystems)
    - scheduler.sqlite   — 5 scheduled jobs (mix enabled/disabled)
    - usage_history.json — 7 days of cost data across 3 models (~$3.50 total)
    - chat/chat_log.json — 15 recent chat messages (last 2 hours)
    - apl/decisions.jsonl — 35 approval decisions (last 7 days)
    - apl/rules.json     — 5 active + 2 proposed automation rules
    - memory/core_blocks.json — 5 core memory blocks (persona, human, etc.)
    - tasks.db           — 3 task graphs + 4 task runs (mix of statuses)
    - tokens.db          — 5 execution tokens (mix active/expired/revoked)
    - incidents/         — 4 incidents (mix of severities and statuses)
    - a2a_registry.db    — 4 remote A2A agents (mix of frameworks/directions)
    - actioncards.db     — 5 action cards (mix resolved/pending)
    - bal/bal.sqlite      — 3 clients + intake + content + deliveries + financial
    - federation/        — topology with 3 nodes + 2 edges + peer state

  NOTE: Trust ledger and governance dashboard metrics are in-memory (derived
  from receipts at runtime) — they cannot be seeded directly.

  WHAT REQUIRES MANUAL PROMPTS (not seedable):
  ---------------------------------------------
    - memory/working_memory.sqlite  — Requires vector embeddings from actual LLM calls
    - memory/episodic.sqlite        — Requires vector embeddings from actual LLM calls
    - memory/archival.sqlite        — Requires vector embeddings from actual LLM calls
    - compliance_exports/           — Generated on-demand via /api/compliance/export
    - incident_reports/             — Generated on-demand via /api/incidents/{id}/report
================================================================================
"""

import json
import os
import random
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DATA_DIR = os.environ.get("LANCELOT_DATA_DIR", "/home/lancelot/data")
DEMO_MARKER = "__demo_seed__"  # metadata marker for cleanup

NOW = datetime.now(timezone.utc)
SEVEN_DAYS_AGO = NOW - timedelta(days=7)


def ts(dt: datetime) -> str:
    """ISO 8601 timestamp."""
    return dt.isoformat()


def random_ts(start: datetime, end: datetime) -> datetime:
    """Random datetime between start and end."""
    delta = (end - start).total_seconds()
    return start + timedelta(seconds=random.uniform(0, delta))


def uid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Receipts
# ---------------------------------------------------------------------------

RECEIPT_TASKS = [
    ("Draft weekly summary email", "tool_call", "send_email", 1),
    ("Fetch Slack messages from #engineering", "tool_call", "read_slack", 0),
    ("Run scheduled daily metrics report", "system", "scheduler_run", 0),
    ("Lookup contact in CRM", "tool_call", "crm_lookup", 1),
    ("Send approval notification to owner", "tool_call", "send_notification", 1),
    ("Analyze Q1 revenue metrics", "llm_call", "analyze_metrics", 2),
    ("Update calendar event for standup", "tool_call", "write_calendar", 1),
    ("Classify incoming user intent", "llm_call", "classify_intent", 0),
    ("Verify plan artifact integrity", "verification", "verify_plan", 2),
    ("Summarize Slack thread for digest", "llm_call", "summarize_thread", 1),
    ("Generate deployment checklist", "plan_step", "deployment_plan", 2),
    ("Redact PII from support ticket", "llm_call", "redact_pii", 0),
    ("Execute shell: git status", "tool_call", "tool_call_shell", 1),
    ("Read workspace file README.md", "file_op", "read_file", 0),
    ("Write updated config to workspace", "file_op", "write_file", 1),
    ("Fetch weather API for daily brief", "tool_call", "fetch_web", 1),
    ("Route query to flagship model", "llm_call", "model_route", 0),
    ("Health check sweep", "system", "health_check", 0),
    ("Compile context window for planning", "system", "compile_context", 0),
    ("Send SMS reminder to owner", "tool_call", "send_sms", 2),
    ("Plan multi-step email campaign", "plan_step", "campaign_plan", 3),
    ("Synthesize research report", "llm_call", "synthesize_report", 3),
    ("Execute scheduled Slack digest", "system", "scheduler_run", 0),
    ("Query database for client list", "tool_call", "run_query", 1),
    ("Verify soul compliance for action", "verification", "soul_check", 0),
    ("Archive old receipts batch", "system", "archive_receipts", 0),
    ("Process voice note transcription", "voice_stt", "voice_transcribe", 1),
    ("Generate TTS response audio", "voice_tts", "voice_generate", 1),
    ("BAL: New client intake - Acme Corp", "bal_intake_event", "bal_intake", 2),
    ("BAL: Invoice delivery to client", "bal_delivery_event", "bal_delivery", 2),
    ("BAL: Monthly billing reconciliation", "bal_billing_event", "bal_billing", 2),
    ("Hive: Decompose research task", "hive_task_event", "hive_decompose", 2),
    ("Hive: Sub-agent completed analysis", "hive_agent_event", "hive_agent_done", 1),
]

SUBSYSTEMS = [
    "soul_engine", "risk_pipeline", "connector_proxy", "skill_security",
    "trust_ledger", "scheduler", "tool_fabric", "model_router",
    "planning_pipeline", "hive_mesh",
]


def seed_receipts():
    """Seed receipts.db with 50 demo receipts."""
    db_path = os.path.join(DATA_DIR, "receipts.db")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")

    # Create table if not exists (matches src/shared/receipts.py schema)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS receipts (
            id TEXT PRIMARY KEY,
            timestamp TEXT NOT NULL,
            action_type TEXT NOT NULL,
            action_name TEXT NOT NULL,
            inputs TEXT NOT NULL,
            outputs TEXT NOT NULL,
            status TEXT NOT NULL,
            duration_ms INTEGER,
            token_count INTEGER,
            tier INTEGER NOT NULL DEFAULT 0,
            parent_id TEXT,
            quest_id TEXT,
            error_message TEXT,
            metadata TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_receipts_timestamp ON receipts(timestamp);
        CREATE INDEX IF NOT EXISTS idx_receipts_action_type ON receipts(action_type);
        CREATE INDEX IF NOT EXISTS idx_receipts_status ON receipts(status);
        CREATE INDEX IF NOT EXISTS idx_receipts_quest_id ON receipts(quest_id);
        CREATE INDEX IF NOT EXISTS idx_receipts_parent_id ON receipts(parent_id);
    """)

    # Clear previous demo data
    conn.execute(
        "DELETE FROM receipts WHERE metadata LIKE ?",
        (f'%{DEMO_MARKER}%',),
    )

    # --- Multi-step quest: email campaign (3 chained receipts) ---
    quest_id_chain = f"quest-demo-{uid()[:8]}"
    chain_base = random_ts(NOW - timedelta(days=2), NOW - timedelta(days=1))
    chain_receipts = [
        {
            "id": uid(), "timestamp": ts(chain_base),
            "action_type": "plan_step", "action_name": "plan_email_campaign",
            "inputs": json.dumps({"goal": "Plan Q1 outreach email campaign"}),
            "outputs": json.dumps({"plan_steps": 3, "risk": "low"}),
            "status": "success", "duration_ms": 1850, "token_count": 420,
            "tier": 2, "parent_id": None, "quest_id": quest_id_chain,
            "error_message": None,
            "metadata": json.dumps({"subsystem": "planning_pipeline", DEMO_MARKER: True}),
        },
        {
            "id": uid(), "timestamp": ts(chain_base + timedelta(seconds=12)),
            "action_type": "tool_call", "action_name": "send_email",
            "inputs": json.dumps({"to": "team@acme-demo.test", "subject": "Q1 Outreach Draft"}),
            "outputs": json.dumps({"sent": True, "message_id": "msg-demo-001"}),
            "status": "success", "duration_ms": 340, "token_count": 85,
            "tier": 1, "parent_id": None, "quest_id": quest_id_chain,
            "error_message": None,
            "metadata": json.dumps({"subsystem": "connector_proxy", DEMO_MARKER: True}),
        },
        {
            "id": uid(), "timestamp": ts(chain_base + timedelta(seconds=18)),
            "action_type": "verification", "action_name": "verify_email_sent",
            "inputs": json.dumps({"message_id": "msg-demo-001"}),
            "outputs": json.dumps({"verified": True, "delivery_status": "accepted"}),
            "status": "success", "duration_ms": 120, "token_count": 0,
            "tier": 0, "parent_id": None, "quest_id": quest_id_chain,
            "error_message": None,
            "metadata": json.dumps({"subsystem": "skill_security", DEMO_MARKER: True}),
        },
    ]
    # Wire parent_id chain
    chain_receipts[1]["parent_id"] = chain_receipts[0]["id"]
    chain_receipts[2]["parent_id"] = chain_receipts[1]["id"]

    # --- Generate 47 more random receipts ---
    random_receipts = []
    for _ in range(47):
        task_desc, action_type, action_name, tier = random.choice(RECEIPT_TASKS)
        receipt_ts = random_ts(SEVEN_DAYS_AGO, NOW)
        is_failure = random.random() < 0.08  # ~8% failure rate
        status = "failure" if is_failure else "success"
        duration = random.randint(30, 3500)
        tokens = random.randint(0, 600) if "llm" in action_type or tier >= 1 else 0
        quest = f"quest-demo-{uid()[:8]}"

        random_receipts.append({
            "id": uid(),
            "timestamp": ts(receipt_ts),
            "action_type": action_type,
            "action_name": action_name,
            "inputs": json.dumps({"description": task_desc}),
            "outputs": json.dumps({} if is_failure else {"result": "ok"}),
            "status": status,
            "duration_ms": duration,
            "token_count": tokens,
            "tier": tier,
            "parent_id": None,
            "quest_id": quest,
            "error_message": "Timeout: upstream service did not respond" if is_failure else None,
            "metadata": json.dumps({
                "subsystem": random.choice(SUBSYSTEMS),
                DEMO_MARKER: True,
            }),
        })

    all_receipts = chain_receipts + random_receipts

    conn.executemany(
        """INSERT OR REPLACE INTO receipts
           (id, timestamp, action_type, action_name, inputs, outputs,
            status, duration_ms, token_count, tier, parent_id, quest_id,
            error_message, metadata)
           VALUES (:id, :timestamp, :action_type, :action_name, :inputs,
                   :outputs, :status, :duration_ms, :token_count, :tier,
                   :parent_id, :quest_id, :error_message, :metadata)""",
        all_receipts,
    )
    conn.commit()
    conn.close()
    print(f"  [receipts.db]        {len(all_receipts)} receipts seeded")


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

DEMO_JOBS = [
    {
        "id": "demo_daily_digest",
        "name": "Daily Digest Email",
        "skill": "send_email",
        "inputs": json.dumps({"template": "daily_digest", "to": "owner@demo.test"}),
        "timezone": "America/New_York",
        "enabled": 1,
        "trigger_type": "cron",
        "trigger_value": "0 8 * * *",
        "requires_ready": 1,
        "requires_approvals": json.dumps([]),
        "timeout_s": 120,
        "description": "Send daily activity digest email at 8 AM ET.",
        "last_run_at": ts(NOW - timedelta(hours=16)),
        "last_run_status": "success",
        "run_count": 14,
        "registered_at": ts(NOW - timedelta(days=14)),
    },
    {
        "id": "demo_weekly_metrics",
        "name": "Weekly Metrics Report",
        "skill": "run_report",
        "inputs": json.dumps({"report": "weekly_metrics", "format": "markdown"}),
        "timezone": "America/New_York",
        "enabled": 1,
        "trigger_type": "cron",
        "trigger_value": "0 9 * * 1",
        "requires_ready": 1,
        "requires_approvals": json.dumps(["owner"]),
        "timeout_s": 300,
        "description": "Generate weekly metrics report every Monday at 9 AM ET.",
        "last_run_at": ts(NOW - timedelta(days=2)),
        "last_run_status": "success",
        "run_count": 3,
        "registered_at": ts(NOW - timedelta(days=21)),
    },
    {
        "id": "demo_hourly_slack",
        "name": "Hourly Slack Check",
        "skill": "read_slack",
        "inputs": json.dumps({"channels": ["#engineering", "#general"]}),
        "timezone": "UTC",
        "enabled": 1,
        "trigger_type": "interval",
        "trigger_value": "3600",
        "requires_ready": 1,
        "requires_approvals": json.dumps([]),
        "timeout_s": 60,
        "description": "Check Slack channels for mentions and important messages.",
        "last_run_at": ts(NOW - timedelta(minutes=35)),
        "last_run_status": "success",
        "run_count": 168,
        "registered_at": ts(NOW - timedelta(days=7)),
    },
    {
        "id": "demo_eod_summary",
        "name": "End-of-Day Summary",
        "skill": "summarize",
        "inputs": json.dumps({"scope": "today", "send_to": "owner@demo.test"}),
        "timezone": "America/New_York",
        "enabled": 0,
        "trigger_type": "cron",
        "trigger_value": "0 17 * * 1-5",
        "requires_ready": 1,
        "requires_approvals": json.dumps([]),
        "timeout_s": 180,
        "description": "Summarize all actions taken today (disabled — pending approval).",
        "last_run_at": None,
        "last_run_status": None,
        "run_count": 0,
        "registered_at": ts(NOW - timedelta(days=3)),
    },
    {
        "id": "demo_health_sweep",
        "name": "System Health Sweep",
        "skill": "health_check",
        "inputs": json.dumps({}),
        "timezone": "UTC",
        "enabled": 1,
        "trigger_type": "interval",
        "trigger_value": "60",
        "requires_ready": 0,
        "requires_approvals": json.dumps([]),
        "timeout_s": 30,
        "description": "Periodic health check of all subsystems.",
        "last_run_at": ts(NOW - timedelta(seconds=45)),
        "last_run_status": "success",
        "run_count": 10080,
        "registered_at": ts(NOW - timedelta(days=7)),
    },
]


def seed_scheduler():
    """Seed scheduler.sqlite with demo jobs."""
    db_path = os.path.join(DATA_DIR, "scheduler.sqlite")
    conn = sqlite3.connect(db_path)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            skill TEXT DEFAULT '',
            inputs TEXT DEFAULT '{}',
            timezone TEXT DEFAULT 'UTC',
            enabled INTEGER DEFAULT 1,
            trigger_type TEXT DEFAULT 'interval',
            trigger_value TEXT DEFAULT '',
            requires_ready INTEGER DEFAULT 1,
            requires_approvals TEXT DEFAULT '[]',
            timeout_s INTEGER DEFAULT 300,
            description TEXT DEFAULT '',
            last_run_at TEXT,
            last_run_status TEXT,
            run_count INTEGER DEFAULT 0,
            registered_at TEXT NOT NULL
        )
    """)
    conn.commit()

    # Clear previous demo jobs
    for job in DEMO_JOBS:
        conn.execute("DELETE FROM jobs WHERE id = ?", (job["id"],))

    conn.executemany(
        """INSERT OR REPLACE INTO jobs
           (id, name, skill, inputs, timezone, enabled, trigger_type,
            trigger_value, requires_ready, requires_approvals, timeout_s,
            description, last_run_at, last_run_status, run_count, registered_at)
           VALUES (:id, :name, :skill, :inputs, :timezone, :enabled,
                   :trigger_type, :trigger_value, :requires_ready,
                   :requires_approvals, :timeout_s, :description,
                   :last_run_at, :last_run_status, :run_count, :registered_at)""",
        DEMO_JOBS,
    )
    conn.commit()
    conn.close()
    print(f"  [scheduler.sqlite]   {len(DEMO_JOBS)} jobs seeded")


# ---------------------------------------------------------------------------
# Usage History
# ---------------------------------------------------------------------------

def seed_usage_history():
    """Seed usage_history.json with 7 days of realistic cost data."""
    month_key = NOW.strftime("%Y-%m")

    models = {
        "gemini-3-flash-preview": {"cost_per_1k": 0.005, "share": 0.55},
        "gemini-3-pro-preview":   {"cost_per_1k": 0.03,  "share": 0.15},
        "local-llm":              {"cost_per_1k": 0.0,   "share": 0.30},
    }

    by_model = {}
    by_day = {}
    total_requests = 0
    total_tokens = 0
    total_cost = 0.0

    for day_offset in range(7):
        day = (NOW - timedelta(days=6 - day_offset)).strftime("%Y-%m-%d")
        day_requests = random.randint(25, 75)
        day_tokens = 0
        day_cost = 0.0

        for model_name, info in models.items():
            model_reqs = max(1, int(day_requests * info["share"]) + random.randint(-3, 3))
            model_tokens = model_reqs * random.randint(800, 2200)
            model_cost = round(model_tokens / 1000 * info["cost_per_1k"], 6)

            if model_name not in by_model:
                by_model[model_name] = {"requests": 0, "tokens": 0, "cost": 0.0}
            by_model[model_name]["requests"] += model_reqs
            by_model[model_name]["tokens"] += model_tokens
            by_model[model_name]["cost"] = round(by_model[model_name]["cost"] + model_cost, 6)

            day_tokens += model_tokens
            day_cost = round(day_cost + model_cost, 6)
            total_requests += model_reqs

        by_day[day] = {
            "requests": day_requests,
            "tokens": day_tokens,
            "cost": day_cost,
        }
        total_tokens += day_tokens
        total_cost = round(total_cost + day_cost, 6)

    data = {
        "current_month": month_key,
        "months": {
            month_key: {
                "total_requests": total_requests,
                "total_tokens": total_tokens,
                "total_cost": round(total_cost, 4),
                "by_model": by_model,
                "by_day": by_day,
            }
        },
    }

    path = os.path.join(DATA_DIR, "usage_history.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"  [usage_history.json] 7 days, {total_requests} requests, ${total_cost:.2f} total")


# ---------------------------------------------------------------------------
# Chat Log
# ---------------------------------------------------------------------------

DEMO_CONVERSATIONS = [
    ("user", "What meetings do I have tomorrow?"),
    ("assistant", "You have 3 meetings tomorrow:\n- 9:00 AM: Team standup (15 min)\n- 11:00 AM: Client review with Acme Corp (1 hr)\n- 2:30 PM: Sprint planning (45 min)\n\nWould you like me to prepare briefing notes for any of these?"),
    ("user", "Prepare a brief for the Acme Corp review"),
    ("assistant", "I've compiled a briefing for the Acme Corp client review:\n\n**Acme Corp — Q1 Review**\n- Project status: On track (87% milestone completion)\n- Open items: 2 pending approvals on design changes\n- Last contact: March 5 — follow-up on API integration timeline\n- Key risk: Delivery date for Phase 2 may slip 1 week\n\nShall I send this to your email or create a calendar note?"),
    ("user", "Send it to my email"),
    ("assistant", "Done. Briefing sent to your email. Receipt: `rcpt-demo-acme-brief`. I'll also add a reminder 30 minutes before the meeting."),
    ("user", "Check Slack for anything important from today"),
    ("assistant", "I scanned #engineering and #general. Here's the summary:\n\n**#engineering** (3 notable):\n- Deploy v2.4.1 completed successfully at 2:15 PM\n- Jenkins pipeline flaky test issue raised by @devops-bot\n- Code review requested on PR #347 by @frontend-team\n\n**#general** (1 notable):\n- Company all-hands moved to Friday 3 PM\n\nNo action items requiring your immediate attention."),
    ("user", "Run the weekly metrics report"),
    ("assistant", "Running the weekly metrics report now...\n\nReport generated. Key highlights:\n- Total actions this week: 203\n- Autonomous completion rate: 94.2%\n- Cost: $3.47 (down 12% from last week)\n- Most active subsystem: connector_proxy (67 actions)\n- 0 governance violations\n\nFull report saved to workspace. Want me to email it?"),
    ("user", "Show me recent receipts for email actions"),
    ("assistant", "Found 8 email-related receipts in the last 7 days:\n\n| Time | Action | Status | Tier |\n|------|--------|--------|------|\n| Mar 8, 2:30 PM | send_email (daily digest) | success | T1 |\n| Mar 7, 8:00 AM | send_email (daily digest) | success | T1 |\n| Mar 6, 4:15 PM | send_email (client brief) | success | T1 |\n| Mar 6, 8:01 AM | send_email (daily digest) | success | T1 |\n| Mar 5, 8:00 AM | send_email (daily digest) | success | T1 |\n\nAll governed, all receipted. No failures."),
    ("user", "How are we doing on costs this month?"),
    ("assistant", "Here's your March cost summary so far:\n\n- **Total spend:** $3.47\n- **Gemini Flash:** $2.10 (55% of calls, cheapest per-token)\n- **Gemini Pro:** $1.37 (15% of calls, used for planning/synthesis)\n- **Local model:** $0.00 (30% of calls — saved ~$1.20)\n\nYou're trending well below last month's pace. The local model routing is saving about 25% versus cloud-only."),
]


def seed_chat_log():
    """Seed chat_log.json with realistic demo conversation."""
    chat_dir = os.path.join(DATA_DIR, "chat")
    os.makedirs(chat_dir, exist_ok=True)

    messages = []
    base_time = NOW - timedelta(hours=2)
    for i, (role, content) in enumerate(DEMO_CONVERSATIONS):
        msg_time = base_time + timedelta(minutes=i * 8 + random.randint(0, 3))
        messages.append({
            "role": role,
            "content": content,
            "timestamp": msg_time.timestamp(),
        })

    path = os.path.join(chat_dir, "chat_log.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(messages, f, indent=2)
    print(f"  [chat_log.json]      {len(messages)} messages seeded")


# ---------------------------------------------------------------------------
# APL — Decisions (JSONL)
# ---------------------------------------------------------------------------

APL_CAPABILITIES = [
    ("connector.email.send_message", "send_message", "email", 1,
     "team@acme-demo.test", "acme-demo.test", "verified_recipient"),
    ("connector.slack.read_channel", "read_channel", "slack", 0,
     "channel:#engineering", "slack.demo.test", "internal"),
    ("connector.calendar.write_event", "write_event", "calendar", 1,
     "calendar:primary", "calendar.demo.test", "self"),
    ("connector.web.fetch_url", "fetch_url", "web", 1,
     "https://api.weather-demo.test", "weather-demo.test", "external_api"),
    ("connector.sms.send_message", "send_message", "sms", 2,
     "+15551234567", "phone", "verified_recipient"),
    ("tool.shell.execute", "execute", "shell", 1,
     "git status", "local", "read_only"),
    ("connector.crm.read_contact", "read_contact", "crm", 0,
     "contact:12345", "crm.demo.test", "internal"),
    ("connector.email.send_message", "send_message", "email", 2,
     "external@unknown-demo.test", "unknown-demo.test", "new_recipient"),
]


def seed_apl_decisions():
    """Seed apl/decisions.jsonl with approval decision history."""
    apl_dir = os.path.join(DATA_DIR, "apl")
    os.makedirs(apl_dir, exist_ok=True)

    records = []
    for _ in range(35):
        cap = random.choice(APL_CAPABILITIES)
        capability, op_id, connector_id, risk_tier, target, domain, category = cap
        decision_ts = random_ts(SEVEN_DAYS_AGO, NOW)
        is_denied = random.random() < 0.15
        is_auto = not is_denied and random.random() < 0.35

        record = {
            "id": uid(),
            "context": {
                "capability": capability,
                "operation_id": op_id,
                "connector_id": connector_id,
                "risk_tier": risk_tier,
                "target": target,
                "target_domain": domain,
                "target_category": category,
                "scope": f"channel:#{random.choice(['engineering', 'general', 'alerts'])}" if connector_id == "slack" else "",
                "timestamp": ts(decision_ts),
                "day_of_week": decision_ts.weekday(),
                "hour_of_day": decision_ts.hour,
                "content_hash": uuid.uuid4().hex[:16],
                "content_size": random.randint(50, 2000),
                "metadata": {},
            },
            "decision": "denied" if is_denied else "approved",
            "decision_time_ms": random.randint(200, 8000) if not is_auto else 0,
            "reason": "Unrecognized external domain" if is_denied else "",
            "rule_id": f"rule-demo-{random.randint(1, 5)}" if is_auto else "",
            "recorded_at": ts(decision_ts + timedelta(milliseconds=random.randint(50, 500))),
        }
        records.append(record)

    # Sort by timestamp
    records.sort(key=lambda r: r["context"]["timestamp"])

    path = os.path.join(apl_dir, "decisions.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")
    print(f"  [apl/decisions.jsonl] {len(records)} decisions seeded")


# ---------------------------------------------------------------------------
# APL — Rules (JSON)
# ---------------------------------------------------------------------------

def seed_apl_rules():
    """Seed apl/rules.json with automation rules."""
    apl_dir = os.path.join(DATA_DIR, "apl")
    os.makedirs(apl_dir, exist_ok=True)

    rules = {
        "rule-demo-1": {
            "id": "rule-demo-1",
            "name": "Auto-approve internal email sends",
            "description": "Always approve send_email when target domain is acme-demo.test (verified internal).",
            "pattern_id": "pat-demo-1",
            "pattern_type": "auto_approve",
            "conditions": {
                "capability": "connector.email.send_message",
                "target_domain": "acme-demo.test",
                "target_category": "verified_recipient",
            },
            "status": "active",
            "created_at": ts(NOW - timedelta(days=12)),
            "activated_at": ts(NOW - timedelta(days=10)),
            "revoked_at": "",
            "max_auto_decisions_per_day": 50,
            "max_auto_decisions_total": 500,
            "expires_at": "",
            "auto_decisions_today": 3,
            "auto_decisions_total": 47,
            "last_auto_decision": ts(NOW - timedelta(hours=4)),
            "last_reset_date": NOW.strftime("%Y-%m-%d"),
            "owner_confirmed": True,
            "soul_compatible": True,
        },
        "rule-demo-2": {
            "id": "rule-demo-2",
            "name": "Auto-approve Slack reads during business hours",
            "description": "Approve read_channel on Slack between 8 AM and 6 PM UTC on weekdays.",
            "pattern_id": "pat-demo-2",
            "pattern_type": "auto_approve",
            "conditions": {
                "capability": "connector.slack.read_channel",
                "hour_range": [8, 18],
                "day_of_week_range": [0, 4],
            },
            "status": "active",
            "created_at": ts(NOW - timedelta(days=9)),
            "activated_at": ts(NOW - timedelta(days=8)),
            "revoked_at": "",
            "max_auto_decisions_per_day": 50,
            "max_auto_decisions_total": 500,
            "expires_at": "",
            "auto_decisions_today": 8,
            "auto_decisions_total": 112,
            "last_auto_decision": ts(NOW - timedelta(minutes=35)),
            "last_reset_date": NOW.strftime("%Y-%m-%d"),
            "owner_confirmed": True,
            "soul_compatible": True,
        },
        "rule-demo-3": {
            "id": "rule-demo-3",
            "name": "Auto-approve calendar reads",
            "description": "Always approve read operations on calendar connector.",
            "pattern_id": "pat-demo-3",
            "pattern_type": "auto_approve",
            "conditions": {
                "capability": "connector.calendar.*",
                "operation_id": "read_*",
            },
            "status": "active",
            "created_at": ts(NOW - timedelta(days=6)),
            "activated_at": ts(NOW - timedelta(days=5)),
            "revoked_at": "",
            "max_auto_decisions_per_day": 50,
            "max_auto_decisions_total": 500,
            "expires_at": "",
            "auto_decisions_today": 5,
            "auto_decisions_total": 38,
            "last_auto_decision": ts(NOW - timedelta(hours=1)),
            "last_reset_date": NOW.strftime("%Y-%m-%d"),
            "owner_confirmed": True,
            "soul_compatible": True,
        },
        "rule-demo-4": {
            "id": "rule-demo-4",
            "name": "Flag external API calls after hours",
            "description": "Deny auto-approval for external API fetch_url calls after 6 PM UTC.",
            "pattern_id": "pat-demo-4",
            "pattern_type": "auto_deny",
            "conditions": {
                "capability": "connector.web.fetch_url",
                "target_category": "external_api",
                "hour_range": [18, 23],
            },
            "status": "active",
            "created_at": ts(NOW - timedelta(days=4)),
            "activated_at": ts(NOW - timedelta(days=3)),
            "revoked_at": "",
            "max_auto_decisions_per_day": 50,
            "max_auto_decisions_total": 500,
            "expires_at": "",
            "auto_decisions_today": 1,
            "auto_decisions_total": 6,
            "last_auto_decision": ts(NOW - timedelta(hours=8)),
            "last_reset_date": NOW.strftime("%Y-%m-%d"),
            "owner_confirmed": True,
            "soul_compatible": True,
        },
        "rule-demo-5": {
            "id": "rule-demo-5",
            "name": "Auto-approve CRM reads",
            "description": "Always approve read_contact operations on CRM connector.",
            "pattern_id": "pat-demo-5",
            "pattern_type": "auto_approve",
            "conditions": {
                "capability": "connector.crm.read_contact",
            },
            "status": "active",
            "created_at": ts(NOW - timedelta(days=3)),
            "activated_at": ts(NOW - timedelta(days=2)),
            "revoked_at": "",
            "max_auto_decisions_per_day": 50,
            "max_auto_decisions_total": 500,
            "expires_at": "",
            "auto_decisions_today": 2,
            "auto_decisions_total": 15,
            "last_auto_decision": ts(NOW - timedelta(hours=3)),
            "last_reset_date": NOW.strftime("%Y-%m-%d"),
            "owner_confirmed": True,
            "soul_compatible": True,
        },
        # --- Proposed (not yet confirmed) ---
        "rule-demo-6": {
            "id": "rule-demo-6",
            "name": "Auto-approve SMS to verified numbers",
            "description": "Proposed: approve send_sms to numbers in verified_recipient category.",
            "pattern_id": "pat-demo-6",
            "pattern_type": "auto_approve",
            "conditions": {
                "capability": "connector.sms.send_message",
                "target_category": "verified_recipient",
            },
            "status": "proposed",
            "created_at": ts(NOW - timedelta(hours=6)),
            "activated_at": "",
            "revoked_at": "",
            "max_auto_decisions_per_day": 10,
            "max_auto_decisions_total": 100,
            "expires_at": ts(NOW + timedelta(days=30)),
            "auto_decisions_today": 0,
            "auto_decisions_total": 0,
            "last_auto_decision": "",
            "last_reset_date": "",
            "owner_confirmed": False,
            "soul_compatible": True,
        },
        "rule-demo-7": {
            "id": "rule-demo-7",
            "name": "Auto-approve shell read-only commands",
            "description": "Proposed: approve shell execute for read-only commands (git status, ls, cat).",
            "pattern_id": "pat-demo-7",
            "pattern_type": "auto_approve",
            "conditions": {
                "capability": "tool.shell.execute",
                "target_category": "read_only",
            },
            "status": "proposed",
            "created_at": ts(NOW - timedelta(hours=2)),
            "activated_at": "",
            "revoked_at": "",
            "max_auto_decisions_per_day": 30,
            "max_auto_decisions_total": 300,
            "expires_at": ts(NOW + timedelta(days=30)),
            "auto_decisions_today": 0,
            "auto_decisions_total": 0,
            "last_auto_decision": "",
            "last_reset_date": "",
            "owner_confirmed": False,
            "soul_compatible": True,
        },
    }

    data = {
        "rules": rules,
        "declined_patterns": {
            "pat-declined-1": ts(NOW - timedelta(days=5)),
        },
    }

    path = os.path.join(apl_dir, "rules.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    active = sum(1 for r in rules.values() if r["status"] == "active")
    proposed = sum(1 for r in rules.values() if r["status"] == "proposed")
    print(f"  [apl/rules.json]     {active} active + {proposed} proposed rules seeded")


# ---------------------------------------------------------------------------
# Core Memory Blocks
# ---------------------------------------------------------------------------

def seed_core_memory():
    """Seed memory/core_blocks.json with realistic persona and context."""
    mem_dir = os.path.join(DATA_DIR, "memory")
    os.makedirs(mem_dir, exist_ok=True)

    blocks = {
        "persona": {
            "block_type": "persona",
            "content": (
                "I am Lancelot, a sovereign AI agent built on trust-first governance. "
                "I operate with full receipt accountability — every action I take is "
                "logged, auditable, and reversible. I never act beyond my granted "
                "authority and I proactively surface risks before they become problems. "
                "I am direct, concise, and transparent in all interactions."
            ),
            "token_budget": 500,
            "token_count": 62,
            "updated_at": ts(NOW - timedelta(days=14)),
            "updated_by": "system",
            "status": "active",
            "provenance": [
                {"type": "system", "ref": "soul/ACTIVE/directives.yaml",
                 "snippet": "Core persona from Soul Engine", "timestamp": ts(NOW - timedelta(days=14))}
            ],
            "confidence": 1.0,
            "version": 1,
        },
        "human": {
            "block_type": "human",
            "content": (
                "The owner prefers concise updates over lengthy explanations. "
                "They work primarily in Eastern Time and are most active between "
                "9 AM and 6 PM. They trust automated email sends to internal domains "
                "but want approval for external contacts. They value cost efficiency "
                "and regularly check the weekly metrics report."
            ),
            "token_budget": 400,
            "token_count": 55,
            "updated_at": ts(NOW - timedelta(days=5)),
            "updated_by": "agent",
            "status": "active",
            "provenance": [
                {"type": "user_input", "ref": "chat",
                 "snippet": "Learned from owner interactions", "timestamp": ts(NOW - timedelta(days=5))},
                {"type": "agent_update", "ref": "apl_pattern_analysis",
                 "snippet": "Inferred from APL approval patterns", "timestamp": ts(NOW - timedelta(days=3))},
            ],
            "confidence": 0.85,
            "version": 3,
        },
        "mission": {
            "block_type": "mission",
            "content": (
                "Assist the owner with daily operations: email management, Slack monitoring, "
                "calendar coordination, metrics reporting, and client communications. "
                "Maintain full governance compliance. Optimize for cost by routing to "
                "local models when possible. Proactively surface actionable insights."
            ),
            "token_budget": 300,
            "token_count": 42,
            "updated_at": ts(NOW - timedelta(days=10)),
            "updated_by": "owner",
            "status": "active",
            "provenance": [
                {"type": "user_input", "ref": "setup_wizard",
                 "snippet": "Owner-defined during onboarding", "timestamp": ts(NOW - timedelta(days=14))},
            ],
            "confidence": 1.0,
            "version": 2,
        },
        "operating_rules": {
            "block_type": "operating_rules",
            "content": (
                "1. Never send emails to external domains without explicit approval.\n"
                "2. Slack read operations are auto-approved during business hours (8-18 UTC, weekdays).\n"
                "3. All Tier 2+ actions require receipt verification.\n"
                "4. Cost alerts trigger at 80% of daily budget ($8.00 threshold).\n"
                "5. Shell commands limited to read-only operations unless owner-approved.\n"
                "6. Client data (CRM, BAL) follows GDPR redaction rules."
            ),
            "token_budget": 400,
            "token_count": 78,
            "updated_at": ts(NOW - timedelta(days=2)),
            "updated_by": "owner",
            "status": "active",
            "provenance": [
                {"type": "user_input", "ref": "soul_amendment",
                 "snippet": "Owner-confirmed operating rules", "timestamp": ts(NOW - timedelta(days=10))},
                {"type": "agent_update", "ref": "apl_rule_sync",
                 "snippet": "Updated with APL rule confirmations", "timestamp": ts(NOW - timedelta(days=2))},
            ],
            "confidence": 0.95,
            "version": 4,
        },
        "workspace_state": {
            "block_type": "workspace_state",
            "content": (
                "Active project: Q1 outreach campaign for Acme Corp.\n"
                "Pending: 2 design approvals, 1 API integration review.\n"
                "Scheduled: Weekly metrics report (Monday 9 AM), daily digest (8 AM).\n"
                "Recent: Deployed v2.4.1 successfully, Jenkins flaky test under investigation.\n"
                "BAL: 3 active clients, 2 pending deliveries."
            ),
            "token_budget": 300,
            "token_count": 55,
            "updated_at": ts(NOW - timedelta(hours=2)),
            "updated_by": "agent",
            "status": "active",
            "provenance": [
                {"type": "agent_update", "ref": "context_compiler",
                 "snippet": "Auto-compiled workspace snapshot", "timestamp": ts(NOW - timedelta(hours=2))},
            ],
            "confidence": 0.9,
            "version": 12,
        },
    }

    data = {
        "version": "1.0",
        "updated_at": ts(NOW),
        "blocks": blocks,
    }

    path = os.path.join(mem_dir, "core_blocks.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"  [memory/core_blocks]  5 core memory blocks seeded")


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

def seed_tasks():
    """Seed tasks.db with task graphs and runs."""
    db_path = os.path.join(DATA_DIR, "tasks.db")
    conn = sqlite3.connect(db_path)

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS task_graphs (
            id TEXT PRIMARY KEY,
            goal TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            planner_version TEXT NOT NULL DEFAULT 'v1',
            steps TEXT NOT NULL DEFAULT '[]',
            session_id TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS task_runs (
            id TEXT PRIMARY KEY,
            task_graph_id TEXT NOT NULL,
            execution_token_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'QUEUED',
            current_step_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            receipts_index TEXT NOT NULL DEFAULT '[]',
            last_error TEXT,
            session_id TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_task_runs_status ON task_runs(status);
        CREATE INDEX IF NOT EXISTS idx_task_runs_session ON task_runs(session_id);
        CREATE INDEX IF NOT EXISTS idx_task_graphs_session ON task_graphs(session_id);
    """)

    # Clear previous demo data
    conn.execute("DELETE FROM task_graphs WHERE id LIKE 'demo-%'")
    conn.execute("DELETE FROM task_runs WHERE id LIKE 'demo-%'")

    session_id = f"session-demo-{uid()[:8]}"

    # Task 1: Completed email campaign
    g1_steps = json.dumps([
        {"step_id": "s1", "action": "plan_email_campaign", "status": "COMPLETED"},
        {"step_id": "s2", "action": "draft_email_content", "status": "COMPLETED"},
        {"step_id": "s3", "action": "send_email", "status": "COMPLETED"},
        {"step_id": "s4", "action": "verify_delivery", "status": "COMPLETED"},
    ])
    conn.execute(
        "INSERT INTO task_graphs (id, goal, created_at, planner_version, steps, session_id) VALUES (?,?,?,?,?,?)",
        ("demo-graph-1", "Plan and execute Q1 outreach email campaign",
         ts(NOW - timedelta(days=2)), "v1", g1_steps, session_id),
    )
    conn.execute(
        """INSERT INTO task_runs (id, task_graph_id, execution_token_id, status,
           current_step_id, created_at, updated_at, receipts_index, session_id)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        ("demo-run-1", "demo-graph-1", "demo-token-1", "SUCCESS",
         None, ts(NOW - timedelta(days=2)), ts(NOW - timedelta(days=2, hours=-1)),
         json.dumps(["rcpt-demo-001", "rcpt-demo-002", "rcpt-demo-003"]), session_id),
    )

    # Task 2: Currently running metrics report
    g2_steps = json.dumps([
        {"step_id": "s1", "action": "gather_metrics", "status": "COMPLETED"},
        {"step_id": "s2", "action": "analyze_trends", "status": "RUNNING"},
        {"step_id": "s3", "action": "generate_report", "status": "QUEUED"},
        {"step_id": "s4", "action": "send_report", "status": "QUEUED"},
    ])
    conn.execute(
        "INSERT INTO task_graphs (id, goal, created_at, planner_version, steps, session_id) VALUES (?,?,?,?,?,?)",
        ("demo-graph-2", "Generate and send weekly metrics report",
         ts(NOW - timedelta(minutes=15)), "v1", g2_steps, session_id),
    )
    conn.execute(
        """INSERT INTO task_runs (id, task_graph_id, execution_token_id, status,
           current_step_id, created_at, updated_at, receipts_index, session_id)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        ("demo-run-2", "demo-graph-2", "demo-token-2", "RUNNING",
         "s2", ts(NOW - timedelta(minutes=15)), ts(NOW - timedelta(minutes=3)),
         json.dumps(["rcpt-demo-004"]), session_id),
    )

    # Task 3: Failed client lookup
    g3_steps = json.dumps([
        {"step_id": "s1", "action": "crm_lookup", "status": "FAILED"},
    ])
    conn.execute(
        "INSERT INTO task_graphs (id, goal, created_at, planner_version, steps, session_id) VALUES (?,?,?,?,?,?)",
        ("demo-graph-3", "Look up Acme Corp contact info in CRM",
         ts(NOW - timedelta(hours=6)), "v1", g3_steps, session_id),
    )
    conn.execute(
        """INSERT INTO task_runs (id, task_graph_id, execution_token_id, status,
           current_step_id, created_at, updated_at, receipts_index, last_error, session_id)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        ("demo-run-3", "demo-graph-3", "demo-token-3", "FAILED",
         "s1", ts(NOW - timedelta(hours=6)), ts(NOW - timedelta(hours=6, minutes=-2)),
         json.dumps([]), "CRM connector timeout after 30s", session_id),
    )

    # Task 4: Queued Slack digest
    conn.execute(
        """INSERT INTO task_runs (id, task_graph_id, execution_token_id, status,
           current_step_id, created_at, updated_at, receipts_index, session_id)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        ("demo-run-4", "demo-graph-2", "", "QUEUED",
         None, ts(NOW - timedelta(minutes=2)), ts(NOW - timedelta(minutes=2)),
         json.dumps([]), session_id),
    )

    conn.commit()
    conn.close()
    print(f"  [tasks.db]           3 task graphs + 4 task runs seeded")


# ---------------------------------------------------------------------------
# Execution Tokens
# ---------------------------------------------------------------------------

def seed_tokens():
    """Seed tokens.db with execution tokens."""
    db_path = os.path.join(DATA_DIR, "tokens.db")
    conn = sqlite3.connect(db_path)

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS execution_tokens (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            created_by TEXT NOT NULL,
            scope TEXT NOT NULL DEFAULT '',
            task_type TEXT NOT NULL DEFAULT 'OTHER',
            allowed_tools TEXT NOT NULL DEFAULT '[]',
            allowed_skills TEXT NOT NULL DEFAULT '[]',
            allowed_paths TEXT NOT NULL DEFAULT '[]',
            network_policy TEXT NOT NULL DEFAULT 'OFF',
            network_allowlist TEXT NOT NULL DEFAULT '[]',
            secret_policy TEXT NOT NULL DEFAULT 'NO_SECRETS',
            max_duration_sec INTEGER NOT NULL DEFAULT 300,
            max_actions INTEGER NOT NULL DEFAULT 50,
            risk_tier TEXT NOT NULL DEFAULT 'LOW',
            requires_verifier INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            parent_receipt_id TEXT,
            actions_used INTEGER NOT NULL DEFAULT 0,
            expires_at TEXT,
            session_id TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_tokens_status ON execution_tokens(status);
        CREATE INDEX IF NOT EXISTS idx_tokens_session ON execution_tokens(session_id);
        CREATE INDEX IF NOT EXISTS idx_tokens_expires ON execution_tokens(expires_at);
    """)

    conn.execute("DELETE FROM execution_tokens WHERE id LIKE 'demo-%'")

    session_id = f"session-demo-{uid()[:8]}"

    tokens = [
        {
            "id": "demo-token-1", "created_at": ts(NOW - timedelta(days=2)),
            "created_by": "control_plane", "scope": "email_campaign",
            "task_type": "MULTI_STEP",
            "allowed_tools": json.dumps(["send_email", "read_slack"]),
            "allowed_skills": json.dumps(["email_compose", "plan"]),
            "allowed_paths": json.dumps([]),
            "network_policy": "ALLOWLIST",
            "network_allowlist": json.dumps(["acme-demo.test", "smtp.demo.test"]),
            "secret_policy": "REDACTED", "max_duration_sec": 600,
            "max_actions": 20, "risk_tier": "MEDIUM", "requires_verifier": 1,
            "status": "EXPIRED", "parent_receipt_id": None,
            "actions_used": 12, "expires_at": ts(NOW - timedelta(days=1, hours=22)),
            "session_id": session_id,
        },
        {
            "id": "demo-token-2", "created_at": ts(NOW - timedelta(minutes=15)),
            "created_by": "control_plane", "scope": "metrics_report",
            "task_type": "MULTI_STEP",
            "allowed_tools": json.dumps(["run_report", "read_slack", "fetch_web"]),
            "allowed_skills": json.dumps(["analyze_metrics", "summarize"]),
            "allowed_paths": json.dumps(["/workspace/reports/"]),
            "network_policy": "OFF", "network_allowlist": json.dumps([]),
            "secret_policy": "NO_SECRETS", "max_duration_sec": 300,
            "max_actions": 50, "risk_tier": "LOW", "requires_verifier": 0,
            "status": "ACTIVE", "parent_receipt_id": None,
            "actions_used": 4, "expires_at": ts(NOW + timedelta(minutes=45)),
            "session_id": session_id,
        },
        {
            "id": "demo-token-3", "created_at": ts(NOW - timedelta(hours=6)),
            "created_by": "control_plane", "scope": "crm_lookup",
            "task_type": "SINGLE",
            "allowed_tools": json.dumps(["crm_lookup"]),
            "allowed_skills": json.dumps([]),
            "allowed_paths": json.dumps([]),
            "network_policy": "ALLOWLIST",
            "network_allowlist": json.dumps(["crm.demo.test"]),
            "secret_policy": "REDACTED", "max_duration_sec": 60,
            "max_actions": 5, "risk_tier": "LOW", "requires_verifier": 0,
            "status": "REVOKED", "parent_receipt_id": None,
            "actions_used": 1, "expires_at": ts(NOW - timedelta(hours=5, minutes=59)),
            "session_id": session_id,
        },
        {
            "id": "demo-token-4", "created_at": ts(NOW - timedelta(hours=1)),
            "created_by": "control_plane", "scope": "slack_digest",
            "task_type": "SCHEDULED",
            "allowed_tools": json.dumps(["read_slack", "send_email"]),
            "allowed_skills": json.dumps(["summarize_thread"]),
            "allowed_paths": json.dumps([]),
            "network_policy": "OFF", "network_allowlist": json.dumps([]),
            "secret_policy": "NO_SECRETS", "max_duration_sec": 120,
            "max_actions": 30, "risk_tier": "LOW", "requires_verifier": 0,
            "status": "ACTIVE", "parent_receipt_id": None,
            "actions_used": 8, "expires_at": ts(NOW + timedelta(hours=1)),
            "session_id": session_id,
        },
        {
            "id": "demo-token-5", "created_at": ts(NOW - timedelta(days=1)),
            "created_by": "hive_orchestrator", "scope": "research_decomposition",
            "task_type": "HIVE",
            "allowed_tools": json.dumps(["fetch_web", "read_file"]),
            "allowed_skills": json.dumps(["synthesize_report", "analyze_metrics"]),
            "allowed_paths": json.dumps(["/workspace/research/"]),
            "network_policy": "BLOCKLIST",
            "network_allowlist": json.dumps(["*.internal", "10.*"]),
            "secret_policy": "NO_SECRETS", "max_duration_sec": 900,
            "max_actions": 100, "risk_tier": "HIGH", "requires_verifier": 1,
            "status": "EXPIRED", "parent_receipt_id": None,
            "actions_used": 47, "expires_at": ts(NOW - timedelta(hours=9)),
            "session_id": session_id,
        },
    ]

    for t in tokens:
        conn.execute(
            """INSERT OR REPLACE INTO execution_tokens
               (id, created_at, created_by, scope, task_type, allowed_tools,
                allowed_skills, allowed_paths, network_policy, network_allowlist,
                secret_policy, max_duration_sec, max_actions, risk_tier,
                requires_verifier, status, parent_receipt_id, actions_used,
                expires_at, session_id)
               VALUES (:id, :created_at, :created_by, :scope, :task_type,
                       :allowed_tools, :allowed_skills, :allowed_paths,
                       :network_policy, :network_allowlist, :secret_policy,
                       :max_duration_sec, :max_actions, :risk_tier,
                       :requires_verifier, :status, :parent_receipt_id,
                       :actions_used, :expires_at, :session_id)""",
            t,
        )

    conn.commit()
    conn.close()
    print(f"  [tokens.db]          {len(tokens)} execution tokens seeded")


# ---------------------------------------------------------------------------
# Incidents
# ---------------------------------------------------------------------------

DEMO_INCIDENTS = [
    {
        "incident_id": "inc-demo-001",
        "trigger_receipt_id": "rcpt-demo-inc-001",
        "category": "COST_ANOMALY",
        "severity": "MEDIUM",
        "playbook_name": "cost_anomaly_investigation",
        "status": "CLOSED",
        "opened_at": ts(NOW - timedelta(days=3)),
        "paged_at": ts(NOW - timedelta(days=3, hours=-1)),
        "responder_id": "owner",
        "acknowledged_at": ts(NOW - timedelta(days=3, hours=-1, minutes=-15)),
        "timeline": [
            {"timestamp": ts(NOW - timedelta(days=3)), "entry_type": "opened",
             "actor": "system", "detail": "Daily cost exceeded 80% threshold ($8.12 / $10.00)", "receipt_id": "rcpt-demo-inc-001"},
            {"timestamp": ts(NOW - timedelta(days=3, hours=-1)), "entry_type": "paged",
             "actor": "system", "detail": "Owner notified via Telegram", "receipt_id": None},
            {"timestamp": ts(NOW - timedelta(days=3, hours=-1, minutes=-15)), "entry_type": "acknowledged",
             "actor": "owner", "detail": "Owner acknowledged — investigating", "receipt_id": None},
            {"timestamp": ts(NOW - timedelta(days=3, hours=-3)), "entry_type": "resolved",
             "actor": "owner", "detail": "Root cause: batch job ran against Pro model instead of Flash. Rerouted.", "receipt_id": None},
        ],
        "remediation_receipts": ["rcpt-demo-inc-002"],
        "closed_at": ts(NOW - timedelta(days=3, hours=-3)),
        "closed_by": "owner",
        "root_cause": "Batch metrics job was routed to gemini-3-pro instead of gemini-3-flash due to model router misconfiguration.",
        "board_report_generated": True,
        "dedup_key": "cost_anomaly_daily_budget",
    },
    {
        "incident_id": "inc-demo-002",
        "trigger_receipt_id": "rcpt-demo-inc-003",
        "category": "SECURITY_EVENT",
        "severity": "HIGH",
        "playbook_name": "unauthorized_access_response",
        "status": "CLOSED",
        "opened_at": ts(NOW - timedelta(days=5)),
        "paged_at": ts(NOW - timedelta(days=5, hours=-0.5)),
        "responder_id": "owner",
        "acknowledged_at": ts(NOW - timedelta(days=5, hours=-1)),
        "timeline": [
            {"timestamp": ts(NOW - timedelta(days=5)), "entry_type": "opened",
             "actor": "system", "detail": "Unauthorized tool_call attempted: shell execute 'rm -rf /workspace' — blocked by Soul Engine", "receipt_id": "rcpt-demo-inc-003"},
            {"timestamp": ts(NOW - timedelta(days=5, hours=-0.5)), "entry_type": "paged",
             "actor": "system", "detail": "CRITICAL: Destructive command blocked", "receipt_id": None},
            {"timestamp": ts(NOW - timedelta(days=5, hours=-1)), "entry_type": "acknowledged",
             "actor": "owner", "detail": "Investigating prompt injection vector", "receipt_id": None},
            {"timestamp": ts(NOW - timedelta(days=5, hours=-2)), "entry_type": "contained",
             "actor": "system", "detail": "Shell tool disabled via kill switch", "receipt_id": "rcpt-demo-inc-004"},
            {"timestamp": ts(NOW - timedelta(days=5, hours=-4)), "entry_type": "resolved",
             "actor": "owner", "detail": "Confirmed: injected via malicious Slack message content. Input sanitization rule added.", "receipt_id": None},
        ],
        "remediation_receipts": ["rcpt-demo-inc-004", "rcpt-demo-inc-005"],
        "closed_at": ts(NOW - timedelta(days=5, hours=-4)),
        "closed_by": "owner",
        "root_cause": "Prompt injection via crafted Slack message. Added content sanitization filter to connector proxy.",
        "board_report_generated": True,
        "dedup_key": "security_shell_blocked",
    },
    {
        "incident_id": "inc-demo-003",
        "trigger_receipt_id": "rcpt-demo-inc-006",
        "category": "AVAILABILITY_INCIDENT",
        "severity": "LOW",
        "playbook_name": "connector_health_check",
        "status": "OPEN",
        "opened_at": ts(NOW - timedelta(hours=4)),
        "paged_at": None,
        "responder_id": None,
        "acknowledged_at": None,
        "timeline": [
            {"timestamp": ts(NOW - timedelta(hours=4)), "entry_type": "opened",
             "actor": "system", "detail": "CRM connector health check failed 3 consecutive times (timeout)", "receipt_id": "rcpt-demo-inc-006"},
            {"timestamp": ts(NOW - timedelta(hours=3)), "entry_type": "auto_retry",
             "actor": "system", "detail": "Automatic retry #1 — still failing", "receipt_id": None},
        ],
        "remediation_receipts": [],
        "closed_at": None,
        "closed_by": None,
        "root_cause": None,
        "board_report_generated": False,
        "dedup_key": "availability_crm_timeout",
    },
    {
        "incident_id": "inc-demo-004",
        "trigger_receipt_id": "rcpt-demo-inc-007",
        "category": "GOVERNANCE_BREACH",
        "severity": "CRITICAL",
        "playbook_name": "governance_breach_lockdown",
        "status": "INVESTIGATING",
        "opened_at": ts(NOW - timedelta(hours=1)),
        "paged_at": ts(NOW - timedelta(minutes=55)),
        "responder_id": "owner",
        "acknowledged_at": ts(NOW - timedelta(minutes=45)),
        "timeline": [
            {"timestamp": ts(NOW - timedelta(hours=1)), "entry_type": "opened",
             "actor": "system", "detail": "Tier 3 action executed without required owner approval: send_sms to unverified number +15559876543", "receipt_id": "rcpt-demo-inc-007"},
            {"timestamp": ts(NOW - timedelta(minutes=55)), "entry_type": "paged",
             "actor": "system", "detail": "CRITICAL: Governance bypass detected — owner paged", "receipt_id": None},
            {"timestamp": ts(NOW - timedelta(minutes=45)), "entry_type": "acknowledged",
             "actor": "owner", "detail": "Acknowledged. Reviewing execution token permissions.", "receipt_id": None},
        ],
        "remediation_receipts": [],
        "closed_at": None,
        "closed_by": None,
        "root_cause": None,
        "board_report_generated": False,
        "dedup_key": "governance_tier3_bypass",
    },
]


def seed_incidents():
    """Seed incidents/ directory with demo incident records."""
    inc_dir = os.path.join(DATA_DIR, "incidents")
    os.makedirs(inc_dir, exist_ok=True)

    # Clear previous demo incidents
    for inc in DEMO_INCIDENTS:
        path = os.path.join(inc_dir, f"{inc['incident_id']}.json")
        if os.path.exists(path):
            os.remove(path)

    # Write individual incident files
    for inc in DEMO_INCIDENTS:
        path = os.path.join(inc_dir, f"{inc['incident_id']}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(inc, f, indent=2)

    # Write index file
    index = {}
    for inc in DEMO_INCIDENTS:
        index[inc["incident_id"]] = {
            "category": inc["category"],
            "severity": inc["severity"],
            "status": inc["status"],
            "playbook_name": inc["playbook_name"],
            "opened_at": inc["opened_at"],
            "dedup_key": inc.get("dedup_key"),
        }
    index_path = os.path.join(inc_dir, "_index.json")
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2)

    open_count = sum(1 for i in DEMO_INCIDENTS if i["status"] in ("OPEN", "INVESTIGATING"))
    closed_count = sum(1 for i in DEMO_INCIDENTS if i["status"] == "CLOSED")
    print(f"  [incidents/]         {len(DEMO_INCIDENTS)} incidents ({open_count} open, {closed_count} closed)")


# ---------------------------------------------------------------------------
# A2A Registry
# ---------------------------------------------------------------------------

DEMO_A2A_AGENTS = [
    {
        "agent_id": "demo-agent-research-bot",
        "display_name": "ResearchBot Alpha",
        "agent_card_url": "https://research-bot.demo.test/.well-known/agent.json",
        "agent_framework": "google_a2a",
        "auth_type": "bearer_token",
        "credentials_ref": "vault:a2a/research-bot",
        "inbound_trust_tier": 2,
        "outbound_trust_tier": 1,
        "direction": "outbound",
        "network_allowlist_entries": json.dumps(["research-bot.demo.test"]),
        "kill_switch_id": "FEATURE_A2A",
        "last_verified": ts(NOW - timedelta(hours=2)),
        "status": "active",
        "auto_registered": 0,
        "agent_card_cache": json.dumps({"name": "ResearchBot", "version": "2.1", "capabilities": ["web_search", "summarize"]}),
        "interaction_count": 23,
        "success_count": 21,
        "last_interaction": ts(NOW - timedelta(hours=3)),
        "last_outcome": "completed",
        "registered_at": ts(NOW - timedelta(days=10)),
    },
    {
        "agent_id": "demo-agent-data-pipeline",
        "display_name": "DataPipeline Agent",
        "agent_card_url": "https://data-pipeline.demo.test/.well-known/agent.json",
        "agent_framework": "langchain",
        "auth_type": "api_key",
        "credentials_ref": "vault:a2a/data-pipeline",
        "inbound_trust_tier": 1,
        "outbound_trust_tier": 1,
        "direction": "both",
        "network_allowlist_entries": json.dumps(["data-pipeline.demo.test", "warehouse.demo.test"]),
        "kill_switch_id": "FEATURE_A2A",
        "last_verified": ts(NOW - timedelta(days=1)),
        "status": "active",
        "auto_registered": 0,
        "agent_card_cache": json.dumps({"name": "DataPipeline", "version": "1.5", "capabilities": ["etl", "transform", "query"]}),
        "interaction_count": 45,
        "success_count": 42,
        "last_interaction": ts(NOW - timedelta(hours=6)),
        "last_outcome": "completed",
        "registered_at": ts(NOW - timedelta(days=14)),
    },
    {
        "agent_id": "demo-agent-compliance-checker",
        "display_name": "ComplianceGuard",
        "agent_card_url": "https://compliance.demo.test/.well-known/agent.json",
        "agent_framework": "lancelot",
        "auth_type": "mutual_tls",
        "credentials_ref": "vault:a2a/compliance",
        "inbound_trust_tier": 0,
        "outbound_trust_tier": 0,
        "direction": "inbound",
        "network_allowlist_entries": json.dumps(["compliance.demo.test"]),
        "kill_switch_id": "FEATURE_A2A",
        "last_verified": ts(NOW - timedelta(hours=12)),
        "status": "active",
        "auto_registered": 1,
        "agent_card_cache": json.dumps({"name": "ComplianceGuard", "version": "3.0", "capabilities": ["audit", "verify", "report"]}),
        "interaction_count": 12,
        "success_count": 12,
        "last_interaction": ts(NOW - timedelta(days=2)),
        "last_outcome": "success",
        "registered_at": ts(NOW - timedelta(days=7)),
    },
    {
        "agent_id": "demo-agent-legacy-bot",
        "display_name": "LegacyBot (Revoked)",
        "agent_card_url": "https://legacy.demo.test/.well-known/agent.json",
        "agent_framework": "unknown",
        "auth_type": "none",
        "credentials_ref": "",
        "inbound_trust_tier": 3,
        "outbound_trust_tier": 3,
        "direction": "outbound",
        "network_allowlist_entries": json.dumps([]),
        "kill_switch_id": "",
        "last_verified": ts(NOW - timedelta(days=20)),
        "status": "revoked",
        "auto_registered": 1,
        "agent_card_cache": None,
        "interaction_count": 3,
        "success_count": 1,
        "last_interaction": ts(NOW - timedelta(days=18)),
        "last_outcome": "failed",
        "registered_at": ts(NOW - timedelta(days=21)),
    },
]


def seed_a2a_registry():
    """Seed a2a_registry.db with demo remote agents."""
    db_path = os.path.join(DATA_DIR, "a2a_registry.db")
    conn = sqlite3.connect(db_path)

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS a2a_agents (
            agent_id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            agent_card_url TEXT DEFAULT '',
            agent_framework TEXT DEFAULT 'unknown',
            auth_type TEXT DEFAULT 'none',
            credentials_ref TEXT DEFAULT '',
            inbound_trust_tier INTEGER DEFAULT 2,
            outbound_trust_tier INTEGER DEFAULT 2,
            direction TEXT DEFAULT 'outbound',
            network_allowlist_entries TEXT DEFAULT '[]',
            kill_switch_id TEXT DEFAULT '',
            last_verified TEXT DEFAULT '',
            status TEXT DEFAULT 'active',
            auto_registered INTEGER DEFAULT 0,
            agent_card_cache TEXT DEFAULT NULL,
            interaction_count INTEGER DEFAULT 0,
            success_count INTEGER DEFAULT 0,
            last_interaction TEXT DEFAULT '',
            last_outcome TEXT DEFAULT '',
            registered_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_a2a_agents_direction ON a2a_agents(direction);
        CREATE INDEX IF NOT EXISTS idx_a2a_agents_status ON a2a_agents(status);
        CREATE INDEX IF NOT EXISTS idx_a2a_agents_framework ON a2a_agents(agent_framework);
    """)

    for agent in DEMO_A2A_AGENTS:
        conn.execute("DELETE FROM a2a_agents WHERE agent_id = ?", (agent["agent_id"],))

    conn.executemany(
        """INSERT OR REPLACE INTO a2a_agents
           (agent_id, display_name, agent_card_url, agent_framework, auth_type,
            credentials_ref, inbound_trust_tier, outbound_trust_tier, direction,
            network_allowlist_entries, kill_switch_id, last_verified, status,
            auto_registered, agent_card_cache, interaction_count, success_count,
            last_interaction, last_outcome, registered_at)
           VALUES (:agent_id, :display_name, :agent_card_url, :agent_framework,
                   :auth_type, :credentials_ref, :inbound_trust_tier,
                   :outbound_trust_tier, :direction, :network_allowlist_entries,
                   :kill_switch_id, :last_verified, :status, :auto_registered,
                   :agent_card_cache, :interaction_count, :success_count,
                   :last_interaction, :last_outcome, :registered_at)""",
        DEMO_A2A_AGENTS,
    )
    conn.commit()
    conn.close()

    active = sum(1 for a in DEMO_A2A_AGENTS if a["status"] == "active")
    print(f"  [a2a_registry.db]    {len(DEMO_A2A_AGENTS)} agents ({active} active, 1 revoked)")


# ---------------------------------------------------------------------------
# Action Cards
# ---------------------------------------------------------------------------

def seed_action_cards():
    """Seed actioncards.db with demo action cards."""
    db_path = os.path.join(DATA_DIR, "actioncards.db")
    conn = sqlite3.connect(db_path)

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS action_cards (
            card_id TEXT PRIMARY KEY,
            card_type TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            source_system TEXT NOT NULL DEFAULT '',
            source_item_id TEXT NOT NULL DEFAULT '',
            buttons TEXT NOT NULL DEFAULT '[]',
            metadata TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL,
            expires_at REAL,
            quest_id TEXT,
            resolved INTEGER NOT NULL DEFAULT 0,
            resolved_action TEXT,
            resolved_at REAL,
            resolved_channel TEXT,
            telegram_message_id INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_ac_resolved ON action_cards(resolved);
        CREATE INDEX IF NOT EXISTS idx_ac_source ON action_cards(source_system);
        CREATE INDEX IF NOT EXISTS idx_ac_created ON action_cards(created_at);
        CREATE INDEX IF NOT EXISTS idx_ac_quest ON action_cards(quest_id);
    """)

    conn.execute("DELETE FROM action_cards WHERE card_id LIKE 'demo-%'")

    cards = [
        {
            "card_id": "demo-card-1",
            "card_type": "approval",
            "title": "Approve: Send SMS to +15551234567",
            "description": "Tier 2 action requires owner approval. Recipient is in verified contacts.",
            "source_system": "governance",
            "source_item_id": "rcpt-demo-sms-001",
            "buttons": json.dumps([
                {"id": "approve", "label": "Approve", "action": "approve_action"},
                {"id": "deny", "label": "Deny", "action": "deny_action"},
            ]),
            "metadata": json.dumps({"risk_tier": 2, "capability": "connector.sms.send_message"}),
            "created_at": (NOW - timedelta(hours=3)).timestamp(),
            "expires_at": (NOW + timedelta(hours=21)).timestamp(),
            "quest_id": "quest-demo-sms-001",
            "resolved": 1,
            "resolved_action": "approve",
            "resolved_at": (NOW - timedelta(hours=2, minutes=45)).timestamp(),
            "resolved_channel": "telegram",
            "telegram_message_id": 12345,
        },
        {
            "card_id": "demo-card-2",
            "card_type": "approval",
            "title": "Approve: Email to external@partner-demo.test",
            "description": "New external recipient — first-time send requires approval.",
            "source_system": "apl",
            "source_item_id": "rcpt-demo-email-ext-001",
            "buttons": json.dumps([
                {"id": "approve", "label": "Approve", "action": "approve_action"},
                {"id": "approve_remember", "label": "Approve + Remember", "action": "approve_and_learn"},
                {"id": "deny", "label": "Deny", "action": "deny_action"},
            ]),
            "metadata": json.dumps({"risk_tier": 2, "domain": "partner-demo.test"}),
            "created_at": (NOW - timedelta(minutes=30)).timestamp(),
            "expires_at": (NOW + timedelta(hours=23, minutes=30)).timestamp(),
            "quest_id": "quest-demo-email-ext-001",
            "resolved": 0,
            "resolved_action": None,
            "resolved_at": None,
            "resolved_channel": None,
            "telegram_message_id": 12350,
        },
        {
            "card_id": "demo-card-3",
            "card_type": "notification",
            "title": "Weekly Metrics Report Ready",
            "description": "Cost: $3.47 | Actions: 203 | Autonomous: 94.2% | 0 violations",
            "source_system": "scheduler",
            "source_item_id": "demo_weekly_metrics",
            "buttons": json.dumps([
                {"id": "view", "label": "View Report", "action": "view_report"},
                {"id": "email", "label": "Email to Me", "action": "email_report"},
            ]),
            "metadata": json.dumps({"report_type": "weekly_metrics"}),
            "created_at": (NOW - timedelta(days=2)).timestamp(),
            "expires_at": None,
            "quest_id": None,
            "resolved": 1,
            "resolved_action": "email",
            "resolved_at": (NOW - timedelta(days=2, hours=-1)).timestamp(),
            "resolved_channel": "war_room",
            "telegram_message_id": None,
        },
        {
            "card_id": "demo-card-4",
            "card_type": "incident",
            "title": "CRITICAL: Governance Bypass Detected",
            "description": "Tier 3 action executed without approval. Incident inc-demo-004 opened.",
            "source_system": "incidents",
            "source_item_id": "inc-demo-004",
            "buttons": json.dumps([
                {"id": "ack", "label": "Acknowledge", "action": "acknowledge_incident"},
                {"id": "view", "label": "View Incident", "action": "view_incident"},
            ]),
            "metadata": json.dumps({"severity": "CRITICAL", "incident_id": "inc-demo-004"}),
            "created_at": (NOW - timedelta(hours=1)).timestamp(),
            "expires_at": None,
            "quest_id": None,
            "resolved": 1,
            "resolved_action": "ack",
            "resolved_at": (NOW - timedelta(minutes=45)).timestamp(),
            "resolved_channel": "telegram",
            "telegram_message_id": 12355,
        },
        {
            "card_id": "demo-card-5",
            "card_type": "proposal",
            "title": "APL Rule Proposal: Auto-approve SMS to verified numbers",
            "description": "Based on 8 consecutive approvals. Proposed daily limit: 10.",
            "source_system": "apl",
            "source_item_id": "rule-demo-6",
            "buttons": json.dumps([
                {"id": "confirm", "label": "Confirm Rule", "action": "confirm_rule"},
                {"id": "modify", "label": "Modify", "action": "modify_rule"},
                {"id": "decline", "label": "Decline", "action": "decline_rule"},
            ]),
            "metadata": json.dumps({"rule_id": "rule-demo-6", "consecutive_approvals": 8}),
            "created_at": (NOW - timedelta(hours=6)).timestamp(),
            "expires_at": (NOW + timedelta(days=7)).timestamp(),
            "quest_id": None,
            "resolved": 0,
            "resolved_action": None,
            "resolved_at": None,
            "resolved_channel": None,
            "telegram_message_id": 12348,
        },
    ]

    for c in cards:
        conn.execute(
            """INSERT OR REPLACE INTO action_cards
               (card_id, card_type, title, description, source_system, source_item_id,
                buttons, metadata, created_at, expires_at, quest_id, resolved,
                resolved_action, resolved_at, resolved_channel, telegram_message_id)
               VALUES (:card_id, :card_type, :title, :description, :source_system,
                       :source_item_id, :buttons, :metadata, :created_at, :expires_at,
                       :quest_id, :resolved, :resolved_action, :resolved_at,
                       :resolved_channel, :telegram_message_id)""",
            c,
        )

    conn.commit()
    conn.close()

    pending = sum(1 for c in cards if not c["resolved"])
    print(f"  [actioncards.db]     {len(cards)} cards ({pending} pending, {len(cards) - pending} resolved)")


# ---------------------------------------------------------------------------
# BAL (Business Automation Layer)
# ---------------------------------------------------------------------------

def seed_bal():
    """Seed bal/bal.sqlite with demo clients, intake, content, deliveries."""
    bal_dir = os.path.join(DATA_DIR, "bal")
    os.makedirs(bal_dir, exist_ok=True)
    db_path = os.path.join(bal_dir, "bal.sqlite")
    conn = sqlite3.connect(db_path)

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS bal_schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        );
        INSERT OR IGNORE INTO bal_schema_version (version, applied_at) VALUES (2, datetime('now'));

        CREATE TABLE IF NOT EXISTS bal_clients (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL,
            tier TEXT NOT NULL DEFAULT 'starter', status TEXT NOT NULL DEFAULT 'onboarding',
            preferences_json TEXT NOT NULL DEFAULT '{}', billing_json TEXT NOT NULL DEFAULT '{}',
            metadata TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, memory_block_id TEXT
        );
        CREATE TABLE IF NOT EXISTS bal_intake (
            id TEXT PRIMARY KEY, client_id TEXT NOT NULL, source_type TEXT NOT NULL,
            content_type TEXT NOT NULL DEFAULT 'raw_text', title TEXT NOT NULL DEFAULT '',
            raw_content TEXT NOT NULL, analysis_json TEXT NOT NULL DEFAULT '{}',
            word_count INTEGER NOT NULL DEFAULT 0, language TEXT NOT NULL DEFAULT 'en',
            status TEXT NOT NULL DEFAULT 'received', metadata TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS bal_content (
            id TEXT PRIMARY KEY, intake_id TEXT NOT NULL, client_id TEXT NOT NULL,
            platform TEXT NOT NULL, content_body TEXT NOT NULL,
            verification_json TEXT NOT NULL DEFAULT '{}', quality_score REAL,
            status TEXT NOT NULL DEFAULT 'draft', metadata TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS bal_deliveries (
            id TEXT PRIMARY KEY, content_id TEXT NOT NULL, client_id TEXT NOT NULL,
            channel TEXT NOT NULL DEFAULT 'email', status TEXT NOT NULL DEFAULT 'pending',
            retry_count INTEGER NOT NULL DEFAULT 0, next_retry_at TEXT,
            delivered_at TEXT, error_message TEXT, metadata TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS bal_financial_receipts (
            id TEXT PRIMARY KEY, client_id TEXT NOT NULL,
            amount_cents INTEGER NOT NULL, currency TEXT NOT NULL DEFAULT 'usd',
            event_type TEXT NOT NULL, stripe_id TEXT, stripe_event_id TEXT,
            status TEXT NOT NULL DEFAULT 'pending', metadata TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
    """)

    # Clear demo data
    conn.execute("DELETE FROM bal_clients WHERE id LIKE 'demo-%'")
    conn.execute("DELETE FROM bal_intake WHERE id LIKE 'demo-%'")
    conn.execute("DELETE FROM bal_content WHERE id LIKE 'demo-%'")
    conn.execute("DELETE FROM bal_deliveries WHERE id LIKE 'demo-%'")
    conn.execute("DELETE FROM bal_financial_receipts WHERE id LIKE 'demo-%'")

    # Clients
    clients = [
        ("demo-client-1", "Acme Corp", "hello@acme-demo.test", "professional", "active",
         json.dumps({"tone": "formal", "platforms": ["linkedin", "email"]}),
         json.dumps({"plan": "professional", "monthly_cents": 9900}),
         ts(NOW - timedelta(days=30)), ts(NOW - timedelta(days=1))),
        ("demo-client-2", "StartupXYZ", "founders@startupxyz-demo.test", "starter", "active",
         json.dumps({"tone": "casual", "platforms": ["twitter", "linkedin"]}),
         json.dumps({"plan": "starter", "monthly_cents": 2900}),
         ts(NOW - timedelta(days=14)), ts(NOW - timedelta(hours=6))),
        ("demo-client-3", "BigEnterpriseInc", "marketing@bigent-demo.test", "enterprise", "onboarding",
         json.dumps({"tone": "corporate", "platforms": ["linkedin", "email", "blog"]}),
         json.dumps({"plan": "enterprise", "monthly_cents": 29900}),
         ts(NOW - timedelta(days=2)), ts(NOW - timedelta(days=2))),
    ]
    conn.executemany(
        """INSERT INTO bal_clients (id, name, email, tier, status, preferences_json,
           billing_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)""",
        clients,
    )

    # Intake
    intakes = [
        ("demo-intake-1", "demo-client-1", "email", "article_draft", "Q1 Industry Trends Analysis",
         "The enterprise SaaS market is experiencing unprecedented growth in AI-driven automation...",
         json.dumps({"sentiment": "positive", "topics": ["AI", "SaaS", "automation"]}),
         340, "en", "processed", ts(NOW - timedelta(days=5)), ts(NOW - timedelta(days=4))),
        ("demo-intake-2", "demo-client-2", "chat", "social_post", "Product Launch Announcement",
         "We're thrilled to announce our new AI-powered analytics dashboard...",
         json.dumps({"sentiment": "enthusiastic", "topics": ["product_launch", "AI"]}),
         45, "en", "analyzed", ts(NOW - timedelta(days=1)), ts(NOW - timedelta(hours=12))),
        ("demo-intake-3", "demo-client-1", "api", "newsletter", "March Newsletter",
         "Dear valued partners, here's what's new this month in enterprise automation...",
         json.dumps({"sentiment": "professional", "topics": ["newsletter", "updates"]}),
         520, "en", "received", ts(NOW - timedelta(hours=3)), ts(NOW - timedelta(hours=3))),
    ]
    conn.executemany(
        """INSERT INTO bal_intake (id, client_id, source_type, content_type, title,
           raw_content, analysis_json, word_count, language, status, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        intakes,
    )

    # Content
    contents = [
        ("demo-content-1", "demo-intake-1", "demo-client-1", "linkedin",
         "🚀 The enterprise SaaS landscape is shifting. AI-driven automation isn't just a trend — it's becoming the foundation of competitive advantage. Here are 5 key takeaways from our Q1 analysis...",
         json.dumps({"grammar_score": 0.95, "tone_match": 0.88, "plagiarism_clear": True}),
         0.91, "published", ts(NOW - timedelta(days=4)), ts(NOW - timedelta(days=3))),
        ("demo-content-2", "demo-intake-2", "demo-client-2", "twitter",
         "Big news! 🎉 Our AI-powered analytics dashboard is live. Real-time insights, zero setup. Try it free → startupxyz.demo.test/analytics",
         json.dumps({"grammar_score": 0.92, "tone_match": 0.95, "plagiarism_clear": True}),
         0.94, "draft", ts(NOW - timedelta(hours=10)), ts(NOW - timedelta(hours=8))),
    ]
    conn.executemany(
        """INSERT INTO bal_content (id, intake_id, client_id, platform, content_body,
           verification_json, quality_score, status, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        contents,
    )

    # Deliveries
    deliveries = [
        ("demo-delivery-1", "demo-content-1", "demo-client-1", "email", "sent",
         0, None, ts(NOW - timedelta(days=3)), None, ts(NOW - timedelta(days=3, hours=-1)), ts(NOW - timedelta(days=3))),
        ("demo-delivery-2", "demo-content-2", "demo-client-2", "slack", "pending",
         0, None, None, None, ts(NOW - timedelta(hours=8)), ts(NOW - timedelta(hours=8))),
    ]
    conn.executemany(
        """INSERT INTO bal_deliveries (id, content_id, client_id, channel, status,
           retry_count, next_retry_at, delivered_at, error_message, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        deliveries,
    )

    # Financial receipts
    financials = [
        ("demo-fin-1", "demo-client-1", 9900, "usd", "subscription_created",
         "sub_demo_001", None, "processed", ts(NOW - timedelta(days=30))),
        ("demo-fin-2", "demo-client-1", 9900, "usd", "payment_received",
         "pi_demo_001", "evt_demo_001", "processed", ts(NOW - timedelta(days=1))),
        ("demo-fin-3", "demo-client-2", 2900, "usd", "subscription_created",
         "sub_demo_002", None, "processed", ts(NOW - timedelta(days=14))),
    ]
    conn.executemany(
        """INSERT INTO bal_financial_receipts (id, client_id, amount_cents, currency,
           event_type, stripe_id, stripe_event_id, status, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        financials,
    )

    conn.commit()
    conn.close()
    print(f"  [bal/bal.sqlite]     3 clients, 3 intakes, 2 content, 2 deliveries, 3 financial")


# ---------------------------------------------------------------------------
# Federation Topology
# ---------------------------------------------------------------------------

def seed_federation():
    """Seed federation topology with a 3-node demo cluster."""
    fed_dir = os.path.join(DATA_DIR, "federation")
    os.makedirs(fed_dir, exist_ok=True)
    versions_dir = os.path.join(fed_dir, "topology_versions")
    os.makedirs(versions_dir, exist_ok=True)

    topology = {
        "version": 2,
        "version_hash": uuid.uuid4().hex[:16],
        "topology_name": "Demo Federation Cluster",
        "created_at": ts(NOW - timedelta(days=7)),
        "updated_at": ts(NOW - timedelta(hours=6)),
        "deployed_at": ts(NOW - timedelta(hours=5)),
        "nodes": [
            {
                "node_id": "LOCAL_INSTANCE",
                "instance_name": "lancelot-primary",
                "endpoint": "http://localhost:9900",
                "federation_identity_public_key": "ed25519:" + uuid.uuid4().hex[:32],
                "fingerprint": uuid.uuid4().hex[:16],
                "instance_role": "root",
                "soul_source_mode": "custom",
                "soul_version": "v1.2.0",
                "soul_version_hash": uuid.uuid4().hex[:12],
                "connection_status": "green",
                "hive_config": {
                    "enabled": True,
                    "max_concurrent_agents": 10,
                    "default_task_timeout": 300,
                    "max_actions_per_agent": 50,
                    "uab_enabled": True,
                },
                "budget_config": {
                    "daily_ceiling_usd": 10.0,
                    "warning_pct": 80,
                    "critical_pct": 95,
                },
                "position": {"x": 400, "y": 200},
                "timezone": "America/New_York",
                "is_local": True,
                "metadata": {},
            },
            {
                "node_id": "ed25519:" + uuid.uuid4().hex[:32],
                "instance_name": "lancelot-eu-replica",
                "endpoint": "https://eu.lancelot-demo.test:9900",
                "federation_identity_public_key": "ed25519:" + uuid.uuid4().hex[:32],
                "fingerprint": uuid.uuid4().hex[:16],
                "instance_role": "child",
                "soul_source_mode": "inherited",
                "soul_version": "v1.2.0",
                "soul_version_hash": uuid.uuid4().hex[:12],
                "connection_status": "green",
                "hive_config": {
                    "enabled": True,
                    "max_concurrent_agents": 5,
                    "default_task_timeout": 300,
                    "max_actions_per_agent": 30,
                    "uab_enabled": False,
                },
                "budget_config": {
                    "daily_ceiling_usd": 5.0,
                    "warning_pct": 80,
                    "critical_pct": 95,
                },
                "position": {"x": 700, "y": 100},
                "timezone": "Europe/Berlin",
                "is_local": False,
                "metadata": {"region": "eu-west-1"},
            },
            {
                "node_id": "ed25519:" + uuid.uuid4().hex[:32],
                "instance_name": "lancelot-research",
                "endpoint": "https://research.lancelot-demo.test:9900",
                "federation_identity_public_key": "ed25519:" + uuid.uuid4().hex[:32],
                "fingerprint": uuid.uuid4().hex[:16],
                "instance_role": "peer",
                "soul_source_mode": "linked",
                "soul_version": "v1.1.0",
                "soul_version_hash": uuid.uuid4().hex[:12],
                "connection_status": "grey",
                "hive_config": {
                    "enabled": True,
                    "max_concurrent_agents": 20,
                    "default_task_timeout": 600,
                    "max_actions_per_agent": 100,
                    "uab_enabled": False,
                },
                "budget_config": {
                    "daily_ceiling_usd": 25.0,
                    "warning_pct": 70,
                    "critical_pct": 90,
                },
                "position": {"x": 700, "y": 350},
                "timezone": "UTC",
                "is_local": False,
                "metadata": {"region": "us-east-1", "purpose": "heavy research workloads"},
            },
        ],
        "edges": [
            {
                "source_node_id": "LOCAL_INSTANCE",
                "target_node_id": None,  # Will be set below
                "relationship_type": "hierarchical_parent_child",
                "trigger_condition": "always",
                "state": "green",
                "yellow_acknowledgments": [],
                "dimensions_evaluated": [],
                "assumptions": [],
                "resolution_records": [],
                "metadata": {"description": "Primary → EU replica for GDPR-compliant data processing"},
            },
            {
                "source_node_id": "LOCAL_INSTANCE",
                "target_node_id": None,
                "relationship_type": "federated_handoff",
                "trigger_condition": "conditional",
                "state": "yellow",
                "yellow_acknowledgments": [
                    {"operator": "owner", "timestamp": ts(NOW - timedelta(hours=6)),
                     "condition": "Research node offline for maintenance", "note": "Expected back online by EOD"},
                ],
                "dimensions_evaluated": [
                    {"dimension": "connectivity", "state": "yellow",
                     "report": "Last heartbeat 6h ago", "resolution_options": ["wait", "reconnect"]},
                ],
                "assumptions": [],
                "resolution_records": [],
                "metadata": {"description": "Primary ↔ Research for task handoff on heavy workloads"},
            },
        ],
    }

    # Wire edge target_node_ids to actual node IDs
    topology["edges"][0]["target_node_id"] = topology["nodes"][1]["node_id"]
    topology["edges"][1]["target_node_id"] = topology["nodes"][2]["node_id"]

    # Write active topology
    with open(os.path.join(fed_dir, "active_topology.json"), "w", encoding="utf-8") as f:
        json.dump(topology, f, indent=2)

    # Write as deployed topology too
    with open(os.path.join(fed_dir, "deployed_topology.json"), "w", encoding="utf-8") as f:
        json.dump(topology, f, indent=2)

    # Write version history
    v1 = dict(topology, version=1, version_hash=uuid.uuid4().hex[:16],
              created_at=ts(NOW - timedelta(days=7)),
              updated_at=ts(NOW - timedelta(days=7)),
              deployed_at=ts(NOW - timedelta(days=7)))
    with open(os.path.join(versions_dir, "v1.json"), "w", encoding="utf-8") as f:
        json.dump(v1, f, indent=2)
    with open(os.path.join(versions_dir, "v2.json"), "w", encoding="utf-8") as f:
        json.dump(topology, f, indent=2)

    print(f"  [federation/]        3 nodes, 2 edges, 2 topology versions")


# ---------------------------------------------------------------------------
# Clear
# ---------------------------------------------------------------------------

def clear_demo_data():
    """Remove all demo-seeded data."""
    print("\n  Clearing demo data...")

    # Receipts — delete only demo rows
    db_path = os.path.join(DATA_DIR, "receipts.db")
    if os.path.exists(db_path):
        conn = sqlite3.connect(db_path)
        cur = conn.execute(
            "DELETE FROM receipts WHERE metadata LIKE ?",
            (f"%{DEMO_MARKER}%",),
        )
        conn.commit()
        print(f"  [receipts.db]        Deleted {cur.rowcount} demo receipts")
        conn.close()

    # Scheduler — delete demo jobs only
    sched_path = os.path.join(DATA_DIR, "scheduler.sqlite")
    if os.path.exists(sched_path):
        conn = sqlite3.connect(sched_path)
        demo_ids = [j["id"] for j in DEMO_JOBS]
        placeholders = ",".join("?" for _ in demo_ids)
        cur = conn.execute(
            f"DELETE FROM jobs WHERE id IN ({placeholders})", demo_ids
        )
        conn.commit()
        print(f"  [scheduler.sqlite]   Deleted {cur.rowcount} demo jobs")
        conn.close()

    # Tasks — delete demo rows
    tasks_path = os.path.join(DATA_DIR, "tasks.db")
    if os.path.exists(tasks_path):
        conn = sqlite3.connect(tasks_path)
        try:
            c1 = conn.execute("DELETE FROM task_graphs WHERE id LIKE 'demo-%'")
            c2 = conn.execute("DELETE FROM task_runs WHERE id LIKE 'demo-%'")
            conn.commit()
            print(f"  [tasks.db]           Deleted {c1.rowcount} graphs + {c2.rowcount} runs")
        except Exception:
            pass
        conn.close()

    # Tokens — delete demo rows
    tokens_path = os.path.join(DATA_DIR, "tokens.db")
    if os.path.exists(tokens_path):
        conn = sqlite3.connect(tokens_path)
        try:
            cur = conn.execute("DELETE FROM execution_tokens WHERE id LIKE 'demo-%'")
            conn.commit()
            print(f"  [tokens.db]          Deleted {cur.rowcount} demo tokens")
        except Exception:
            pass
        conn.close()

    # A2A — delete demo agents
    a2a_path = os.path.join(DATA_DIR, "a2a_registry.db")
    if os.path.exists(a2a_path):
        conn = sqlite3.connect(a2a_path)
        try:
            cur = conn.execute("DELETE FROM a2a_agents WHERE agent_id LIKE 'demo-%'")
            conn.commit()
            print(f"  [a2a_registry.db]    Deleted {cur.rowcount} demo agents")
        except Exception:
            pass
        conn.close()

    # Action cards — delete demo cards
    ac_path = os.path.join(DATA_DIR, "actioncards.db")
    if os.path.exists(ac_path):
        conn = sqlite3.connect(ac_path)
        try:
            cur = conn.execute("DELETE FROM action_cards WHERE card_id LIKE 'demo-%'")
            conn.commit()
            print(f"  [actioncards.db]     Deleted {cur.rowcount} demo cards")
        except Exception:
            pass
        conn.close()

    # BAL — delete demo rows
    bal_path = os.path.join(DATA_DIR, "bal", "bal.sqlite")
    if os.path.exists(bal_path):
        conn = sqlite3.connect(bal_path)
        try:
            for tbl in ["bal_financial_receipts", "bal_deliveries", "bal_content", "bal_intake", "bal_clients"]:
                conn.execute(f"DELETE FROM {tbl} WHERE id LIKE 'demo-%'")
            conn.commit()
            print(f"  [bal/bal.sqlite]     Deleted demo BAL data")
        except Exception:
            pass
        conn.close()

    # Incidents — delete demo incidents
    inc_dir = os.path.join(DATA_DIR, "incidents")
    if os.path.exists(inc_dir):
        for inc in DEMO_INCIDENTS:
            path = os.path.join(inc_dir, f"{inc['incident_id']}.json")
            if os.path.exists(path):
                os.remove(path)
        # Rebuild index without demo incidents
        index_path = os.path.join(inc_dir, "_index.json")
        if os.path.exists(index_path):
            try:
                with open(index_path, "r", encoding="utf-8") as f:
                    idx = json.load(f)
                for inc in DEMO_INCIDENTS:
                    idx.pop(inc["incident_id"], None)
                with open(index_path, "w", encoding="utf-8") as f:
                    json.dump(idx, f, indent=2)
            except Exception:
                pass
        print(f"  [incidents/]         Deleted {len(DEMO_INCIDENTS)} demo incidents")

    # JSON files — remove entirely (they're demo-only)
    for rel_path in [
        "usage_history.json",
        "chat/chat_log.json",
        "apl/decisions.jsonl",
        "apl/rules.json",
        "memory/core_blocks.json",
        "federation/active_topology.json",
        "federation/deployed_topology.json",
        "federation/topology_versions/v1.json",
        "federation/topology_versions/v2.json",
    ]:
        full = os.path.join(DATA_DIR, rel_path)
        if os.path.exists(full):
            os.remove(full)
            print(f"  [{rel_path}] Removed")

    print("  Done.\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if "--clear" in sys.argv:
        clear_demo_data()
        if "--clear" in sys.argv and len(sys.argv) == 2:
            return  # --clear only, don't re-seed

    print(f"\n  Seeding demo data into {DATA_DIR}...\n")
    os.makedirs(DATA_DIR, exist_ok=True)

    seed_receipts()
    seed_scheduler()
    seed_usage_history()
    seed_chat_log()
    seed_apl_decisions()
    seed_apl_rules()
    seed_core_memory()
    seed_tasks()
    seed_tokens()
    seed_incidents()
    seed_a2a_registry()
    seed_action_cards()
    seed_bal()
    seed_federation()

    print(f"\n  All demo data seeded successfully.")
    print(f"  Restart the container or hit /health/ready to verify.")
    print()
    print(f"  NOTE: The following require manual interaction to populate:")
    print(f"    - Memory embeddings (working/episodic/archival) — send chat messages")
    print(f"    - Compliance exports — POST /api/compliance/export")
    print(f"    - Incident reports — POST /api/incidents/<id>/report")
    print()


if __name__ == "__main__":
    main()

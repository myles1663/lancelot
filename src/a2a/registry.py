# Lancelot — A Governed Autonomous System
# Copyright (c) 2026 Myles Russell Hamilton
# Licensed under BUSL-1.1. See LICENSE for details.

"""
A2A Remote Agent Registry — manages external agent records.

SQLite-backed registry for remote A2A agent identity, trust tiers,
and connection details. Auto-generates per-agent kill switches.

Public API:
    A2ARegistry(data_dir) — constructor
    register(agent) → RemoteAgent
    get(agent_id) → RemoteAgent | None
    list_agents(direction, status) → List[RemoteAgent]
    update(agent) → RemoteAgent
    revoke(agent_id) → None
    auto_register(agent_id, display_name, framework, card_url, default_tier) → RemoteAgent
    update_trust(agent_id, direction, outcome) → None
    update_interaction(agent_id, outcome) → None
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.a2a.types import RemoteAgent, RemoteAgentStatus, AgentDirection

logger = logging.getLogger(__name__)


class A2ARegistry:
    """SQLite-backed registry for remote A2A agents."""

    CREATE_TABLE_SQL = """
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
    """

    def __init__(self, data_dir: str = "/home/lancelot/data"):
        self.data_dir = data_dir
        self.db_path = os.path.join(data_dir, "a2a_registry.db")
        self._local = threading.local()
        os.makedirs(data_dir, exist_ok=True)
        self._init_database()

    def _get_connection(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(
                self.db_path, check_same_thread=False, timeout=30.0,
            )
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA synchronous=NORMAL")
        return self._local.conn

    @contextmanager
    def _transaction(self):
        conn = self._get_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def _init_database(self):
        with self._transaction() as conn:
            conn.executescript(self.CREATE_TABLE_SQL)

    def register(self, agent: RemoteAgent) -> RemoteAgent:
        """Register a new remote agent."""
        with self._transaction() as conn:
            conn.execute("""
                INSERT INTO a2a_agents (
                    agent_id, display_name, agent_card_url, agent_framework,
                    auth_type, credentials_ref, inbound_trust_tier, outbound_trust_tier,
                    direction, network_allowlist_entries, kill_switch_id, last_verified,
                    status, auto_registered, agent_card_cache,
                    interaction_count, success_count, last_interaction, last_outcome,
                    registered_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, self._agent_to_row(agent))
        logger.info("A2A agent registered: %s (%s)", agent.agent_id, agent.direction)
        return agent

    def get(self, agent_id: str) -> Optional[RemoteAgent]:
        """Get a remote agent by ID."""
        conn = self._get_connection()
        cursor = conn.execute(
            "SELECT * FROM a2a_agents WHERE agent_id = ?", (agent_id,),
        )
        row = cursor.fetchone()
        return self._row_to_agent(row) if row else None

    def list_agents(
        self,
        direction: Optional[str] = None,
        status: Optional[str] = None,
        framework: Optional[str] = None,
    ) -> List[RemoteAgent]:
        """List remote agents with optional filters."""
        query = "SELECT * FROM a2a_agents WHERE 1=1"
        params: List[Any] = []
        if direction:
            query += " AND (direction = ? OR direction = 'both')"
            params.append(direction)
        if status:
            query += " AND status = ?"
            params.append(status)
        if framework:
            query += " AND agent_framework = ?"
            params.append(framework)
        query += " ORDER BY registered_at DESC"

        conn = self._get_connection()
        cursor = conn.execute(query, params)
        return [self._row_to_agent(row) for row in cursor.fetchall()]

    def update(self, agent: RemoteAgent) -> RemoteAgent:
        """Update an existing remote agent."""
        with self._transaction() as conn:
            conn.execute("""
                UPDATE a2a_agents SET
                    display_name = ?, agent_card_url = ?, agent_framework = ?,
                    auth_type = ?, credentials_ref = ?,
                    inbound_trust_tier = ?, outbound_trust_tier = ?,
                    direction = ?, network_allowlist_entries = ?,
                    kill_switch_id = ?, last_verified = ?,
                    status = ?, auto_registered = ?, agent_card_cache = ?,
                    interaction_count = ?, success_count = ?,
                    last_interaction = ?, last_outcome = ?
                WHERE agent_id = ?
            """, (
                agent.display_name, agent.agent_card_url, agent.agent_framework,
                agent.auth_type, agent.credentials_ref,
                agent.inbound_trust_tier, agent.outbound_trust_tier,
                agent.direction, json.dumps(agent.network_allowlist_entries),
                agent.kill_switch_id, agent.last_verified,
                agent.status, 1 if agent.auto_registered else 0,
                json.dumps(agent.agent_card_cache) if agent.agent_card_cache else None,
                agent.interaction_count, agent.success_count,
                agent.last_interaction, agent.last_outcome,
                agent.agent_id,
            ))
        return agent

    def revoke(self, agent_id: str) -> None:
        """Revoke (soft-delete) a remote agent."""
        with self._transaction() as conn:
            conn.execute(
                "UPDATE a2a_agents SET status = ? WHERE agent_id = ?",
                (RemoteAgentStatus.REVOKED.value, agent_id),
            )
        logger.info("A2A agent revoked: %s", agent_id)

    def auto_register(
        self,
        agent_id: str,
        display_name: str,
        framework: str = "unknown",
        card_url: str = "",
        default_tier: int = 2,
    ) -> RemoteAgent:
        """Auto-register an inbound caller on first contact."""
        existing = self.get(agent_id)
        if existing:
            return existing

        agent = RemoteAgent(
            agent_id=agent_id,
            display_name=display_name,
            agent_card_url=card_url,
            agent_framework=framework,
            inbound_trust_tier=default_tier,
            outbound_trust_tier=default_tier,
            direction=AgentDirection.INBOUND.value,
            auto_registered=True,
        )
        if card_url:
            from urllib.parse import urlparse
            parsed = urlparse(card_url)
            if parsed.hostname:
                agent.network_allowlist_entries = [parsed.hostname]

        return self.register(agent)

    def update_interaction(
        self,
        agent_id: str,
        outcome: str,
        direction: str = "inbound",
    ) -> None:
        """Record an interaction outcome and update trust tier."""
        agent = self.get(agent_id)
        if not agent:
            return

        agent.interaction_count += 1
        agent.last_interaction = datetime.now(timezone.utc).isoformat()
        agent.last_outcome = outcome

        if outcome in ("completed", "success"):
            agent.success_count += 1
            # Graduate trust tier after sustained success
            if direction == "inbound" and agent.inbound_trust_tier > 0:
                if agent.success_count >= 10 * (3 - agent.inbound_trust_tier + 1):
                    agent.inbound_trust_tier = max(0, agent.inbound_trust_tier - 1)
            elif direction == "outbound" and agent.outbound_trust_tier > 0:
                if agent.success_count >= 10 * (3 - agent.outbound_trust_tier + 1):
                    agent.outbound_trust_tier = max(0, agent.outbound_trust_tier - 1)
        elif outcome in ("failed", "failure"):
            # Single failure resets to T3
            if direction == "inbound":
                agent.inbound_trust_tier = 3
            else:
                agent.outbound_trust_tier = 3

        self.update(agent)

    def _agent_to_row(self, agent: RemoteAgent) -> tuple:
        return (
            agent.agent_id, agent.display_name, agent.agent_card_url,
            agent.agent_framework, agent.auth_type, agent.credentials_ref,
            agent.inbound_trust_tier, agent.outbound_trust_tier,
            agent.direction, json.dumps(agent.network_allowlist_entries),
            agent.kill_switch_id, agent.last_verified,
            agent.status, 1 if agent.auto_registered else 0,
            json.dumps(agent.agent_card_cache) if agent.agent_card_cache else None,
            agent.interaction_count, agent.success_count,
            agent.last_interaction, agent.last_outcome,
            agent.registered_at,
        )

    def _row_to_agent(self, row: sqlite3.Row) -> RemoteAgent:
        return RemoteAgent(
            agent_id=row["agent_id"],
            display_name=row["display_name"],
            agent_card_url=row["agent_card_url"],
            agent_framework=row["agent_framework"],
            auth_type=row["auth_type"],
            credentials_ref=row["credentials_ref"],
            inbound_trust_tier=row["inbound_trust_tier"],
            outbound_trust_tier=row["outbound_trust_tier"],
            direction=row["direction"],
            network_allowlist_entries=json.loads(row["network_allowlist_entries"] or "[]"),
            kill_switch_id=row["kill_switch_id"],
            last_verified=row["last_verified"],
            status=row["status"],
            auto_registered=bool(row["auto_registered"]),
            agent_card_cache=json.loads(row["agent_card_cache"]) if row["agent_card_cache"] else None,
            interaction_count=row["interaction_count"],
            success_count=row["success_count"],
            last_interaction=row["last_interaction"],
            last_outcome=row["last_outcome"],
            registered_at=row["registered_at"],
        )

    def close(self):
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None

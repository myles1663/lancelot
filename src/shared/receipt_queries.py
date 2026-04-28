"""Query helpers for the immutable receipt log."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


class ReceiptQueryMixin:
    """Read-only finalized receipt query APIs shared by audit and metrics paths."""

    @staticmethod
    def _filtered_receipt_query(
        select: str,
        *,
        action_type: Optional[str] = None,
        status: Optional[str] = None,
        quest_id: Optional[str] = None,
        operator_id: Optional[str] = None,
        risk_tier: Optional[int] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
    ) -> tuple[str, List[Any]]:
        query = f"SELECT {select} FROM receipts WHERE 1=1"
        params: List[Any] = []
        for clause, value in (
            ("action_type = ?", action_type),
            ("status = ?", status),
            ("quest_id = ?", quest_id),
            ("operator_id = ?", operator_id),
            ("timestamp >= ?", since),
            ("timestamp <= ?", until),
        ):
            if value:
                query += f" AND {clause}"
                params.append(value)
        if risk_tier is not None:
            query += " AND tier = ?"
            params.append(risk_tier)
        return query, params

    def list(
        self,
        limit: int = 100,
        offset: int = 0,
        action_type: Optional[str] = None,
        status: Optional[str] = None,
        quest_id: Optional[str] = None,
        operator_id: Optional[str] = None,
        risk_tier: Optional[int] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
    ) -> List[Any]:
        """List finalized receipts with optional filters, newest first."""
        query, params = self._filtered_receipt_query(
            "*",
            action_type=action_type,
            status=status,
            quest_id=quest_id,
            operator_id=operator_id,
            risk_tier=risk_tier,
            since=since,
            until=until,
        )
        query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        cursor = self._get_connection().execute(query, params)
        return [self._row_to_receipt(row) for row in cursor.fetchall()]

    def count(
        self,
        *,
        action_type: Optional[str] = None,
        status: Optional[str] = None,
        quest_id: Optional[str] = None,
        operator_id: Optional[str] = None,
        risk_tier: Optional[int] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
    ) -> int:
        """Count finalized receipts with the same filters used by list()."""
        query, params = self._filtered_receipt_query(
            "COUNT(*) as total",
            action_type=action_type,
            status=status,
            quest_id=quest_id,
            operator_id=operator_id,
            risk_tier=risk_tier,
            since=since,
            until=until,
        )
        row = self._get_connection().execute(query, params).fetchone()
        return int(row["total"] if row else 0)

    def list_chronological(
        self,
        *,
        limit: Optional[int] = None,
        offset: int = 0,
        action_type: Optional[str] = None,
        status: Optional[str] = None,
        quest_id: Optional[str] = None,
        operator_id: Optional[str] = None,
        risk_tier: Optional[int] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
    ) -> List[Any]:
        """List finalized receipts oldest first for audit/export workflows."""
        query, params = self._filtered_receipt_query(
            "*",
            action_type=action_type,
            status=status,
            quest_id=quest_id,
            operator_id=operator_id,
            risk_tier=risk_tier,
            since=since,
            until=until,
        )
        query += " ORDER BY timestamp ASC"
        if limit is not None:
            query += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        elif offset:
            query += " LIMIT -1 OFFSET ?"
            params.append(offset)

        cursor = self._get_connection().execute(query, params)
        return [self._row_to_receipt(row) for row in cursor.fetchall()]

    def aggregate_counts(
        self,
        *,
        group_by: str,
        since: Optional[str] = None,
        until: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Aggregate finalized receipt counts by a supported receipt column."""
        allowed_columns = {"tier", "action_type", "operator_id", "quest_id"}
        if group_by not in allowed_columns:
            raise ValueError(f"Unsupported receipt aggregation column: {group_by}")

        query, params = self._filtered_receipt_query(
            f"{group_by} as group_key, COUNT(*) as count",
            since=since,
            until=until,
        )
        query += f" GROUP BY {group_by} ORDER BY count DESC"
        rows = self._get_connection().execute(query, params).fetchall()
        return [{"key": row["group_key"], "count": row["count"]} for row in rows]

    def list_action_outputs(
        self,
        *,
        action_type: str,
        since: Optional[str] = None,
        until: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return parsed output payloads for finalized receipts of one action type."""
        query, params = self._filtered_receipt_query(
            "outputs",
            action_type=action_type,
            since=since,
            until=until,
        )
        rows = self._get_connection().execute(query, params).fetchall()
        outputs: List[Dict[str, Any]] = []
        for row in rows:
            payload = row["outputs"]
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    continue
            if isinstance(payload, dict):
                outputs.append(payload)
        return outputs

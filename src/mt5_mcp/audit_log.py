"""Audit log for order/position execution attempts. See docs/aidlc/INCEPTION.md
"Safety layer": every attempt (dry-run or real) is logged, per SPEC.md sec 9.

Same SQLite pattern as stream_log.py (fresh connection per call, a write
lock, one table) — a separate store/table rather than reusing
StreamLogStore, since this is a different kind of record (an execution
attempt, not a market data point) with its own query shape.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    action TEXT NOT NULL,
    symbol TEXT,
    dry_run INTEGER NOT NULL,
    request TEXT NOT NULL,
    success INTEGER NOT NULL,
    result TEXT,
    error_code TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp ON audit_log (timestamp);
"""


class AuditLogStore:
    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._write_lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def record(
        self,
        timestamp: str,
        action: str,
        symbol: str | None,
        dry_run: bool,
        request: dict[str, Any],
        success: bool,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> None:
        with self._write_lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO audit_log "
                "(timestamp, action, symbol, dry_run, request, success, result, error_code) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    timestamp,
                    action,
                    symbol,
                    1 if dry_run else 0,
                    json.dumps(request),
                    1 if success else 0,
                    json.dumps(result) if result is not None else None,
                    error_code,
                ),
            )

    def query(
        self,
        *,
        action: str | None = None,
        symbol: str | None = None,
        from_time: str | None = None,
        to_time: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []

        if action is not None:
            clauses.append("action = ?")
            params.append(action)
        if symbol is not None:
            clauses.append("symbol = ?")
            params.append(symbol)
        if from_time is not None:
            clauses.append("timestamp >= ?")
            params.append(from_time)
        if to_time is not None:
            clauses.append("timestamp <= ?")
            params.append(to_time)

        sql = "SELECT timestamp, action, symbol, dry_run, request, success, result, error_code FROM audit_log"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id ASC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        return [
            {
                "timestamp": r[0],
                "action": r[1],
                "symbol": r[2],
                "dry_run": bool(r[3]),
                "request": json.loads(r[4]),
                "success": bool(r[5]),
                "result": json.loads(r[6]) if r[6] is not None else None,
                "error_code": r[7],
            }
            for r in rows
        ]

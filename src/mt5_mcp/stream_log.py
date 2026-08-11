"""Durable stream log store. See docs/aidlc/SPEC.md sec 7.

One SQLite file, one table, indexed on (symbol, timestamp). "Keyed by
symbol + date" (SPEC.md's phrasing) is met via that index and time-range
WHERE clauses, not physical per-date tables/files — simpler to operate
for a single-operator local tool, revisit only if this ever needs
sharding/archival at a scale where one table stops being enough.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS stream_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subscription_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    data_type TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    payload TEXT NOT NULL,
    sequence_number INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_stream_log_symbol_time
    ON stream_log (symbol, timestamp);
"""


class StreamLogStore:
    """Thread-safe append/query for the durable stream log.

    One StreamLogStore is shared between the background polling threads
    (writers, one per active subscription) and tool-call handlers
    (readers) — sqlite3 connections aren't safe to share across threads,
    so this opens its own connection per call rather than holding one
    open, guarded by a lock for the write path (SQLite itself serializes
    writers; the lock just avoids "database is locked" retries under
    light concurrency instead of building out a retry/backoff scheme for
    a single-operator local tool).
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._write_lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def append(
        self,
        subscription_id: str,
        symbol: str,
        data_type: str,
        timestamp: str,
        payload: dict[str, Any],
        sequence_number: int,
    ) -> None:
        with self._write_lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO stream_log "
                "(subscription_id, symbol, data_type, timestamp, payload, sequence_number) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (subscription_id, symbol, data_type, timestamp, json.dumps(payload), sequence_number),
            )

    def query(
        self,
        symbol: str,
        *,
        from_time: str | None = None,
        to_time: str | None = None,
        data_type: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["symbol = ?"]
        params: list[Any] = [symbol]

        if from_time is not None:
            clauses.append("timestamp >= ?")
            params.append(from_time)
        if to_time is not None:
            clauses.append("timestamp <= ?")
            params.append(to_time)
        if data_type is not None:
            clauses.append("data_type = ?")
            params.append(data_type)

        sql = (
            f"SELECT subscription_id, symbol, data_type, timestamp, payload, sequence_number "
            f"FROM stream_log WHERE {' AND '.join(clauses)} ORDER BY timestamp ASC, sequence_number ASC"
        )
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()

        return [
            {
                "subscription_id": r[0],
                "symbol": r[1],
                "data_type": r[2],
                "timestamp": r[3],
                "payload": json.loads(r[4]),
                "sequence_number": r[5],
            }
            for r in rows
        ]

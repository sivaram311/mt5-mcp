"""Live streaming: subscribe_live_data / unsubscribe_live_data. See docs/aidlc/SPEC.md sec 4.2, sec 7.

MT5's Python API has no push/callback mechanism (confirmed during this
Bolt — every read, including symbol_info_tick, is a synchronous poll) —
"near real-time forwarding to the MCP client" per SPEC.md sec 7 is
realized here as background polling + a durable log, not a true
server-push channel. A client gets live data via get_stream_log,
including catch-up after a disconnect — that *is* the mechanism SPEC.md
sec 7 describes for reconnection, just also the only delivery path in
this Bolt. True MCP-level push (e.g. resource-subscription
notifications) is a possible future enhancement, not built here.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from mt5_mcp.connector import MT5Connector
from mt5_mcp.stream_log import StreamLogStore
from mt5_mcp.timeframes import resolve_timeframe

SUPPORTED_DATA_TYPES = ("tick", "bar")
DEFAULT_POLL_INTERVAL_SECONDS = 1.0
_BAR_TIMEFRAME = "M1"  # fixed for this Bolt; configurable bar timeframe is future work


class StreamingError(Exception):
    """Carries an error_code + retryable flag through to the tool response envelope."""

    def __init__(self, error_code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable


class _Subscription:
    def __init__(
        self,
        subscription_id: str,
        symbol: str,
        data_types: list[str],
        stop_event: threading.Event,
        thread: threading.Thread,
    ) -> None:
        self.subscription_id = subscription_id
        self.symbol = symbol
        self.data_types = data_types
        self.stop_event = stop_event
        self.thread = thread


class SubscriptionManager:
    """Owns background polling threads that write ticks/bars into a StreamLogStore.

    `MT5Connector.raw` calls from the polling thread(s) are serialized
    against each other and against the main tool-dispatch thread via
    `_mt5_call_lock` — the MetaTrader5 package's thread-safety for
    concurrent calls from multiple Python threads isn't documented/
    guaranteed, and this Bolt is the first thing in this codebase to
    introduce real multi-thread MT5 access (Bolt 3's tools all ran on
    FastMCP's single request-handling thread). The lock only wraps calls
    made through this class today — Bolt 3's market_data.py calls are
    not yet covered, since those still only ever run on that single
    thread. Revisit if that assumption changes (e.g. concurrent tool
    calls become a real pattern).
    """

    def __init__(
        self,
        connector: MT5Connector,
        store: StreamLogStore,
        poll_interval: float = DEFAULT_POLL_INTERVAL_SECONDS,
    ) -> None:
        self._connector = connector
        self._store = store
        self._poll_interval = poll_interval
        self._lock = threading.Lock()
        self._mt5_call_lock = threading.Lock()
        self._subscriptions: dict[str, _Subscription] = {}
        self._sequence_counters: dict[str, int] = {}

    def subscribe(self, symbol: str, data_types: list[str] | None = None) -> str:
        data_types = data_types or ["tick"]
        for dt in data_types:
            if dt not in SUPPORTED_DATA_TYPES:
                raise StreamingError(
                    "unsupported_data_type",
                    f"data_type {dt!r} not supported yet. Supported: {', '.join(SUPPORTED_DATA_TYPES)} "
                    "(depth-of-market/custom types need MT5's separate market_book_add/get API, "
                    "not implemented in this Bolt).",
                )

        subscription_id = str(uuid.uuid4())
        stop_event = threading.Event()
        thread = threading.Thread(
            target=self._poll_loop,
            args=(subscription_id, symbol, data_types, stop_event),
            daemon=True,
            name=f"mt5-mcp-stream-{subscription_id[:8]}",
        )

        with self._lock:
            self._subscriptions[subscription_id] = _Subscription(
                subscription_id, symbol, data_types, stop_event, thread
            )
            self._sequence_counters[subscription_id] = 0

        thread.start()
        return subscription_id

    def unsubscribe(self, subscription_id: str | None = None, symbol: str | None = None) -> list[str]:
        if subscription_id is None and symbol is None:
            raise StreamingError(
                "invalid_parameter", "unsubscribe_live_data requires subscription_id or symbol"
            )

        with self._lock:
            if subscription_id is not None:
                matches = [subscription_id] if subscription_id in self._subscriptions else []
            else:
                matches = [sid for sid, sub in self._subscriptions.items() if sub.symbol == symbol]
            subs_to_stop = [self._subscriptions[sid] for sid in matches]

        for sub in subs_to_stop:
            sub.stop_event.set()
        for sub in subs_to_stop:
            sub.thread.join(timeout=self._poll_interval + 2.0)

        with self._lock:
            for sid in matches:
                self._subscriptions.pop(sid, None)
                self._sequence_counters.pop(sid, None)

        return matches

    def query_log(
        self,
        symbol: str,
        *,
        from_time: str | None = None,
        to_time: str | None = None,
        data_type: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        return self._store.query(symbol, from_time=from_time, to_time=to_time, data_type=data_type, limit=limit)

    def active_subscriptions(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {"subscription_id": sid, "symbol": sub.symbol, "data_types": sub.data_types}
                for sid, sub in self._subscriptions.items()
            ]

    def _next_sequence(self, subscription_id: str) -> int:
        with self._lock:
            self._sequence_counters[subscription_id] += 1
            return self._sequence_counters[subscription_id]

    def _poll_loop(
        self, subscription_id: str, symbol: str, data_types: list[str], stop_event: threading.Event
    ) -> None:
        last_tick_time: int | None = None
        last_bar_time: int | None = None
        while not stop_event.is_set():
            try:
                if "tick" in data_types:
                    last_tick_time = self._poll_tick(subscription_id, symbol, last_tick_time)
                if "bar" in data_types:
                    last_bar_time = self._poll_bar(subscription_id, symbol, last_bar_time)
            except Exception:
                # Best-effort background poll: a transient MT5 hiccup must not kill the
                # subscription thread. No health/status surface for this yet — future Bolt.
                pass
            stop_event.wait(self._poll_interval)

    def _poll_tick(self, subscription_id: str, symbol: str, last_tick_time: int | None) -> int | None:
        with self._mt5_call_lock:
            tick = self._connector.raw.symbol_info_tick(symbol)
        if tick is None or tick.time == last_tick_time:
            return last_tick_time

        payload = {
            "bid": tick.bid,
            "ask": tick.ask,
            "last": tick.last,
            "volume": tick.volume,
            "flags": tick.flags,
        }
        timestamp = datetime.fromtimestamp(tick.time, tz=timezone.utc).isoformat()
        self._store.append(subscription_id, symbol, "tick", timestamp, payload, self._next_sequence(subscription_id))
        return tick.time

    def _poll_bar(self, subscription_id: str, symbol: str, last_bar_time: int | None) -> int | None:
        with self._mt5_call_lock:
            tf = resolve_timeframe(self._connector.raw, _BAR_TIMEFRAME)
            rates = self._connector.raw.copy_rates_from_pos(symbol, tf, 0, 1)
        if rates is None or len(rates) == 0:
            return last_bar_time

        bar = rates[0]
        bar_time = int(bar["time"])
        if bar_time == last_bar_time:
            return last_bar_time

        payload = {
            "open": float(bar["open"]),
            "high": float(bar["high"]),
            "low": float(bar["low"]),
            "close": float(bar["close"]),
            "tick_volume": int(bar["tick_volume"]),
            "spread": int(bar["spread"]),
            "real_volume": int(bar["real_volume"]),
        }
        timestamp = datetime.fromtimestamp(bar_time, tz=timezone.utc).isoformat()
        self._store.append(subscription_id, symbol, "bar", timestamp, payload, self._next_sequence(subscription_id))
        return bar_time

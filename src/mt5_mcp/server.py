"""MT5-MCP server entrypoint.

Bolt 1 shipped the transport + response envelope (`ping`, no MT5
connection needed). Bolt 2 added `MT5Connector`. Bolt 3 wired
`get_historical_ohlcv` / `get_symbol_info` as real MCP tools backed by a
single lazily-created, process-lifetime `MT5Connector`.

Transport: default is `streamable-http`, not `stdio`. A real MCP client
calling into the `MetaTrader5` package over stdio was found to hang
indefinitely (not just slowly — confirmed stuck, near-zero CPU, for
13+ minutes before being killed) — see diagnostics/FINDINGS.md for the
full isolation writeup. `streamable-http` was verified to not have this
problem (diagnostics/live_smoke_http.py). stdio is kept available
(MT5_MCP_TRANSPORT=stdio) for whichever MCP client needs it, but is no
longer the default and is not currently known to work reliably for any
tool that touches MetaTrader5.
"""

from __future__ import annotations

import functools
import inspect
import os
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from mt5_mcp import __version__
from mt5_mcp import market_data
from mt5_mcp import orders
from mt5_mcp import positions
from mt5_mcp.audit_log import AuditLogStore
from mt5_mcp.connector import MT5Connector, MT5ConnectionError
from mt5_mcp.envelope import err, ok
from mt5_mcp.market_data import MarketDataError
from mt5_mcp.orders import OrderError
from mt5_mcp.positions import PositionError
from mt5_mcp.safety import SafetyError
from mt5_mcp.stream_log import StreamLogStore
from mt5_mcp.streaming import StreamingError, SubscriptionManager

# Anchored to the repo root rather than plain load_dotenv()'s CWD-relative
# search: an MCP client spawning this server can set an arbitrary working
# directory, and MT5_PATH (needed before any tool call can succeed — see
# docs/aidlc/BOLTS.md Bolt 3) must not silently go missing because of that.
load_dotenv(dotenv_path=Path(__file__).resolve().parents[2] / ".env")

TRANSPORT_ENV_VAR = "MT5_MCP_TRANSPORT"
DEFAULT_TRANSPORT = "streamable-http"
SUPPORTED_TRANSPORTS = ("stdio", "sse", "streamable-http")

# :3403 reserved in E:\MyAgent\workflow\ports\REGISTRY.md — loopback only,
# DEV testing. Only used by the http/sse transports; stdio ignores it.
HTTP_HOST = os.getenv("MT5_MCP_HTTP_HOST", "127.0.0.1")
HTTP_PORT = int(os.getenv("MT5_MCP_HTTP_PORT", "3403"))

mcp = FastMCP(name="mt5-mcp", host=HTTP_HOST, port=HTTP_PORT)

# Stream log DB: repo-root-anchored for the same reason as .env above —
# an MCP client can spawn this server from an arbitrary CWD. Gitignored
# (*.db, see .gitignore).
STREAM_LOG_DB_PATH = os.getenv(
    "MT5_MCP_STREAM_LOG_DB",
    str(Path(__file__).resolve().parents[2] / "mt5_mcp_stream_log.db"),
)
AUDIT_LOG_DB_PATH = os.getenv(
    "MT5_MCP_AUDIT_LOG_DB",
    str(Path(__file__).resolve().parents[2] / "mt5_mcp_audit_log.db"),
)

_connector: MT5Connector | None = None
_subscription_manager: SubscriptionManager | None = None
_audit_log: AuditLogStore | None = None


def _get_connector() -> MT5Connector:
    """One MT5Connector per server process, connected lazily on first use.

    Does not auto-reconnect on a dropped connection — a tool call that
    fails because the connection was lost returns a retryable
    connection_error; the caller can retry. Auto-reconnect is a possible
    future Bolt, not required by Bolt 3's acceptance criteria.
    """
    global _connector
    if _connector is None:
        _connector = MT5Connector()
        _connector.connect()
    return _connector


def _get_subscription_manager() -> SubscriptionManager:
    """One SubscriptionManager per server process, sharing the same
    MT5Connector as the market-data tools (see streaming.py's class
    docstring for the concurrency implications of that sharing)."""
    global _subscription_manager
    if _subscription_manager is None:
        store = StreamLogStore(STREAM_LOG_DB_PATH)
        _subscription_manager = SubscriptionManager(_get_connector(), store)
    return _subscription_manager


def _get_audit_log() -> AuditLogStore:
    global _audit_log
    if _audit_log is None:
        _audit_log = AuditLogStore(AUDIT_LOG_DB_PATH)
    return _audit_log


def _as_envelope(fn: Callable[..., Any]) -> Callable[..., dict]:
    """Wraps a tool function's return value/exceptions into the standard
    envelope (docs/aidlc/SPEC.md sec 8), so individual tools don't each
    repeat the same try/except.

    The wrapper always returns a dict (the envelope), regardless of what
    `fn` itself returns (e.g. `get_historical_ohlcv` returns a list of
    candles). `functools.wraps` sets `__wrapped__`, and `inspect.signature`
    — which FastMCP uses to build each tool's output schema — follows
    `__wrapped__` by default, so without this override FastMCP would infer
    the *inner* function's return type (e.g. `list[dict]`) as the tool's
    output schema, then reject the wrapper's actual `dict` envelope at
    validation time. Found live via diagnostics/live_smoke_http.py: every
    get_historical_ohlcv call failed with a pydantic "Input should be a
    valid list" error. Explicitly setting `__signature__` (checked by
    `inspect.signature` before it follows `__wrapped__`) keeps the real
    parameter list (still needed for the tool's input schema) while
    correcting just the return annotation to `dict`.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> dict:
        try:
            return ok(data=fn(*args, **kwargs))
        except MarketDataError as exc:
            return err(exc.error_code, str(exc), retryable=exc.retryable)
        except StreamingError as exc:
            return err(exc.error_code, str(exc), retryable=exc.retryable)
        except OrderError as exc:
            return err(exc.error_code, str(exc), retryable=exc.retryable)
        except PositionError as exc:
            return err(exc.error_code, str(exc), retryable=exc.retryable)
        except SafetyError as exc:
            return err(exc.error_code, str(exc), retryable=exc.retryable)
        except MT5ConnectionError as exc:
            return err("connection_error", str(exc), retryable=True)
        except ValueError as exc:
            return err("invalid_parameter", str(exc), retryable=False)
        except Exception as exc:  # pragma: no cover - defensive catch-all
            return err("internal_error", str(exc), retryable=False)

    wrapper.__signature__ = inspect.signature(fn).replace(return_annotation=dict)
    return wrapper


@mcp.tool()
def ping() -> dict:
    """Health check. Returns the standard envelope with server version — no MT5 connection required."""
    return ok(data={"server": "mt5-mcp", "version": __version__})


@mcp.tool()
@_as_envelope
def get_historical_ohlcv(
    symbol: str,
    timeframe: str,
    from_date: str | None = None,
    to_date: str | None = None,
    from_bar: int | None = None,
    to_bar: int | None = None,
    limit: int | None = None,
    include_volume: bool = False,
    include_spread: bool = False,
    session_filter: str | None = None,
    price_type: str | None = None,
    only_completed_bars: bool = True,
) -> list[dict]:
    """Historical OHLCV candles for a symbol. See docs/aidlc/SPEC.md sec 4.1 for full parameter semantics."""
    return market_data.get_historical_ohlcv(
        _get_connector(),
        symbol,
        timeframe,
        from_date=from_date,
        to_date=to_date,
        from_bar=from_bar,
        to_bar=to_bar,
        limit=limit,
        include_volume=include_volume,
        include_spread=include_spread,
        session_filter=session_filter,
        price_type=price_type,
        only_completed_bars=only_completed_bars,
    )


@mcp.tool()
@_as_envelope
def get_symbol_info(symbol: str) -> dict:
    """Static + dynamic metadata for a symbol (contract size, tick size/value, digits, swaps, margin, current bid/ask). See docs/aidlc/SPEC.md sec 4.1."""
    return market_data.get_symbol_info(_get_connector(), symbol)


@mcp.tool()
@_as_envelope
def subscribe_live_data(symbol: str, data_types: list[str] | None = None) -> dict:
    """Starts a background poll that writes ticks/bars for a symbol into the durable stream log.

    MT5's Python API has no push/callback mechanism, so this is polling
    (default ~1s interval), not a true server-push channel — see
    docs/aidlc/SPEC.md sec 7 and streaming.py's module docstring.
    Supported data_types: "tick", "bar" (fixed M1 timeframe for now).
    Returns a subscription_id; use get_stream_log to read the data back,
    unsubscribe_live_data to stop.
    """
    subscription_id = _get_subscription_manager().subscribe(symbol, data_types)
    return {"subscription_id": subscription_id, "symbol": symbol, "data_types": data_types or ["tick"]}


@mcp.tool()
@_as_envelope
def unsubscribe_live_data(subscription_id: str | None = None, symbol: str | None = None) -> dict:
    """Stops an active stream by subscription_id or symbol (symbol stops every subscription for it)."""
    stopped = _get_subscription_manager().unsubscribe(subscription_id=subscription_id, symbol=symbol)
    return {"stopped_subscription_ids": stopped}


@mcp.tool()
@_as_envelope
def get_stream_log(
    symbol: str,
    from_time: str | None = None,
    to_time: str | None = None,
    data_type: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Reads back previously logged live data for a symbol — lets a client catch up after a disconnect. See docs/aidlc/SPEC.md sec 4.2/sec 7."""
    return _get_subscription_manager().query_log(
        symbol, from_time=from_time, to_time=to_time, data_type=data_type, limit=limit
    )


@mcp.tool()
@_as_envelope
def place_order(
    symbol: str,
    order_type: str,
    side: str,
    volume: float,
    price: float | None = None,
    stop_loss: float | None = None,
    take_profit: float | None = None,
    comment: str | None = None,
    magic_number: int | None = None,
    deviation: int = 20,
    client_order_id: str | None = None,
) -> dict:
    """Places a new order (market/limit/stop; buy/sell). See docs/aidlc/SPEC.md sec 4.3.

    Defaults to dry-run (MT5_MCP_DRY_RUN env var, never a tool parameter
    — see docs/aidlc/INCEPTION.md "Safety layer"). Gated by the
    kill-switch and the configured max lot size / max open positions —
    this is the only order/position tool the kill-switch blocks, since
    it's the only one that can create new market exposure.

    `client_order_id` is recorded in the audit log for traceability but
    does not yet provide request deduplication/idempotency — a retried
    call with the same client_order_id will place a second real order.
    """
    return orders.place_order(
        _get_connector(), _get_audit_log(), symbol, order_type, side, volume,
        price=price, stop_loss=stop_loss, take_profit=take_profit, comment=comment,
        magic_number=magic_number, deviation=deviation, client_order_id=client_order_id,
    )


@mcp.tool()
@_as_envelope
def modify_order(
    ticket: int,
    price: float | None = None,
    stop_loss: float | None = None,
    take_profit: float | None = None,
    volume: float | None = None,
    expiration: str | None = None,
) -> dict:
    """Modifies a pending order's price/SL/TP/volume. Dry-run by default; not kill-switch-gated (risk-neutral/reducing — see orders.py). `expiration` is accepted for future use but not yet implemented — passing it raises unsupported_parameter rather than silently ignoring it."""
    return orders.modify_order(
        _get_connector(), _get_audit_log(), ticket,
        price=price, stop_loss=stop_loss, take_profit=take_profit, volume=volume, expiration=expiration,
    )


@mcp.tool()
@_as_envelope
def cancel_order(ticket: int) -> dict:
    """Cancels a pending order by ticket. Dry-run by default; not kill-switch-gated (pure risk reduction)."""
    return orders.cancel_order(_get_connector(), _get_audit_log(), ticket)


@mcp.tool()
@_as_envelope
def get_open_positions(
    symbol: str | None = None,
    magic_number: int | None = None,
    comment: str | None = None,
) -> list[dict]:
    """Read-only: lists open positions, optionally filtered by symbol/magic_number/comment. No safety checks (not an execution tool)."""
    return positions.get_open_positions(_get_connector(), symbol=symbol, magic_number=magic_number, comment=comment)


@mcp.tool()
@_as_envelope
def modify_position(ticket: int, stop_loss: float | None = None, take_profit: float | None = None) -> dict:
    """Modifies an open position's SL/TP. Dry-run by default; not kill-switch-gated (risk-neutral/reducing)."""
    return positions.modify_position(_get_connector(), _get_audit_log(), ticket, stop_loss=stop_loss, take_profit=take_profit)


@mcp.tool()
@_as_envelope
def close_position(ticket: int, volume: float | None = None) -> dict:
    """Closes a position fully or partially (by volume). Dry-run by default; not kill-switch-gated (pure risk reduction)."""
    return positions.close_position(_get_connector(), _get_audit_log(), ticket, volume=volume)


@mcp.tool()
@_as_envelope
def close_all_positions(symbol: str | None = None, side: str | None = None, magic_number: int | None = None) -> dict:
    """Bulk-closes open positions matching the given filters. Dry-run by default; not kill-switch-gated (pure risk reduction)."""
    return positions.close_all_positions(_get_connector(), _get_audit_log(), symbol=symbol, side=side, magic_number=magic_number)


def main() -> None:
    transport = os.getenv(TRANSPORT_ENV_VAR, DEFAULT_TRANSPORT)
    if transport not in SUPPORTED_TRANSPORTS:
        raise ValueError(
            f"Unsupported {TRANSPORT_ENV_VAR}={transport!r}. "
            f"Supported: {', '.join(SUPPORTED_TRANSPORTS)}"
        )
    mcp.run(transport=transport)  # type: ignore[arg-type]


if __name__ == "__main__":
    main()

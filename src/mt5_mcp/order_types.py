"""order_type + side -> MetaTrader5 ORDER_TYPE_* constant mapping.

Constants are read off the injected mt5 module (same pattern as
timeframes.py), never hardcoded — the real values always come from
whatever MetaTrader5 build is installed.
"""

from __future__ import annotations

from typing import Any

# (order_type, side) -> MT5 constant name. Only market/limit/stop are
# implemented this Bolt (matches BOLTS.md Bolt 5 acceptance: "at least
# market/limit/stop"). stop_limit needs a different request shape
# (price + stop-limit price, MT5's *_STOP_LIMIT constants) and
# trailing_stop isn't a pending-order type in MT5 at all — it's a
# terminal-side or EA-managed position modifier, not something
# order_send() places directly. Both are legitimate future Bolts, not
# silently assumed unsupported.
_ORDER_TYPE_MAP: dict[tuple[str, str], str] = {
    ("market", "buy"): "ORDER_TYPE_BUY",
    ("market", "sell"): "ORDER_TYPE_SELL",
    ("limit", "buy"): "ORDER_TYPE_BUY_LIMIT",
    ("limit", "sell"): "ORDER_TYPE_SELL_LIMIT",
    ("stop", "buy"): "ORDER_TYPE_BUY_STOP",
    ("stop", "sell"): "ORDER_TYPE_SELL_STOP",
}

SUPPORTED_ORDER_TYPES = ("market", "limit", "stop")
SUPPORTED_SIDES = ("buy", "sell")

UNSUPPORTED_ORDER_TYPE_NOTE = (
    "stop_limit and trailing_stop are not implemented in this Bolt — stop_limit needs a "
    "different request shape (price + stop-limit price via MT5's *_STOP_LIMIT constants), "
    "and trailing_stop in MT5 is not a pending-order type at all, it's a terminal-side or "
    "EA-managed position modifier. Both are legitimate future work, not silently dropped."
)


def resolve_order_type(mt5_module: Any, order_type: str, side: str) -> Any:
    ot = order_type.strip().lower()
    sd = side.strip().lower()
    key = (ot, sd)
    if key not in _ORDER_TYPE_MAP:
        raise ValueError(
            f"Unsupported order_type={order_type!r}/side={side!r}. "
            f"Supported order_type: {', '.join(SUPPORTED_ORDER_TYPES)}; side: {', '.join(SUPPORTED_SIDES)}. "
            f"{UNSUPPORTED_ORDER_TYPE_NOTE}"
        )
    attr = _ORDER_TYPE_MAP[key]
    if not hasattr(mt5_module, attr):
        raise ValueError(f"MetaTrader5 module has no {attr} constant")
    return getattr(mt5_module, attr)


def resolve_filling_mode(mt5_module: Any, symbol_filling_mode_bitmask: int) -> Any:
    """Picks a supported ORDER_FILLING_* constant for this symbol, per its
    real advertised filling_mode bitmask (SYMBOL_FILLING_* bits) — checked
    live against the broker, not assumed (BOLTS.md Bolt 5 acceptance:
    "checked against Octa Markets' actual ... support, not assumed").
    Preference order FOK, then IOC, then RETURN (RETURN has no
    corresponding bit in the bitmask — it's always a valid fallback for
    non-market-execution brokers per MT5's own docs).
    """
    fok_bit = getattr(mt5_module, "SYMBOL_FILLING_FOK", 1)
    ioc_bit = getattr(mt5_module, "SYMBOL_FILLING_IOC", 2)

    if symbol_filling_mode_bitmask & fok_bit and hasattr(mt5_module, "ORDER_FILLING_FOK"):
        return mt5_module.ORDER_FILLING_FOK
    if symbol_filling_mode_bitmask & ioc_bit and hasattr(mt5_module, "ORDER_FILLING_IOC"):
        return mt5_module.ORDER_FILLING_IOC
    return mt5_module.ORDER_FILLING_RETURN

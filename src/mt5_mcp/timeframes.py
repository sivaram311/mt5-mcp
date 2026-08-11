"""Timeframe string <-> MetaTrader5 TIMEFRAME_* constant mapping.

Constants are read off the injected mt5 module rather than hardcoded, so
the real values always come from whatever MetaTrader5 build is installed
(and tests can supply their own sentinel values via a fake module).
"""

from __future__ import annotations

from typing import Any

SUPPORTED_TIMEFRAMES = ["M1", "M5", "M15", "M30", "H1", "H4", "D1", "W1", "MN1"]

# Approximate bar duration in seconds, used only to decide whether the most
# recent bar is still "forming" (only_completed_bars). MN1 varies by month
# length so it's intentionally omitted — only_completed_bars is a no-op there.
TIMEFRAME_SECONDS: dict[str, int] = {
    "M1": 60,
    "M5": 300,
    "M15": 900,
    "M30": 1800,
    "H1": 3600,
    "H4": 14400,
    "D1": 86400,
    "W1": 604800,
}


def resolve_timeframe(mt5_module: Any, timeframe: str) -> Any:
    key = timeframe.strip().upper()
    if key not in SUPPORTED_TIMEFRAMES:
        raise ValueError(
            f"Unsupported timeframe {timeframe!r}. Supported: {', '.join(SUPPORTED_TIMEFRAMES)}"
        )
    attr = f"TIMEFRAME_{key}"
    if not hasattr(mt5_module, attr):
        raise ValueError(f"MetaTrader5 module has no {attr} constant")
    return getattr(mt5_module, attr)

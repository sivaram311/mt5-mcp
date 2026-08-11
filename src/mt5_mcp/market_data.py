"""get_historical_ohlcv / get_symbol_info. See docs/aidlc/SPEC.md sec 4.1."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from mt5_mcp.connector import MT5Connector
from mt5_mcp.timeframes import TIMEFRAME_SECONDS, resolve_timeframe

DEFAULT_LIMIT = 100

# Rough, widely-used UTC session windows. Not broker/DST-adjusted — good
# enough for a coarse filter, not a precise trading-hours source of truth.
SESSION_HOURS_UTC: dict[str, tuple[int, int]] = {
    "Sydney": (21, 6),
    "Asian": (0, 9),
    "London": (7, 16),
    "NewYork": (12, 21),
    "Full": (0, 24),
}


class MarketDataError(Exception):
    """Carries an error_code + retryable flag through to the tool response envelope."""

    def __init__(self, error_code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable


def _parse_date(value: str | int | float) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    text = str(value)
    if text.lstrip("-").isdigit():
        return datetime.fromtimestamp(int(text), tz=timezone.utc)
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _in_session(bar_time: datetime, session: str) -> bool:
    if session not in SESSION_HOURS_UTC:
        raise MarketDataError(
            "invalid_session_filter",
            f"Unknown session_filter {session!r}. Supported: {', '.join(SESSION_HOURS_UTC)}",
        )
    start, end = SESSION_HOURS_UTC[session]
    hour = bar_time.hour
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end  # wraps past midnight (e.g. Sydney)


def _map_rate(rate: Any, *, include_volume: bool, include_spread: bool) -> dict[str, Any]:
    candle: dict[str, Any] = {
        "time": datetime.fromtimestamp(int(rate["time"]), tz=timezone.utc).isoformat(),
        "open": float(rate["open"]),
        "high": float(rate["high"]),
        "low": float(rate["low"]),
        "close": float(rate["close"]),
    }
    if include_volume:
        candle["tick_volume"] = int(rate["tick_volume"])
        candle["real_volume"] = int(rate["real_volume"])
    if include_spread:
        candle["spread"] = int(rate["spread"])
    return candle


def _fetch_rates(
    mt5: Any,
    symbol: str,
    tf: Any,
    *,
    from_date: str | int | float | None,
    to_date: str | int | float | None,
    from_bar: int | None,
    to_bar: int | None,
    limit: int | None,
) -> Any:
    if from_date is not None or to_date is not None:
        end = _parse_date(to_date) if to_date is not None else datetime.now(timezone.utc)
        start = _parse_date(from_date) if from_date is not None else end
        return mt5.copy_rates_range(symbol, tf, start, end)

    if from_bar is not None or to_bar is not None:
        start_pos = from_bar if from_bar is not None else 0
        if to_bar is not None:
            count = max(to_bar - start_pos + 1, 1)
        else:
            count = limit or DEFAULT_LIMIT
        return mt5.copy_rates_from_pos(symbol, tf, start_pos, count)

    return mt5.copy_rates_from_pos(symbol, tf, 0, limit or DEFAULT_LIMIT)


def get_historical_ohlcv(
    connector: MT5Connector,
    symbol: str,
    timeframe: str,
    *,
    from_date: str | int | float | None = None,
    to_date: str | int | float | None = None,
    from_bar: int | None = None,
    to_bar: int | None = None,
    limit: int | None = None,
    include_volume: bool = False,
    include_spread: bool = False,
    session_filter: str | None = None,
    price_type: str | None = None,
    only_completed_bars: bool = True,
) -> list[dict[str, Any]]:
    if price_type not in (None, "bid"):
        raise MarketDataError(
            "unsupported_price_type",
            f"price_type={price_type!r} is not supported yet — MT5's copy_rates API only exposes "
            "Bid-based OHLC directly; ask/mid/last would require reconstructing bars from tick "
            "data (not implemented in this Bolt).",
        )

    mt5 = connector.raw
    tf = resolve_timeframe(mt5, timeframe)

    if not mt5.symbol_select(symbol, True):
        raise MarketDataError(
            "invalid_symbol", f"symbol_select({symbol!r}) failed: {connector.last_error()}"
        )

    rates = _fetch_rates(
        mt5,
        symbol,
        tf,
        from_date=from_date,
        to_date=to_date,
        from_bar=from_bar,
        to_bar=to_bar,
        limit=limit,
    )
    if rates is None or len(rates) == 0:
        raise MarketDataError(
            "data_unavailable",
            f"No historical data returned for {symbol} {timeframe}: {connector.last_error()}",
            retryable=True,
        )

    candles = [
        _map_rate(r, include_volume=include_volume, include_spread=include_spread) for r in rates
    ]

    if only_completed_bars and candles:
        tf_seconds = TIMEFRAME_SECONDS.get(timeframe.strip().upper())
        if tf_seconds is not None:
            now = datetime.now(timezone.utc)
            last_bar_time = datetime.fromisoformat(candles[-1]["time"])
            if last_bar_time.timestamp() + tf_seconds > now.timestamp():
                candles = candles[:-1]

    if session_filter:
        candles = [c for c in candles if _in_session(datetime.fromisoformat(c["time"]), session_filter)]

    return candles


def get_symbol_info(connector: MT5Connector, symbol: str) -> dict[str, Any]:
    mt5 = connector.raw

    if not mt5.symbol_select(symbol, True):
        raise MarketDataError(
            "invalid_symbol", f"symbol_select({symbol!r}) failed: {connector.last_error()}"
        )

    info = mt5.symbol_info(symbol)
    if info is None:
        raise MarketDataError(
            "invalid_symbol", f"symbol_info({symbol!r}) returned None: {connector.last_error()}"
        )

    tick = mt5.symbol_info_tick(symbol)

    return {
        "symbol": symbol,
        "digits": info.digits,
        "contract_size": info.trade_contract_size,
        "tick_size": info.trade_tick_size,
        "tick_value": info.trade_tick_value,
        "trade_mode": info.trade_mode,
        "volume_min": info.volume_min,
        "volume_max": info.volume_max,
        "volume_step": info.volume_step,
        "swap_long": info.swap_long,
        "swap_short": info.swap_short,
        "margin_initial": info.margin_initial,
        "bid": tick.bid if tick is not None else info.bid,
        "ask": tick.ask if tick is not None else info.ask,
    }

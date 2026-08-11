"""Position management. See docs/aidlc/SPEC.md sec 4.4.

get_open_positions is a read-only query — no safety checks, no audit
log (matches market_data.py's tools). modify_position/close_position/
close_all_positions are risk-reducing or risk-neutral actions, so per
orders.py's module docstring, the kill-switch does not gate them —
only orders.py's `place_order` (the sole risk-*increasing* action) is
kill-switch-gated. All three are still dry-run-by-default and fully
audit-logged, same as every execution tool.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from mt5_mcp import safety
from mt5_mcp.audit_log import AuditLogStore
from mt5_mcp.connector import MT5Connector
from mt5_mcp.order_types import resolve_filling_mode


class PositionError(Exception):
    """Carries an error_code + retryable flag through to the tool response envelope."""

    def __init__(self, error_code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _map_position(p: Any) -> dict[str, Any]:
    return {
        "ticket": p.ticket,
        "symbol": p.symbol,
        "side": "buy" if p.type == 0 else "sell",
        "volume": p.volume,
        "open_price": p.price_open,
        "current_price": p.price_current,
        "stop_loss": p.sl,
        "take_profit": p.tp,
        "profit": p.profit,
        "swap": p.swap,
        "comment": p.comment,
        "magic_number": p.magic,
        "open_time": datetime.fromtimestamp(p.time, tz=timezone.utc).isoformat(),
    }


def get_open_positions(
    connector: MT5Connector,
    symbol: str | None = None,
    magic_number: int | None = None,
    comment: str | None = None,
) -> list[dict[str, Any]]:
    mt5 = connector.raw
    positions = mt5.positions_get(symbol=symbol) if symbol else mt5.positions_get()
    if positions is None:
        return []

    result = [_map_position(p) for p in positions]
    if magic_number is not None:
        result = [p for p in result if p["magic_number"] == magic_number]
    if comment is not None:
        result = [p for p in result if p["comment"] == comment]
    return result


def modify_position(
    connector: MT5Connector,
    audit: AuditLogStore,
    ticket: int,
    stop_loss: float | None = None,
    take_profit: float | None = None,
) -> dict[str, Any]:
    request = {"ticket": ticket, "stop_loss": stop_loss, "take_profit": take_profit}
    dry_run = safety.is_dry_run()

    try:
        mt5 = connector.raw
        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            raise PositionError("position_not_found", f"No open position with ticket {ticket}")
        existing = positions[0]

        new_sl = stop_loss if stop_loss is not None else existing.sl
        new_tp = take_profit if take_profit is not None else existing.tp

        if dry_run:
            result = {
                "simulated": True, "ticket": ticket, "status": "simulated_modify",
                "stop_loss": new_sl, "take_profit": new_tp,
            }
        else:
            send_result = mt5.order_send(
                {"action": mt5.TRADE_ACTION_SLTP, "position": ticket, "symbol": existing.symbol, "sl": new_sl, "tp": new_tp}
            )
            if send_result is None or send_result.retcode != mt5.TRADE_RETCODE_DONE:
                code = send_result.retcode if send_result is not None else None
                raise PositionError("execution_failed", f"position modify rejected: retcode={code}", retryable=True)
            result = {"simulated": False, "ticket": ticket, "status": "modified", "stop_loss": new_sl, "take_profit": new_tp}

        audit.record(_now_iso(), "modify_position", existing.symbol, dry_run, request, success=True, result=result)
        return result
    except PositionError as exc:
        audit.record(_now_iso(), "modify_position", None, dry_run, request, success=False, error_code=exc.error_code)
        raise


def close_position(
    connector: MT5Connector,
    audit: AuditLogStore,
    ticket: int,
    volume: float | None = None,
) -> dict[str, Any]:
    request = {"ticket": ticket, "volume": volume}
    dry_run = safety.is_dry_run()

    try:
        mt5 = connector.raw
        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            raise PositionError("position_not_found", f"No open position with ticket {ticket}")
        existing = positions[0]
        close_volume = volume if volume is not None else existing.volume

        if close_volume > existing.volume:
            raise PositionError(
                "invalid_parameter",
                f"Requested close volume {close_volume} exceeds the position's actual volume {existing.volume}",
            )

        if dry_run:
            tick = mt5.symbol_info_tick(existing.symbol)
            close_price = (tick.bid if existing.type == 0 else tick.ask) if tick is not None else existing.price_current
            result = {
                "simulated": True, "ticket": ticket, "status": "simulated_close",
                "closed_volume": close_volume, "close_price": close_price,
            }
        else:
            symbol_info = mt5.symbol_info(existing.symbol)
            close_side_type = mt5.ORDER_TYPE_SELL if existing.type == 0 else mt5.ORDER_TYPE_BUY
            tick = mt5.symbol_info_tick(existing.symbol)
            close_price = tick.bid if existing.type == 0 else tick.ask

            send_result = mt5.order_send({
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": existing.symbol,
                "volume": close_volume,
                "type": close_side_type,
                "position": ticket,
                "price": close_price,
                "deviation": 20,
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": resolve_filling_mode(mt5, symbol_info.filling_mode),
            })
            if send_result is None or send_result.retcode != mt5.TRADE_RETCODE_DONE:
                code = send_result.retcode if send_result is not None else None
                raise PositionError("execution_failed", f"position close rejected: retcode={code}", retryable=True)
            result = {
                "simulated": False, "ticket": ticket, "status": "closed",
                "closed_volume": send_result.volume, "close_price": send_result.price,
            }

        audit.record(_now_iso(), "close_position", existing.symbol, dry_run, request, success=True, result=result)
        return result
    except PositionError as exc:
        audit.record(_now_iso(), "close_position", None, dry_run, request, success=False, error_code=exc.error_code)
        raise


def close_all_positions(
    connector: MT5Connector,
    audit: AuditLogStore,
    symbol: str | None = None,
    side: str | None = None,
    magic_number: int | None = None,
) -> dict[str, Any]:
    positions = get_open_positions(connector, symbol=symbol, magic_number=magic_number)
    if side is not None:
        positions = [p for p in positions if p["side"] == side]

    closed: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for p in positions:
        try:
            closed.append(close_position(connector, audit, p["ticket"]))
        except PositionError as exc:
            failed.append({"ticket": p["ticket"], "error_code": exc.error_code, "error_message": str(exc)})

    return {"closed": closed, "failed": failed, "requested_count": len(positions)}

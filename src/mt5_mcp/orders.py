"""Order placement/modification/cancellation. See docs/aidlc/SPEC.md sec 4.3.

Every function here goes through the safety layer (docs/aidlc/
INCEPTION.md "Safety layer") and the audit log unconditionally.

Kill-switch design decision (documented for human review, per BOLTS.md
Bolt 5's "changes here always require explicit human review"): the
kill-switch and lot/open-position limits apply ONLY to `place_order` —
the one action here that can create new market exposure. `modify_order`
and `cancel_order` are never blocked by the kill-switch, because a kill
switch whose job is "stop trading now" must not also trap an operator
inside an existing exposure by blocking the actions that reduce or
cancel it. `cancel_order` in particular is a pure risk-reduction action.
`modify_order`'s `volume` parameter, if increased, does go through the
lot-size limit check (that specific path *does* increase exposure),
but is never blocked by the kill-switch for the same reason as above —
if this is judged wrong on review, it is a one-line change to make
volume-increasing modifies also kill-switch-gated.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from mt5_mcp import safety
from mt5_mcp.audit_log import AuditLogStore
from mt5_mcp.connector import MT5Connector
from mt5_mcp.order_types import resolve_filling_mode, resolve_order_type


class OrderError(Exception):
    """Carries an error_code + retryable flag through to the tool response envelope."""

    def __init__(self, error_code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _count_open_positions(connector: MT5Connector) -> int:
    positions = connector.raw.positions_get()
    return len(positions) if positions is not None else 0


def _audited(audit: AuditLogStore, action: str, symbol: str | None, dry_run: bool, request: dict[str, Any]):
    """Context-manager-free helper: call on success/failure explicitly (kept
    simple/explicit rather than a decorator, since each function's error
    handling needs slightly different shape for OrderError vs SafetyError)."""

    def record_success(result: dict[str, Any]) -> dict[str, Any]:
        audit.record(_now_iso(), action, symbol, dry_run, request, success=True, result=result)
        return result

    def record_failure(error_code: str) -> None:
        audit.record(_now_iso(), action, symbol, dry_run, request, success=False, error_code=error_code)

    return record_success, record_failure


def place_order(
    connector: MT5Connector,
    audit: AuditLogStore,
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
) -> dict[str, Any]:
    request = {
        "symbol": symbol,
        "order_type": order_type,
        "side": side,
        "volume": volume,
        "price": price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "comment": comment,
        "magic_number": magic_number,
        "deviation": deviation,
        "client_order_id": client_order_id,
    }
    dry_run = safety.is_dry_run()
    ok, fail = _audited(audit, "place_order", symbol, dry_run, request)

    try:
        mt5 = connector.raw
        try:
            mt5_order_type = resolve_order_type(mt5, order_type, side)
        except ValueError as exc:
            raise OrderError("unsupported_order_type", str(exc)) from exc

        if order_type in ("limit", "stop") and price is None:
            raise OrderError("invalid_parameter", f"price is required for {order_type} orders")

        open_count = _count_open_positions(connector)
        try:
            safety.check_before_execution(volume=volume, current_open_position_count=open_count)
        except safety.SafetyError as exc:
            raise OrderError(exc.error_code, str(exc), retryable=exc.retryable) from exc

        if not mt5.symbol_select(symbol, True):
            raise OrderError("invalid_symbol", f"symbol_select({symbol!r}) failed: {connector.last_error()}")

        if dry_run:
            result = _simulate_place_order(connector, symbol, order_type, side, volume, price, stop_loss, take_profit)
        else:
            result = _execute_place_order(
                connector, symbol, mt5_order_type, order_type, volume, price, stop_loss, take_profit,
                deviation, magic_number, comment,
            )
        return ok(result)
    except OrderError as exc:
        fail(exc.error_code)
        raise
    except Exception as exc:
        # Anything unexpected (order_send() itself raising on a network
        # blip/terminal disconnect, an unanticipated attribute error, ...)
        # must still be audited — an execution attempt that isn't in the
        # audit log is exactly the failure mode this log exists to prevent.
        # Re-raised unchanged; server.py's envelope layer maps it to a
        # generic internal_error for the caller.
        fail("internal_error")
        raise


def _simulate_place_order(
    connector: MT5Connector,
    symbol: str,
    order_type: str,
    side: str,
    volume: float,
    price: float | None,
    stop_loss: float | None,
    take_profit: float | None,
) -> dict[str, Any]:
    """Never calls order_send. Uses the real current tick to produce a
    realistic simulated fill price for market orders, so a dry-run
    response is shaped like what a real one would look like."""
    tick = connector.raw.symbol_info_tick(symbol)
    if order_type == "market":
        fill_price = (tick.ask if side == "buy" else tick.bid) if tick is not None else price
    else:
        fill_price = price

    return {
        "simulated": True,
        "ticket": None,
        "status": "simulated",
        "symbol": symbol,
        "order_type": order_type,
        "side": side,
        "volume": volume,
        "filled_volume": volume if order_type == "market" else 0.0,
        "average_price": fill_price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
    }


def _execute_place_order(
    connector: MT5Connector,
    symbol: str,
    mt5_order_type: Any,
    order_type: str,
    volume: float,
    price: float | None,
    stop_loss: float | None,
    take_profit: float | None,
    deviation: int,
    magic_number: int | None,
    comment: str | None,
) -> dict[str, Any]:
    mt5 = connector.raw
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None:
        raise OrderError("invalid_symbol", f"symbol_info({symbol!r}) returned None: {connector.last_error()}")

    if order_type == "market":
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            raise OrderError("data_unavailable", f"symbol_info_tick({symbol!r}) returned None", retryable=True)
        order_price = tick.ask if mt5_order_type == mt5.ORDER_TYPE_BUY else tick.bid
        action = mt5.TRADE_ACTION_DEAL
    else:
        if price is None:
            raise OrderError("invalid_parameter", f"price is required for {order_type} orders")
        order_price = price
        action = mt5.TRADE_ACTION_PENDING

    request: dict[str, Any] = {
        "action": action,
        "symbol": symbol,
        "volume": volume,
        "type": mt5_order_type,
        "price": order_price,
        "deviation": deviation,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": resolve_filling_mode(mt5, symbol_info.filling_mode),
    }
    if stop_loss is not None:
        request["sl"] = stop_loss
    if take_profit is not None:
        request["tp"] = take_profit
    if magic_number is not None:
        request["magic"] = magic_number
    if comment is not None:
        request["comment"] = comment

    result = mt5.order_send(request)
    if result is None:
        raise OrderError("execution_failed", f"order_send() returned None: {connector.last_error()}", retryable=True)

    if result.retcode != mt5.TRADE_RETCODE_DONE:
        raise OrderError(
            "execution_failed",
            f"order_send() rejected: retcode={result.retcode} comment={result.comment!r}",
            retryable=result.retcode in (getattr(mt5, "TRADE_RETCODE_REQUOTE", -1), getattr(mt5, "TRADE_RETCODE_PRICE_CHANGED", -2)),
        )

    return {
        "simulated": False,
        "ticket": result.order,
        "status": "filled" if order_type == "market" else "pending",
        "symbol": symbol,
        "order_type": order_type,
        "volume": volume,
        "filled_volume": result.volume,
        "average_price": result.price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
    }


def modify_order(
    connector: MT5Connector,
    audit: AuditLogStore,
    ticket: int,
    price: float | None = None,
    stop_loss: float | None = None,
    take_profit: float | None = None,
    volume: float | None = None,
    expiration: str | None = None,
) -> dict[str, Any]:
    request = {
        "ticket": ticket, "price": price, "stop_loss": stop_loss, "take_profit": take_profit,
        "volume": volume, "expiration": expiration,
    }
    dry_run = safety.is_dry_run()
    ok, fail = _audited(audit, "modify_order", None, dry_run, request)

    try:
        mt5 = connector.raw
        orders = mt5.orders_get(ticket=ticket)
        if not orders:
            raise OrderError("order_not_found", f"No pending order with ticket {ticket}")
        existing = orders[0]

        if volume is not None and volume > existing.volume_current:
            # Lot-size check applies only when volume is actually being
            # increased — that's the only direction that increases exposure.
            # A decrease must never be blocked by this check, even if the
            # requested (lower) volume still exceeds today's configured
            # limit (e.g. a legacy order placed under a since-lowered cap):
            # blocking a risk-*reducing* modify would contradict the same
            # reasoning that keeps the kill-switch off this function.
            # Kill-switch itself deliberately does not gate modify_order at
            # all (see module docstring); open-position-count isn't
            # relevant to modifying an existing pending order.
            try:
                safety.check_lot_size(volume)
            except safety.SafetyError as exc:
                raise OrderError(exc.error_code, str(exc), retryable=exc.retryable) from exc

        if dry_run:
            result = {
                "simulated": True, "ticket": ticket, "status": "simulated_modify",
                "price": price if price is not None else existing.price_open,
                "stop_loss": stop_loss if stop_loss is not None else existing.sl,
                "take_profit": take_profit if take_profit is not None else existing.tp,
                "volume": volume if volume is not None else existing.volume_current,
            }
        else:
            mt5_request: dict[str, Any] = {"action": mt5.TRADE_ACTION_MODIFY, "order": ticket}
            mt5_request["price"] = price if price is not None else existing.price_open
            mt5_request["sl"] = stop_loss if stop_loss is not None else existing.sl
            mt5_request["tp"] = take_profit if take_profit is not None else existing.tp
            if volume is not None:
                mt5_request["volume"] = volume

            send_result = mt5.order_send(mt5_request)
            if send_result is None or send_result.retcode != mt5.TRADE_RETCODE_DONE:
                code = send_result.retcode if send_result is not None else None
                raise OrderError("execution_failed", f"order modify rejected: retcode={code}", retryable=True)
            result = {
                "simulated": False, "ticket": ticket, "status": "modified",
                "price": mt5_request["price"], "stop_loss": mt5_request["sl"], "take_profit": mt5_request["tp"],
                "volume": volume if volume is not None else existing.volume_current,
            }
        return ok(result)
    except OrderError as exc:
        fail(exc.error_code)
        raise
    except Exception as exc:
        # Anything unexpected (order_send() itself raising on a network
        # blip/terminal disconnect, an unanticipated attribute error, ...)
        # must still be audited — an execution attempt that isn't in the
        # audit log is exactly the failure mode this log exists to prevent.
        # Re-raised unchanged; server.py's envelope layer maps it to a
        # generic internal_error for the caller.
        fail("internal_error")
        raise


def cancel_order(connector: MT5Connector, audit: AuditLogStore, ticket: int) -> dict[str, Any]:
    request = {"ticket": ticket}
    dry_run = safety.is_dry_run()
    ok, fail = _audited(audit, "cancel_order", None, dry_run, request)

    try:
        mt5 = connector.raw
        orders = mt5.orders_get(ticket=ticket)
        if not orders:
            raise OrderError("order_not_found", f"No pending order with ticket {ticket}")

        if dry_run:
            result = {"simulated": True, "ticket": ticket, "status": "simulated_cancel"}
        else:
            send_result = mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": ticket})
            if send_result is None or send_result.retcode != mt5.TRADE_RETCODE_DONE:
                code = send_result.retcode if send_result is not None else None
                raise OrderError("execution_failed", f"order cancel rejected: retcode={code}", retryable=True)
            result = {"simulated": False, "ticket": ticket, "status": "cancelled"}
        return ok(result)
    except OrderError as exc:
        fail(exc.error_code)
        raise
    except Exception as exc:
        # Anything unexpected (order_send() itself raising on a network
        # blip/terminal disconnect, an unanticipated attribute error, ...)
        # must still be audited — an execution attempt that isn't in the
        # audit log is exactly the failure mode this log exists to prevent.
        # Re-raised unchanged; server.py's envelope layer maps it to a
        # generic internal_error for the caller.
        fail("internal_error")
        raise

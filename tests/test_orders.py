from dataclasses import dataclass, field

import pytest

from mt5_mcp.audit_log import AuditLogStore
from mt5_mcp.connector import MT5Connector
from mt5_mcp.orders import OrderError, cancel_order, modify_order, place_order


@dataclass
class _FakeTick:
    bid: float = 4360.0
    ask: float = 4360.5
    last: float = 0.0
    time: int = 1_700_000_000


@dataclass
class _FakeSymbolInfo:
    filling_mode: int = 1  # FOK supported


@dataclass
class _FakeOrder:
    ticket: int
    price_open: float = 4360.0
    sl: float = 0.0
    tp: float = 0.0
    volume_current: float = 0.01


@dataclass
class _FakeSendResult:
    retcode: int
    order: int = 555
    volume: float = 0.01
    price: float = 4360.5
    comment: str = "ok"


class _FakeMT5:
    ORDER_TYPE_BUY = "BUY"
    ORDER_TYPE_SELL = "SELL"
    ORDER_TYPE_BUY_LIMIT = "BUY_LIMIT"
    ORDER_TYPE_SELL_LIMIT = "SELL_LIMIT"
    ORDER_TYPE_BUY_STOP = "BUY_STOP"
    ORDER_TYPE_SELL_STOP = "SELL_STOP"
    TRADE_ACTION_DEAL = "DEAL"
    TRADE_ACTION_PENDING = "PENDING"
    TRADE_ACTION_MODIFY = "MODIFY"
    TRADE_ACTION_REMOVE = "REMOVE"
    ORDER_TIME_GTC = "GTC"
    ORDER_FILLING_FOK = "FOK"
    ORDER_FILLING_IOC = "IOC"
    ORDER_FILLING_RETURN = "RETURN"
    SYMBOL_FILLING_FOK = 1
    SYMBOL_FILLING_IOC = 2
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_REQUOTE = 10004
    TRADE_RETCODE_PRICE_CHANGED = 10020

    def __init__(self):
        self.positions = []
        self.pending_orders: dict[int, _FakeOrder] = {}
        self.symbol_select_result = True
        self.send_retcode = self.TRADE_RETCODE_DONE
        self.order_send_calls: list[dict] = []
        self.raise_on_send = False

    def initialize(self, **kwargs):
        return True

    def shutdown(self):
        pass

    def last_error(self):
        return (1, "no error")

    def symbol_select(self, symbol, enable):
        return self.symbol_select_result

    def symbol_info(self, symbol):
        return _FakeSymbolInfo()

    def symbol_info_tick(self, symbol):
        return _FakeTick()

    def positions_get(self, symbol=None, ticket=None):
        return tuple(self.positions)

    def orders_get(self, ticket=None):
        if ticket is not None:
            order = self.pending_orders.get(ticket)
            return (order,) if order else ()
        return tuple(self.pending_orders.values())

    def order_send(self, request):
        self.order_send_calls.append(request)
        if self.raise_on_send:
            raise ConnectionError("simulated IPC disconnect mid-send")
        return _FakeSendResult(retcode=self.send_retcode)


@pytest.fixture
def connector():
    fake = _FakeMT5()
    c = MT5Connector(mt5_module=fake)
    c.connect()
    return c


@pytest.fixture
def audit(tmp_path):
    return AuditLogStore(tmp_path / "audit.db")


@pytest.fixture(autouse=True)
def dry_run_default(monkeypatch, tmp_path):
    # Every safety env var reset to a known, harmless state per test.
    monkeypatch.delenv("MT5_MCP_DRY_RUN", raising=False)
    monkeypatch.setenv("MT5_MCP_KILL_SWITCH_PATH", str(tmp_path / "no-kill-switch"))
    monkeypatch.setenv("MT5_MCP_MAX_LOT_SIZE", "1.0")
    monkeypatch.setenv("MT5_MCP_MAX_OPEN_POSITIONS", "5")


def test_place_order_dry_run_never_calls_order_send(connector, audit):
    result = place_order(connector, audit, "XAUUSD", "market", "buy", 0.01)

    assert result["simulated"] is True
    assert result["status"] == "simulated"
    assert connector.raw.order_send_calls == []


def test_place_order_dry_run_uses_real_tick_for_market_fill_price(connector, audit):
    result = place_order(connector, audit, "XAUUSD", "market", "buy", 0.01)
    assert result["average_price"] == 4360.5  # ask, since side=buy

    result_sell = place_order(connector, audit, "XAUUSD", "market", "sell", 0.01)
    assert result_sell["average_price"] == 4360.0  # bid


def test_place_order_dry_run_is_audited(connector, audit):
    place_order(connector, audit, "XAUUSD", "market", "buy", 0.01)

    rows = audit.query(action="place_order")
    assert len(rows) == 1
    assert rows[0]["dry_run"] is True
    assert rows[0]["success"] is True


def test_place_order_real_execution_calls_order_send(connector, audit, monkeypatch):
    monkeypatch.setenv("MT5_MCP_DRY_RUN", "false")

    result = place_order(connector, audit, "XAUUSD", "market", "buy", 0.01)

    assert result["simulated"] is False
    assert result["ticket"] == 555
    assert len(connector.raw.order_send_calls) == 1
    sent = connector.raw.order_send_calls[0]
    assert sent["action"] == "DEAL"
    assert sent["type"] == "BUY"
    assert sent["type_filling"] == "FOK"  # symbol supports FOK per _FakeSymbolInfo


def test_place_order_real_execution_rejected_by_broker_raises(connector, audit, monkeypatch):
    monkeypatch.setenv("MT5_MCP_DRY_RUN", "false")
    connector.raw.send_retcode = 10006  # some rejection code, not TRADE_RETCODE_DONE

    with pytest.raises(OrderError) as excinfo:
        place_order(connector, audit, "XAUUSD", "market", "buy", 0.01)
    assert excinfo.value.error_code == "execution_failed"

    rows = audit.query(action="place_order")
    assert rows[0]["success"] is False
    assert rows[0]["error_code"] == "execution_failed"


def test_place_order_blocked_by_kill_switch_even_in_dry_run(connector, audit, tmp_path, monkeypatch):
    switch = tmp_path / "KILL"
    switch.write_text("stop")
    monkeypatch.setenv("MT5_MCP_KILL_SWITCH_PATH", str(switch))

    with pytest.raises(OrderError) as excinfo:
        place_order(connector, audit, "XAUUSD", "market", "buy", 0.01)
    assert excinfo.value.error_code == "kill_switch_active"
    assert connector.raw.order_send_calls == []


def test_place_order_blocked_by_lot_size_limit(connector, audit, monkeypatch):
    monkeypatch.setenv("MT5_MCP_MAX_LOT_SIZE", "0.05")

    with pytest.raises(OrderError) as excinfo:
        place_order(connector, audit, "XAUUSD", "market", "buy", 0.5)
    assert excinfo.value.error_code == "lot_size_exceeds_limit"


def test_place_order_blocked_by_max_open_positions(connector, audit, monkeypatch):
    connector.raw.positions = [object(), object()]
    monkeypatch.setenv("MT5_MCP_MAX_OPEN_POSITIONS", "2")

    with pytest.raises(OrderError) as excinfo:
        place_order(connector, audit, "XAUUSD", "market", "buy", 0.01)
    assert excinfo.value.error_code == "max_open_positions_exceeded"


def test_place_order_limit_requires_price(connector, audit):
    with pytest.raises(OrderError) as excinfo:
        place_order(connector, audit, "XAUUSD", "limit", "buy", 0.01)
    assert excinfo.value.error_code == "invalid_parameter"


def test_place_order_unsupported_order_type_raises(connector, audit):
    with pytest.raises(OrderError) as excinfo:
        place_order(connector, audit, "XAUUSD", "stop_limit", "buy", 0.01, price=4300)
    assert excinfo.value.error_code == "unsupported_order_type" or "Unsupported order_type" in str(excinfo.value)


def test_place_order_invalid_symbol_raises(connector, audit):
    connector.raw.symbol_select_result = False

    with pytest.raises(OrderError) as excinfo:
        place_order(connector, audit, "NOPE", "market", "buy", 0.01)
    assert excinfo.value.error_code == "invalid_symbol"


def test_modify_order_not_found_raises(connector, audit):
    with pytest.raises(OrderError) as excinfo:
        modify_order(connector, audit, ticket=999, price=4300)
    assert excinfo.value.error_code == "order_not_found"


def test_modify_order_dry_run_never_calls_order_send(connector, audit):
    connector.raw.pending_orders[42] = _FakeOrder(ticket=42)

    result = modify_order(connector, audit, ticket=42, price=4350)

    assert result["simulated"] is True
    assert connector.raw.order_send_calls == []


def test_modify_order_kill_switch_does_not_block(connector, audit, tmp_path, monkeypatch):
    switch = tmp_path / "KILL"
    switch.write_text("stop")
    monkeypatch.setenv("MT5_MCP_KILL_SWITCH_PATH", str(switch))
    connector.raw.pending_orders[42] = _FakeOrder(ticket=42)

    result = modify_order(connector, audit, ticket=42, price=4350)  # must not raise

    assert result["simulated"] is True


def test_modify_order_volume_increase_still_checked_against_lot_limit(connector, audit, monkeypatch):
    monkeypatch.setenv("MT5_MCP_MAX_LOT_SIZE", "0.05")
    connector.raw.pending_orders[42] = _FakeOrder(ticket=42)

    with pytest.raises(OrderError) as excinfo:
        modify_order(connector, audit, ticket=42, volume=0.5)
    assert excinfo.value.error_code == "lot_size_exceeds_limit"


def test_modify_order_volume_decrease_never_blocked_by_lot_limit(connector, audit, monkeypatch):
    """A volume *decrease* must never be blocked by the lot-size limit,
    even if the requested (lower) volume still exceeds today's configured
    max — e.g. a legacy order placed under a since-lowered cap. Blocking
    a risk-reducing modify would contradict the same reasoning that keeps
    the kill-switch off this function."""
    monkeypatch.setenv("MT5_MCP_MAX_LOT_SIZE", "0.05")
    connector.raw.pending_orders[42] = _FakeOrder(ticket=42, volume_current=0.5)

    result = modify_order(connector, audit, ticket=42, volume=0.2)  # 0.2 < 0.5, a decrease, but still > 0.05 limit

    assert result["simulated"] is True
    assert result["volume"] == 0.2


def test_cancel_order_not_found_raises(connector, audit):
    with pytest.raises(OrderError) as excinfo:
        cancel_order(connector, audit, ticket=999)
    assert excinfo.value.error_code == "order_not_found"


def test_cancel_order_dry_run_never_calls_order_send(connector, audit):
    connector.raw.pending_orders[42] = _FakeOrder(ticket=42)

    result = cancel_order(connector, audit, ticket=42)

    assert result["simulated"] is True
    assert connector.raw.order_send_calls == []


def test_cancel_order_kill_switch_does_not_block(connector, audit, tmp_path, monkeypatch):
    switch = tmp_path / "KILL"
    switch.write_text("stop")
    monkeypatch.setenv("MT5_MCP_KILL_SWITCH_PATH", str(switch))
    connector.raw.pending_orders[42] = _FakeOrder(ticket=42)

    result = cancel_order(connector, audit, ticket=42)  # must not raise
    assert result["simulated"] is True


def test_cancel_order_real_execution(connector, audit, monkeypatch):
    monkeypatch.setenv("MT5_MCP_DRY_RUN", "false")
    connector.raw.pending_orders[42] = _FakeOrder(ticket=42)

    result = cancel_order(connector, audit, ticket=42)

    assert result["simulated"] is False
    assert result["status"] == "cancelled"
    assert connector.raw.order_send_calls[0]["action"] == "REMOVE"


# --- audit log must capture EVERY attempt, including unexpected exceptions
# (not just the domain-typed OrderError/SafetyError paths) — regression
# tests for a real gap an independent review caught in the first version ---


def test_place_order_unexpected_exception_is_still_audited(connector, audit, monkeypatch):
    monkeypatch.setenv("MT5_MCP_DRY_RUN", "false")
    connector.raw.raise_on_send = True

    with pytest.raises(ConnectionError):
        place_order(connector, audit, "XAUUSD", "market", "buy", 0.01)

    rows = audit.query(action="place_order")
    assert len(rows) == 1
    assert rows[0]["success"] is False
    assert rows[0]["error_code"] == "internal_error"


def test_modify_order_unexpected_exception_is_still_audited(connector, audit, monkeypatch):
    monkeypatch.setenv("MT5_MCP_DRY_RUN", "false")
    connector.raw.pending_orders[42] = _FakeOrder(ticket=42)
    connector.raw.raise_on_send = True

    with pytest.raises(ConnectionError):
        modify_order(connector, audit, ticket=42, price=4350)

    rows = audit.query(action="modify_order")
    assert len(rows) == 1
    assert rows[0]["success"] is False
    assert rows[0]["error_code"] == "internal_error"


def test_cancel_order_unexpected_exception_is_still_audited(connector, audit, monkeypatch):
    monkeypatch.setenv("MT5_MCP_DRY_RUN", "false")
    connector.raw.pending_orders[42] = _FakeOrder(ticket=42)
    connector.raw.raise_on_send = True

    with pytest.raises(ConnectionError):
        cancel_order(connector, audit, ticket=42)

    rows = audit.query(action="cancel_order")
    assert len(rows) == 1
    assert rows[0]["success"] is False
    assert rows[0]["error_code"] == "internal_error"

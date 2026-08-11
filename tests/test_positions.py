from dataclasses import dataclass

import pytest

from mt5_mcp.audit_log import AuditLogStore
from mt5_mcp.connector import MT5Connector
from mt5_mcp.positions import PositionError, close_all_positions, close_position, get_open_positions, modify_position


@dataclass
class _FakeTick:
    bid: float = 4360.0
    ask: float = 4360.5


@dataclass
class _FakePosition:
    ticket: int
    symbol: str = "XAUUSD"
    type: int = 0  # 0 = buy, 1 = sell
    volume: float = 0.01
    price_open: float = 4355.0
    price_current: float = 4360.0
    sl: float = 0.0
    tp: float = 0.0
    profit: float = 5.0
    swap: float = -0.5
    comment: str = ""
    magic: int = 0
    time: int = 1_700_000_000


@dataclass
class _FakeSymbolInfo:
    filling_mode: int = 1


@dataclass
class _FakeSendResult:
    retcode: int
    volume: float = 0.01
    price: float = 4360.0


class _FakeMT5:
    ORDER_TYPE_BUY = "BUY"
    ORDER_TYPE_SELL = "SELL"
    TRADE_ACTION_DEAL = "DEAL"
    TRADE_ACTION_SLTP = "SLTP"
    ORDER_TIME_GTC = "GTC"
    ORDER_FILLING_FOK = "FOK"
    SYMBOL_FILLING_FOK = 1
    SYMBOL_FILLING_IOC = 2
    TRADE_RETCODE_DONE = 10009

    def __init__(self):
        self.positions: list[_FakePosition] = []
        self.send_retcode = self.TRADE_RETCODE_DONE
        self.order_send_calls: list[dict] = []

    def initialize(self, **kwargs):
        return True

    def shutdown(self):
        pass

    def last_error(self):
        return (1, "no error")

    def symbol_info(self, symbol):
        return _FakeSymbolInfo()

    def symbol_info_tick(self, symbol):
        return _FakeTick()

    def positions_get(self, symbol=None, ticket=None):
        results = self.positions
        if symbol is not None:
            results = [p for p in results if p.symbol == symbol]
        if ticket is not None:
            results = [p for p in results if p.ticket == ticket]
        return tuple(results)

    def order_send(self, request):
        self.order_send_calls.append(request)
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
def clean_safety_env(monkeypatch, tmp_path):
    monkeypatch.delenv("MT5_MCP_DRY_RUN", raising=False)
    monkeypatch.setenv("MT5_MCP_KILL_SWITCH_PATH", str(tmp_path / "no-kill-switch"))


def test_get_open_positions_returns_all(connector):
    connector.raw.positions = [_FakePosition(ticket=1), _FakePosition(ticket=2, symbol="EURUSD")]

    result = get_open_positions(connector)

    assert len(result) == 2
    assert {p["ticket"] for p in result} == {1, 2}
    assert result[0]["side"] == "buy"


def test_get_open_positions_filters_by_symbol(connector):
    connector.raw.positions = [_FakePosition(ticket=1, symbol="XAUUSD"), _FakePosition(ticket=2, symbol="EURUSD")]

    result = get_open_positions(connector, symbol="EURUSD")

    assert len(result) == 1
    assert result[0]["symbol"] == "EURUSD"


def test_get_open_positions_filters_by_magic_and_comment(connector):
    connector.raw.positions = [
        _FakePosition(ticket=1, magic=42, comment="agent-a"),
        _FakePosition(ticket=2, magic=99, comment="agent-b"),
    ]

    by_magic = get_open_positions(connector, magic_number=42)
    by_comment = get_open_positions(connector, comment="agent-b")

    assert [p["ticket"] for p in by_magic] == [1]
    assert [p["ticket"] for p in by_comment] == [2]


def test_get_open_positions_empty(connector):
    assert get_open_positions(connector) == []


def test_modify_position_not_found_raises(connector, audit):
    with pytest.raises(PositionError) as excinfo:
        modify_position(connector, audit, ticket=999, stop_loss=4300)
    assert excinfo.value.error_code == "position_not_found"


def test_modify_position_dry_run_never_calls_order_send(connector, audit):
    connector.raw.positions = [_FakePosition(ticket=1)]

    result = modify_position(connector, audit, ticket=1, stop_loss=4340)

    assert result["simulated"] is True
    assert connector.raw.order_send_calls == []


def test_modify_position_kill_switch_does_not_block(connector, audit, tmp_path, monkeypatch):
    switch = tmp_path / "KILL"
    switch.write_text("stop")
    monkeypatch.setenv("MT5_MCP_KILL_SWITCH_PATH", str(switch))
    connector.raw.positions = [_FakePosition(ticket=1)]

    result = modify_position(connector, audit, ticket=1, stop_loss=4340)  # must not raise
    assert result["simulated"] is True


def test_modify_position_real_execution(connector, audit, monkeypatch):
    monkeypatch.setenv("MT5_MCP_DRY_RUN", "false")
    connector.raw.positions = [_FakePosition(ticket=1)]

    result = modify_position(connector, audit, ticket=1, stop_loss=4340, take_profit=4400)

    assert result["simulated"] is False
    assert connector.raw.order_send_calls[0]["action"] == "SLTP"
    assert connector.raw.order_send_calls[0]["sl"] == 4340
    assert connector.raw.order_send_calls[0]["tp"] == 4400


def test_close_position_not_found_raises(connector, audit):
    with pytest.raises(PositionError) as excinfo:
        close_position(connector, audit, ticket=999)
    assert excinfo.value.error_code == "position_not_found"


def test_close_position_dry_run_never_calls_order_send(connector, audit):
    connector.raw.positions = [_FakePosition(ticket=1, volume=0.02)]

    result = close_position(connector, audit, ticket=1)

    assert result["simulated"] is True
    assert result["closed_volume"] == 0.02
    assert connector.raw.order_send_calls == []


def test_close_position_kill_switch_does_not_block(connector, audit, tmp_path, monkeypatch):
    switch = tmp_path / "KILL"
    switch.write_text("stop")
    monkeypatch.setenv("MT5_MCP_KILL_SWITCH_PATH", str(switch))
    connector.raw.positions = [_FakePosition(ticket=1)]

    result = close_position(connector, audit, ticket=1)  # must not raise
    assert result["simulated"] is True


def test_close_position_partial_volume(connector, audit):
    connector.raw.positions = [_FakePosition(ticket=1, volume=0.05)]

    result = close_position(connector, audit, ticket=1, volume=0.02)

    assert result["closed_volume"] == 0.02


def test_close_position_over_volume_rejected(connector, audit):
    connector.raw.positions = [_FakePosition(ticket=1, volume=0.02)]

    with pytest.raises(PositionError) as excinfo:
        close_position(connector, audit, ticket=1, volume=0.5)
    assert excinfo.value.error_code == "invalid_parameter"


def test_close_position_real_execution_uses_opposite_side(connector, audit, monkeypatch):
    monkeypatch.setenv("MT5_MCP_DRY_RUN", "false")
    connector.raw.positions = [_FakePosition(ticket=1, type=0)]  # buy position -> close needs a sell

    result = close_position(connector, audit, ticket=1)

    assert result["simulated"] is False
    assert connector.raw.order_send_calls[0]["type"] == "SELL"
    assert connector.raw.order_send_calls[0]["position"] == 1


def test_close_all_positions_closes_every_match(connector, audit):
    connector.raw.positions = [_FakePosition(ticket=1), _FakePosition(ticket=2), _FakePosition(ticket=3, symbol="EURUSD")]

    result = close_all_positions(connector, audit, symbol="XAUUSD")

    assert result["requested_count"] == 2
    assert {c["ticket"] for c in result["closed"]} == {1, 2}
    assert result["failed"] == []


def test_close_all_positions_filters_by_side(connector, audit):
    connector.raw.positions = [
        _FakePosition(ticket=1, type=0),  # buy
        _FakePosition(ticket=2, type=1),  # sell
    ]

    result = close_all_positions(connector, audit, side="sell")

    assert result["requested_count"] == 1
    assert result["closed"][0]["ticket"] == 2


def test_close_all_positions_none_open_returns_empty(connector, audit):
    result = close_all_positions(connector, audit)
    assert result == {"closed": [], "failed": [], "requested_count": 0}


def test_every_position_action_is_audited(connector, audit):
    connector.raw.positions = [_FakePosition(ticket=1)]
    modify_position(connector, audit, ticket=1, stop_loss=4340)
    connector.raw.positions = [_FakePosition(ticket=1)]
    close_position(connector, audit, ticket=1)

    rows = audit.query()
    actions = {r["action"] for r in rows}
    assert actions == {"modify_position", "close_position"}
    assert all(r["success"] for r in rows)

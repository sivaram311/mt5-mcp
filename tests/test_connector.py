from dataclasses import dataclass

import pytest

from mt5_mcp.connector import AccountInfo, MT5ConnectionError, MT5Connector, TerminalInfo


@dataclass
class _FakeTerminalInfo:
    name: str = "Octa Markets MetaTrader 5"
    connected: bool = True
    trade_allowed: bool = True


@dataclass
class _FakeAccountInfo:
    login: int = 213878432
    server: str = "OctaFX-Demo"
    balance: float = 100.0
    currency: str = "USD"


class _FakeMT5:
    """Stand-in for the MetaTrader5 module — no real terminal required."""

    def __init__(self, initialize_result: bool = True, error: tuple[int, str] = (1, "no error")):
        self._initialize_result = initialize_result
        self._error = error
        self._terminal_info = _FakeTerminalInfo()
        self._account_info: _FakeAccountInfo | None = _FakeAccountInfo()
        self.initialize_calls: list[dict] = []
        self.shutdown_calls = 0

    def initialize(self, **kwargs):
        self.initialize_calls.append(kwargs)
        return self._initialize_result

    def shutdown(self):
        self.shutdown_calls += 1

    def last_error(self):
        return self._error

    def terminal_info(self):
        return self._terminal_info

    def account_info(self):
        return self._account_info


def test_connect_success_passes_path():
    fake = _FakeMT5()
    connector = MT5Connector(path=r"E:\ProgramFiles\MT5\terminal64.exe", mt5_module=fake)

    connector.connect()

    assert connector.is_connected() is True
    assert fake.initialize_calls == [{"path": r"E:\ProgramFiles\MT5\terminal64.exe"}]


def test_connect_without_path_omits_kwarg():
    fake = _FakeMT5()
    connector = MT5Connector(mt5_module=fake)

    connector.connect()

    assert fake.initialize_calls == [{}]


def test_connect_failure_raises_with_mt5_error():
    fake = _FakeMT5(initialize_result=False, error=(10013, "IPC connect failed"))
    connector = MT5Connector(mt5_module=fake)

    with pytest.raises(MT5ConnectionError) as excinfo:
        connector.connect()

    assert connector.is_connected() is False
    assert excinfo.value.mt5_error == (10013, "IPC connect failed")


def test_disconnect_calls_shutdown_only_if_connected():
    fake = _FakeMT5()
    connector = MT5Connector(mt5_module=fake)

    connector.disconnect()
    assert fake.shutdown_calls == 0

    connector.connect()
    connector.disconnect()
    assert fake.shutdown_calls == 1
    assert connector.is_connected() is False


def test_terminal_info_before_connect_raises():
    connector = MT5Connector(mt5_module=_FakeMT5())

    with pytest.raises(MT5ConnectionError, match="Not connected"):
        connector.terminal_info()


def test_account_info_before_connect_raises():
    connector = MT5Connector(mt5_module=_FakeMT5())

    with pytest.raises(MT5ConnectionError, match="Not connected"):
        connector.account_info()


def test_terminal_info_maps_fields():
    connector = MT5Connector(mt5_module=_FakeMT5())
    connector.connect()

    info = connector.terminal_info()

    assert info == TerminalInfo(name="Octa Markets MetaTrader 5", connected=True, trade_allowed=True)


def test_account_info_maps_fields():
    connector = MT5Connector(mt5_module=_FakeMT5())
    connector.connect()

    info = connector.account_info()

    assert info == AccountInfo(login=213878432, server="OctaFX-Demo", balance=100.0, currency="USD")


def test_account_info_none_when_not_logged_in():
    fake = _FakeMT5()
    fake._account_info = None
    connector = MT5Connector(mt5_module=fake)
    connector.connect()

    assert connector.account_info() is None


def test_terminal_info_none_raises():
    fake = _FakeMT5()
    fake._terminal_info = None
    fake._error = (1, "terminal not ready")
    connector = MT5Connector(mt5_module=fake)
    connector.connect()

    with pytest.raises(MT5ConnectionError, match="terminal_info"):
        connector.terminal_info()


def test_context_manager_connects_and_disconnects():
    fake = _FakeMT5()

    with MT5Connector(mt5_module=fake) as connector:
        assert connector.is_connected() is True

    assert fake.shutdown_calls == 1

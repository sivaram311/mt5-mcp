from datetime import datetime, timedelta, timezone

import pytest

from mt5_mcp.connector import MT5Connector
from mt5_mcp.market_data import MarketDataError, get_historical_ohlcv, get_symbol_info


def _rate(time: int, o: float, h: float, l: float, c: float, tick_volume=10, spread=2, real_volume=0):
    return {
        "time": time,
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "tick_volume": tick_volume,
        "spread": spread,
        "real_volume": real_volume,
    }


class _FakeSymbolInfo:
    def __init__(self):
        self.digits = 2
        self.trade_contract_size = 100.0
        self.trade_tick_size = 0.01
        self.trade_tick_value = 1.0
        self.trade_mode = 0
        self.volume_min = 0.01
        self.volume_max = 100.0
        self.volume_step = 0.01
        self.swap_long = -5.0
        self.swap_short = -2.0
        self.margin_initial = 1000.0
        self.bid = 4035.5
        self.ask = 4035.9


class _FakeTick:
    def __init__(self):
        self.bid = 4035.7
        self.ask = 4036.1


class _FakeMT5:
    TIMEFRAME_M1 = "TF_M1"
    TIMEFRAME_M5 = "TF_M5"
    TIMEFRAME_H1 = "TF_H1"
    TIMEFRAME_D1 = "TF_D1"

    def __init__(self):
        self.symbol_select_result = True
        self.rates = [_rate(1_700_000_000 + i * 60, 100 + i, 101 + i, 99 + i, 100.5 + i) for i in range(5)]
        self.symbol_info_result: _FakeSymbolInfo | None = _FakeSymbolInfo()
        self.symbol_info_tick_result: _FakeTick | None = _FakeTick()
        self.copy_rates_from_pos_calls: list[tuple] = []
        self.copy_rates_range_calls: list[tuple] = []
        self._error = (1, "no error")

    def initialize(self, **kwargs):
        return True

    def shutdown(self):
        pass

    def symbol_select(self, symbol, enable):
        return self.symbol_select_result

    def last_error(self):
        return self._error

    def copy_rates_from_pos(self, symbol, tf, start_pos, count):
        self.copy_rates_from_pos_calls.append((symbol, tf, start_pos, count))
        return self.rates[start_pos : start_pos + count]

    def copy_rates_range(self, symbol, tf, date_from, date_to):
        self.copy_rates_range_calls.append((symbol, tf, date_from, date_to))
        return self.rates

    def symbol_info(self, symbol):
        return self.symbol_info_result

    def symbol_info_tick(self, symbol):
        return self.symbol_info_tick_result


@pytest.fixture
def connector():
    fake = _FakeMT5()
    c = MT5Connector(mt5_module=fake)
    c.connect()
    return c


@pytest.fixture
def fake(connector):
    return connector.raw


def test_default_limit_uses_copy_rates_from_pos(connector, fake):
    candles = get_historical_ohlcv(connector, "XAUUSD", "M1", only_completed_bars=False)

    assert fake.copy_rates_from_pos_calls == [("XAUUSD", "TF_M1", 0, 100)]
    assert len(candles) == 5
    assert candles[0]["open"] == 100


def test_explicit_limit_passed_through(connector, fake):
    get_historical_ohlcv(connector, "XAUUSD", "M1", limit=3, only_completed_bars=False)

    assert fake.copy_rates_from_pos_calls == [("XAUUSD", "TF_M1", 0, 3)]


def test_from_bar_to_bar_computes_count(connector, fake):
    get_historical_ohlcv(connector, "XAUUSD", "M1", from_bar=1, to_bar=3, only_completed_bars=False)

    assert fake.copy_rates_from_pos_calls == [("XAUUSD", "TF_M1", 1, 3)]


def test_date_range_uses_copy_rates_range(connector, fake):
    get_historical_ohlcv(
        connector, "XAUUSD", "M1", from_date="2023-11-14T00:00:00Z", to_date="2023-11-15T00:00:00Z",
        only_completed_bars=False,
    )

    assert len(fake.copy_rates_range_calls) == 1
    symbol, tf, start, end = fake.copy_rates_range_calls[0]
    assert symbol == "XAUUSD"
    assert start == datetime(2023, 11, 14, tzinfo=timezone.utc)
    assert end == datetime(2023, 11, 15, tzinfo=timezone.utc)


def test_unix_timestamp_date_accepted(connector, fake):
    get_historical_ohlcv(connector, "XAUUSD", "M1", from_date=1_700_000_000, only_completed_bars=False)

    _, _, start, _ = fake.copy_rates_range_calls[0]
    assert start == datetime.fromtimestamp(1_700_000_000, tz=timezone.utc)


def test_include_volume_and_spread_toggle_fields(connector, fake):
    without = get_historical_ohlcv(connector, "XAUUSD", "M1", limit=1, only_completed_bars=False)
    assert "tick_volume" not in without[0]
    assert "spread" not in without[0]

    with_both = get_historical_ohlcv(
        connector, "XAUUSD", "M1", limit=1, include_volume=True, include_spread=True,
        only_completed_bars=False,
    )
    assert with_both[0]["tick_volume"] == 10
    assert with_both[0]["real_volume"] == 0
    assert with_both[0]["spread"] == 2


def test_only_completed_bars_drops_forming_bar(connector, fake):
    now = datetime.now(timezone.utc)
    completed_time = int((now - timedelta(minutes=5)).timestamp())
    forming_time = int(now.timestamp()) - (int(now.timestamp()) % 60)  # current minute, still forming
    fake.rates = [
        _rate(completed_time, 1, 2, 0, 1.5),
        _rate(forming_time, 1, 2, 0, 1.5),
    ]

    candles = get_historical_ohlcv(connector, "XAUUSD", "M1", limit=2, only_completed_bars=True)

    assert len(candles) == 1


def test_only_completed_bars_false_keeps_all(connector, fake):
    now = datetime.now(timezone.utc)
    forming_time = int(now.timestamp()) - (int(now.timestamp()) % 60)
    fake.rates = [_rate(forming_time, 1, 2, 0, 1.5)]

    candles = get_historical_ohlcv(connector, "XAUUSD", "M1", limit=1, only_completed_bars=False)

    assert len(candles) == 1


def test_session_filter_keeps_only_matching_hours(connector, fake):
    london_time = int(datetime(2023, 11, 14, 10, tzinfo=timezone.utc).timestamp())
    asian_time = int(datetime(2023, 11, 14, 2, tzinfo=timezone.utc).timestamp())
    fake.rates = [_rate(asian_time, 1, 2, 0, 1.5), _rate(london_time, 1, 2, 0, 1.5)]

    candles = get_historical_ohlcv(
        connector, "XAUUSD", "M1", limit=2, session_filter="London", only_completed_bars=False,
    )

    assert len(candles) == 1
    assert candles[0]["time"].startswith("2023-11-14T10")


def test_invalid_session_filter_raises():
    fake = _FakeMT5()
    c = MT5Connector(mt5_module=fake)
    c.connect()

    with pytest.raises(MarketDataError) as excinfo:
        get_historical_ohlcv(c, "XAUUSD", "M1", limit=1, session_filter="Mars", only_completed_bars=False)
    assert excinfo.value.error_code == "invalid_session_filter"


def test_unsupported_price_type_raises():
    fake = _FakeMT5()
    c = MT5Connector(mt5_module=fake)
    c.connect()

    with pytest.raises(MarketDataError) as excinfo:
        get_historical_ohlcv(c, "XAUUSD", "M1", price_type="ask")
    assert excinfo.value.error_code == "unsupported_price_type"


def test_invalid_symbol_raises(connector, fake):
    fake.symbol_select_result = False

    with pytest.raises(MarketDataError) as excinfo:
        get_historical_ohlcv(connector, "NOPE", "M1")
    assert excinfo.value.error_code == "invalid_symbol"


def test_no_data_raises_retryable(connector, fake):
    fake.rates = []

    with pytest.raises(MarketDataError) as excinfo:
        get_historical_ohlcv(connector, "XAUUSD", "M1")

    assert excinfo.value.error_code == "data_unavailable"
    assert excinfo.value.retryable is True


def test_unsupported_timeframe_raises(connector):
    with pytest.raises(ValueError, match="Unsupported timeframe"):
        get_historical_ohlcv(connector, "XAUUSD", "M17")


def test_get_symbol_info_maps_fields(connector):
    info = get_symbol_info(connector, "XAUUSD")

    assert info["symbol"] == "XAUUSD"
    assert info["digits"] == 2
    assert info["contract_size"] == 100.0
    assert info["tick_size"] == 0.01
    assert info["tick_value"] == 1.0
    assert info["bid"] == 4035.7  # from tick, not stale symbol_info snapshot
    assert info["ask"] == 4036.1


def test_get_symbol_info_falls_back_to_symbol_info_bid_ask_without_tick(connector, fake):
    fake.symbol_info_tick_result = None

    info = get_symbol_info(connector, "XAUUSD")

    assert info["bid"] == 4035.5
    assert info["ask"] == 4035.9


def test_get_symbol_info_invalid_symbol_raises(connector, fake):
    fake.symbol_select_result = False

    with pytest.raises(MarketDataError) as excinfo:
        get_symbol_info(connector, "NOPE")
    assert excinfo.value.error_code == "invalid_symbol"


def test_get_symbol_info_none_from_api_raises(connector, fake):
    fake.symbol_info_result = None

    with pytest.raises(MarketDataError) as excinfo:
        get_symbol_info(connector, "XAUUSD")
    assert excinfo.value.error_code == "invalid_symbol"

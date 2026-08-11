import time

import pytest

from mt5_mcp.connector import MT5Connector
from mt5_mcp.stream_log import StreamLogStore
from mt5_mcp.streaming import StreamingError, SubscriptionManager

POLL_INTERVAL = 0.02
SETTLE = POLL_INTERVAL * 6  # generous multiple of the poll interval, not a fixed magic sleep


class _FakeTick:
    def __init__(self, time_, bid, ask):
        self.time = time_
        self.bid = bid
        self.ask = ask
        self.last = 0.0
        self.volume = 1
        self.flags = 0


class _FakeMT5:
    TIMEFRAME_M1 = "TF_M1"

    def __init__(self):
        self._tick_seq = 0
        self._bar_seq = 0
        self.tick_error = False

    def initialize(self, **kwargs):
        return True

    def shutdown(self):
        pass

    def last_error(self):
        return (1, "no error")

    def symbol_info_tick(self, symbol):
        if self.tick_error:
            raise RuntimeError("simulated MT5 hiccup")
        self._tick_seq += 1
        return _FakeTick(self._tick_seq, 4360.0 + self._tick_seq, 4360.5 + self._tick_seq)

    def copy_rates_from_pos(self, symbol, tf, start_pos, count):
        self._bar_seq += 1
        return [
            {
                "time": self._bar_seq,
                "open": 1.0,
                "high": 2.0,
                "low": 0.5,
                "close": 1.5,
                "tick_volume": 10,
                "spread": 2,
                "real_volume": 0,
            }
        ]


@pytest.fixture
def manager(tmp_path):
    connector = MT5Connector(mt5_module=_FakeMT5())
    connector.connect()
    store = StreamLogStore(tmp_path / "stream.db")
    mgr = SubscriptionManager(connector, store, poll_interval=POLL_INTERVAL)
    yield mgr, store
    for sub in mgr.active_subscriptions():
        mgr.unsubscribe(subscription_id=sub["subscription_id"])


def test_subscribe_returns_id_and_starts_writing(manager):
    mgr, store = manager

    sub_id = mgr.subscribe("XAUUSD", ["tick"])
    time.sleep(SETTLE)

    assert sub_id in [s["subscription_id"] for s in mgr.active_subscriptions()]
    rows = store.query("XAUUSD", data_type="tick")
    assert len(rows) >= 1
    assert rows[0]["subscription_id"] == sub_id


def test_unsubscribe_stops_new_writes(manager):
    mgr, store = manager

    sub_id = mgr.subscribe("XAUUSD", ["tick"])
    time.sleep(SETTLE)
    mgr.unsubscribe(subscription_id=sub_id)
    count_after_stop = len(store.query("XAUUSD"))
    time.sleep(SETTLE)

    assert len(store.query("XAUUSD")) == count_after_stop
    assert sub_id not in [s["subscription_id"] for s in mgr.active_subscriptions()]


def test_unsubscribe_by_symbol_stops_all_matching(manager):
    mgr, _store = manager

    id_a = mgr.subscribe("XAUUSD", ["tick"])
    id_b = mgr.subscribe("XAUUSD", ["bar"])
    mgr.subscribe("EURUSD", ["tick"])

    stopped = mgr.unsubscribe(symbol="XAUUSD")

    assert set(stopped) == {id_a, id_b}
    remaining_symbols = {s["symbol"] for s in mgr.active_subscriptions()}
    assert remaining_symbols == {"EURUSD"}


def test_unsubscribe_unknown_id_is_a_noop(manager):
    mgr, _store = manager

    result = mgr.unsubscribe(subscription_id="does-not-exist")

    assert result == []


def test_unsubscribe_requires_id_or_symbol(manager):
    mgr, _store = manager

    with pytest.raises(StreamingError) as excinfo:
        mgr.unsubscribe()
    assert excinfo.value.error_code == "invalid_parameter"


def test_subscribe_unsupported_data_type_raises(manager):
    mgr, _store = manager

    with pytest.raises(StreamingError) as excinfo:
        mgr.subscribe("XAUUSD", ["depth_of_market"])
    assert excinfo.value.error_code == "unsupported_data_type"


def test_multiple_concurrent_subscriptions_write_independently(manager):
    mgr, store = manager

    id_a = mgr.subscribe("XAUUSD", ["tick"])
    id_b = mgr.subscribe("EURUSD", ["tick"])
    time.sleep(SETTLE)

    xau_rows = store.query("XAUUSD")
    eur_rows = store.query("EURUSD")
    assert all(r["subscription_id"] == id_a for r in xau_rows) and len(xau_rows) >= 1
    assert all(r["subscription_id"] == id_b for r in eur_rows) and len(eur_rows) >= 1


def test_duplicate_ticks_are_not_rewritten(manager):
    """The fake advances its own tick counter every call, so this exercises the
    dedup guard indirectly via a connector whose tick genuinely never changes."""

    class _StaticTickMT5(_FakeMT5):
        def symbol_info_tick(self, symbol):
            return _FakeTick(42, 1.0, 1.1)

    connector = MT5Connector(mt5_module=_StaticTickMT5())
    connector.connect()
    store = manager[1]
    mgr = SubscriptionManager(connector, store, poll_interval=POLL_INTERVAL)

    mgr.subscribe("GBPUSD", ["tick"])
    time.sleep(SETTLE)
    mgr.unsubscribe(symbol="GBPUSD")

    rows = store.query("GBPUSD")
    assert len(rows) == 1


def test_unsubscribe_does_not_report_stopped_if_thread_still_alive(tmp_path):
    """Regression test: unsubscribe() must not claim success for a
    subscription whose poll thread is genuinely still running (e.g. blocked
    inside an MT5 call) just because stop_event was set and join() timed
    out. A stuck thread stays registered rather than being silently
    dropped, so a caller can tell the difference between "confirmed
    stopped" and "asked to stop, still waiting"."""

    class _BlockingMT5(_FakeMT5):
        def symbol_info_tick(self, symbol):
            time.sleep(1.0)  # ignores stop_event entirely, simulating a stuck MT5 call
            return super().symbol_info_tick(symbol)

    connector = MT5Connector(mt5_module=_BlockingMT5())
    connector.connect()
    store = StreamLogStore(tmp_path / "blocking.db")
    mgr = SubscriptionManager(
        connector, store, poll_interval=POLL_INTERVAL, unsubscribe_join_timeout=0.05
    )

    sub_id = mgr.subscribe("XAUUSD", ["tick"])
    time.sleep(POLL_INTERVAL)  # let the poll loop actually enter the blocking call

    stopped = mgr.unsubscribe(subscription_id=sub_id)

    assert stopped == []
    assert sub_id in [s["subscription_id"] for s in mgr.active_subscriptions()]


def test_poll_error_does_not_kill_subscription(manager):
    mgr, store = manager
    fake = mgr._connector.raw
    fake.tick_error = True

    sub_id = mgr.subscribe("XAUUSD", ["tick"])
    time.sleep(SETTLE)
    assert store.query("XAUUSD") == []
    assert sub_id in [s["subscription_id"] for s in mgr.active_subscriptions()]

    fake.tick_error = False
    time.sleep(SETTLE)
    assert len(store.query("XAUUSD")) >= 1

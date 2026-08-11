import pytest

from mt5_mcp.stream_log import StreamLogStore


@pytest.fixture
def store(tmp_path):
    return StreamLogStore(tmp_path / "stream_log.db")


def test_append_and_query_round_trip(store):
    store.append("sub-1", "XAUUSD", "tick", "2026-08-11T09:00:00+00:00", {"bid": 4360.1, "ask": 4360.5}, 1)

    rows = store.query("XAUUSD")

    assert len(rows) == 1
    assert rows[0]["symbol"] == "XAUUSD"
    assert rows[0]["data_type"] == "tick"
    assert rows[0]["payload"] == {"bid": 4360.1, "ask": 4360.5}
    assert rows[0]["sequence_number"] == 1
    assert rows[0]["subscription_id"] == "sub-1"


def test_query_filters_by_symbol(store):
    store.append("sub-1", "XAUUSD", "tick", "2026-08-11T09:00:00+00:00", {"bid": 1}, 1)
    store.append("sub-2", "EURUSD", "tick", "2026-08-11T09:00:00+00:00", {"bid": 2}, 1)

    rows = store.query("XAUUSD")

    assert len(rows) == 1
    assert rows[0]["symbol"] == "XAUUSD"


def test_query_filters_by_data_type(store):
    store.append("sub-1", "XAUUSD", "tick", "2026-08-11T09:00:00+00:00", {"bid": 1}, 1)
    store.append("sub-1", "XAUUSD", "bar", "2026-08-11T09:00:00+00:00", {"open": 1}, 2)

    ticks = store.query("XAUUSD", data_type="tick")
    bars = store.query("XAUUSD", data_type="bar")

    assert len(ticks) == 1 and ticks[0]["data_type"] == "tick"
    assert len(bars) == 1 and bars[0]["data_type"] == "bar"


def test_query_time_range(store):
    store.append("sub-1", "XAUUSD", "tick", "2026-08-11T09:00:00+00:00", {"i": 0}, 1)
    store.append("sub-1", "XAUUSD", "tick", "2026-08-11T09:05:00+00:00", {"i": 1}, 2)
    store.append("sub-1", "XAUUSD", "tick", "2026-08-11T09:10:00+00:00", {"i": 2}, 3)

    rows = store.query("XAUUSD", from_time="2026-08-11T09:03:00+00:00", to_time="2026-08-11T09:07:00+00:00")

    assert len(rows) == 1
    assert rows[0]["payload"] == {"i": 1}


def test_query_orders_by_timestamp_then_sequence(store):
    store.append("sub-1", "XAUUSD", "tick", "2026-08-11T09:00:02+00:00", {"i": 1}, 5)
    store.append("sub-1", "XAUUSD", "tick", "2026-08-11T09:00:01+00:00", {"i": 0}, 3)

    rows = store.query("XAUUSD")

    assert [r["payload"]["i"] for r in rows] == [0, 1]


def test_query_respects_limit(store):
    for i in range(5):
        store.append("sub-1", "XAUUSD", "tick", f"2026-08-11T09:00:0{i}+00:00", {"i": i}, i)

    rows = store.query("XAUUSD", limit=2)

    assert len(rows) == 2
    assert [r["payload"]["i"] for r in rows] == [0, 1]


def test_query_no_data_returns_empty_list(store):
    assert store.query("NOPE") == []


def test_store_reopens_existing_db_without_error(tmp_path):
    path = tmp_path / "reopen.db"
    StreamLogStore(path).append("sub-1", "XAUUSD", "tick", "2026-08-11T09:00:00+00:00", {"i": 0}, 1)

    reopened = StreamLogStore(path)

    assert len(reopened.query("XAUUSD")) == 1

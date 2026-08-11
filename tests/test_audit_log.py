import pytest

from mt5_mcp.audit_log import AuditLogStore


@pytest.fixture
def store(tmp_path):
    return AuditLogStore(tmp_path / "audit.db")


def test_record_and_query_round_trip(store):
    store.record(
        "2026-08-11T10:00:00+00:00",
        "place_order",
        "XAUUSD",
        dry_run=True,
        request={"symbol": "XAUUSD", "volume": 0.01},
        success=True,
        result={"ticket": 123, "simulated": True},
    )

    rows = store.query()

    assert len(rows) == 1
    row = rows[0]
    assert row["action"] == "place_order"
    assert row["symbol"] == "XAUUSD"
    assert row["dry_run"] is True
    assert row["request"] == {"symbol": "XAUUSD", "volume": 0.01}
    assert row["success"] is True
    assert row["result"] == {"ticket": 123, "simulated": True}
    assert row["error_code"] is None


def test_record_failure_with_error_code(store):
    store.record(
        "2026-08-11T10:00:00+00:00",
        "place_order",
        "XAUUSD",
        dry_run=False,
        request={"volume": 5.0},
        success=False,
        error_code="lot_size_exceeds_limit",
    )

    row = store.query()[0]
    assert row["success"] is False
    assert row["error_code"] == "lot_size_exceeds_limit"
    assert row["result"] is None


def test_query_filters_by_action(store):
    store.record("t1", "place_order", "XAUUSD", True, {}, True)
    store.record("t2", "close_position", "XAUUSD", True, {}, True)

    rows = store.query(action="close_position")

    assert len(rows) == 1
    assert rows[0]["action"] == "close_position"


def test_query_filters_by_symbol(store):
    store.record("t1", "place_order", "XAUUSD", True, {}, True)
    store.record("t2", "place_order", "EURUSD", True, {}, True)

    rows = store.query(symbol="EURUSD")

    assert len(rows) == 1
    assert rows[0]["symbol"] == "EURUSD"


def test_query_filters_by_time_range(store):
    store.record("2026-08-11T09:00:00+00:00", "place_order", "XAUUSD", True, {}, True)
    store.record("2026-08-11T09:05:00+00:00", "place_order", "XAUUSD", True, {}, True)
    store.record("2026-08-11T09:10:00+00:00", "place_order", "XAUUSD", True, {}, True)

    rows = store.query(from_time="2026-08-11T09:03:00+00:00", to_time="2026-08-11T09:07:00+00:00")

    assert len(rows) == 1
    assert rows[0]["timestamp"] == "2026-08-11T09:05:00+00:00"


def test_query_respects_limit_and_insertion_order(store):
    for i in range(5):
        store.record(f"t{i}", "place_order", "XAUUSD", True, {"i": i}, True)

    rows = store.query(limit=2)

    assert len(rows) == 2
    assert [r["request"]["i"] for r in rows] == [0, 1]


def test_query_empty_store_returns_empty_list(store):
    assert store.query() == []


def test_every_dry_run_attempt_is_logged_even_on_failure(store):
    """The audit log must record every attempt, not just successful ones —
    this is the property the safety layer depends on."""
    store.record("t1", "place_order", "XAUUSD", dry_run=True, request={"volume": 99}, success=False, error_code="lot_size_exceeds_limit")

    rows = store.query()
    assert len(rows) == 1
    assert rows[0]["success"] is False

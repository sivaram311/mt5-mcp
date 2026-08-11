import pytest

from mt5_mcp.safety import (
    DEFAULT_MAX_LOT_ENV_VAR,
    DEFAULT_MAX_OPEN_POSITIONS_ENV_VAR,
    DRY_RUN_ENV_VAR,
    KILL_SWITCH_ENV_VAR,
    SafetyError,
    check_before_execution,
    check_kill_switch,
    check_lot_size,
    check_open_position_limit,
    is_dry_run,
    kill_switch_active,
    max_lot_size,
    max_open_positions,
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in (DRY_RUN_ENV_VAR, KILL_SWITCH_ENV_VAR, DEFAULT_MAX_LOT_ENV_VAR, DEFAULT_MAX_OPEN_POSITIONS_ENV_VAR):
        monkeypatch.delenv(var, raising=False)


def test_dry_run_defaults_to_true():
    assert is_dry_run() is True


def test_dry_run_false_disables_it(monkeypatch):
    monkeypatch.setenv(DRY_RUN_ENV_VAR, "false")
    assert is_dry_run() is False


@pytest.mark.parametrize("value", ["False", "FALSE", "  false  "])
def test_dry_run_false_is_case_and_whitespace_insensitive(monkeypatch, value):
    monkeypatch.setenv(DRY_RUN_ENV_VAR, value)
    assert is_dry_run() is False


@pytest.mark.parametrize("value", ["", "no", "0", "true", "TRUE", "off"])
def test_dry_run_anything_but_exact_false_fails_safe(monkeypatch, value):
    monkeypatch.setenv(DRY_RUN_ENV_VAR, value)
    assert is_dry_run() is True


def test_kill_switch_inactive_when_file_absent(tmp_path, monkeypatch):
    monkeypatch.setenv(KILL_SWITCH_ENV_VAR, str(tmp_path / "does-not-exist"))
    assert kill_switch_active() is False


def test_kill_switch_active_when_file_present(tmp_path, monkeypatch):
    switch = tmp_path / "KILL"
    switch.write_text("stop")
    monkeypatch.setenv(KILL_SWITCH_ENV_VAR, str(switch))
    assert kill_switch_active() is True


def test_max_lot_size_default():
    assert max_lot_size() == 0.10


def test_max_lot_size_override(monkeypatch):
    monkeypatch.setenv(DEFAULT_MAX_LOT_ENV_VAR, "0.5")
    assert max_lot_size() == 0.5


def test_max_open_positions_default():
    assert max_open_positions() == 3


def test_max_open_positions_override(monkeypatch):
    monkeypatch.setenv(DEFAULT_MAX_OPEN_POSITIONS_ENV_VAR, "10")
    assert max_open_positions() == 10


def test_check_before_execution_passes_within_limits(tmp_path, monkeypatch):
    monkeypatch.setenv(KILL_SWITCH_ENV_VAR, str(tmp_path / "absent"))
    check_before_execution(volume=0.05, current_open_position_count=0)  # must not raise


def test_check_before_execution_blocked_by_kill_switch(tmp_path, monkeypatch):
    switch = tmp_path / "KILL"
    switch.write_text("stop")
    monkeypatch.setenv(KILL_SWITCH_ENV_VAR, str(switch))

    with pytest.raises(SafetyError) as excinfo:
        check_before_execution(volume=0.01, current_open_position_count=0)
    assert excinfo.value.error_code == "kill_switch_active"


def test_check_before_execution_kill_switch_wins_even_over_valid_volume(tmp_path, monkeypatch):
    """Kill-switch must block regardless of dry-run or how small the request is."""
    switch = tmp_path / "KILL"
    switch.write_text("stop")
    monkeypatch.setenv(KILL_SWITCH_ENV_VAR, str(switch))
    monkeypatch.setenv(DRY_RUN_ENV_VAR, "false")

    with pytest.raises(SafetyError) as excinfo:
        check_before_execution(volume=0.01, current_open_position_count=0)
    assert excinfo.value.error_code == "kill_switch_active"


def test_check_before_execution_rejects_over_max_lot(tmp_path, monkeypatch):
    monkeypatch.setenv(KILL_SWITCH_ENV_VAR, str(tmp_path / "absent"))
    monkeypatch.setenv(DEFAULT_MAX_LOT_ENV_VAR, "0.1")

    with pytest.raises(SafetyError) as excinfo:
        check_before_execution(volume=0.11, current_open_position_count=0)
    assert excinfo.value.error_code == "lot_size_exceeds_limit"


def test_check_before_execution_allows_exactly_max_lot(tmp_path, monkeypatch):
    monkeypatch.setenv(KILL_SWITCH_ENV_VAR, str(tmp_path / "absent"))
    monkeypatch.setenv(DEFAULT_MAX_LOT_ENV_VAR, "0.1")
    check_before_execution(volume=0.1, current_open_position_count=0)  # must not raise


def test_check_before_execution_rejects_at_max_open_positions(tmp_path, monkeypatch):
    monkeypatch.setenv(KILL_SWITCH_ENV_VAR, str(tmp_path / "absent"))
    monkeypatch.setenv(DEFAULT_MAX_OPEN_POSITIONS_ENV_VAR, "2")

    with pytest.raises(SafetyError) as excinfo:
        check_before_execution(volume=0.01, current_open_position_count=2)
    assert excinfo.value.error_code == "max_open_positions_exceeded"


def test_check_before_execution_allows_below_max_open_positions(tmp_path, monkeypatch):
    monkeypatch.setenv(KILL_SWITCH_ENV_VAR, str(tmp_path / "absent"))
    monkeypatch.setenv(DEFAULT_MAX_OPEN_POSITIONS_ENV_VAR, "2")
    check_before_execution(volume=0.01, current_open_position_count=1)  # must not raise


# --- individual composable checks, used directly by risk-reducing actions
# (modify_order/cancel_order/etc — see orders.py/positions.py docstrings for
# why those don't go through the kill-switch via check_before_execution) ---


def test_check_kill_switch_raises_when_active(tmp_path, monkeypatch):
    switch = tmp_path / "KILL"
    switch.write_text("stop")
    monkeypatch.setenv(KILL_SWITCH_ENV_VAR, str(switch))

    with pytest.raises(SafetyError) as excinfo:
        check_kill_switch()
    assert excinfo.value.error_code == "kill_switch_active"


def test_check_kill_switch_passes_when_inactive(tmp_path, monkeypatch):
    monkeypatch.setenv(KILL_SWITCH_ENV_VAR, str(tmp_path / "absent"))
    check_kill_switch()  # must not raise


def test_check_lot_size_standalone_does_not_check_kill_switch(tmp_path, monkeypatch):
    """check_lot_size must be usable independently of the kill-switch —
    that's the whole point of splitting it out of check_before_execution."""
    switch = tmp_path / "KILL"
    switch.write_text("stop")
    monkeypatch.setenv(KILL_SWITCH_ENV_VAR, str(switch))
    monkeypatch.setenv(DEFAULT_MAX_LOT_ENV_VAR, "1.0")

    check_lot_size(volume=0.5)  # must not raise, even with kill-switch active

    with pytest.raises(SafetyError) as excinfo:
        check_lot_size(volume=1.5)
    assert excinfo.value.error_code == "lot_size_exceeds_limit"


def test_check_open_position_limit_standalone_does_not_check_kill_switch(tmp_path, monkeypatch):
    switch = tmp_path / "KILL"
    switch.write_text("stop")
    monkeypatch.setenv(KILL_SWITCH_ENV_VAR, str(switch))
    monkeypatch.setenv(DEFAULT_MAX_OPEN_POSITIONS_ENV_VAR, "2")

    check_open_position_limit(current_open_position_count=1)  # must not raise

    with pytest.raises(SafetyError) as excinfo:
        check_open_position_limit(current_open_position_count=2)
    assert excinfo.value.error_code == "max_open_positions_exceeded"

"""Mandatory safety layer for order/position execution tools.

See docs/aidlc/INCEPTION.md "Safety layer (mandatory, ships with Bolt 5
— not deferred)". Every execution tool must call `check_before_execution`
before it does anything that could reach MT5's real order_send()/
order_close() APIs, and must go through `is_dry_run()` to decide whether
to simulate or actually execute.

Changes to this file always require explicit human review on merge,
regardless of what's unlocked elsewhere (INCEPTION.md, BOLTS.md Bolt 5).
"""

from __future__ import annotations

import os
from pathlib import Path

# Dry-run is the default. It can only be turned off via this env var —
# deliberately not a tool parameter, so an agent can't flip it mid-
# conversation. Anything other than the exact string "false"
# (case-insensitive) keeps dry-run on, so a typo/unset var fails safe.
DRY_RUN_ENV_VAR = "MT5_MCP_DRY_RUN"

# Kill-switch: presence of this file disables every execution tool
# immediately, independent of the dry-run setting. Repo-root-anchored
# for the same reason as .env/the stream log DB (Path(__file__), not
# CWD-relative — an MCP client can spawn this server from anywhere).
KILL_SWITCH_ENV_VAR = "MT5_MCP_KILL_SWITCH_PATH"
_DEFAULT_KILL_SWITCH_PATH = Path(__file__).resolve().parents[2] / "MT5_MCP_KILL_SWITCH"

DEFAULT_MAX_LOT_ENV_VAR = "MT5_MCP_MAX_LOT_SIZE"
DEFAULT_MAX_LOT = 0.10  # OctaFX-Demo's own volume_min is 0.01; keep the default order of magnitude small
DEFAULT_MAX_OPEN_POSITIONS_ENV_VAR = "MT5_MCP_MAX_OPEN_POSITIONS"
DEFAULT_MAX_OPEN_POSITIONS = 3


class SafetyError(Exception):
    """Carries an error_code + retryable flag through to the tool response envelope."""

    def __init__(self, error_code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable


def is_dry_run() -> bool:
    value = os.getenv(DRY_RUN_ENV_VAR, "true").strip().lower()
    return value != "false"


def kill_switch_path() -> Path:
    override = os.getenv(KILL_SWITCH_ENV_VAR)
    return Path(override) if override else _DEFAULT_KILL_SWITCH_PATH


def kill_switch_active() -> bool:
    return kill_switch_path().exists()


def max_lot_size() -> float:
    return float(os.getenv(DEFAULT_MAX_LOT_ENV_VAR, str(DEFAULT_MAX_LOT)))


def max_open_positions() -> int:
    return int(os.getenv(DEFAULT_MAX_OPEN_POSITIONS_ENV_VAR, str(DEFAULT_MAX_OPEN_POSITIONS)))


def check_kill_switch() -> None:
    if kill_switch_active():
        raise SafetyError(
            "kill_switch_active",
            f"Kill-switch file present at {kill_switch_path()} — all execution tools are disabled.",
        )


def check_lot_size(volume: float) -> None:
    limit = max_lot_size()
    if volume > limit:
        raise SafetyError(
            "lot_size_exceeds_limit",
            f"Requested volume {volume} exceeds the configured max lot size {limit} "
            f"({DEFAULT_MAX_LOT_ENV_VAR}).",
        )


def check_open_position_limit(current_open_position_count: int) -> None:
    open_limit = max_open_positions()
    if current_open_position_count >= open_limit:
        raise SafetyError(
            "max_open_positions_exceeded",
            f"Already at the configured max open positions ({current_open_position_count}/{open_limit}, "
            f"{DEFAULT_MAX_OPEN_POSITIONS_ENV_VAR}) — no new position-opening order allowed.",
        )


def check_before_execution(volume: float, current_open_position_count: int) -> None:
    """Full gate for actions that can create new exposure (place_order):
    kill-switch, then lot size, then open-position count. Does not check
    dry-run — dry-run decides *how* to proceed (simulate vs. real call),
    not *whether* it's allowed to; even a dry-run path calls this first
    so the simulated response reflects what a real attempt would hit.

    Actions that only reduce or maintain exposure (modify_order,
    cancel_order, modify_position, close_position, close_all_positions)
    should call the individual check_* functions directly rather than
    this composite — see orders.py/positions.py module docstrings for
    which checks apply to which action and why.
    """
    check_kill_switch()
    check_lot_size(volume)
    check_open_position_limit(current_open_position_count)

"""Thin wrapper around the MetaTrader5 package.

Mirrors the initialize()/terminal_info()/account_info()/shutdown() pattern
already proven working against the OctaFX-Demo account in an earlier
session (E:\\Source\\mt5_xauusd_price.py). `mt5_module` is injectable so
tests can pass a fake object instead of the real MetaTrader5 package,
which requires a live Windows terminal and can't run in CI.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from types import ModuleType
from typing import Any


class MT5ConnectionError(Exception):
    """Raised when the MT5 terminal can't be reached or hasn't been connected yet."""

    def __init__(self, message: str, mt5_error: tuple[int, str] | None = None) -> None:
        super().__init__(message)
        self.mt5_error = mt5_error


@dataclass(frozen=True)
class TerminalInfo:
    name: str
    connected: bool
    trade_allowed: bool


@dataclass(frozen=True)
class AccountInfo:
    login: int
    server: str
    balance: float
    currency: str


DEFAULT_INIT_TIMEOUT_MS = 10_000


class MT5Connector:
    """Owns one MetaTrader5 terminal connection: connect/disconnect + basic info queries.

    Without an explicit `path` (or `MT5_PATH` env var), `initialize()` falls back to
    MetaTrader5's own terminal auto-discovery, which has been observed on this machine
    to hang indefinitely (not just slowly) rather than fail fast when it can't locate a
    terminal — see docs/aidlc/BOLTS.md Bolt 3 for the full incident writeup. Always set
    MT5_PATH explicitly in real deployments; `timeout_ms` only bounds the documented
    IPC handshake once a terminal is actually found, it does not bound that discovery
    hang.
    """

    def __init__(
        self,
        path: str | None = None,
        mt5_module: ModuleType | Any | None = None,
        timeout_ms: int = DEFAULT_INIT_TIMEOUT_MS,
    ) -> None:
        self._path = path or os.getenv("MT5_PATH")
        self._timeout_ms = timeout_ms
        if mt5_module is None:
            import MetaTrader5 as mt5_module  # imported lazily: package is Windows-only

        self._mt5 = mt5_module
        self._connected = False

    def connect(self) -> None:
        kwargs: dict[str, Any] = {"timeout": self._timeout_ms}
        if self._path:
            kwargs["path"] = self._path
        if not self._mt5.initialize(**kwargs):
            raise MT5ConnectionError(
                "MetaTrader5.initialize() failed",
                mt5_error=self._mt5.last_error(),
            )
        self._connected = True

    def disconnect(self) -> None:
        if self._connected:
            self._mt5.shutdown()
            self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def terminal_info(self) -> TerminalInfo:
        self._require_connected()
        info = self._mt5.terminal_info()
        if info is None:
            raise MT5ConnectionError("terminal_info() returned None", mt5_error=self._mt5.last_error())
        return TerminalInfo(name=info.name, connected=info.connected, trade_allowed=info.trade_allowed)

    @property
    def raw(self) -> Any:
        """Escape hatch to the underlying MetaTrader5 module (or injected fake) for
        APIs not wrapped by this class yet (rates, symbol info, orders, ...).
        Requires an active connection, same as the other query methods."""
        self._require_connected()
        return self._mt5

    def last_error(self) -> tuple[int, str]:
        return self._mt5.last_error()

    def account_info(self) -> AccountInfo | None:
        """Returns None if the terminal is connected but not logged into a trading account."""
        self._require_connected()
        info = self._mt5.account_info()
        if info is None:
            return None
        return AccountInfo(login=info.login, server=info.server, balance=info.balance, currency=info.currency)

    def _require_connected(self) -> None:
        if not self._connected:
            raise MT5ConnectionError("Not connected — call connect() first")

    def __enter__(self) -> MT5Connector:
        self.connect()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.disconnect()

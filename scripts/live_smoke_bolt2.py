"""Manual live smoke test for Bolt 2 (MT5Connector) — not run in automated CI.

Connects to the real MT5 terminal and prints terminal/account info. Run
manually after any change to mt5_mcp.connector to confirm it still works
against a live (demo) terminal, not just the mocked unit tests.

    .venv\\Scripts\\python.exe scripts\\live_smoke_bolt2.py
"""

from __future__ import annotations

import sys

from mt5_mcp.connector import MT5ConnectionError, MT5Connector


def main() -> int:
    try:
        with MT5Connector() as connector:
            terminal = connector.terminal_info()
            print(f"Terminal: {terminal.name}")
            print(f"Connected: {terminal.connected}")
            print(f"Trade allowed: {terminal.trade_allowed}")

            account = connector.account_info()
            if account is None:
                print("Account: not logged in")
            else:
                print(f"Account: {account.login} ({account.server})")
                print(f"Balance: {account.balance} {account.currency}")
    except MT5ConnectionError as exc:
        print(f"FAILED: {exc} (mt5_error={exc.mt5_error})")
        return 1

    print("SUCCESS: MT5Connector connected and returned real terminal/account info.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

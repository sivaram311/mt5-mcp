"""Manual live smoke test for Bolt 5 -- not run in automated CI.

Two phases, through the real MCP client over streamable-http:

1. Dry-run: place_order (market buy XAUUSD, minimum lot), confirms the
   simulated response shape and that get_open_positions shows nothing
   new (dry-run never touches the real account).
2. Real: MT5_MCP_DRY_RUN=false for one real demo-account market order
   (OctaFX-Demo, $100 balance, minimum lot 0.01) -- placed, confirmed
   via get_open_positions, then closed via close_position, confirmed
   gone. This is the one live smoke in this project that actually
   executes a trade (demo account, not real money) -- required by
   docs/aidlc/BOLTS.md Bolt 5's acceptance criteria ("one real
   demo-account market order placed and closed end-to-end").

    .venv\\Scripts\\python.exe scripts\\live_smoke_bolt5.py
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

HOST = "127.0.0.1"
PORT = 3403
URL = f"http://{HOST}:{PORT}/mcp"
STARTUP_TIMEOUT_S = 15
SYMBOL = "XAUUSD"
VOLUME = 0.01


def _wait_for_port() -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT_S
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            if s.connect_ex((HOST, PORT)) == 0:
                return
        time.sleep(0.3)
    raise RuntimeError(f"Server did not start listening on {HOST}:{PORT} within {STARTUP_TIMEOUT_S}s")


def _unwrap(result) -> dict:
    if not result.content:
        raise RuntimeError(f"tool call returned no content blocks: {result!r}")
    return json.loads(result.content[0].text)


async def _call(session: ClientSession, tool: str, args: dict) -> dict:
    result = _unwrap(await asyncio.wait_for(session.call_tool(tool, args), timeout=20))
    print(f"{tool}:", json.dumps(result, indent=2))
    return result


async def run_phase(env: dict, phase_name: str) -> int:
    server_proc = subprocess.Popen(
        [sys.executable, "-u", "-m", "mt5_mcp"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )
    try:
        print(f"\n=== PHASE: {phase_name} ===", flush=True)
        print("waiting for server to bind...", flush=True)
        _wait_for_port()
        print("server listening, connecting real client...", flush=True)

        async with streamablehttp_client(URL) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()

                if phase_name == "dry-run":
                    before = await _call(session, "get_open_positions", {"symbol": SYMBOL})
                    if not before["success"]:
                        return 1
                    before_count = len(before["data"])

                    placed = await _call(
                        session, "place_order",
                        {"symbol": SYMBOL, "order_type": "market", "side": "buy", "volume": VOLUME},
                    )
                    if not placed["success"] or placed["data"]["simulated"] is not True:
                        print("FAILED: dry-run place_order did not return a simulated response")
                        return 1

                    after = await _call(session, "get_open_positions", {"symbol": SYMBOL})
                    if len(after["data"]) != before_count:
                        print("FAILED: dry-run place_order changed real open positions — it must not")
                        return 1

                    print("SUCCESS (dry-run phase): simulated order, no real position opened.")
                    return 0

                else:  # real
                    placed = await _call(
                        session, "place_order",
                        {"symbol": SYMBOL, "order_type": "market", "side": "buy", "volume": VOLUME,
                         "comment": "mt5-mcp-bolt5-live-smoke"},
                    )
                    if not placed["success"] or placed["data"]["simulated"] is not False:
                        print("FAILED: real place_order did not execute for real")
                        return 1
                    ticket = placed["data"]["ticket"]

                    open_positions = await _call(session, "get_open_positions", {"symbol": SYMBOL})
                    tickets = [p["ticket"] for p in open_positions["data"]]
                    if ticket not in tickets:
                        print(f"FAILED: opened ticket {ticket} not found in get_open_positions")
                        return 1

                    closed = await _call(session, "close_position", {"ticket": ticket})
                    if not closed["success"] or closed["data"]["simulated"] is not False:
                        print("FAILED: close_position did not execute for real")
                        return 1

                    after_close = await _call(session, "get_open_positions", {"symbol": SYMBOL})
                    tickets_after = [p["ticket"] for p in after_close["data"]]
                    if ticket in tickets_after:
                        print(f"FAILED: ticket {ticket} still open after close_position")
                        return 1

                    print(f"SUCCESS (real phase): ticket {ticket} placed and closed end-to-end.")
                    return 0
    finally:
        server_proc.terminate()
        try:
            out, _ = server_proc.communicate(timeout=5)
            if out:
                print(f"--- server output ({phase_name}) ---")
                print(out)
        except subprocess.TimeoutExpired:
            server_proc.kill()


async def main() -> int:
    dry_run_env = {**os.environ}
    dry_run_env.pop("MT5_MCP_DRY_RUN", None)  # absent -> defaults to dry-run (safe default)
    rc = await run_phase(dry_run_env, "dry-run")
    if rc != 0:
        return rc

    real_env = {**os.environ, "MT5_MCP_DRY_RUN": "false"}
    return await run_phase(real_env, "real")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

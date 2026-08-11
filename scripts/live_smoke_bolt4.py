"""Manual live smoke test for Bolt 4 -- not run in automated CI.

Spawns the real mt5-mcp server (streamable-http, the default transport
since Bolt 3 -- see diagnostics/FINDINGS.md for why stdio isn't used
here) and, through a real MCP client: subscribes to XAUUSD ticks, waits
a few seconds, reads the durable log back, unsubscribes, then reads the
log again to confirm it's still retrievable after the subscription is
gone -- the "reconstruct the sequence after a disconnect" scenario from
docs/aidlc/SPEC.md sec 7, using unsubscribe as the stand-in disconnect.

    .venv\\Scripts\\python.exe scripts\\live_smoke_bolt4.py
"""

from __future__ import annotations

import asyncio
import json
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
STREAM_WINDOW_S = 5


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


async def main() -> int:
    server_proc = subprocess.Popen(
        [sys.executable, "-u", "-m", "mt5_mcp"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        print("waiting for server to bind...", flush=True)
        _wait_for_port()
        print("server listening, connecting real client...", flush=True)

        async with streamablehttp_client(URL) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()

                sub_result = _unwrap(
                    await asyncio.wait_for(
                        session.call_tool("subscribe_live_data", {"symbol": "XAUUSD", "data_types": ["tick"]}),
                        timeout=15,
                    )
                )
                print("subscribe_live_data:", json.dumps(sub_result, indent=2))
                if not sub_result["success"]:
                    print("FAILED: subscribe_live_data did not succeed")
                    return 1
                subscription_id = sub_result["data"]["subscription_id"]

                print(f"streaming for {STREAM_WINDOW_S}s...", flush=True)
                await asyncio.sleep(STREAM_WINDOW_S)

                log_result = _unwrap(
                    await asyncio.wait_for(
                        session.call_tool("get_stream_log", {"symbol": "XAUUSD", "data_type": "tick"}),
                        timeout=15,
                    )
                )
                entries_while_subscribed = log_result["data"]
                print(f"get_stream_log while subscribed: {len(entries_while_subscribed)} entries")
                if not log_result["success"] or len(entries_while_subscribed) == 0:
                    print("FAILED: no tick entries logged while subscribed")
                    return 1

                unsub_result = _unwrap(
                    await asyncio.wait_for(
                        session.call_tool("unsubscribe_live_data", {"subscription_id": subscription_id}),
                        timeout=15,
                    )
                )
                print("unsubscribe_live_data:", json.dumps(unsub_result, indent=2))
                if not unsub_result["success"] or subscription_id not in unsub_result["data"]["stopped_subscription_ids"]:
                    print("FAILED: unsubscribe_live_data did not stop the expected subscription")
                    return 1

                # "Reconstruct after a disconnect": the log must still be readable
                # with no active subscription -- that's the whole point of the
                # durable-log design (SPEC.md sec 7).
                log_after_unsub = _unwrap(
                    await asyncio.wait_for(
                        session.call_tool("get_stream_log", {"symbol": "XAUUSD", "data_type": "tick"}),
                        timeout=15,
                    )
                )
                entries_after_unsub = log_after_unsub["data"]
                print(f"get_stream_log after unsubscribe: {len(entries_after_unsub)} entries")
                if len(entries_after_unsub) < len(entries_while_subscribed):
                    print("FAILED: log shrank after unsubscribe -- should only ever grow or stay the same")
                    return 1
                if entries_after_unsub[: len(entries_while_subscribed)] != entries_while_subscribed:
                    print("FAILED: log entries changed after unsubscribe -- sequence not stable")
                    return 1

        print("SUCCESS: subscribe -> stream -> log -> unsubscribe -> log-still-readable, all real XAUUSD data.")
        return 0
    finally:
        server_proc.terminate()
        try:
            out, _ = server_proc.communicate(timeout=5)
            if out:
                print("--- server output ---")
                print(out)
        except subprocess.TimeoutExpired:
            server_proc.kill()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

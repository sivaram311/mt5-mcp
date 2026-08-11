"""Live smoke test: mt5-mcp over the streamable-http transport (the new
default, replacing stdio -- see server.py's module docstring and
diagnostics/FINDINGS.md). Not run in automated CI (needs a live MT5
terminal). Starts the real server as a subprocess bound to
127.0.0.1:3403 (reserved in workflow/ports/REGISTRY.md), connects a real
MCP client over HTTP, and calls get_symbol_info / get_historical_ohlcv
for real XAUUSD data -- the same two tools that hung indefinitely over
stdio.

    .venv\\Scripts\\python.exe diagnostics\\live_smoke_http.py
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


def _wait_for_port() -> None:
    """Poll a plain TCP connect until uvicorn is listening, or raise after
    STARTUP_TIMEOUT_S. Deliberately does NOT open a real MCP session here —
    an earlier version did, and opening two MCP sessions back-to-back
    against the same server (one throwaway readiness probe + the real one)
    produced its own intermittent hang, a confound worth avoiding rather
    than debugging further for a smoke script."""
    deadline = time.monotonic() + STARTUP_TIMEOUT_S
    while time.monotonic() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            if s.connect_ex((HOST, PORT)) == 0:
                return
        time.sleep(0.3)
    raise RuntimeError(f"Server did not start listening on {HOST}:{PORT} within {STARTUP_TIMEOUT_S}s")


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

                info_result = await asyncio.wait_for(
                    session.call_tool("get_symbol_info", {"symbol": "XAUUSD"}), timeout=15
                )
                info = json.loads(info_result.content[0].text)
                print("get_symbol_info:", json.dumps(info, indent=2))
                if not info["success"]:
                    print("FAILED: get_symbol_info did not succeed")
                    return 1

                ohlcv_result = await asyncio.wait_for(
                    session.call_tool(
                        "get_historical_ohlcv",
                        {
                            "symbol": "XAUUSD",
                            "timeframe": "M15",
                            "limit": 5,
                            "include_volume": True,
                            "include_spread": True,
                        },
                    ),
                    timeout=15,
                )
                print("get_historical_ohlcv raw result:", repr(ohlcv_result))
                if not ohlcv_result.content:
                    print("FAILED: get_historical_ohlcv returned no content blocks")
                    return 1
                ohlcv = json.loads(ohlcv_result.content[0].text)
                print("get_historical_ohlcv:", json.dumps(ohlcv, indent=2))
                if not ohlcv["success"] or len(ohlcv["data"]) == 0:
                    print("FAILED: get_historical_ohlcv did not return candles")
                    return 1

        print("SUCCESS: streamable-http tool calls returned live XAUUSD symbol info + OHLCV.")
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

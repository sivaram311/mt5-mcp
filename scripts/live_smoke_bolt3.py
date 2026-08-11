"""Manual live smoke test for Bolt 3 — not run in automated CI.

Spawns the real mt5-mcp stdio server as an MCP client would (via the
official mcp SDK's ClientSession/stdio_client) and calls
get_historical_ohlcv / get_symbol_info for real XAUUSD data against the
OctaFX-Demo terminal. This exercises the full path: MCP client -> stdio
transport -> tool dispatch -> MT5Connector -> real MetaTrader5 terminal.

    .venv\\Scripts\\python.exe scripts\\live_smoke_bolt3.py
"""

from __future__ import annotations

import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> int:
    params = StdioServerParameters(command=sys.executable, args=["-u", "-m", "mt5_mcp"])

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            print("initializing session...", flush=True)
            await asyncio.wait_for(session.initialize(), timeout=15)
            print("session initialized, calling get_symbol_info...", flush=True)

            info_result = await asyncio.wait_for(
                session.call_tool("get_symbol_info", {"symbol": "XAUUSD"}), timeout=15
            )
            info = json.loads(info_result.content[0].text)
            print("get_symbol_info:", json.dumps(info, indent=2))
            if not info["success"]:
                print("FAILED: get_symbol_info did not succeed")
                return 1

            print("calling get_historical_ohlcv...", flush=True)
            ohlcv_result = await asyncio.wait_for(
                session.call_tool(
                    "get_historical_ohlcv",
                    {"symbol": "XAUUSD", "timeframe": "M15", "limit": 5, "include_volume": True, "include_spread": True},
                ),
                timeout=15,
            )
            ohlcv = json.loads(ohlcv_result.content[0].text)
            print("get_historical_ohlcv:", json.dumps(ohlcv, indent=2))
            if not ohlcv["success"] or len(ohlcv["data"]) == 0:
                print("FAILED: get_historical_ohlcv did not return candles")
                return 1

    print("SUCCESS: real MCP tool calls returned live XAUUSD symbol info + OHLCV.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

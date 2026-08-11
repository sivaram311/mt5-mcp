"""Integration test: spawns the real mt5-mcp stdio server as an MCP client would."""

import json
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@pytest.mark.asyncio
async def test_server_lists_and_calls_ping():
    params = StdioServerParameters(command=sys.executable, args=["-m", "mt5_mcp"])

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            assert "ping" in [t.name for t in tools.tools]

            result = await session.call_tool("ping", {})
            assert result.isError is False

            envelope = json.loads(result.content[0].text)
            assert envelope["success"] is True
            assert envelope["error_code"] is None
            assert envelope["data"]["server"] == "mt5-mcp"

"""Integration test: spawns the real mt5-mcp server over stdio, as an MCP client would.

stdio is no longer the default transport (see server.py's module docstring
and diagnostics/FINDINGS.md — it hangs on any real MetaTrader5 call), but
it's kept available and this test covers it explicitly via
MT5_MCP_TRANSPORT=stdio. Only exercises `ping`, which never touches MT5,
so it stays green regardless of the stdio+MetaTrader5 issue.
"""

import json
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from mt5_mcp.server import mcp


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["ping", "get_historical_ohlcv", "get_symbol_info"])
async def test_envelope_wrapped_tools_have_no_mismatched_output_schema(tool_name):
    """Regression test for a real bug found via diagnostics/live_smoke_http.py:
    _as_envelope always returns a dict envelope at runtime, but before the
    __signature__ fix in server.py, FastMCP inferred get_historical_ohlcv's
    output schema from its *inner* function's `-> list[dict]` annotation
    (via inspect.signature following __wrapped__), then rejected the
    envelope dict at validation time with a real MCP client. A bare `dict`
    annotation makes FastMCP skip structured-output schema generation
    entirely (outputSchema is None, plain JSON content used instead) —
    which is what every tool here actually relies on. No live server or
    MT5 connection needed; this only inspects the registered tool schema.
    """
    tools = await mcp.list_tools()
    tool = next(t for t in tools if t.name == tool_name)
    assert tool.outputSchema is None


@pytest.mark.asyncio
async def test_server_lists_and_calls_ping():
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "mt5_mcp"], env={"MT5_MCP_TRANSPORT": "stdio"}
    )

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

"""MT5-MCP server entrypoint.

Bolt 1: transport + response envelope only. No MT5 connection yet
(that's Bolt 2) — the one tool registered here (`ping`) exists to prove
the server starts, lists tools, and replies in the standard envelope
shape (docs/aidlc/SPEC.md sec 8).
"""

from __future__ import annotations

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from mt5_mcp import __version__
from mt5_mcp.envelope import ok

load_dotenv()

mcp = FastMCP(name="mt5-mcp")


@mcp.tool()
def ping() -> dict:
    """Health check. Returns the standard envelope with server version — no MT5 connection required."""
    return ok(data={"server": "mt5-mcp", "version": __version__})


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

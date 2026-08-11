import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main() -> int:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-u", r"C:\Users\ADMINI~1\AppData\Local\Temp\3\claude\E--MyWorkspace\f26736a2-5a01-462b-b865-e829ae8ba5b3\scratchpad\bare_mt5_server.py"],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            print("init...", flush=True)
            await asyncio.wait_for(session.initialize(), timeout=15)

            for tool_name in ["sync_sleep_2s", "async_sleep_2s", "mt5_account"]:
                print(f"calling {tool_name}...", flush=True)
                try:
                    result = await asyncio.wait_for(session.call_tool(tool_name, {}), timeout=10)
                    print(f"{tool_name} result:", result.content[0].text if result.content else result, flush=True)
                except asyncio.TimeoutError:
                    print(f"{tool_name} TIMED OUT", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

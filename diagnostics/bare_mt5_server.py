import time

import anyio
from mcp.server.fastmcp import FastMCP

mcp = FastMCP(name="bare-test")


def _sync_mt5_account() -> dict:
    import MetaTrader5 as mt5

    ok = mt5.initialize(path=r"E:\ProgramFiles\MT5\terminal64.exe", timeout=10000)
    if not ok:
        return {"ok": False, "error": str(mt5.last_error())}
    info = mt5.account_info()
    mt5.shutdown()
    return {"ok": True, "login": info.login if info else None}


@mcp.tool()
async def mt5_account() -> dict:
    return await anyio.to_thread.run_sync(_sync_mt5_account)


@mcp.tool()
def sync_sleep_2s() -> dict:
    time.sleep(2)
    return {"ok": True, "slept": 2}


@mcp.tool()
async def async_sleep_2s() -> dict:
    await anyio.sleep(2)
    return {"ok": True, "slept": 2}


if __name__ == "__main__":
    mcp.run(transport="stdio")

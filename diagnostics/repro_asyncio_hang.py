import asyncio
import sys
import time


async def main():
    print("before import", flush=True)
    import MetaTrader5 as mt5
    print("after import, calling initialize()", flush=True)
    t0 = time.time()
    ok = mt5.initialize(path=r"E:\ProgramFiles\MT5\terminal64.exe")
    print(f"initialize() returned {ok} after {time.time() - t0:.2f}s, error={mt5.last_error()}", flush=True)
    if ok:
        info = mt5.account_info()
        print("account_info:", info, flush=True)
        mt5.shutdown()


if __name__ == "__main__":
    print(f"event loop policy: {asyncio.get_event_loop_policy()}", flush=True)
    asyncio.run(main())

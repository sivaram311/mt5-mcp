"""Does asyncio.create_subprocess_exec (ProactorEventLoop) + piped stdin/stdout
cause MetaTrader5.initialize() to hang in the child, even without any MCP code?"""

import asyncio
import subprocess
import sys

CHILD_CODE = r"""
import sys, time
import MetaTrader5 as mt5
print("child started", flush=True)
t0 = time.time()
ok = mt5.initialize(path=r"E:\ProgramFiles\MT5\terminal64.exe", timeout=10000)
print(f"RESULT ok={ok} elapsed={time.time()-t0:.2f}s error={mt5.last_error()}", flush=True)
if ok:
    mt5.shutdown()
"""


async def main():
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-u", "-c", CHILD_CODE,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=20)
        print("stdout:", out.decode())
        print("stderr:", err.decode()[-500:])
    except asyncio.TimeoutError:
        print("TIMED OUT after 20s -- HUNG")
        proc.kill()
        await proc.communicate()


if __name__ == "__main__":
    asyncio.run(main())

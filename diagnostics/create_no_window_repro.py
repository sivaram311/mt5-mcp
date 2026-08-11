"""Does plain CREATE_NO_WINDOW (no MCP, no anyio) alone cause MetaTrader5.initialize() to hang?"""

import subprocess
import sys
import time

CHILD_CODE = r"""
import sys, time
import MetaTrader5 as mt5
t0 = time.time()
ok = mt5.initialize(path=r"E:\ProgramFiles\MT5\terminal64.exe", timeout=10000)
print(f"RESULT ok={ok} elapsed={time.time()-t0:.2f}s error={mt5.last_error()}", flush=True)
if ok:
    print("account:", mt5.account_info(), flush=True)
    mt5.shutdown()
"""

for flags, label in [(0, "normal (console inherited)"), (subprocess.CREATE_NO_WINDOW, "CREATE_NO_WINDOW")]:
    print(f"--- {label} ---", flush=True)
    proc = subprocess.Popen(
        [sys.executable, "-u", "-c", CHILD_CODE],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=flags,
        text=True,
    )
    try:
        out, err = proc.communicate(timeout=20)
        print("stdout:", out)
        print("stderr:", err[-500:] if err else "")
    except subprocess.TimeoutExpired:
        print("TIMED OUT after 20s -- HUNG")
        proc.kill()
        proc.communicate()

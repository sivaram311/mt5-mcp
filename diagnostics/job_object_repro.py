"""Does assigning the child process to a Windows Job Object (JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE),
exactly like mcp/os/win32/utilities.py does, reproduce the MT5 hang -- even with plain
asyncio.create_subprocess_exec (which alone was proven fine without a job object)?"""

import asyncio
import subprocess
import sys

import win32api
import win32con
import win32job

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


def create_job():
    job = win32job.CreateJobObject(None, "")
    info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    info["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
    return job


async def main():
    job = create_job()

    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-u", "-c", CHILD_CODE,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )

    handle = win32api.OpenProcess(win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, proc.pid)
    win32job.AssignProcessToJobObject(job, handle)
    win32api.CloseHandle(handle)
    print(f"assigned pid {proc.pid} to job object", flush=True)

    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=20)
        print("stdout:", out.decode())
        print("stderr:", err.decode()[-500:])
    except asyncio.TimeoutError:
        print("TIMED OUT after 20s -- HUNG (job object reproduces it)")
        proc.kill()
        await proc.communicate()


if __name__ == "__main__":
    asyncio.run(main())

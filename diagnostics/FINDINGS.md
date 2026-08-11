# Findings: MT5-MCP stdio server hangs on any real MetaTrader5 call

> **⚠ Do not use `stdio` transport for any tool that calls `MetaTrader5`.** The hang's root cause was never identified despite extensive isolation (see "What was ruled out" below). Use `streamable-http` (the default since Bolt 3) instead. This restriction stays in effect until someone actually isolates and fixes the stdio issue — it is not resolved just because a workaround exists.

**Status:** WORKED AROUND via transport switch (streamable-http is now the default and the only transport verified reliable for MT5 calls). Root cause of the stdio hang itself was **never identified** (see "Not yet tried" below — none of it was needed once http worked). The workaround is verified reliable (3/3 clean runs) and a second, unrelated schema bug it uncovered is also fixed. Committed to `master` (commit `66c9fe3`, "Bolt 3: market data tools, default transport switched to streamable-http").

**Date:** 2026-08-11, during Bolt 3 (`get_historical_ohlcv`/`get_symbol_info`).

**Update (same day, later):** switched the server's default transport from `stdio` to `streamable-http` (`src/mt5_mcp/server.py`, `MT5_MCP_TRANSPORT` env var, default `streamable-http`, `stdio` still available). `diagnostics/live_smoke_http.py` calls both previously-hanging tools through a real MCP client over HTTP — 3/3 runs clean, real XAUUSD data returned in both cases, no hang. **stdio's exact root cause was never pinned down** — everything in "What was ruled out" below is still accurate, but once streamable-http was confirmed to work reliably, further stdio-specific isolation was explicitly out of scope (per instruction: don't chase stdio further once http works). See "Second bug found via the http verification" below for a real, separate schema bug the http testing surfaced (get_historical_ohlcv specifically) that had nothing to do with the hang.

## Symptom

`src/mt5_mcp/market_data.py` and its wiring into `server.py` are correct and fully unit-tested (35/35 pass, all mocked — see `tests/test_market_data.py`). Direct calls to `MetaTrader5.initialize()` (any way tried) return instantly.

But: spawn the **real** `mt5-mcp` server as a real MCP client would (official `mcp` SDK's `stdio_client`/`ClientSession`, exactly what `scripts/live_smoke_bolt3.py` does), call a tool that touches `MetaTrader5`, and it **hangs indefinitely** — let it run 13 minutes before killing it. Near-zero CPU the whole time (confirmed via `Get-Process`), i.e. genuinely stuck, not slow.

Trivial tools with zero MT5 code (`time.sleep(2)`, `await anyio.sleep(2)`) on the exact same real server respond instantly. So it's specific to "real mcp SDK stdio round-trip" × "a call into the `MetaTrader5` package" — not the MCP framework generally, not this project's tool-dispatch code.

## What was ruled out (each tested in isolation, all fast/fine)

1. **Missing `MT5_PATH` env var.** MCP clients spawn servers with a curated minimal environment (`mcp/client/stdio/__init__.py`'s `get_default_environment()` — see `DEFAULT_INHERITED_ENV_VARS`), which does *not* include arbitrary vars like `MT5_PATH`. Confirmed this was really happening (env var absent in the child). **Fixed for real regardless of the hang**: `server.py` now anchors `load_dotenv(dotenv_path=Path(__file__).resolve().parents[2] / ".env")` instead of relying on `load_dotenv()`'s CWD-relative search, and a real local `.env` (gitignored) now sets `MT5_PATH`. Confirmed via `python -c "import mt5_mcp.server; print(os.getenv('MT5_PATH'))"` that this resolves correctly. Did not fix the hang.
2. **`CREATE_NO_WINDOW` subprocess flag alone** — `diagnostics/create_no_window_repro.py`. Plain `subprocess.Popen(..., creationflags=CREATE_NO_WINDOW)` calling MT5 in the child: instant.
3. **`asyncio`/`anyio` subprocess creation alone** — `diagnostics/asyncio_subprocess_repro.py`. `asyncio.create_subprocess_exec` with piped stdin/stdout/stderr (ProactorEventLoop): instant.
4. **Running the MT5 call on a worker thread** (`anyio.to_thread.run_sync`) instead of directly on the event loop — tested via `diagnostics/bare_mt5_server.py`'s `mt5_account` tool. Still hung through the real MCP round-trip.
5. **Windows Job Object assignment** — `mcp/os/win32/utilities.py`'s `create_windows_process` assigns the spawned child to a Job Object (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, for cleanup-on-parent-exit). Replicated this exactly (Job Object + `CREATE_NO_WINDOW` + `asyncio.create_subprocess_exec`) in `diagnostics/job_object_repro.py`: still instant.
6. **Python version** — identical hang reproduced on both the project's Python 3.14 venv and a throwaway Python 3.12 venv (ruled out a 3.14-specific native-extension ABI issue).

## Not yet tried / possible next directions

- **Read `mcp/server/stdio.py`** (the *server*-side stdio loop, not the client-side code already read) — everything above replicated the *client's* view of how the child is spawned, but the actual hang could be in how the server's own stdin-reading task interacts with a blocking native call, which none of the above repros exercise (they all still go through the real `mcp.server.fastmcp.FastMCP` server internals for the actual hang reproduction — only the *spawn mechanics* were replicated standalone, not the server's own request loop).
- **Try `streamable-http` transport instead of `stdio`** (`mcp.run(transport="streamable-http")` — `FastMCP` supports it, see `server.py`'s `run()` signature literal type). This removes the "spawned child process talking over stdio pipes" angle entirely; if the hang disappears, that's strong confirmation it's stdio/pipe/subprocess-specific rather than about MT5+asyncio in general.
- **Hand-roll a minimal JSON-RPC stdio loop**, bypassing the `mcp` SDK's client and server entirely, calling `MetaTrader5.initialize()` from within it — isolates whether it's something in the `mcp` package specifically (vs. any stdio-framed async protocol server).
- **Check for known issues**: search the `MetaTrader5` PyPI package's issue tracker / the `mcp` Python SDK's issue tracker for "hang", "stdio", "subprocess", "deadlock" — this could be a known, already-diagnosed interaction.
- **stderr/stdout handle sharing**: `MetaTrader5`'s C extension might do something with `GetStdHandle`/console APIs on first `initialize()` call that behaves differently when stdout is a *redirected pipe used for a different protocol* (JSON-RPC framing) versus a pipe used for plain text (my repros' children only ever printed plain text to their piped stdout, never structated JSON-RPC frames) — worth checking if the *server's own* stdout-writing (JSON-RPC responses) interacting with something MT5 does internally is the actual trigger, not the spawning mechanics at all.

## Reproduction

```
.venv\Scripts\python.exe -u diagnostics\bare_mt5_client.py
```

Spawns `diagnostics\bare_mt5_server.py` (three tools: `sync_sleep_2s`, `async_sleep_2s`, `mt5_account`) via the real MCP stdio client and calls all three with a 10s timeout each. First two succeed immediately; `mt5_account` times out every time.

## Impact (as originally written, before the fix)

Blocks Bolt 3 (and everything after it) from being verifiable as actually working through a real MCP client — the unit tests are real and passing, but the live-smoke acceptance criterion (`docs/aidlc/BOLTS.md` Bolt 3) cannot currently be met. This is an architecture-relevant risk against `INCEPTION.md`'s tech stack decision (`mcp` over stdio + the `MetaTrader5` package) — not scoped to just this one Bolt.

## Resolution: switch to streamable-http

`src/mt5_mcp/server.py` now defaults to `transport="streamable-http"` (env var `MT5_MCP_TRANSPORT`, values `stdio`/`sse`/`streamable-http`; `stdio` still fully supported, just no longer the default). Bound to `127.0.0.1:3403` (reserved in `E:\MyAgent\workflow\ports\REGISTRY.md`, loopback DEV testing only).

`diagnostics/live_smoke_http.py` starts the real server as a subprocess, connects a real MCP client over HTTP (`mcp.client.streamable_http.streamablehttp_client`), and calls `get_symbol_info` + `get_historical_ohlcv` for real XAUUSD data. **3/3 clean runs**, real data both times, no hang.

One implementation wrinkle in the smoke script itself, not the server: an early version opened a throwaway MCP session just to poll server readiness, then a second real session for the actual calls — that produced its own *separate*, intermittent hang (one run out of several). Simplified to a plain TCP `connect_ex` poll for readiness instead of a second MCP session, which resolved it. Worth remembering if anyone builds a client that opens multiple sessions against the same server in quick succession.

## Second bug found via the http verification (unrelated to the hang)

Once http actually let `get_historical_ohlcv` be called for the first time, it failed immediately with a real MCP client (never surfaced in unit tests, which call `market_data.get_historical_ohlcv` directly, bypassing FastMCP's schema layer entirely):

```
Error executing tool get_historical_ohlcv: 1 validation error for get_historical_ohlcvOutput
result
  Input should be a valid list [type=list_type, input_value={'success': True, 'error_...
```

**Cause:** `server.py`'s `_as_envelope` decorator wraps every tool's return value into a `dict` envelope (`ok(data=...)`/`err(...)`) at runtime, but the *inner* `get_historical_ohlcv` function is annotated `-> list[dict]` (its real, pre-envelope return type). `functools.wraps` sets `__wrapped__`, and `inspect.signature()` — which FastMCP uses to build each tool's output schema — follows `__wrapped__` by default, so FastMCP built an output schema from `list[dict]`, then rejected the wrapper's actual `dict` return value at validation time. `get_symbol_info` happened to be annotated `-> dict` already, so it was never exposed by this bug — pure coincidence, not a distinguishing design difference.

**Fix:** `_as_envelope` now explicitly sets `wrapper.__signature__ = inspect.signature(fn).replace(return_annotation=dict)` — `inspect.signature()` checks `__signature__` before it follows `__wrapped__`, so this keeps the real parameter list (still needed for the tool's *input* schema) while correcting just the return annotation. Confirmed via `mcp.list_tools()` that all three tools (`ping`, `get_historical_ohlcv`, `get_symbol_info`) now report `outputSchema: None` — a bare `dict` annotation makes FastMCP skip structured-output schema generation entirely and fall back to plain JSON text content, which is what every tool here actually relies on (nothing anywhere parses `result.structuredContent`). Regression test: `tests/test_server.py::test_envelope_wrapped_tools_have_no_mismatched_output_schema` (parametrized over all three tools, no live server or MT5 needed — inspects the registered schema directly).

## Remaining risks / follow-ups

- **stdio's root cause is still unknown.** If a future MCP client absolutely requires stdio (some don't support streamable-http yet), this will need real isolation work — start from `diagnostics/bare_mt5_client.py` and the two untried directions above (read `mcp/server/stdio.py`'s own read loop; check upstream issue trackers).
- **`diagnostics/live_smoke_http.py`'s dual-session-hang wrinkle** (see above) was routed around, not explained. If a real MCP client opens more than one session against this server in quick succession, watch for this.
- **Every future Bolt's tools must go through `_as_envelope`** (or independently set a correct `__signature__`/return annotation) to avoid reintroducing the schema bug — the regression test only covers the three tools that exist today.
- Code, tests, and this file are committed (`master` @ `66c9fe3`). The port registry rows (`E:\MyAgent\workflow\ports\REGISTRY.md`/`.json`, port `3403`) live in a **separate repo** (`E:\MyAgent`) and were committed there independently, not part of this repo's history.
- `docs/aidlc/INCEPTION.md`'s transport/auth/deploy-topology sections have been updated to match reality (2026-08-11) — the auth deferral's own stated trigger condition (network-facing transport) fired when streamable-http became default, and that section is now flagged as an open, unresolved decision rather than silently left stale.

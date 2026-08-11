# MT5-MCP — Construction Bolt Backlog

Ordered list of AI-DLC Bolts for Construction. Each Bolt = one mergeable change + evidence + docs update, small enough to review in one sitting. Full tool/parameter spec: [`SPEC.md`](./SPEC.md). Sequencing/safety decisions: [`INCEPTION.md`](./INCEPTION.md).

Evidence expectations: unit tests (mocked `MetaTrader5` module) for all logic; live smoke against the real `OctaFX-Demo` account documented manually per Bolt, not run in automated CI (touches a real, if demo, trading account).

---

## Bolt 1 — Project skeleton, MCP transport, response envelope

**Status: DONE (2026-08-11).**

**Goal:** `src/mt5_mcp/` package, `pyproject.toml`, `.gitignore`, `.env.example`; an MCP server (stdio transport, official `mcp` Python SDK) that starts, lists a placeholder tool, and returns the standard response envelope (`success`, `error_code`, `error_message`, `retryable`, `request_id` — `SPEC.md` §8) — no MT5 connection yet.

**Acceptance: met.** `pip install -e ".[dev]"` succeeds; `mt5_mcp/envelope.py` implements the envelope (`ok()`/`err()`); `mt5_mcp/server.py` registers a `ping` tool via `FastMCP` (stdio transport) returning the envelope as JSON; `tests/test_envelope.py` (5 unit tests) + `tests/test_server.py` (1 integration test that spawns the real server as a subprocess via the official `mcp` `ClientSession`/`stdio_client`, calls `tools/list` and `tools/call('ping')`, and asserts the returned JSON matches the envelope shape) — 6/6 pass. `.env` gitignored, `.env.example` has no secrets, no credentials anywhere in the tree.

**Implementation note:** pinned `mcp>=1.29.0,<2.0.0` rather than the just-released `mcp==2.0.0` — 2.0.0 renamed `FastMCP`→`MCPServer` and restructured the server module (`mcp.server.fastmcp`→`mcp.server.mcpserver`) with an API not yet well-documented anywhere; 1.29.0's `FastMCP` is the stable, well-established surface. Revisit the 2.0.0 upgrade as its own future Bolt once its API is better understood, not as a side effect of Bolt 1.

---

## Bolt 2 — MT5 connector wrapper

**Status: DONE (2026-08-11).**

**Goal:** A thin wrapper module around the `MetaTrader5` package: `initialize()`/`shutdown()`, terminal/account info, connection-state checks. Mirrors the pattern already proven working in `E:\Source\mt5_xauusd_price.py` this session.

**Acceptance: met.** `src/mt5_mcp/connector.py`: `MT5Connector` class (`connect()`/`disconnect()`/`is_connected()`/`terminal_info()`/`account_info()`, usable as a context manager), `MT5ConnectionError`, `TerminalInfo`/`AccountInfo` dataclasses. `mt5_module` is dependency-injected (defaults to the real `MetaTrader5` package, swappable for a fake in tests) so unit tests never need a live Windows terminal.

`tests/test_connector.py` (11 unit tests, all against a `_FakeMT5` stand-in): init success passes `path` kwarg correctly, init without a path omits the kwarg, init failure raises `MT5ConnectionError` carrying the real `mt5.last_error()` tuple, calling `terminal_info()`/`account_info()` before `connect()` raises "Not connected", `disconnect()` only calls `shutdown()` if actually connected, `terminal_info()`/`account_info()` field mapping, `account_info()` returns `None` when the terminal is connected but not logged into a trading account (distinct from a connection failure), `terminal_info()` returning `None` from the underlying package raises with the MT5 error attached, and the context-manager form connects on enter / disconnects on exit. 17/17 tests pass repo-wide (5 envelope + 11 connector + 1 server integration).

`scripts/live_smoke_bolt2.py` — documented, repeatable, not run in CI (needs a live Windows terminal). Real run against `OctaFX-Demo` (2026-08-11):

```
Terminal: Octa Markets MetaTrader 5
Connected: True
Trade allowed: True
Account: 213878432 (OctaFX-Demo)
Balance: 100.0 USD
SUCCESS: MT5Connector connected and returned real terminal/account info.
```

No MCP tool exposes this yet — `MT5Connector` is an internal module only, consumed starting Bolt 3 (matches the `cineforge` precedent of a Bolt 2 client wrapper landing before Bolt 3's user-facing surface).

---

## Bolt 3 — Market data tools

**Status: DONE (2026-08-11), but changed the server's default transport along the way — see below. Committed to `master` @ `66c9fe3`.**

**Goal:** `get_historical_ohlcv` and `get_symbol_info` as real MCP tools (`SPEC.md` §4.1), wired through the Bolt 2 connector. All filter parameters from the spec (`from_date`/`to_date`, `from_bar`/`to_bar`, `limit`, `include_volume`, `include_spread`, `session_filter`, `price_type`, `only_completed_bars`) implemented or explicitly stubbed with a documented reason if MT5's API can't support one directly (e.g. `session_filter` may need to be computed client-side from UTC time, not a native MT5 filter).

**Acceptance: met, after a transport pivot.** Unit tests: `tests/test_market_data.py` (25 tests) cover the candle/symbol-info data model mapping and every filter path (date range, bar range, limit-default, include_volume/include_spread toggles, only_completed_bars, session_filter, unsupported `price_type` explicitly rejected with a documented reason per the Goal above, invalid symbol, empty-data). 39/39 tests pass repo-wide.

**Live smoke: real, but not over stdio.** The first live-smoke attempt over `stdio` (the transport `INCEPTION.md` originally decided on) hung indefinitely on any real `MetaTrader5` call — reproduced reliably, root cause never fully identified despite extensive isolation (env vars, `CREATE_NO_WINDOW`, thread offload, Windows Job Objects, Python 3.12 vs 3.14 all ruled out — see `diagnostics/FINDINGS.md`). **Fix: switched the default transport to `streamable-http`** (`MT5_MCP_TRANSPORT` env var, default `streamable-http`; `stdio` still fully supported, just no longer default; bound to `127.0.0.1:3403`, reserved in `E:\MyAgent\workflow\ports\REGISTRY.md`). `diagnostics/live_smoke_http.py`: 3/3 clean runs, real XAUUSD `get_symbol_info` + `get_historical_ohlcv` data returned through an actual MCP client over HTTP, no hang.

That same http verification also caught a second, unrelated real bug: `get_historical_ohlcv`'s declared return type (`list[dict]`) didn't match what `_as_envelope` actually returns at runtime (a `dict` envelope) — invisible to the unit tests (which call the inner function directly, bypassing FastMCP's schema layer) but a hard failure through any real MCP client. Fixed in `server.py`'s `_as_envelope` (explicit `__signature__` override) with a regression test (`test_server.py::test_envelope_wrapped_tools_have_no_mismatched_output_schema`). Full writeup: `diagnostics/FINDINGS.md`.

**INCEPTION.md updated (2026-08-11, separate pass):** transport, deploy topology, and tech stack tables now reflect streamable-http as default. The auth section's own stated trigger condition ("void the moment MT5-MCP gains a network-facing transport") fired — flagged there as an explicitly **unresolved** open decision (not silently treated as still-deferred), pending Bolt 7 below.

---

## Bolt 4 — Live streaming + durable stream log

**Goal:** `subscribe_live_data`, `unsubscribe_live_data`, `get_stream_log` (`SPEC.md` §4.2, §7). Local SQLite stream-log store (per `INCEPTION.md` tech stack decision), keyed by symbol + date, time-range queryable. Multiple concurrent subscriptions supported.

**Acceptance:** Unit tests cover subscribe/unsubscribe lifecycle and log write/query against a temp SQLite file (no real MT5 needed for the log-store tests); live smoke subscribes to XAUUSD ticks for a short window, confirms both the forwarded stream and the durable log contain matching entries, and confirms `get_stream_log` reconstructs the sequence after a simulated disconnect.

---

## Bolt 5 — Order & position lifecycle tools + safety layer

**Goal:** `place_order`, `modify_order`, `cancel_order`, `get_open_positions`, `modify_position`, `close_position`, `close_all_positions` (`SPEC.md` §4.3–4.4), **plus** the mandatory safety layer from `INCEPTION.md`: dry-run-by-default (explicit opt-out via config/env, never a per-call tool parameter), kill-switch file check before any execution path, server-side max-lot / max-open-position limits, and an audit log of every attempt (dry-run or real).

**This is the highest-risk Bolt in the backlog.** Per `INCEPTION.md`'s safety-layer section, changes here always require explicit human review on merge, regardless of what's unlocked elsewhere.

**Acceptance:**
- Unit tests cover: dry-run path never calls the real MT5 execution API (mock asserts zero calls to `order_send`/`order_close` when dry-run is on); kill-switch file present blocks execution even with dry-run off; risk-limit rejection paths (over max lots, over max open positions); order-type parameter mapping for at least market/limit/stop (`SPEC.md`'s "broker-specific variants" support checked against Octa Markets' actual `ORDER_TYPE_*` support, not assumed).
- Live smoke on `OctaFX-Demo` **only**, dry-run mode first (confirms simulated response shape, confirms nothing hit the real account), then one real demo-account market order placed and closed end-to-end with the audit log entries shown as evidence.
- Explicit note in the evidence: this Bolt does not authorize live (non-demo) trading — that stays a distinct future decision per `INCEPTION.md`.

---

## Bolt 6 — History & audit tools

**Goal:** `get_order_history`, `get_deal_history`/`get_trade_history`, `get_position_history` (`SPEC.md` §4.5), including all listed filters (symbol, date range, order_type, status, magic_number, comment_contains).

**Acceptance:** Unit tests cover filter composition and the order/deal/position history data model mapping; live smoke pulls real history for the Bolt 5 demo trade(s) and confirms comment/magic-number round-trip correctly.

---

## Bolt 7 — Auth decision checkpoint (CSS or waiver)

**Now more relevant than originally scoped:** its trigger condition ("any network-facing transport ships") already fired during Bolt 3 — streamable-http is the default transport as of `66c9fe3`, not a hypothetical future change. Currently mitigated only by loopback-only binding (`127.0.0.1:3403`), which `INCEPTION.md`'s "Auth / security note" explicitly flags as a mitigating factor, not a resolution.

**Goal:** Revisit `INCEPTION.md`'s deferred-auth decision and record either a CSS integration plan or a fresh explicit documented waiver scoped to "loopback-only, streamable-http."

**Acceptance:** Decision written under `docs/aidlc/`; no silent no-auth assumption for any future network-facing deploy — especially before ever binding to a non-loopback host.

---

## Explicitly deferred (not a Bolt yet)

`SPEC.md` §10 extensibility items — economic calendar/news filter, sentiment/alt-data injection, multi-account support, portfolio-level risk queries, custom indicator filters, webhook/event push, second backend connector (cTrader/REST brokers) — noted for the future, no Bolt assigned.

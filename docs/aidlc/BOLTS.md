# MT5-MCP — Construction Bolt Backlog

Ordered list of AI-DLC Bolts for Construction. Each Bolt = one mergeable change + evidence + docs update, small enough to review in one sitting. Full tool/parameter spec: [`SPEC.md`](./SPEC.md). Sequencing/safety decisions: [`INCEPTION.md`](./INCEPTION.md).

Evidence expectations: unit tests (mocked `MetaTrader5` module) for all logic; live smoke against the real `OctaFX-Demo` account documented manually per Bolt, not run in automated CI (touches a real, if demo, trading account).

---

## Bolt 1 — Project skeleton, MCP transport, response envelope

**Goal:** `src/mt5_mcp/` package, `pyproject.toml`, `.gitignore`, `.env.example`; an MCP server (stdio transport, official `mcp` Python SDK) that starts, lists a placeholder tool, and returns the standard response envelope (`success`, `error_code`, `error_message`, `retryable`, `request_id` — `SPEC.md` §8) — no MT5 connection yet.

**Acceptance:** `pip install -e .` succeeds; server starts and responds to a `tools/list` MCP call; every tool response (including the placeholder) follows the envelope shape; `.env` gitignored; no secrets in tree.

---

## Bolt 2 — MT5 connector wrapper

**Goal:** A thin wrapper module around the `MetaTrader5` package: `initialize()`/`shutdown()`, terminal/account info, connection-state checks. Mirrors the pattern already proven working in `E:\Source\mt5_xauusd_price.py` this session.

**Acceptance:** Unit tests mock the `MetaTrader5` module (init success/failure, disconnected state); one documented live smoke against `OctaFX-Demo` confirming real `initialize()` + account info.

---

## Bolt 3 — Market data tools

**Goal:** `get_historical_ohlcv` and `get_symbol_info` as real MCP tools (`SPEC.md` §4.1), wired through the Bolt 2 connector. All filter parameters from the spec (`from_date`/`to_date`, `from_bar`/`to_bar`, `limit`, `include_volume`, `include_spread`, `session_filter`, `price_type`, `only_completed_bars`) implemented or explicitly stubbed with a documented reason if MT5's API can't support one directly (e.g. `session_filter` may need to be computed client-side from UTC time, not a native MT5 filter).

**Acceptance:** Unit tests cover the candle/symbol-info data model mapping (`SPEC.md` §5) and filter composition; live smoke pulls real XAUUSD OHLCV from `OctaFX-Demo`.

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

**Goal:** Before any network-facing transport (HTTP/SSE/Streamable HTTP) or multi-user surface, revisit `INCEPTION.md`'s deferred-auth decision and record either a CSS integration plan or a fresh explicit documented waiver for the new surface.

**Acceptance:** Decision written under `docs/aidlc/`; no silent no-auth assumption for any future network-facing deploy. Not required to start Bolt 7 itself if MT5-MCP stays stdio-only indefinitely — this Bolt is a gate on *adding network transport*, not a deadline.

---

## Explicitly deferred (not a Bolt yet)

`SPEC.md` §10 extensibility items — economic calendar/news filter, sentiment/alt-data injection, multi-account support, portfolio-level risk queries, custom indicator filters, webhook/event push, second backend connector (cTrader/REST brokers) — noted for the future, no Bolt assigned.

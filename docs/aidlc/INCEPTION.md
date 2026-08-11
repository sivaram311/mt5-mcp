# MT5-MCP — AI-DLC Inception Charter

**Phase:** Inception (Day 1)
**Status:** Charter locked after brainstorming — not yet functional
**Next phase:** Construction (Bolt-sized diffs + evidence), see [`BOLTS.md`](./BOLTS.md)

---

## Purpose

**MT5-MCP** is an MCP (Model Context Protocol) server that exposes MetaTrader 5 — market data, live streaming with durable logs, and full order/position lifecycle management — as a structured tool surface for AI agents (Claude, Cursor, or any MCP-aware client).

It is a **single-operator local tool**, run against the MT5 terminal already installed and verified on this machine (`E:\ProgramFiles\MT5\terminal64.exe`, Octa Markets, demo account `213878432`). The full tool/data-model spec was provided by the user and is recorded verbatim in [`SPEC.md`](./SPEC.md); this charter records the sequencing and safety decisions made against that spec.

---

## Relationship to `mt5-dev`

`E:\MyWorkspace\mt5-dev` is a separate, existing project: a file-based bridge (`ChartDrawBridge.mq5` + a Python command writer) that lets a script push chart-drawing commands into a running MT5 terminal via the Common Files folder. It does not use the `MetaTrader5` Python package, has no MCP protocol surface, and does no market-data or order/position work.

**Decision:** MT5-MCP is a new, separate project. It does not replace or wrap `mt5-dev` — different protocol (proper MCP, over streamable-http as of Bolt 3 — see below — vs. a file-poll bridge), different capability (data + orders vs. chart annotation). Both can coexist.

---

## Scope for this Inception + first Construction pass

**In scope (first Construction pass — see `BOLTS.md` for exact Bolt ordering)**

- All tool categories from `SPEC.md` §4: market data (`get_historical_ohlcv`, `get_symbol_info`), live streaming + durable log (`subscribe_live_data`, `unsubscribe_live_data`, `get_stream_log`), order placement (`place_order`, `modify_order`, `cancel_order`), position management (`get_open_positions`, `modify_position`, `close_position`, `close_all_positions`), history/audit (`get_order_history`, `get_deal_history`, `get_position_history`)
- **Decision (execution sequencing):** order/position tools ship in the **same** first Construction pass as the read-only tools — not deferred to a later milestone. The tradeoff for shipping them together is that every execution tool defaults to a **dry-run/paper mode** (see "Safety layer" below) that must be explicitly disabled to touch a real account. The safety layer is not a follow-up Bolt; it ships alongside the feature.
- MetaTrader 5 as the only backend connector for this project. The spec is written backend-agnostic (§13 calls out pluggability to cTrader / REST brokers) — that pluggability is **not** built now; a single hard-wired `MetaTrader5`-package connector is the Inception decision. Revisit only if a second backend is actually needed.
- **Superseded during Bolt 3 (2026-08-11):** stdio was the original Inception transport decision. A real MCP client calling any `MetaTrader5`-touching tool over stdio was found to hang indefinitely (root cause not fully identified despite extensive isolation — see `diagnostics/FINDINGS.md`). **`streamable-http` is now the default and recommended transport** (`MT5_MCP_TRANSPORT` env var; bound to `127.0.0.1:3403`, reserved in `E:\MyAgent\workflow\ports\REGISTRY.md`, loopback-only). `stdio` remains available for whichever client needs it but is **not currently known to work reliably for any tool that calls MetaTrader5** — don't use it for that until someone isolates the hang further.

**Explicitly out of scope for this pass**

- Any second backend connector (cTrader, generic REST brokers)
- CSS auth integration (deferred, see "Auth / security" below)
- `SPEC.md` §10 extensibility items (economic calendar, multi-account, portfolio-level risk, custom indicator filters, webhooks) — explicitly future work, not this pass
- Live (non-demo) trading — see "Safety layer": dry-run is the default and disabling it against a live account is a separate, explicit, human-made decision, not a side effect of finishing Construction

---

## Tech stack decision

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Language / runtime | Python 3.11+ | The `MetaTrader5` Python package is already installed on this machine and independently verified (this session) to pull live XAUUSD ticks against the real terminal; `mt5-dev` already established Python as this machine's MT5-integration language; the official MCP Python SDK covers the server side. |
| MCP SDK | Official `mcp` Python SDK (`FastMCP`, streamable-http server) | Standard, avoids hand-rolling the protocol. |
| Packaging | `pyproject.toml`, standard `pip`/`venv` | Matches `cineforge` and `mt5-dev` convention on this machine. |
| Package name | `mt5_mcp` | Fixed project identity. |
| Backend connector | `MetaTrader5` package only (hard-wired, not pluggable yet) | See scope decision above. |
| Stream log store | Local SQLite file, one table, indexed on `(symbol, timestamp)` | Meets `SPEC.md` §7's "durable, time-range queryable, keyed by symbol+date" requirement via the index and WHERE-clause filtering rather than physical per-date tables/files — simpler to operate for a single-operator tool (built Bolt 4). Revisit only if this needs sharding/archival at a scale where one table stops being enough, or if multi-process concurrent write access is ever needed. |
| Transport | MCP over `streamable-http` (default), `stdio` available but not currently reliable for MT5-touching tools | Changed from the original stdio-only decision during Bolt 3 — stdio hangs indefinitely on any real `MetaTrader5` call, root cause not fully identified. Bound to `127.0.0.1:3403` (loopback only, reserved in the port registry). This is now a network-facing transport, even if currently loopback-restricted — see "Auth / security note" below, which this changes. |
| Layout | `src/mt5_mcp/`, `tests/`, `docs/` (`docs/aidlc/` for AI-DLC), root `README.md`, `pyproject.toml`, `.gitignore`, `.env.example` | Machine convention (matches `cineforge`). |

This decision is **fixed** — do not re-decide during Construction.

---

## Architecture sketch

```
┌──────────────┐  MCP over streamable-http   ┌───────────────────────┐
│  MCP Client  │ ◄─────────────────────────► │   MT5-MCP server       │
│ (Claude Code/│  (127.0.0.1:3403, default;  │   (Python, mt5_mcp)    │
│  Desktop/    │   stdio available, not      │                        │
│  Cursor/etc) │   reliable for MT5 calls)   │  tool dispatch +       │
└──────────────┘                             │  response envelope     │
                                              │  (success/error_code/  │
                                              │   retryable/request_id)│
                                              └───────────┬────────────┘
                                                          │  MetaTrader5
                                                          │  Python package
                                                          ▼
                                              ┌───────────────────────┐
                                              │  MT5 terminal          │
                                              │  terminal64.exe        │
                                              │  E:\ProgramFiles\MT5   │
                                              └───────────┬────────────┘
                                                          │
                                                          ▼
                                              ┌───────────────────────┐
                                              │  Broker                │
                                              │  (OctaFX-Demo today)   │
                                              └───────────────────────┘

Side channel (Bolt 4): subscribe_live_data writes every tick/bar to a local
SQLite stream log (keyed by symbol + date). get_stream_log reads it back —
lets a client reconstruct history across a disconnect without re-subscribing.

Safety layer (Bolt 5, wraps every order/position tool):
  dry-run flag (default ON) → kill-switch file check → risk limits
  (max lots, max open positions) → only then does a call reach the real
  MetaTrader5 order_send()/order_close() APIs.
```

---

## Safety layer (mandatory, ships with Bolt 5 — not deferred)

Because order/position tools ship in the first Construction pass, the following are **not optional polish** — they are Bolt 5's acceptance criteria, per `SPEC.md` §9:

- **Dry-run/paper mode is the default.** Any execution tool call (`place_order`, `modify_order`, `cancel_order`, `modify_position`, `close_position`, `close_all_positions`) that would touch a real account must be explicitly opted into (an explicit config/env flag, not a tool parameter an agent can flip mid-conversation) — the default path validates and simulates the call, returns a realistic response shape, and never calls MT5's real execution API.
- **Kill-switch.** A project-local check (a lock/flag file MT5-MCP looks for before any execution call) that disables all execution tools immediately when present, independent of the dry-run setting.
- **Risk limits.** Max lot size and max concurrent open positions, enforced server-side before any order reaches MT5 — not just documented as a convention.
- **Audit log.** Every order/modification attempt (dry-run or real) is logged, per `SPEC.md` §9.
- **Live (non-demo) account use is a separate, explicit, human-made decision** — not something flipping the dry-run flag off should silently permit. This mirrors this machine's standing rule that trade-execution unlock stays human-gated regardless of any automation trust tier (`E:\MyAgent\workflow\aidlc\README.md`, permanent gate list) — MT5-MCP is not `trading-portal`, but the same principle applies: a bad diff to sizing/risk logic must not be able to bypass this by itself. **Construction-phase changes to Bolt 5's dry-run/kill-switch/risk-limit code always require explicit human review on merge, regardless of what auth or trust tier is otherwise in effect.**

---

## Auth / security note (open decision — deferred by design, but see the 2026-08-11 update below)

This machine's standing rule is centralized auth via the Centralized Security System (CSS) for anything beyond a public no-login static surface. The original reasoning here was the `cineforge` precedent: a single-operator local tool speaking MCP over stdio has no network attack surface to protect with CSS yet — the client spawning the server process already has whatever access the operator's machine account has.

**Decision (original, Inception):** defer CSS integration, following the `cineforge` open-decision pattern — recorded here explicitly, not silently waived.

**This decision was written to be void the moment MT5-MCP gains a network-facing transport, with Bolt 7 as the named checkpoint. That happened during Bolt 3 (2026-08-11)** — the default transport is now `streamable-http`, not `stdio` (see the Tech stack table and `docs/aidlc/BOLTS.md` Bolt 3 for why: stdio hangs indefinitely on any real `MetaTrader5` call). This section has **not** been fully re-resolved — flagging that explicitly rather than silently treating the old deferral as still valid:

- **Mitigating factor, not a resolution:** the server is currently bound to `127.0.0.1:3403` only — loopback, not reachable off this machine. The trust boundary today is arguably still "same machine, same operator," similar to stdio.
- **Not yet decided:** whether binding to loopback-only is itself sufficient to keep treating this as within the original deferral's spirit, or whether Bolt 7's checkpoint condition ("any network-facing transport ships") should be read literally — streamable-http is a network protocol even when loopback-restricted, and nothing currently stops a future change from binding `0.0.0.0` instead of `127.0.0.1` without this section being revisited again.
- **Explicit next step, not resolved here:** before MT5-MCP ever binds to a non-loopback host, or before Bolt 7 (order/position tools' network-facing use) is treated as done, this section needs a real decision — CSS integration or a fresh documented waiver scoped to "loopback-only, streamable-http" specifically. Not done as part of this transport-fix pass; out of scope per the instruction not to start new feature work.

### 2026-08-11 update — checkpoint hit, decision made (no-auth waiver, temporary)

The server itself still binds loopback only (`127.0.0.1:3403`), but it is now reachable from other machines via a reverse-proxied public hostname, `https://mt5-mcp-dev.delena.buzz` (nginx → Cloudflare DNS, see `E:\MyAgent\workflow\ports\REGISTRY.md`'s `:3403` row for the full setup). This is exactly the condition this section said would void the deferral.

Researched first: CSS (`E:\MyAgent\workflow\css\`) has no headless/machine-to-machine auth grant — only browser-redirect SSO (PKCE) or a legacy username/password login endpoint, and the only ready-made resource-server integration is a Spring Boot starter. Adopting CSS today for a Python, non-interactive tool server would mean either misusing the password-login endpoint with a stored service credential, or hand-building a JWT/JWKS validator from scratch — real, non-trivial work. There's also no Cloudflare Access precedent on this machine to borrow instead.

**Decision (explicit, user-directed, 2026-08-11):** ship with **no auth gate for now**; full CSS integration is planned for later, not abandoned. Recorded in `workflow/css/CLIENT-REGISTRY.md` as `waived-no-auth` (a status distinct from the existing `waived-public-read` apps, because this is not a read-only surface). Bolt 5's dry-run architecture is the load-bearing mitigation, not a substitute for the decision: `MT5_MCP_DRY_RUN` is a server-side env var, so an unauthenticated remote caller cannot flip it and cannot place a real order through this endpoint. What an unauthenticated caller *can* do today: read real market data, read real open-position state (tickets/volumes/P&L), and receive realistic simulated fills. That is the accepted, explicitly-chosen risk until CSS lands — not an oversight.

### 2026-08-11, same day, follow-up — the load-bearing mitigation was explicitly withdrawn

A few minutes later the user asked to enable real order placement (`MT5_MCP_DRY_RUN=false`) on this same public, no-auth endpoint. Flagged directly before acting: this specifically undoes the mitigation the paragraph above relies on. The user's explicit answer, given that framing: enable it as-is, public and no-auth, real orders included — not deferred, not misunderstood.

**Current state as of 2026-08-11 20:20 (operational, not a code change — `.env` is gitignored, not committed):** `MT5_MCP_DRY_RUN=false`. An unauthenticated caller can now place/modify/cancel/close a real order on the connected account (currently `OctaFX-Demo`, a demo account — not a live-money account, which stays a separate undecided gate per the "Live (non-demo) account use" bullet above). Blast radius is bounded by `MT5_MCP_MAX_LOT_SIZE`/`MT5_MCP_MAX_OPEN_POSITIONS` (unaffected by this toggle, still enforced) and the kill-switch (`MT5_MCP_KILL_SWITCH_PATH` — create that file to immediately block `place_order`). This is a live, moment-in-time operational value, not a permanent decision recorded in code — check `E:\MyAgent\workflow\ports\REGISTRY.md`'s `:3403` row for the actual current value before assuming this doc is still accurate.

---

## Secrets handling

- The MT5 terminal's own logged-in session is what today's connection relies on (verified working this session, no explicit login/password passed by the calling Python code). No MT5 account password needs to be stored by MT5-MCP under this model.
- If a future Bolt needs headless/explicit login (`mt5.login(account, password, server)`), those values are env vars only (`MT5_ACCOUNT`, `MT5_PASSWORD`, `MT5_SERVER` or similar) — never committed, never logged in plaintext (including in the audit log above — log the ticket/action, not credentials).
- `.env` gitignored; `.env.example` holds placeholder names only.
- No secrets exist in this repo as of Inception (verified by the Reviewer at the scaffold push).

---

## Deploy topology (target, not yet real)

| Role | Drive | Ports | Status |
|------|-------|-------|--------|
| DEV | E: | `:3403` (streamable-http, default transport as of Bolt 3; loopback `127.0.0.1` only) — reserved in `E:\MyAgent\workflow\ports\REGISTRY.md`; `stdio` also available, no port, not reliable for MT5 calls | Bound and verified locally during Bolt 3 testing; not a persistent/durable DEV service yet |
| PREPROD | F: | — | Not reserved yet |
| PROD | G: | — | Not reserved yet |
| RELEASES | H: | — | N/A for Inception |

Nothing is deployed to F: or G: during Inception. `:3403` is currently only ever started ad hoc for local testing (e.g. `diagnostics/live_smoke_http.py`), not run as a standing DEV service — no durable-startup task exists. Revisit deploy shape (promote pipeline, durable process) once this moves past ad hoc local testing.

---

## Known risks / open questions

| Topic | Note |
|-------|------|
| Live trading risk | Dry-run default + kill-switch + risk limits are Bolt 5 acceptance criteria, not later hardening. See "Safety layer" above. |
| Auth | Deferred by design originally; that deferral's own stated trigger (network-facing transport) fired during Bolt 3 (streamable-http is now default). Not yet re-resolved — see "Auth / security note" above for the current (unresolved) state. |
| stdio hang | Root cause not fully identified (extensive isolation ruled out env vars, `CREATE_NO_WINDOW`, thread offload, Windows Job Objects, Python 3.12 vs 3.14 — see `diagnostics/FINDINGS.md`). Do not use `stdio` for any tool that calls `MetaTrader5` until this is properly isolated and fixed. |
| SQLite under concurrent access | Fine for a single local server process; would need a real decision (WAL mode tuning, or a move to Postgres per this machine's schema-per-env rule) if ever run multi-process. |
| Demo vs live account | Current verified connection is `OctaFX-Demo`. Nothing in this charter authorizes pointing MT5-MCP at a live account — that's a distinct future decision, not implied by finishing Construction. |
| MT5 Python package platform lock-in | The `MetaTrader5` package is Windows-only (wraps the native terminal's IPC) — fine here since this machine is Windows, but worth noting if this ever needs to run elsewhere. |
| Broker-specific order type support | Resolved in Bolt 5: market/limit/stop (buy+sell) implemented, filling mode resolved per-symbol from OctaFX's actually-advertised bitmask (not assumed). `stop_limit`/`trailing_stop` explicitly out of scope — different request shape / not a native MT5 pending-order concept — future work, not silently dropped. |
| Kill-switch scope (Bolt 5 design decision) | Kill-switch and lot/open-position limits gate `place_order` only, not `modify_order`/`cancel_order`/`modify_position`/`close_position`/`close_all_positions` — a kill-switch must not trap an operator inside an existing position. Flagged explicitly for human review before Bolt 5 merged (see `BOLTS.md`); merged on that basis, not silently decided. Revisit if this reasoning is later judged wrong — it's a one-line change to widen the gate. |
| `modify_order`'s `expiration` parameter (found in final review) | Accepted, audited, but explicitly rejected with `unsupported_parameter` rather than silently applied — MT5's expiration semantics (`ORDER_TIME_SPECIFIED` vs `ORDER_TIME_SPECIFIED_DAY`, timestamp format) aren't implemented yet. Future work, not silently unsupported. |

---

## Initial Bolt backlog

See **[`BOLTS.md`](./BOLTS.md)** for the ordered Construction backlog.

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

**Decision:** MT5-MCP is a new, separate project. It does not replace or wrap `mt5-dev` — different protocol (proper MCP over stdio vs. a file-poll bridge), different capability (data + orders vs. chart annotation). Both can coexist.

---

## Scope for this Inception + first Construction pass

**In scope (first Construction pass — see `BOLTS.md` for exact Bolt ordering)**

- All tool categories from `SPEC.md` §4: market data (`get_historical_ohlcv`, `get_symbol_info`), live streaming + durable log (`subscribe_live_data`, `unsubscribe_live_data`, `get_stream_log`), order placement (`place_order`, `modify_order`, `cancel_order`), position management (`get_open_positions`, `modify_position`, `close_position`, `close_all_positions`), history/audit (`get_order_history`, `get_deal_history`, `get_position_history`)
- **Decision (execution sequencing):** order/position tools ship in the **same** first Construction pass as the read-only tools — not deferred to a later milestone. The tradeoff for shipping them together is that every execution tool defaults to a **dry-run/paper mode** (see "Safety layer" below) that must be explicitly disabled to touch a real account. The safety layer is not a follow-up Bolt; it ships alongside the feature.
- MetaTrader 5 as the only backend connector for this project. The spec is written backend-agnostic (§13 calls out pluggability to cTrader / REST brokers) — that pluggability is **not** built now; a single hard-wired `MetaTrader5`-package connector is the Inception decision. Revisit only if a second backend is actually needed.
- stdio MCP transport only (local process, spawned by the client) — no network listener, no port binding.

**Explicitly out of scope for this pass**

- Any second backend connector (cTrader, generic REST brokers)
- Network-facing transport (HTTP/SSE/Streamable HTTP) — stdio only for now; see the auth checkpoint below for what has to happen first if this changes
- CSS auth integration (deferred, see "Auth / security" below)
- `SPEC.md` §10 extensibility items (economic calendar, multi-account, portfolio-level risk, custom indicator filters, webhooks) — explicitly future work, not this pass
- Live (non-demo) trading — see "Safety layer": dry-run is the default and disabling it against a live account is a separate, explicit, human-made decision, not a side effect of finishing Construction

---

## Tech stack decision

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Language / runtime | Python 3.11+ | The `MetaTrader5` Python package is already installed on this machine and independently verified (this session) to pull live XAUUSD ticks against the real terminal; `mt5-dev` already established Python as this machine's MT5-integration language; the official MCP Python SDK covers the server side. |
| MCP SDK | Official `mcp` Python SDK (stdio server) | Standard, avoids hand-rolling the protocol. |
| Packaging | `pyproject.toml`, standard `pip`/`venv` | Matches `cineforge` and `mt5-dev` convention on this machine. |
| Package name | `mt5_mcp` | Fixed project identity. |
| Backend connector | `MetaTrader5` package only (hard-wired, not pluggable yet) | See scope decision above. |
| Stream log store | Local SQLite file (one DB, tables keyed by symbol+date) | Meets `SPEC.md` §7's "durable, time-range queryable, keyed by symbol+date" requirement without standing up Postgres/schema-per-env for a single-operator local tool. Revisit only if multi-process concurrent write access is ever needed. |
| Transport | MCP over stdio | Local single-operator tool; no port to reserve. Revisit at the Bolt 7 auth checkpoint if network transport is ever wanted. |
| Layout | `src/mt5_mcp/`, `tests/`, `docs/` (`docs/aidlc/` for AI-DLC), root `README.md`, `pyproject.toml`, `.gitignore`, `.env.example` | Machine convention (matches `cineforge`). |

This decision is **fixed** — do not re-decide during Construction.

---

## Architecture sketch

```
┌──────────────┐   MCP over stdio    ┌───────────────────────┐
│  MCP Client  │ ◄─────────────────► │   MT5-MCP server       │
│ (Claude Code/│   (local process)   │   (Python, mt5_mcp)    │
│  Desktop/    │                     │                        │
│  Cursor/etc) │                     │  tool dispatch +       │
└──────────────┘                     │  response envelope     │
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

## Auth / security note (open decision — deferred by design)

This machine's standing rule is centralized auth via the Centralized Security System (CSS) for anything beyond a public no-login static surface. MT5-MCP is not that — but per the `cineforge` precedent, a **single-operator local tool speaking MCP over stdio has no network attack surface to protect with CSS yet**: the client spawning the server process already has whatever access the operator's machine account has.

**Decision:** defer CSS integration, following the `cineforge` open-decision pattern — recorded here explicitly, not silently waived.

**This decision is void the moment MT5-MCP gains a network-facing transport** (HTTP/SSE/Streamable HTTP, or anything reachable off this machine). Bolt 7 is the checkpoint: before any such transport ships, this section must be revisited and either CSS gets integrated or a fresh documented waiver is written for the new surface.

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
| DEV | E: | None — stdio transport, no listener | N/A for Inception |
| PREPROD | F: | — | Not applicable while stdio-only |
| PROD | G: | — | Not applicable while stdio-only |
| RELEASES | H: | — | N/A for Inception |

Nothing is deployed to F: or G: during Inception; a local stdio MCP server has no promote-pipeline deploy step in the traditional sense (it's invoked by the client, not a long-running bound service) — this will need to be revisited if/when Bolt 7's network-transport decision changes the shape of "deploy."

---

## Known risks / open questions

| Topic | Note |
|-------|------|
| Live trading risk | Dry-run default + kill-switch + risk limits are Bolt 5 acceptance criteria, not later hardening. See "Safety layer" above. |
| Auth | Deferred by design (see above) — void once network-facing transport exists. |
| SQLite under concurrent access | Fine for a single local server process; would need a real decision (WAL mode tuning, or a move to Postgres per this machine's schema-per-env rule) if ever run multi-process. |
| Demo vs live account | Current verified connection is `OctaFX-Demo`. Nothing in this charter authorizes pointing MT5-MCP at a live account — that's a distinct future decision, not implied by finishing Construction. |
| MT5 Python package platform lock-in | The `MetaTrader5` package is Windows-only (wraps the native terminal's IPC) — fine here since this machine is Windows, but worth noting if this ever needs to run elsewhere. |
| Broker-specific order type support | `SPEC.md` §4.3 lists broker-specific variants as best-effort ("if supported by the backend") — Octa Markets' actual supported set needs to be checked against MT5's `ORDER_TYPE_*` constants during Bolt 5, not assumed. |

---

## Initial Bolt backlog

See **[`BOLTS.md`](./BOLTS.md)** for the ordered Construction backlog.

# MT5-MCP — AI-DLC Inception Charter

**Phase:** Inception (Day 1)
**Status:** Folder scaffolded — charter not yet written, awaiting brainstorming
**Next phase:** Construction (Bolt-sized diffs + evidence), once this charter and `BOLTS.md` are filled in

---

## Purpose

_TBD — to be filled in during brainstorming._

Known starting context (from prior session work, not yet a decision):
- MT5 terminal confirmed installed at `E:\ProgramFiles\MT5\terminal64.exe` (Octa Markets MetaTrader 5, demo account `213878432`).
- Python `MetaTrader5` package installed and verified able to pull live XAUUSD ticks via `mt5.initialize(path=...)`.
- Open question: what MT5-MCP actually exposes as an MCP server (which tools/resources), to which client(s), read-only market data vs. trade actions, and how it relates to the existing `mt5-dev` project (`E:\MyWorkspace\mt5-dev`, a separate file-bridge tool for chart drawing).

## Scope for this Inception + first Construction pass

**In scope**
- _TBD_

**Explicitly out of scope for this pass**
- _TBD_

## Tech stack decision

_TBD — not yet decided._

## Architecture sketch

_TBD._

## Auth / security note (open decision)

This machine's standing rule is centralized auth via the Centralized Security System (CSS) for anything beyond a public no-login static surface. If MT5-MCP ever exposes trade-execution capability, note the standing permanent gate: **live trade execution unlock stays human-only**, independent of any trust tier (see `E:\MyAgent\workflow\aidlc\README.md`, permanent gate list). This must be revisited explicitly once scope is decided — not silently waived.

## Secrets handling

_TBD — MT5 account credentials (if any beyond the terminal's own saved session) must never be committed to git; env vars only, `.env` gitignored, `.env.example` placeholders only._

## Deploy topology (target, not yet real)

| Role | Drive | Ports | Status |
|------|-------|-------|--------|
| DEV | E: | _TBD_ | Not reserved yet |
| PREPROD | F: | — | Not reserved yet |
| PROD | G: | — | Not reserved yet |
| RELEASES | H: | — | N/A for Inception |

## Known risks / open questions

| Topic | Note |
|-------|------|
| Relationship to `mt5-dev` | Existing project already bridges MT5 ↔ file-based chart-draw commands. Does MT5-MCP replace it, wrap it, or sit alongside it as a separate MCP-protocol surface? |
| Trade execution | If in scope, this is a permanent human-gated capability per machine rules — needs explicit design, not default-on. |
| Demo vs live account | Current verified connection is to an `OctaFX-Demo` account — scope must state clearly whether live accounts are ever in scope. |

## Initial Bolt backlog

See **[`BOLTS.md`](./BOLTS.md)** — not yet written, pending brainstorming.

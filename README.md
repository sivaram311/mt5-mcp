# MT5-MCP

MT5-MCP is an MCP (Model Context Protocol) server exposing MetaTrader 5 — market data, live streaming with a durable log, and full order/position lifecycle management — as tools for AI agents (Claude, Cursor, or any MCP-aware client). Runs locally over stdio against the MT5 terminal already installed on this machine; MetaTrader 5 is the only backend connector for now.

**Current status: Construction, Bolt 1 shipped.** Server skeleton, MCP stdio transport, and response envelope are live — no real MT5 connection yet (Bolt 2).

## Docs

- **[AI-DLC Inception charter](docs/aidlc/INCEPTION.md)** — purpose, scope, stack, architecture sketch, safety layer, risks
- **[Tool & data model spec](docs/aidlc/SPEC.md)** — the full tool surface (market data, streaming, orders, positions, history), data models, filters, error envelope
- **[Construction Bolt backlog](docs/aidlc/BOLTS.md)** — ordered, reviewable units of work; Bolt 5 (order/position tools + safety layer) is the highest-risk item and always requires human review on merge

## Safety note

Order and position tools default to a **dry-run/paper mode** — see `INCEPTION.md`'s "Safety layer" section before touching Bolt 5. Live (non-demo) account use is a distinct, explicit future decision, not implied by finishing Construction.

## Getting started

```
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\pytest -q
.venv\Scripts\mt5-mcp        # runs the stdio MCP server (or: python -m mt5_mcp)
```

Copy `.env.example` to `.env` if you need to override the MT5 terminal path. No secrets required yet — the current `ping` tool doesn't touch MT5 at all (that's Bolt 2).

See `docs/aidlc/BOLTS.md` for what ships next.

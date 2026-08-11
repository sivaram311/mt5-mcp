# MT5-MCP

MT5-MCP is an MCP (Model Context Protocol) server exposing MetaTrader 5 — market data, live streaming with a durable log, and full order/position lifecycle management — as tools for AI agents (Claude, Cursor, or any MCP-aware client). Runs locally against the MT5 terminal already installed on this machine; MetaTrader 5 is the only backend connector for now.

**Current status: Bolts 1–5 shipped.** Market data, live streaming, and order/position tools (with a mandatory dry-run-by-default safety layer) are all live. See `docs/aidlc/BOLTS.md` for what's next (history/audit tools, the auth checkpoint).

## Docs

- **[AI-DLC Inception charter](docs/aidlc/INCEPTION.md)** — purpose, scope, stack, architecture, safety layer, risks
- **[Tool & data model spec](docs/aidlc/SPEC.md)** — the original full tool surface spec
- **[Construction Bolt backlog](docs/aidlc/BOLTS.md)** — ordered, reviewable units of work and what each one actually shipped
- **[Incident/investigation notes](diagnostics/FINDINGS.md)** — the stdio-hang investigation; **do not use `stdio` transport for any tool that touches MetaTrader5** (see below)

## Install

```
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\pytest -q
```

Copy `.env.example` to `.env` and set `MT5_PATH` to your terminal's `terminal64.exe` (auto-detection works if the terminal is already running, but an explicit path is more reliable — see `diagnostics/FINDINGS.md` for why relying on auto-detection is risky).

## Run the server

```
.venv\Scripts\mt5-mcp
# or: .venv\Scripts\python.exe -m mt5_mcp
```

Default transport is **`streamable-http`**, bound to `127.0.0.1:3403` (loopback only, reserved in `E:\MyAgent\workflow\ports\REGISTRY.md`). This is deliberate — `stdio` was the original design but was found to hang indefinitely on any tool that calls into `MetaTrader5` (root cause never identified despite extensive isolation; see `diagnostics/FINDINGS.md`). `stdio` is still available (`MT5_MCP_TRANSPORT=stdio`) but should not be trusted for anything beyond the `ping` tool.

## Connect an MCP client

**Claude Code:**
```
claude mcp add --transport http mt5-mcp http://127.0.0.1:3403/mcp
```

**Claude Desktop / other JSON-config clients**, add to the client's MCP server config:
```json
{
  "mcpServers": {
    "mt5-mcp": {
      "url": "http://127.0.0.1:3403/mcp"
    }
  }
}
```
(Exact key names for HTTP-transport servers vary by client — check that client's docs if this doesn't work as-is.)

The server must already be running (`mt5-mcp` in a terminal) before the client connects — this project doesn't yet register itself as a background/managed process.

## Tools

All tools return the standard envelope: `{success, error_code, error_message, retryable, request_id, data}`.

**Market data** (read-only)
- `get_historical_ohlcv(symbol, timeframe, from_date?, to_date?, from_bar?, to_bar?, limit?, include_volume?, include_spread?, session_filter?, price_type?, only_completed_bars?)` — `timeframe`: M1/M5/M15/M30/H1/H4/D1/W1/MN1. `price_type` only supports `"bid"` (or omitted) — ask/mid/last not implemented.
- `get_symbol_info(symbol)` — contract size, tick size/value, digits, swaps, margin, current bid/ask.

**Live streaming** (durable log, not true push — see below)
- `subscribe_live_data(symbol, data_types?)` — `data_types`: `["tick"]` and/or `["bar"]` (fixed M1). Returns a `subscription_id`.
- `unsubscribe_live_data(subscription_id?, symbol?)` — stop by id or all subscriptions for a symbol.
- `get_stream_log(symbol, from_time?, to_time?, data_type?, limit?)` — read back logged ticks/bars, including after unsubscribing.

**Orders** (execution — dry-run by default, see Safety below)
- `place_order(symbol, order_type, side, volume, price?, stop_loss?, take_profit?, comment?, magic_number?, deviation?, client_order_id?)` — `order_type`: `market`/`limit`/`stop`. `side`: `buy`/`sell`. `price` required for limit/stop. `client_order_id` is logged for traceability only — it does **not** deduplicate retries.
- `modify_order(ticket, price?, stop_loss?, take_profit?, volume?, expiration?)` — modifies a pending order. `expiration` is not implemented — passing it raises `unsupported_parameter`.
- `cancel_order(ticket)` — cancels a pending order.

**Positions**
- `get_open_positions(symbol?, magic_number?, comment?)` — read-only.
- `modify_position(ticket, stop_loss?, take_profit?)` — changes SL/TP on an open position.
- `close_position(ticket, volume?)` — full or partial close.
- `close_all_positions(symbol?, side?, magic_number?)` — bulk close.

**Health**
- `ping()` — no MT5 connection required, works even if MT5_PATH is wrong.

Not implemented: `stop_limit`/`trailing_stop` order types, `get_order_history`/`get_deal_history`/`get_position_history` (Bolt 6), CSS auth (deferred, see `INCEPTION.md`).

## Safety layer (order/position tools)

- **Dry-run is on by default.** No real order ever gets placed unless you explicitly set `MT5_MCP_DRY_RUN=false` in the server's environment. It's an env var, not a tool parameter — an agent can't flip it mid-conversation. Dry-run responses use real current market prices, so they're shaped like a real fill would be.
- **Kill-switch**: create a file at `MT5_MCP_KILL_SWITCH_PATH` (default: `<repo root>\MT5_MCP_KILL_SWITCH`) and `place_order` (only — the sole action that creates new exposure) is blocked immediately, independent of the dry-run setting. Delete the file to re-enable. `modify_order`/`cancel_order`/`modify_position`/`close_position`/`close_all_positions` are deliberately **not** blocked by the kill-switch — an emergency stop shouldn't also trap you inside an existing position.
- **Limits**: `MT5_MCP_MAX_LOT_SIZE` (default `0.10`) and `MT5_MCP_MAX_OPEN_POSITIONS` (default `3`) — both enforced server-side on `place_order`, not just documented.
- **Audit log**: every order/position attempt — dry-run or real, success or failure — is written to a local SQLite log (`MT5_MCP_AUDIT_LOG_DB`, default `<repo root>\mt5_mcp_audit_log.db`), before the tool call returns.
- **Nothing here authorizes a live (non-demo) account.** The connector only ever points at whatever `MT5_PATH`'s terminal is logged into — currently `OctaFX-Demo`. Pointing this at a real account is a distinct, explicit decision this project has not made.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `MT5_PATH` | auto-detect | Path to `terminal64.exe` |
| `MT5_MCP_TRANSPORT` | `streamable-http` | `stdio` \| `sse` \| `streamable-http` — see the stdio warning above |
| `MT5_MCP_HTTP_HOST` | `127.0.0.1` | Bind host for http/sse transports |
| `MT5_MCP_HTTP_PORT` | `3403` | Bind port (reserved in the port registry) |
| `MT5_MCP_DRY_RUN` | `true` | Must be exactly `false` to allow real order execution; anything else (including unset/typo) stays dry-run |
| `MT5_MCP_KILL_SWITCH_PATH` | `<repo root>\MT5_MCP_KILL_SWITCH` | If this file exists, `place_order` is blocked |
| `MT5_MCP_MAX_LOT_SIZE` | `0.10` | Max volume per `place_order` call |
| `MT5_MCP_MAX_OPEN_POSITIONS` | `3` | Max concurrent open positions before `place_order` is blocked |
| `MT5_MCP_STREAM_LOG_DB` | `<repo root>\mt5_mcp_stream_log.db` | SQLite path for `subscribe_live_data`/`get_stream_log` |
| `MT5_MCP_AUDIT_LOG_DB` | `<repo root>\mt5_mcp_audit_log.db` | SQLite path for the order/position audit trail |

All `.db` files are gitignored — they're local runtime state, not source.

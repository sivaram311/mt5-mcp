# MT5-MCP — Tool & Data Model Spec (v1, user-provided)

**Source:** user brainstorming input, 2026-08-11 session. Recorded verbatim as the reference spec for the tool surface, data models, filtering, streaming/logging design, error handling, security considerations, and naming convention. Sequencing/scope decisions made against this spec (what ships in which Bolt, auth approach, dry-run default, etc.) live in [`INCEPTION.md`](./INCEPTION.md) and [`BOLTS.md`](./BOLTS.md) — this doc is the "what", those are the "when/how".

---

## 4. Core Tool Categories

### 4.1 Market Data Tools

#### Tool: `get_historical_ohlcv`
Retrieves historical OHLCV (+ optional volume, tick volume, spread) data for a symbol.

**Required Parameters**
- `symbol` (string) — e.g. "XAUUSD", "EURUSD", "BTCUSD"
- `timeframe` (string) — M1, M5, M15, M30, H1, H4, D1, W1, MN1 (or broker-specific)

**Filter Parameters** (all optional, composable)
- `from_date` / `to_date` (ISO 8601 or Unix timestamp)
- `from_bar` / `to_bar` (relative bar offsets)
- `limit` (max number of candles)
- `include_volume` (boolean)
- `include_spread` (boolean)
- `session_filter` (e.g. "London", "NewYork", "Asian", "Full")
- `price_type` (bid / ask / mid / last)
- `only_completed_bars` (boolean)

**Response Shape**
- Array of candle objects containing: time, open, high, low, close, volume (if requested), tick_volume, spread, and any custom fields.

---

#### Tool: `get_symbol_info`
Returns static and dynamic metadata for a symbol.

Returns: contract size, tick size, tick value, digits, trade mode, swap rates, margin requirements, trading sessions, current bid/ask, etc.

---

### 4.2 Live Data Streaming Tools

#### Tool: `subscribe_live_data`
Starts a live market data stream for one or more symbols.

**Parameters**
- `symbol` or `symbols[]`
- `data_types` — ticks, M1 bars, depth-of-market, or custom
- `log_to_storage` (boolean) — if true, every update is written to the persistent log
- `buffer_size` / `flush_interval`

**Behavior**
- Opens a persistent stream channel back to the MCP client.
- Simultaneously writes structured log entries (timestamp + OHLCV or tick) to durable storage.
- Supports multiple concurrent subscriptions.

#### Tool: `unsubscribe_live_data`
Stops an active stream by subscription ID or symbol.

#### Tool: `get_stream_log`
Retrieves previously logged live data for a symbol within a time range.
This allows an agent to "catch up" or reconstruct the full OHLCV sequence even after a disconnect.

**Parameters**
- `symbol`
- `from_time` / `to_time`
- `data_type` (tick / bar)
- `limit`

---

### 4.3 Order Placement Tools

#### Tool: `place_order`
Places a new order of any supported type.

**Common Parameters**
- `symbol`
- `order_type` — market, limit, stop, stop_limit, trailing_stop, buy_stop, sell_stop, buy_limit, sell_limit, etc.
- `side` — buy / sell
- `volume` (lots or units)
- `price` (required for pending orders)
- `stop_loss` / `take_profit` (optional absolute prices or relative points)
- `trailing_stop` (points or distance)
- `comment` / `magic_number` / `expiration`
- `deviation` / `filling_mode` (FOK, IOC, Return)
- `client_order_id` (for idempotency)

**Order Type Coverage**
- Market orders
- Limit orders
- Stop orders
- Stop-Limit orders
- Trailing stop orders
- Broker-specific variants (if supported by the backend)

**Response**
- Order ticket / ID
- Current status
- Filled volume and average price (for market orders)
- Rejection reason if failed

---

#### Tool: `modify_order`
Modifies an existing pending order (price, SL, TP, expiration, volume).

#### Tool: `cancel_order`
Cancels a pending order by ticket or client order ID.

---

### 4.4 Position Management Tools

#### Tool: `get_open_positions`
Returns all open positions, optionally filtered by symbol, magic number, or comment.

#### Tool: `modify_position`
Modifies stop-loss, take-profit, or trailing stop of an open position.

#### Tool: `close_position`
Closes a position fully or partially (by volume).

#### Tool: `close_all_positions`
Bulk close with optional filters (symbol, side, magic).

---

### 4.5 History & Audit Tools

#### Tool: `get_order_history`
Retrieves historical orders within a date range.

**Filters**
- `symbol`
- `from_date` / `to_date`
- `order_type`
- `status` (filled, cancelled, rejected, expired)
- `magic_number`
- `comment_contains`

Returns full order details including comments.

#### Tool: `get_deal_history` / `get_trade_history`
Returns executed deals / closed trades with profit, commission, swap, and comments.

#### Tool: `get_position_history`
Historical closed positions with entry/exit details and comments.

---

## 5. Data Models (Conceptual)

### Candle / OHLCV
- time (UTC)
- open, high, low, close
- volume / tick_volume
- spread (optional)
- real_volume (if available)

### Tick
- time
- bid / ask / last
- volume
- flags

### Order
- ticket / order_id
- symbol, type, side, volume
- price, sl, tp
- status, filled_volume, average_price
- comment, magic, expiration
- create_time, update_time

### Position
- ticket / position_id
- symbol, side, volume
- open_price, current_price
- sl, tp, profit, swap, commission
- comment, magic
- open_time

### Stream Log Entry
- subscription_id
- symbol
- data_type
- timestamp
- payload (tick or candle)
- sequence_number

---

## 6. Filtering System

All historical data and history tools share a common filter philosophy:

- Time-based: absolute dates or relative offsets
- Symbol-based
- Status / type based
- Magic number / comment based
- Volume / price range (future extension)
- Session / trading hours filter

Filters are designed to be additive. Missing filters default to "no restriction".

---

## 7. Streaming & Logging Design

1. Client calls `subscribe_live_data`.
2. MCP opens a backend stream (WebSocket or native MT5 event).
3. Every update is:
   - Forwarded in near real-time to the MCP client
   - Appended to a durable log store (keyed by symbol + date)
4. Client can later call `get_stream_log` to retrieve any missing or historical live data.
5. Logs support efficient time-range queries and can be compacted or archived.

This dual stream + log approach allows agents to maintain continuity across restarts or network interruptions.

---

## 8. Error Handling & Status Codes

Every tool response includes:
- `success` (boolean)
- `error_code` (machine-readable)
- `error_message` (human-readable)
- `retryable` (boolean)
- Optional `request_id` for tracing

Common error categories:
- Invalid symbol
- Market closed
- Insufficient margin
- Invalid price / stops
- Order not found
- Rate limit / connection issues
- Backend connector failure

---

## 9. Security & Safety Considerations

- Authentication / authorization layer between MCP client and server
- Symbol and volume whitelist / blacklist (optional)
- Maximum risk limits (max lots, max open positions)
- Dry-run / paper-trading mode
- Audit log of every order and modification
- Kill-switch capability (disable all trading tools)
- Comment and magic number conventions for agent attribution

---

## 10. Extensibility Points

Future tools / filters that can be added without breaking the core:
- Economic calendar / news filter
- Sentiment or alternative data injection
- Multi-account support
- Portfolio-level risk queries
- Custom indicator values as filters
- Webhook / event push for order fills
- Account information & margin tools

---

## 11. Recommended Tool Naming Convention

All tools follow a consistent verb-noun pattern:

- `get_*`
- `subscribe_*` / `unsubscribe_*`
- `place_*`
- `modify_*`
- `cancel_*`
- `close_*`

This keeps the tool surface predictable for AI agents.

---

## 12. Summary of Primary Tools

| Category              | Tools |
|-----------------------|-------|
| Historical Data       | `get_historical_ohlcv`, `get_symbol_info` |
| Live Streaming        | `subscribe_live_data`, `unsubscribe_live_data`, `get_stream_log` |
| Order Placement       | `place_order` |
| Order Management      | `modify_order`, `cancel_order` |
| Position Management   | `get_open_positions`, `modify_position`, `close_position`, `close_all_positions` |
| History & Audit       | `get_order_history`, `get_deal_history`, `get_position_history` |

---

## 13. Implementation Notes (Non-Code)

- Backend connector should be pluggable (MT5, cTrader, REST brokers, etc.).
- Streaming should prefer native push events over polling.
- Persistent stream logs should be queryable by time range and support pagination.
- All timestamps should be normalized to UTC.
- Volume and price precision must respect symbol digits and lot step.
- Comments should be treated as first-class data for both orders and deals.

---

This specification provides a complete blueprint for a production-grade Trading MCP focused on data retrieval, live streaming with durable logs, and full order/position lifecycle management. It is intentionally backend-agnostic so it can sit in front of MetaTrader 5, proprietary execution engines, or multi-broker routers. **For this project, the initial backend connector is MetaTrader 5 only** — see `INCEPTION.md` for the pluggability decision.

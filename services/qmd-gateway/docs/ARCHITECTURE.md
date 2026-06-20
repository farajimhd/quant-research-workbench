# QMD Gateway Architecture

## Purpose

`qmd-gateway` is the live market-data gateway for the quote/trade regime. It subscribes to Massive stock trades and quotes, builds live bars and indicators, emits Massive-only scanner primitives, and writes replayable market data to ClickHouse.

The gateway is deliberately narrow. It should be fast, observable, and replaceable. It should not know about trading accounts, broker orders, portfolio state, `conid`, float, short interest, logos, fundamentals, or the final UI scanner.

## Boundary

```text
Massive websocket
  -> qmd-gateway
      -> in-memory market state
      -> in-memory bars
      -> in-memory indicators
      -> Massive-only scanner primitives
      -> compact live event stream/table
      -> ClickHouse q_live compact events/bars/optional raw/optional indicators
      -> local REST/websocket API
  -> app backend
      -> reference joins
      -> IBKR execution/account state
      -> final scanner and trading decisions
      -> chart history merge
  -> UI
```

The gateway outputs market-data primitives. The app backend combines those primitives with reference data and broker state.

## Module Ownership

| Module | Owns | Input | Output | Does Not Do |
|---|---|---|---|---|
| `config.rs` | Env configuration | `QMD_*`, `MASSIVE_API_KEY` | `GatewayConfig` | Validate trading strategy |
| `event.rs` | Massive payload parsing | Massive websocket JSON | `MarketEvent::Trade`, `MarketEvent::Quote` | Business logic |
| `massive.rs` | Websocket connection and fan-out | Massive websocket | Queues for state, bars, indicators, compact writer, optional raw writer | Blocking writes |
| `state.rs` | Simple latest market snapshot | Market events | `/snapshot/scanner`, `/snapshot/ticker` | Final scanner scoring |
| `bars.rs` | Live bar aggregation | Market events | `BarRow`, `live_market_bars` | Historical chart storage |
| `indicators.rs` | Streaming tick and bar indicators | Market events, closed bars | Tick snapshots, `IndicatorRow` | Wide research feature generation |
| `scanner.rs` | Massive-only scanner primitives | Closed bars | Primitive snapshot/stream | Broker/reference-aware signals |
| `compact_event.rs` | Live compact event contract | Market events | `/stream/compact-events`, `live_market_events_v1` | Encoder chunk construction |
| `clickhouse.rs` | Optional raw Massive persistence | Market events | `live_massive_trades`, `live_massive_quotes` | Primary ML surface |
| `gapfill.rs` | Massive REST gap fill | Live compact latest timestamps, Massive REST | Same event fan-out as websocket, gap-fill audit rows | Deep historical repair |
| `replay.rs` | Raw-data replay | ClickHouse raw rows | Same in-memory pipeline as live | Re-persist raw events |
| `metrics.rs` | Operational counters | Hot-path observations | `/metrics` payload | External monitoring service |
| `api.rs` | Local API and websocket streams | Shared stores | REST/websocket responses | UI-specific formatting |

## Live Data Flow

```text
Massive websocket text
  -> event parser
  -> market state
  -> event broadcast stream
  -> bar queue
  -> indicator tick queue
  -> compact event queue
  -> optional raw ClickHouse queue
```

Every hot-path send uses `try_send`. If a downstream queue is full, the gateway drops that downstream item, increments a counter, and keeps reading Massive data.

## Bar Flow

```text
MarketEvent
  -> shard by ticker
  -> update all configured timeframes
  -> keep current open bar in memory
  -> close bar when timeframe ends
  -> send closed bar to:
       indicator engine
       scanner primitive engine
       ClickHouse bar writer
```

Bars are aligned to the top of their timeframe using event time. A `5m` bar starts at `:00`, `:05`, `:10`, and so on. A `1h` bar starts at the top of the hour.

## Indicator Flow

Tick indicators are updated from quotes/trades. Bar indicators are updated only from closed bars. This avoids rescanning stored rows while live data is arriving.

Tick samples are retained in memory for `QMD_TICK_INDICATOR_WINDOW_SECONDS`, default `300`. Fields with a fixed horizon, such as `trade_rate_60s`, still use exactly the last 60 seconds inside that retained window.

## Scanner Primitive Flow

Scanner primitives are evaluated from closed `BarRow` values. They are Massive-only. Current primitive families are:

- tape acceleration
- volume shock
- liquidity recovery
- VWAP reclaim
- high-momentum bar

These primitives are candidates for the app backend. They are not final trade signals.

## Persistence Flow

Default durable writes:

| Table | Written By | Default | Purpose |
|---|---|---:|---|
| `live_market_events_v1` | `compact_event.rs` | yes | Live ML-serving event stream/table |
| `live_massive_trades` | `clickhouse.rs` | no | Optional raw trade replay/debug source |
| `live_massive_quotes` | `clickhouse.rs` | no | Optional raw quote replay/debug source |
| `live_market_bars` | `bars.rs` | yes | Published bar history |
| `live_market_indicators` | `indicators.rs` | no | Optional promoted indicator rows |
| `qmd_gap_fill_runs` | `gapfill.rs` | yes | Gap-fill audit log |

Gap fill is a recent-window repair path. It converts REST rows to normalized
events and uses the same fan-out as websocket ingest. Deeper historical history
belongs to the read-only `market_sip_compact.events` table maintained by the
flatfile pipelines.

Set `QMD_PERSIST_INDICATORS=true` only after choosing the indicator set that should become durable.

## Replay Flow

Replay is disabled by default. When enabled, `replay.rs` reads raw Massive rows from ClickHouse and sends them through market state, bars, indicators, and scanner primitives. It does not write raw rows again.

## Out Of Scope For Gateway

These belong in the app backend:

- IBKR account, order, fill, portfolio, and execution logic.
- `conid`, float, short interest, fundamentals, logos, and news enrichment.
- Final scanner rows shown in the UI.
- Final trading signals that combine Massive data with broker/reference/account context.
- Chart history merge between ClickHouse historical data and live gateway tail.
- Trading-session lifecycle and UI workspace state.

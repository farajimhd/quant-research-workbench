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
| `compact_event.rs` | Live compact event contract, live ring buffers, sorted persistence ordinals | Market events | `/stream/compact-events`, `/snapshot/compact-events/{ticker}`, `live_market_events_v1` | Encoder chunk construction |
| `clickhouse.rs` | Optional raw Massive persistence | Market events | `live_massive_trades`, `live_massive_quotes` | Primary ML surface |
| `gapfill.rs` | Startup live coverage audit, Massive REST tail repair, historical flatfile planning | Live compact event rows, Massive REST, historical continuity rows | Same event fan-out as websocket, gap-fill audit rows, coarse coverage manifest | Deep historical row generation |
| `replay.rs` | Raw-data replay | ClickHouse raw rows | Same in-memory pipeline as live | Re-persist raw events |
| `metrics.rs` | Operational counters | Hot-path observations | `/metrics` payload | External monitoring service |
| `api.rs` | Local API and websocket streams | Shared stores | REST/websocket responses | UI-specific formatting |

## Live Data Flow

```text
Massive websocket text
  -> event parser
  -> market state
  -> best-effort event broadcast stream
  -> bar queue
  -> indicator tick queue
  -> compact event queue
  -> optional raw ClickHouse queue
```

Required data-path sends are awaited. If a required queue is full, the gateway
backpressures live ingest instead of dropping canonical work. Local websocket
broadcasts are the exception: they are best effort and may skip updates when no
client is connected or a client cannot keep up.

## Compact Event Flow

Live compact events have two separate paths:

```text
low-latency path:
  MarketEvent
    -> compact LiveCompactEvent with arrival_sequence and ordinal=0
    -> /stream/compact-events
    -> per-ticker in-memory ring buffer
    -> /snapshot/compact-events/{ticker}?limit=128

persistence path:
  same compact LiveCompactEvent
    -> per-ticker reorder buffer
    -> sort by sip_timestamp_us, source_sequence, event_type, arrival_sequence
    -> assign final ticker-local ordinal
    -> batch insert q_live.live_market_events_v1
    -> append q_live.live_event_ordinal_continuity snapshots
```

The live ML/app path does not wait for the persistence reorder watermark.
Readers that need a model context should request a recent window from the
in-memory buffer and sort by `sip_timestamp_us, source_sequence, event_type,
arrival_sequence` before taking the latest 128 events. The ClickHouse ordinal is
for durable replay/audit and is assigned only after the persistence buffer is
sorted.

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

Bars also include local `estimated_luld_*` fields for scanner and chart risk.
These are computed from quote/trade-derived state inside the bar shard, not from
Massive's official LULD websocket. The estimate keeps a five-minute rolling
simple average of valid trade prices per ticker as the reference price, applies
default Tier 2 LULD parameters, and snapshots estimated bands/proximity into
each bar. The fields are intentionally labeled as estimates because official
Tier 1/ETP classification and SIP eligible-trade handling are not yet part of
the QMD hot path.

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
| `live_event_ordinal_continuity` | `compact_event.rs` | yes | Append-only live ticker ordinal snapshots |
| `live_massive_trades` | `clickhouse.rs` | no | Optional raw trade replay/debug source |
| `live_massive_quotes` | `clickhouse.rs` | no | Optional raw quote replay/debug source |
| `live_market_bars` | `bars.rs` | yes | Published bar history |
| `live_market_indicators` | `indicators.rs` | no | Optional materialized bar-level indicator rows |
| `qmd_gap_fill_runs` | `gapfill.rs` | yes | Gap-fill audit log |
| `qmd_market_coverage_manifest_v1` | `gapfill.rs` | yes | Coarse startup repair and historical flatfile planning manifest |
| `qmd_live_event_coverage_v1` | `compact_event.rs`, `bars.rs`, `gapfill.rs` | yes | Recent q_live coverage manifest for compact events and bars |
| `qmd_flatfile_event_coverage_v1` | `gapfill.rs` | yes | Historical flatfile coverage manifest |

Startup maintenance audits recent `q_live.live_market_events_v1` rows for
structural ordinal issues before websocket ingest begins. It does not infer
missing time coverage from min/max timestamps. Recent time gaps are detected
from `qmd_live_event_coverage_v1`. Live streaming writes one compact-event
confirmation row and one bar confirmation row per run. A time range is treated
as covered only where those two confirmations overlap, or where a completed
REST repair row explicitly covers that interval. This keeps compact events,
continuity, and bars coherent.

Clean recent gaps are repaired with Massive REST rows through the same fan-out
as websocket ingest. The repair covers the current market day plus
`QMD_RECENT_LIVE_PRIOR_MARKET_DAYS` prior US market sessions, inside the
04:00-20:00 ET extended-hours window. During streaming hours, QMD starts the
websocket first and repairs tickers only after they are discovered from live
compact events. Outside streaming hours, it uses latest q_live symbols, then the
latest historical `market_sip_compact.events` symbols if q_live is empty.
Intervals without a discovered ticker set remain open and are not marked clean.
Structural ordinal corruption is recorded in the manifest and left for explicit
rebuild; QMD does not rewrite committed historical rows silently.

After-hours historical planning compares the read-only
`market_sip_compact.events_ordinal_continuity` coverage with the configured
safe lag and prints or launches the flatfile `download_update_events.py`
command. Deeper historical history belongs to the read-only
`market_sip_compact.events` table maintained by the flatfile pipelines. QMD live
events are never merged into that historical table.

`QMD_PERSIST_INDICATORS` defaults to `false` because the current bar-level indicators can be recomputed from `live_market_bars`. Enable it only when a run specifically needs a materialized indicator table.

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

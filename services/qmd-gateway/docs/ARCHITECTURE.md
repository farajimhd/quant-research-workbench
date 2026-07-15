# QMD Gateway Architecture

## Purpose

`qmd-gateway` is the live market-data gateway for the quote/trade regime. It subscribes to Massive stock trades and quotes, builds live bars and indicators, emits Massive-only scanner primitives, and writes replayable market data to ClickHouse.

The crate also exports the existing processing modules as `qmd_core`. The live
binary and the separate Rust historical gateway depend on these exact modules;
there is no copied event decoder or bar implementation.

The gateway is deliberately narrow. It should be fast, observable, and replaceable. It should not know about trading accounts, broker orders, portfolio state, `conid`, float, short interest, logos, fundamentals, or the final UI scanner.

## Boundary

```text
Massive websocket
  -> qmd-gateway
      -> in-memory market state
      -> in-memory bars
      -> in-memory indicators
      -> in-memory abnormal market-state overlay
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
| `bars.rs` | Memory-only enriched scanner/indicator bars | Market events | `BarRow`, snapshots, downstream scanner/indicator inputs | Durable bar storage |
| `live_market_state.rs` | Live abnormal market-state overlay | Market events, closed bars | `/snapshot/live-market-state`, `/stream/live-market-state`, `live_symbol_market_event_v1` | Reference identity/routing decisions |
| `indicators.rs` | Streaming tick and bar indicators | Market events, closed bars | Tick snapshots, `IndicatorRow` | Wide research feature generation |
| `scanner.rs` | Massive-only scanner primitives | Closed bars | Primitive snapshot/stream | Broker/reference-aware signals |
| `compact_event.rs` | Historical-parity live encoding, ring buffers, bounded reorder, batched persistence/audits | Market events, ClickHouse references | `/stream/compact-events`, `/snapshot/compact-events/{ticker}`, `q_live.events` | Encoder chunk construction |
| `market_calendar.rs` | Cached Rust Massive status/holiday authority | Massive status and upcoming-holiday APIs | Session and close decisions | Live event processing |
| `flatfile.rs` | Signed remote flatfile discovery | Massive S3 metadata | Quote/trade readiness | Historical ingestion |
| `intraday_bars.rs` | Required canonical intraday bars | Sanitized compact events | `/stream/intraday-bars`, `intraday_family_bars_v2` | Enriched scanner calculations |
| `clickhouse.rs` | Optional raw Massive persistence | Market events | `live_massive_trades`, `live_massive_quotes` | Primary ML surface |
| `gapfill.rs` | Startup live coverage audit, Massive REST tail repair, historical flatfile planning | Live compact event rows, Massive REST, historical continuity rows | Same event fan-out as websocket, gap-fill audit rows, coarse coverage manifest | Deep historical row generation |
| `metrics.rs` | Operational counters | Hot-path observations | `/metrics` payload | External monitoring service |
| `api.rs` | Local API and websocket streams | Shared stores | REST/websocket responses | UI-specific formatting |

Historical source selection is outside this binary:

```text
Massive websocket -> qmd-gateway ---------+
                                           +-> shared qmd_core event/bar contracts
events_YYYY ------> qmd-history-gateway --+
```

This keeps live collection state isolated while making decoder and bar drift a
Rust dependency/test failure.

## Live Data Flow

```text
Massive websocket text
  -> event parser
  -> market state
  -> best-effort event broadcast stream
  -> bar queue
  -> indicator tick queue
  -> abnormal market-state queue
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
    -> compact LiveCompactEvent with arrival_sequence
    -> /stream/compact-events
    -> per-ticker in-memory ring buffer
    -> /snapshot/compact-events/{ticker}?limit=128

persistence path:
  same compact LiveCompactEvent
    -> per-ticker reorder buffer
    -> sort by sip_timestamp_us, source_sequence, event_type, arrival_sequence
    -> batch insert the singular daily-partitioned q_live.events table
    -> batch condition/tape overflow and unknown-code audit rows
```

The live ML/app path does not wait for the persistence reorder watermark.
Readers that need a model context should request a recent window from the
in-memory buffer and sort by `sip_timestamp_us, source_sequence, event_type,
arrival_sequence` before taking the latest 128 events. Live storage has no
durable ordinal; historical ordinals remain local to `events_YYYY`.

## Bar Flows

```text
MarketEvent
  -> shard by ticker
  -> update all configured timeframes
  -> keep current open bar in memory
  -> close bar when timeframe ends
  -> send closed bar to:
       indicator engine
       scanner primitive engine

sanitized LiveCompactEvent
  -> shard by ticker
  -> aggregate sparse 100ms trade/quote-family bars
  -> roll closed 100ms bars into configured parent resolutions
  -> persist only to q_live.intraday_family_bars_v2
  -> publish /stream/intraday-bars
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

## Live Abnormal Market-State Flow

```text
MarketEvent and closed BarRow
  -> live_market_state.rs
  -> update in-memory active abnormal states
  -> append durable transition only when a special state opens/closes
  -> broadcast transition to /stream/live-market-state
```

This flow deliberately does not persist ordinary `normal` state. The active
state map is the low-latency current overlay for scanner/order gates. The
ClickHouse table is an audit stream for exceptional states that can affect
tradability or risk review.

Default sources:

- closed 1s bars open/close estimated LULD near/breach states
- closed 1s bars open/close locked/crossed quote states
- configured quote/trade condition ids open/close `condition_halt`

The gateway treats this as a live overlay only. Reference tradability and broker
routing remain owned by the reference gateway and broker/order services.

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
| `events` | `compact_event.rs` | yes | Live ML-serving event stream/table |
| `live_massive_trades` | `clickhouse.rs` | no | Optional raw trade replay/debug source |
| `live_massive_quotes` | `clickhouse.rs` | no | Optional raw quote replay/debug source |
| `intraday_family_bars_v2` | `intraday_bars.rs` | yes | Rolling sparse family bars from 100ms through 1h |
| `live_symbol_market_event_v1` | `live_market_state.rs` | yes | Abnormal live market-state transition audit |
| `live_market_indicators` | `indicators.rs` | no | Optional materialized bar-level indicator rows |
| `qmd_gap_fill_runs` | `gapfill.rs` | yes | Gap-fill audit log |
| `qmd_market_coverage_manifest_v1` | `gapfill.rs` | yes | Coarse startup repair and historical flatfile planning manifest |
| `qmd_live_event_coverage_v1` | `compact_event.rs`, `intraday_bars.rs`, `gapfill.rs` | yes | Recent q_live coverage manifest for compact events and canonical intraday bars |
| `qmd_flatfile_coverage_v2` | `gapfill.rs` | yes | Per-session, per-source remote and historical coverage |
| `qmd_compact_event_issue_v1` | `compact_event.rs` | yes | Full-identity overflow/unknown condition or tape audits |

Startup maintenance audits recent `q_live.events` rows for
duplicate canonical identities before websocket ingest begins. It does not infer
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
latest historical `market_sip_compact.events_YYYY` symbols if q_live is empty.
Intervals without a discovered ticker set remain open and are not marked clean.
Canonical identity corruption is recorded in the manifest and left for explicit
rebuild; QMD does not rewrite committed historical rows silently.

After 08:00 ET, historical planning compares signed remote quote/trade object
availability with read-only `market_sip_compact.events_ordinal_continuity`.
The Rust calendar handles weekends, holidays, and early closes. QMD records an
exact command on laptops or launches the unchanged updater asynchronously on
the workstation after collection closes. Live events are never merged into the
historical `events_YYYY` tables.

`QMD_PERSIST_INDICATORS` defaults to `false` because the current bar-level indicators can be recomputed from compact events and `intraday_family_bars_v2`. Enable it only when a run specifically needs a materialized indicator table.

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

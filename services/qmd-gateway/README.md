# QMD Gateway

Standalone Rust market-data gateway for the quote/trade regime.

This crate is also the source of the shared `qmd_core` Rust library. The live
binary and `services/qmd_history_gateway` compile against the exact same event
contract, compact decoder, and bar implementation. Live QMD remains the owner
of these modules; the historical service does not copy them.

Review documentation lives in [docs/README.md](docs/README.md). Start there for the architecture, configuration, data contracts, scanner/signal contracts, and operations guide.

The gateway runs as one OS process. Inside that process, Tokio runs async tasks
for websocket ingest, ClickHouse persistence, and local API/WebSocket serving.

Current responsibilities:

- subscribe to Massive stock websocket channels
- default subscription scope is full tape: `T.*` and `Q.*`
- normalize quote/trade events
- maintain in-memory live market state
- maintain an in-memory abnormal market-state overlay and persist only special transitions
- build memory-only enriched quote/trade bars for scanner and indicator consumers
- persist one canonical sparse intraday bar table from `100ms` through `1h`
- build sharded streaming tick and bar-level indicators
- build Massive-only scanner primitive candidates from live bars
- publish compact local snapshots/streams to the quant app
- stream compact unified market events for live ML consumers
- batch-write compact unified market events to the app-owned ClickHouse database
- optionally batch-write raw quote/trade events to the app-owned ClickHouse database
- batch-write closed canonical intraday bars to the app-owned ClickHouse database
- optionally batch-write closed indicator rows to the app-owned ClickHouse database
- expose a documented indicator catalog for live/offline compute policy
- expose a documented signal-method catalog with explicit working and confirmation timeframes

The gateway keeps two paths separate:

```text
fast path: Massive -> compact memory buffers/scanner/bars -> local app stream
persistence path: Massive -> compact reorder buffers -> ClickHouse batch inserts
```

ClickHouse writes must never block the live trading decision path.
The historical `market_sip_compact.events_YYYY` tables remain flatfile-only. Live
QMD events are written only to the app-owned `q_live` database.

## Service Policy Alignment

QMD follows the shared gateway rule that canonical data capture is lossless and
UI delivery is best effort. Quote and trade events must either reach the live
bar/indicator/scanner processors and durable compact-event writer, or be held in
an overflow/spill path that can be replayed. Local websocket/UI streams may skip
stale downstream updates when a client cannot keep up, but those skips must be
counted and exposed in metrics.

Queue capacities are intentionally large by default to reduce the chance of lag
during full-market `T.*` and `Q.*` subscription bursts. If a required processor
falls behind, the correct behavior is to backpressure into the gateway's
overflow policy rather than silently losing canonical market data.

QMD implements the shared Massive market-status/holiday policy directly in
Rust. Cached `/v1/marketstatus/now` and `/v1/marketstatus/upcoming` responses
cover weekends, full holidays, early closes, and the `04:00-20:00` Eastern
collection window. A local New York schedule is used only while Massive is
temporarily unavailable.

## Configuration

Environment variables:

- `MASSIVE_API_KEY`
- `QMD_GATEWAY_BIND`, default `127.0.0.1:8795`
- `QMD_MASSIVE_WS_URL`, default `wss://socket.massive.com/stocks`
- `QMD_SUBSCRIBE_ALL_SYMBOLS`, default `true`
- `QMD_SUBSCRIBE_TRADES`, default `true`
- `QMD_SUBSCRIBE_QUOTES`, default `true`
- `QMD_CLICKHOUSE_URL`, falls back to `REAL_LIVE_CLICKHOUSE_WRITE_URL`, then `http://localhost:8123`
- `QMD_CLICKHOUSE_DATABASE`, falls back to `REAL_LIVE_CLICKHOUSE_WRITE_DATABASE`, then `q_live`
- `QMD_CLICKHOUSE_USER`, falls back to `REAL_LIVE_CLICKHOUSE_WRITE_USER` and shared ClickHouse user variables, then `default`
- `QMD_CLICKHOUSE_PASSWORD`, falls back to `REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD` and shared ClickHouse password variables
- `QMD_CLICKHOUSE_STORAGE_POLICY`, optional; falls back to `CLICKHOUSE_LIVE_STORAGE_POLICY`
- `QMD_CLICKHOUSE_MAX_BATCH`, default `10000`
- `QMD_CLICKHOUSE_FLUSH_INTERVAL_MS`, default `5000`
- `QMD_EVENT_CHANNEL_CAPACITY`, default `250000`
- `QMD_COMPACT_EVENTS_ENABLED`, default `true`
- `QMD_PERSIST_COMPACT_EVENTS`, default `true`
- `QMD_COMPACT_EVENT_TABLE`, default `events`; the singular `q_live.events`
  table is partitioned by event date
- `QMD_COMPACT_EVENT_CHANNEL_CAPACITY`, default `250000`
- `QMD_COMPACT_EVENT_LIVE_BUFFER_EVENTS_PER_TICKER`, default `512`
- `QMD_COMPACT_EVENT_REORDER_LAG_MS`, default `500`
- `QMD_COMPACT_EVENT_REORDER_FORCE_FLUSH_MS`, default `2000`
- `QMD_COMPACT_EVENT_REORDER_MAX_EVENTS_PER_TICKER`, default `4096`
- compact condition/indicator tokens and tape ids load from the canonical
  `market_sip_compact` reference tables; QMD fails preflight on reference drift
- `QMD_PERSIST_RAW_EVENTS`, default `false`
- `QMD_LIVE_MARKET_STATE_ENABLED`, default `true`
- `QMD_LIVE_MARKET_STATE_TABLE`, default `live_symbol_market_event_v1`
- `QMD_LIVE_MARKET_STATE_CHANNEL_CAPACITY`, default `250000`
- `QMD_LIVE_MARKET_STATE_HISTORY_LIMIT`, default `5000`
- `QMD_LIVE_MARKET_STATE_TRADE_HALT_CONDITIONS`, default empty comma-separated condition ids
- `QMD_LIVE_MARKET_STATE_TRADE_RESUME_CONDITIONS`, default empty comma-separated condition ids
- `QMD_LIVE_MARKET_STATE_QUOTE_HALT_CONDITIONS`, default empty comma-separated condition ids
- `QMD_LIVE_MARKET_STATE_QUOTE_RESUME_CONDITIONS`, default empty comma-separated condition ids
- `QMD_BAR_CHANNEL_CAPACITY`, default `250000`
- `QMD_BAR_HISTORY_LIMIT`, default `1000`
- `QMD_PRODUCT_CACHE_MAX_BYTES`, default `536870912` (512 MiB total)
- `QMD_PRODUCT_CACHE_MAX_ROWS`, default `2000000`
- `QMD_PRODUCT_CACHE_MAX_PARTITIONS`, default `8192` ticker-day partitions
- `QMD_BAR_SHARD_COUNT`, default `8`
- `QMD_BAR_TIMEFRAMES`, default `100ms,1s,5s,10s,30s,1m,5m,1h`
- `QMD_SCANNER_BROADCAST_MS`, default `1000`
- `QMD_TICKER_BROADCAST_MS`, default `250`
- `QMD_GAP_FILL_ENABLED`, default `true`
- `QMD_GAP_FILL_MODE`, default `auto`; allowed values are `auto`, `session_catch_up`, `after_hours`, `repair`, or `session`
- `QMD_GAP_FILL_INTERVAL_MS`, default `300000`
- `QMD_GAP_FILL_AWAITING_SYMBOLS_RETRY_MS`, default `10000`
- `QMD_GAP_FILL_LOOKBACK_MINUTES`, default `120`
- `QMD_GAP_FILL_MAX_LOOKBACK_DAYS`, default `3`
- `QMD_GAP_FILL_MIN_GAP_SECONDS`, default `1`
- `QMD_GAP_FILL_MAX_PAGES_PER_SYMBOL`, default `5`
- `QMD_STARTUP_MAINTENANCE_ENABLED`, default `true`
- `QMD_COVERAGE_TABLE`, default `qmd_market_coverage_manifest_v1`
- `QMD_LIVE_EVENT_COVERAGE_TABLE`, default `qmd_live_event_coverage_v1`
- `QMD_FLATFILE_COVERAGE_TABLE`, default `qmd_flatfile_coverage_v2`
- `QMD_GAP_FILL_SYMBOL_UNIVERSE_TABLE`, default `qmd_gap_fill_symbol_universe_v1`
- `QMD_GAP_FILL_UNIVERSE_MARKET_DAYS`, default `5`
- `QMD_HOST_ROLE`, default `auto`; use `workstation` or `laptop` to override
- `QMD_HISTORICAL_CLICKHOUSE_DATABASE`, default `market_sip_compact`
- `QMD_HISTORICAL_FLATFILE_UPDATE_ENABLED`, default `true`
- `QMD_HISTORICAL_FLATFILE_AUTORUN`, default `true`; effective only on the
  workstation after the active collection window closes
- `QMD_HISTORICAL_PIPELINE_CODE_ROOT`, default `D:\TradingML\codes\quant_research_workbench_pipelines`
- `QMD_MARKET_STATUS_ENABLED`, default `true`
- `QMD_MARKET_STATUS_URL`, default Massive `/v1/marketstatus/now`
- `QMD_MARKET_HOLIDAYS_URL`, default Massive `/v1/marketstatus/upcoming`
- `QMD_FLATFILE_ENDPOINT_URL`, `QMD_FLATFILE_BUCKET`, and `QMD_FLATFILE_REGION`
- `QMD_INTRADAY_BAR_CHANNEL_CAPACITY`, default `250000`
- `QMD_INTRADAY_BAR_SHARD_COUNT`, default `8`
- `QMD_INTRADAY_BAR_TABLE`, default `intraday_family_bars_v2`
- `QMD_INTRADAY_BAR_TIMEFRAMES`, default `100ms,1s,5s,10s,30s,1m,5m,1h`
- `QMD_INDICATOR_CHANNEL_CAPACITY`, default `250000`
- `QMD_INDICATOR_BAR_CHANNEL_CAPACITY`, default `250000`
- `QMD_INDICATOR_HISTORY_LIMIT`, default `1000`
- `QMD_INDICATOR_HISTORY_BY_TIMEFRAME`, default `1s:900,10s:360,30s:480,1m:960,5m:192,1h:32`
- `QMD_INDICATOR_SHARD_COUNT`, default `8`
- `QMD_TICK_INDICATOR_WINDOW_SECONDS`, default `300`
- `QMD_PERSIST_INDICATORS`, default `false`
- `QMD_PERSIST_STRUCTURE_EVENTS`, default `true`; persists causal generic-structure events and the full versioned engine checkpoint even when full bar-indicator persistence is disabled
- `QMD_SCANNER_PRIMITIVE_CHANNEL_CAPACITY`, default `250000`
- `QMD_SCANNER_PRIMITIVE_HISTORY_LIMIT`, default `10000`

The service writes to:

- `events`
- `live_massive_trades`, only when `QMD_PERSIST_RAW_EVENTS=true`
- `live_massive_quotes`, only when `QMD_PERSIST_RAW_EVENTS=true`
- `intraday_family_bars_v2`
- `live_market_indicators`, only when `QMD_PERSIST_INDICATORS=true`
- `qmd_structure_events_v1`, when `QMD_PERSIST_STRUCTURE_EVENTS=true`; immutable BoS/CHoCH and zone lifecycle events with separate pivot and confirmation timestamps
- `qmd_structure_state_v1`, when `QMD_PERSIST_STRUCTURE_EVENTS=true`; latest full event-native engine checkpoint per symbol for exact restart continuity, coalesced once per changed symbol per writer flush
- `qmd_gap_fill_runs`
- `qmd_market_coverage_manifest_v1`
- `qmd_live_event_coverage_v1`
- `qmd_flatfile_coverage_v2`
- `qmd_compact_event_issue_v1`
- `qmd_gap_fill_symbol_universe_v1`

The lowest-latency live ML path should consume the in-memory compact event
buffer through `/snapshot/compact-events/{ticker}?limit=128` or the websocket
stream `/stream/compact-events`. ClickHouse is the durability/audit path. Raw
quote/trade persistence is intentionally optional and is not part of the default
coverage repair contract. The compact event row is the durable live equivalent
of the historical `market_sip_compact.events_YYYY` training tables.

Strategies that need an explainable short-horizon directional feature can call
`GET /snapshot/microstructure-forecast/{ticker}?limit=1024`. QMD returns the
same deterministic `deterministic_microstructure_v2` contract used by QMD
History and Canvas. Schema v3 retains the 25-, 100-, and 500-event diagnostic
horizons and adds one closed-100-ms `interval` contract. That interval exposes
transaction and signed-volume imbalance, Level-1 OFI, queue imbalance,
microprice lean, midpoint and trade return, aggressor persistence,
arrival-intensity imbalance, arrival rate, and liquidity resiliency. The
recommended signal architecture combines those features into Aggressive Flow
(45%), Displayed Liquidity (35%), and Response & Resiliency (20%). Confidence
separately measures event coverage, quote validity, trade classification,
evidence density, and agreement among the three blocks. `action` is `buy` or
`sell` only when confidence is at least
35% and absolute score is at least 0.15; otherwise it is `wait`. This is a
next-midpoint-direction feature, not an order
instruction, return forecast, or price target; strategies must still apply
market-state, LULD, spread, risk, and execution gates.

Chart indicator rows use that contract on a fixed causal grid. QMD samples the
raw sufficient statistics once per closed 100 ms bar. For every higher
timeframe, it sums counts and volume, chains log returns, accumulates raw OFI
and liquidity depletion/replenishment, and sample-weights queue, microprice,
persistence, and arrival evidence. QMD then calculates exactly one signal and
confidence from the merged bar evidence. It never averages overlapping rolling
forecast scores. This makes a 1-minute indicator describe the quotes and trades
inside that minute rather than its final event or a series of overlapping
windows.

## After-Hours Maintenance

The QMD maintenance source of truth for historical event availability is
`market_sip_compact.events_YYYY` plus `market_sip_compact.events_ordinal_continuity`.
QMD owns its own coverage checks, recent REST repair, historical flatfile
planning, and retention cleanup. It intentionally does not copy historical rows
directly into `q_live`. Recent `q_live` event gaps must be repaired through the
QMD replay/fanout path so `events` and `intraday_family_bars_v2` remain consistent.

During active streaming hours, recent q_live REST repair starts from symbols
kept in the durable gap-fill symbol universe. If the universe is empty, QMD
seeds it from the latest `QMD_GAP_FILL_UNIVERSE_MARKET_DAYS` market sessions in
`market_sip_compact`. New tickers observed by the live websocket compact-event
buffer are added as `not_gap_filled`. Each repair attempt updates the symbol
status to `in_progress`, `completed`, `partial_page_limit`, or `failed`. Later
runs reuse the same universe table instead of rediscovering symbols from
scratch. If repair records `awaiting_live_symbols`, the scheduled repair loop
retries every `QMD_GAP_FILL_AWAITING_SYMBOLS_RETRY_MS` while streaming is
active instead of waiting for the normal after-hours interval.

## Live Bars

`q_live.intraday_family_bars_v2` is the durable live three-family bar table and is always
enabled. It consumes the same sanitized compact events written to
`q_live.events`. Every valid event contributes to a sparse long-form `trade`,
`quote_bid`, or `quote_ask` family row; QMD does not fabricate empty family rows.
The base bars are aligned to the 04:00-20:00 America/New_York session grid at
`100ms`. Closed base bars incrementally roll up to `1s`, `5s`, `10s`, `30s`,
`1m`, `5m`, and `1h` using first open/last close, maximum high, minimum low,
summed size and event count, and first/last event timestamps. This includes the
exact packed-training grid (`100ms`, `1s`, `5s`, `30s`, `1m`) while retaining
the operational chart/scanner resolutions.

The canonical identity is
`(local_date, ticker, label_resolution_us, bucket_index, bar_family)`. A bounded
event-time watermark closes buckets. If REST repair later supplies an event for
an already closed bucket, QMD does not append a partial replacement: it rebuilds
that 100ms bucket from `q_live.events` and then rebuilds each affected parent
from the corrected base bars.

At first startup, QMD creates and validates `intraday_family_bars_v2`. If the table is
empty while rolling compact events already exist, it bootstraps 100ms rows from
those events and derives the higher resolutions from the base rows. Only after
the new table passes readiness validation does QMD drop the obsolete
`live_market_bars`, `bars_by_symbol_time`, `bars_by_time_symbol`, and
`live_model_microbars` tables. No active Rust or Python writer recreates them.

The separate enriched bar store used by scanners, indicators, LULD estimates,
and `/snapshot/bars/{ticker}` remains memory-only and is sharded by ticker. Each shard has its own async
worker and mutex-protected store, so full-market `T.*` and `Q.*` processing does
not contend on one global bar lock. API bar snapshots use the same deterministic
ticker hash as ingest, so a request for `AAPL` reads only the shard that owns
`AAPL`.

The bar abstraction includes trade OHLCV, VWAP, quote mid/spread measures,
quote/trade rates, buy/sell tape imbalance proxies, liquidity/friction proxies,
momentum/acceleration fields, and volatility/noise fields. Metrics that require
future quote matching are currently recorded as close/VWAP or spread proxies,
so the schema is stable while delayed post-trade refinement can be added later.

## Live Indicators And Signals

Indicators are also built as streaming state, not by rescanning stored rows.
The indicator layer has its own ticker-hash shards and receives two inputs:

- raw Massive quote/trade events for tick-level indicators
- closed bars from the bar engine for bar-level indicators

Tick-level indicators keep a configurable rolling sample window in memory.
`QMD_TICK_INDICATOR_WINDOW_SECONDS` defaults to `300`, so the scanner has five
minutes of recent quote/trade samples for fast calculations. Fields with a
specific horizon in their name, such as `trade_rate_60s`, are still calculated
from that exact horizon inside the retained window.

Tick-level indicators expose:

- `trade_rate_10s`, `trade_rate_60s`
- `trade_accel_10s_60s`
- `quote_rate_10s`, `quote_rate_60s`
- `quote_accel_10s_60s`
- `rolling_vwap_60s`
- `tape_imbalance_60s`
- `buy_pressure_60s`, `sell_pressure_60s`
- `spread_bps`, `quote_pressure`

Bar-level indicators are updated when each timeframe bar closes and include:

- session-anchored cumulative VWAP using the standard `hlc3 * volume` basis, with the regular benchmark resetting at the 09:30 New York open
- `ema_9`, `ema_20`, `ema_50`
- `rsi_14`
- `atr_14`
- `macd_line`, `macd_signal`, `macd_histogram`
- `bollinger_mid_20`, `bollinger_upper_20`, `bollinger_lower_20`, `bollinger_std_20`
- `close_sma_20`, `volume_sma_20`
- `return_1_bar`, `price_vs_ema20_pct`, `price_vs_vwap_pct`, `trend_score`

The memory-only enriched bars also carry estimated LULD proximity fields. These are
local scanner/chart risk fields, not official SIP LULD messages. The gateway
uses a rolling five-minute simple average of valid trade prices as
`estimated_luld_reference_price`, applies default Tier 2 LULD percentage
parameters, and publishes estimated upper/lower bands, distance-to-band
percentages, an active regular-session flag, and a compact state such as
`inside`, `near_upper`, or `near_lower`. Because Tier 1/ETP membership and SIP
eligible-trade handling are not yet wired into QMD, these fields are named
`estimated_luld_*` and should not be used as authoritative halt/limit-state
messages.

The live abnormal market-state overlay consumes quote/trade events and closed
1s bars. It keeps current state in memory and appends durable rows only when a
predefined special state opens or closes. Ordinary `normal` state is
not persisted. The default persisted families are estimated LULD near/breach
states and locked/crossed quote states. Configured halt/resume condition ids can
also open or close `condition_halt` rows. Consumers read
`/snapshot/live-market-state`, `/snapshot/live-market-state/{ticker}`, or
`/stream/live-market-state` and combine those live blocks with reference
tradability and broker/account checks.

Bar-level indicator history is retained per timeframe using
`QMD_INDICATOR_HISTORY_BY_TIMEFRAME`. The default scanner/chart compromise is:

- `1s:900`
- `10s:360`
- `30s:480`
- `1m:960`
- `5m:192`
- `1h:32`

If a timeframe is not listed, `QMD_INDICATOR_HISTORY_LIMIT` is used as the
fallback. Deeper chart history should be loaded from ClickHouse, then joined
with the live in-memory tail.

Closed bar-level indicator rows are kept in memory by default. They are not
persisted because the current indicator set can be recomputed from compact
events and canonical intraday bars. Set `QMD_PERSIST_INDICATORS=true` only for a specific run
that needs a materialized indicator table for chart-load speed or audit.

The indicator catalog is exposed at `/indicator-catalog`. It documents each
indicator family with:

- feature category, such as `momentum`, `volume_liquidity`, or `tape_microstructure`
- priority from `P0` to `P3`
- intended compute mode, such as realtime tick, realtime bar-close, or Polars on demand
- persistence policy
- implementation status
- concrete output fields

This catalog is the contract for deciding which features belong in the live
Rust hot path and which should stay as offline/vectorized Polars features.

The default persistence stance is intentionally conservative:

- raw quotes and trades are durable replay sources
- enriched bars are durable publication sources
- tick-level scanner features are memory-first
- signal methods persist decision snapshots, not every intermediate tick metric
- a persisted indicator field should be treated as immutable once production
  writes begin; change definitions through new versioned fields or tables

The signal-method catalog is exposed at `/signal-catalog`. A signal method is
not an enabled trading rule by itself; it is the contract a detector must follow.
Each row declares:

- the working timeframe, such as `1s`, `10s`, `30s`, `1m`, or `5m`
- optional confirmation timeframes, such as `1m`, `5m`, or `1h`
- required bar fields, indicator fields, and reference fields
- trigger rules, confirmation rules, and rejection rules
- emitted fields for scanner/order-routing decisions
- snapshot fields that should be written when a signal is emitted or rejected

Most live scanner methods are tick-first or hybrid tick/bar methods because
trade acceleration, quote-rate acceleration, tape imbalance, and spread recovery
arrive before a clean multi-minute pattern. Slower methods such as opening range,
trend continuation, and mean reversion run on closed bars and use higher
timeframe confirmation where appropriate.

## Scanner Primitives

The gateway emits Massive-only scanner primitives from closed live bars. These
are not final trading signals and do not use broker state, `conid`, float, short
interest, fundamentals, logos, portfolio state, or account state.

Current primitive families include:

- tape acceleration
- volume shock
- liquidity recovery
- VWAP reclaim
- high-momentum bar

Scanner primitive endpoints:

```text
GET http://127.0.0.1:8795/snapshot/scanner-primitives?limit=250
ws://127.0.0.1:8795/stream/scanner-primitives
```

Each primitive row includes `schema_version`, ticker, timeframe, primitive key,
side bias, score, trigger reason, reject reason, and Massive-derived evidence
fields, including the estimated LULD state and distance-to-band values from the
source bar.

## Metrics And Backpressure

The `/metrics` endpoint exposes operational counters for:

- Massive ingest event counts and last event lag
- parse/connect/disconnect failures
- broadcast skip counters and required-path receiver-closed counters
- emitted bar rows
- scanner primitive counts
- gap-fill runs, failures, and written rows
- process uptime

The `/snapshot/maintenance` endpoint exposes in-flight startup maintenance and
gap-fill progress: active status, mode, total jobs, completed jobs, active
symbols, current interval, repaired rows, page-limit count, and errors. The Rich
terminal renders this only while maintenance is active. Idle `0/0` progress is
not presented as meaningful work.

`/snapshot/status` also exposes QMD-specific operational lanes for the Massive
feed, `q_live.events`, canonical intraday bars, the live coverage ledger,
optional raw and indicator writers, the compact-event warning audit, and
abnormal market-state persistence. Each enabled lane reports current state, pending and high-water rows,
successful rows, failure counts, last success/failure timestamps, and a bounded
error detail. Writer failures retain the current batch for retry and are shown
as active terminal actions; a later successful commit records recovery.

The terminal is organized around the gateway's data contracts rather than its
internal tasks:

- `Attention / Required Action`: active writer/feed failures, stale monitor
  sources, recent-coverage problems, retention blocks, and exact laptop-to-
  workstation historical commands.
- `Live Event Pipeline`: Massive quote/trade arrival, normalization/encoding,
  durable `q_live.events` commits, and canonical intraday-bar commits.
- `Recent Live Coverage`: current plus three prior market sessions using the
  confirmed event/bar coverage ledger.
- `Historical Sync`: remote quote/trade object readiness and read-only
  `market_sip_compact` confirmation.
- `Downstream Products`: canonical intraday bars, indicators, scanner
  primitives, and sparse abnormal market state.

Short/narrow terminals retain only the header, required actions, live pipeline,
coverage/handoff summary, and active repair. Diagnostic event samples are
polled only with the terminal's `--details` option.

Required processing and persistence queues use awaited sends. If a required
queue is full, live ingest backpressures instead of dropping canonical work. UI
websocket broadcasts remain best effort, so the app can be offline while the
gateway continues updating memory and ClickHouse.

## Session Lifecycle

The gateway keeps the Massive websocket ingest task running for live capture.
It treats 04:00-20:00 New York time on weekdays as the active streaming window:

- 04:00-09:29 ET: premarket
- 09:30-15:59 ET: regular
- 16:00-19:59 ET: aftermarket

Gap fill uses the same normalized event fan-out as the live websocket path:
REST quote/trade rows are converted to `MarketEvent`, then routed through
in-memory state, local streams, bars, indicators, compact-event persistence, and
optional raw persistence. It does not require raw quote/trade persistence. If
the gateway starts during premarket, regular market, or aftermarket and
`QMD_GAP_FILL_MODE` is `auto`, `session`, or `session_catch_up`, it immediately
runs a high-priority session catch-up pass. Outside streaming hours, `auto`,
`after_hours`, and `repair` run lower-priority repair cycles. Gap fill uses
Massive REST historical trades and quotes:

- `/v3/trades/{stockTicker}`
- `/v3/quotes/{stockTicker}`

Those REST endpoints require one ticker in the path. The websocket wildcard
subscriptions `T.*` and `Q.*` do not translate to REST gap fill, so the repair
runs concurrent per-ticker REST jobs.

QMD does not use configured seed tickers or a configured universe table for REST
repair. During streaming hours, it starts the websocket immediately and repairs
only tickers discovered from newly persisted live compact events. If a clean
slate has gaps but no live ticker has arrived yet, QMD reports
`awaiting_live_symbols` and leaves the gap open. Outside streaming hours, it
uses the latest symbols from `q_live.events`; if q_live has no
symbols, it falls back to the latest symbol set in the read-only
year-specific `market_sip_compact.events_YYYY` tables. q_live gap detection does not infer missing
data from
`min/max/count` in `events`; it subtracts confirmed intervals in
`qmd_live_event_coverage_v1` from required 04:00-20:00 ET market-session
windows. Streaming intervals are confirmed only where `compact_persisted` and
`intraday_bars_persisted` rows overlap for the same run. REST repair rows are confirmed
only when recorded as `repair_completed` per gap interval. REST repair covers
the current market day plus `QMD_RECENT_LIVE_PRIOR_MARKET_DAYS` prior US market
sessions, default `3`. Any uncovered interval inside those session windows is
treated as a gap, except for intervals shorter than
`QMD_GAP_FILL_MIN_GAP_SECONDS`. Repair rows are converted to the normal fanout
path so compact events, bars, in-memory state, streams, indicators,
and scanner primitives see the same data. Raw `live_massive_trades` and
`live_massive_quotes` are excluded from this contract unless raw persistence is
explicitly enabled for a separate debug workflow.
Deeper historical event history should be read from the read-only
year-specific `market_sip_compact.events_YYYY` tables, which are maintained only
by `download_update_events.py`.

At startup, when `QMD_STARTUP_MAINTENANCE_ENABLED=true`, the gateway audits the
recent `q_live.events` rows directly for structural event-table
problems. The audit checks duplicate canonical event identities after `FINAL`.
Time coverage is then read from
`qmd_live_event_coverage_v1`, not inferred from event-table min/max timestamps.
If recent rows are structurally sound, the gateway runs bounded Massive REST
coverage repair before opening the websocket. Startup repair uses the same
current-plus-prior-session window as recurring repair.
If committed rows have duplicate canonical identities, the gateway records
`needs_manual_rebuild` in the coverage manifest and does not silently rewrite
existing rows. Durable live ordinals do not exist; live reads order by
`(sip_timestamp_us, source_sequence, event_type, arrival_sequence)`.

The legacy `qmd_market_coverage_manifest_v1` table is coarse and run-scoped. It
records startup audits, repair summaries, and historical flatfile update plans.
It is not the source of truth for recent live holes. The live source of truth is
`qmd_live_event_coverage_v1`: compact-event and intraday-bar writers publish separate
confirmation rows, and QMD counts only their overlap or explicit completed
repair rows. The flatfile source of truth is `qmd_flatfile_coverage_v2`, keyed
by session and source kind. After 08:00 ET, QMD performs signed metadata checks
for both Massive quote and trade objects and records key, ETag, size, readiness,
historical confirmation, host, command, and errors. Confirmed objects are
rechecked on a bounded 12-hour cadence; a changed key, ETag, modification time,
or size reopens that session and triggers the same historical updater handoff.
Confirmation requires historical continuity to advance after the handoff, so a
stale pre-existing row cannot falsely close the repair. The old
`qmd_flatfile_event_coverage_v1` table is dropped during startup migration.
QMD never writes `market_sip_compact`; it only invokes the unchanged
`download_update_events.py` command. A workstation launches it asynchronously
after the cached Massive calendar says collection is closed. A laptop records
`manual_action_required` and the exact workstation command. Weekend checks can
therefore ingest Friday after both objects become available Saturday morning.
On trading days, historical coverage is required only through T-2 because the
current session and T-1 remain authoritative in `q_live.events`.

Retention keeps the current session plus three prior market sessions in the
daily-partitioned `q_live.events` and `q_live.intraday_family_bars_v2` tables. Deletion occurs only after
historical continuity confirms the older session; otherwise QMD records
`retention_blocked_historical_gap` and temporarily retains the rows.
Legacy `q_live.events_YYYY` tables are no longer read or written. They are left
untouched for the production cutover audit and may be dropped only after the
singular table's repaired rolling coverage has been verified.

Canonical intraday bars stream from `/stream/intraday-bars`. They are part of
the required QMD persistence contract and do not depend on model readiness or
an enable/persist feature flag.

Replay and backtest are deliberately absent from this binary. They use the
separate Rust `services/qmd_history_gateway` source so historical reads cannot
contaminate live in-memory state or live persistence.

## Install Rust On Windows

From the repo root:

```powershell
.\scripts\install_rust_windows.ps1
```

Then open a new PowerShell window and verify:

```powershell
rustc --version
cargo --version
```

## Run

```powershell
.\scripts\run_qmd_gateway.ps1
```

This is the command for both first startup and continuation. On the first
startup after this cutover, QMD creates, validates, and when necessary
bootstraps `q_live.intraday_family_bars_v2` from rolling `q_live.events`; it then drops
the four obsolete bar tables. A later restart detects the populated canonical
table and continues without rebuilding it. `-CheckOnly` validates launcher
configuration but does not perform the database migration.

The launcher creates a per-run shutdown token. Exiting the monitor sends a
token-protected local shutdown request, stops live producers, and gives writer
queues up to 15 seconds to drain before the launcher applies its 20-second
forced-stop fallback. Gateway stdout/stderr remain under `.tmp/qmd-gateway/`.
Failed final batches continue retrying within that drain window; they are never
cleared merely because shutdown was requested.

Useful terminal modes:

```powershell
python .\services\qmd-gateway\tools\qmd_terminal.py --once
python .\services\qmd-gateway\tools\qmd_terminal.py --plain --once
python .\services\qmd-gateway\tools\qmd_terminal.py --json
python .\services\qmd-gateway\tools\qmd_terminal.py --details
```

Redirected stdout automatically uses bounded human-readable summaries without
cursor-control sequences. `--json` is the explicit one-snapshot machine format.

Check only:

```powershell
.\scripts\run_qmd_gateway.ps1 -CheckOnly
```

Health endpoint:

```text
GET http://127.0.0.1:8795/health
GET http://127.0.0.1:8795/metrics
```

Snapshot endpoints:

```text
GET http://127.0.0.1:8795/snapshot/scanner?limit=250
GET http://127.0.0.1:8795/snapshot/scanner-primitives?limit=250
GET http://127.0.0.1:8795/snapshot/ticker/AAPL
GET http://127.0.0.1:8795/snapshot/bars/AAPL?timeframe=1m&limit=500
GET http://127.0.0.1:8795/snapshot/indicators/AAPL?timeframe=1m&limit=500
GET http://127.0.0.1:8795/snapshot/microstructure-forecast/AAPL?limit=1024
GET http://127.0.0.1:8795/indicator-catalog
GET http://127.0.0.1:8795/signal-catalog
```

Local websocket endpoints:

```text
ws://127.0.0.1:8795/stream/scanner
ws://127.0.0.1:8795/stream/scanner-primitives
ws://127.0.0.1:8795/stream/ticker/AAPL
ws://127.0.0.1:8795/stream/bars/AAPL?timeframe=1m&limit=500
ws://127.0.0.1:8795/stream/indicators/AAPL?timeframe=1m&limit=500
ws://127.0.0.1:8795/stream/events
ws://127.0.0.1:8795/stream/compact-events
ws://127.0.0.1:8795/stream/intraday-bars
```

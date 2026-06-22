# QMD Gateway

Standalone Rust market-data gateway for the quote/trade regime.

Review documentation lives in [docs/README.md](docs/README.md). Start there for the architecture, configuration, data contracts, scanner/signal contracts, and operations guide.

The gateway runs as one OS process. Inside that process, Tokio runs async tasks
for websocket ingest, ClickHouse persistence, and local API/WebSocket serving.

Current responsibilities:

- subscribe to Massive stock websocket channels
- default subscription scope is full tape: `T.*` and `Q.*`
- normalize quote/trade events
- maintain in-memory live market state
- build sharded live quote/trade bars for `1s`, `10s`, `30s`, `1m`, `5m`, and `1h`
- build sharded streaming tick and bar-level indicators
- build Massive-only scanner primitive candidates from live bars
- publish compact local snapshots/streams to the quant app
- stream compact unified market events for live ML consumers
- batch-write compact unified market events to the app-owned ClickHouse database
- optionally batch-write raw quote/trade events to the app-owned ClickHouse database
- batch-write closed bars to the app-owned ClickHouse database
- optionally batch-write closed indicator rows to the app-owned ClickHouse database
- expose a documented indicator catalog for live/offline compute policy
- expose a documented signal-method catalog with explicit working and confirmation timeframes

The gateway keeps two paths separate:

```text
fast path: Massive -> compact memory buffers/scanner/bars -> local app stream
persistence path: Massive -> compact reorder buffers -> ClickHouse batch inserts
```

ClickHouse writes must never block the live trading decision path.
The historical `market_sip_compact.events` table remains flatfile-only. Live
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

QMD keeps its own market-session scheduler because live quote/trade collection
is the most latency-sensitive service. Historical flatfile refresh, gap repair,
and heavy maintenance work should run outside the active collection window by
default. The standard active collection window is `04:00-20:00` Eastern Time,
matching premarket, regular market, and after-hours handling used by the News
and SEC gateways.

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
- `QMD_COMPACT_EVENT_TABLE`, default `live_market_events_v1`
- `QMD_COMPACT_EVENT_CONTINUITY_TABLE`, default `live_event_ordinal_continuity`
- `QMD_COMPACT_EVENT_CHANNEL_CAPACITY`, default `250000`
- `QMD_COMPACT_EVENT_LIVE_BUFFER_EVENTS_PER_TICKER`, default `512`
- `QMD_COMPACT_EVENT_REORDER_LAG_MS`, default `500`
- `QMD_COMPACT_EVENT_REORDER_FORCE_FLUSH_MS`, default `2000`
- `QMD_COMPACT_EVENT_REORDER_MAX_EVENTS_PER_TICKER`, default `4096`
- `QMD_REFERENCE_DIR`, default resolves to repo `research/market_references/massive`
- `QMD_PERSIST_RAW_EVENTS`, default `false`
- `QMD_BAR_CHANNEL_CAPACITY`, default `250000`
- `QMD_BAR_HISTORY_LIMIT`, default `1000`
- `QMD_BAR_SHARD_COUNT`, default `8`
- `QMD_BAR_TIMEFRAMES`, default `1s,10s,30s,1m,5m,1h`
- `QMD_SCANNER_BROADCAST_MS`, default `1000`
- `QMD_TICKER_BROADCAST_MS`, default `250`
- `QMD_GAP_FILL_ENABLED`, default `true`
- `QMD_GAP_FILL_MODE`, default `auto`; allowed values are `auto`, `session_catch_up`, `after_hours`, `repair`, or `session`
- `QMD_GAP_FILL_INTERVAL_MS`, default `300000`
- `QMD_GAP_FILL_LOOKBACK_MINUTES`, default `120`
- `QMD_GAP_FILL_MAX_LOOKBACK_DAYS`, default `3`
- `QMD_GAP_FILL_MIN_GAP_SECONDS`, default `1`
- `QMD_GAP_FILL_MAX_PAGES_PER_SYMBOL`, default `5`
- `QMD_STARTUP_MAINTENANCE_ENABLED`, default `true`
- `QMD_COVERAGE_TABLE`, default `qmd_market_coverage_manifest_v1`
- `QMD_LIVE_EVENT_COVERAGE_TABLE`, default `qmd_live_event_coverage_v1`
- `QMD_FLATFILE_EVENT_COVERAGE_TABLE`, default `qmd_flatfile_event_coverage_v1`
- `QMD_HOST_ROLE`, default `auto`; use `workstation` or `laptop` to override
- `QMD_HISTORICAL_CLICKHOUSE_DATABASE`, default `market_sip_compact`
- `QMD_HISTORICAL_FLATFILE_UPDATE_ENABLED`, default `true`
- `QMD_HISTORICAL_FLATFILE_AUTORUN`, default `false`
- `QMD_HISTORICAL_FLATFILE_SAFE_LAG_DAYS`, default `1`
- `QMD_HISTORICAL_KNOWN_COVERAGE_END_DATE`, default `2026-06-05`
- `QMD_HISTORICAL_PIPELINE_CODE_ROOT`, default `D:\TradingML\codes\quant_research_workbench_pipelines`
- `QMD_INDICATOR_CHANNEL_CAPACITY`, default `250000`
- `QMD_INDICATOR_BAR_CHANNEL_CAPACITY`, default `250000`
- `QMD_INDICATOR_HISTORY_LIMIT`, default `1000`
- `QMD_INDICATOR_HISTORY_BY_TIMEFRAME`, default `1s:900,10s:360,30s:480,1m:960,5m:192,1h:32`
- `QMD_INDICATOR_SHARD_COUNT`, default `8`
- `QMD_TICK_INDICATOR_WINDOW_SECONDS`, default `300`
- `QMD_PERSIST_INDICATORS`, default `false`
- `QMD_SCANNER_PRIMITIVE_CHANNEL_CAPACITY`, default `250000`
- `QMD_SCANNER_PRIMITIVE_HISTORY_LIMIT`, default `10000`
- `QMD_REPLAY_ENABLED`, default `false`
- `QMD_REPLAY_DATE`, optional `YYYY-MM-DD`
- `QMD_REPLAY_SYMBOLS`, optional comma-separated tickers
- `QMD_REPLAY_MAX_ROWS`, default `1000000`

The service writes to:

- `live_market_events_v1`
- `live_event_ordinal_continuity`
- `live_massive_trades`, only when `QMD_PERSIST_RAW_EVENTS=true`
- `live_massive_quotes`, only when `QMD_PERSIST_RAW_EVENTS=true`
- `live_market_bars`
- `live_market_indicators`, only when `QMD_PERSIST_INDICATORS=true`
- `qmd_gap_fill_runs`
- `qmd_market_coverage_manifest_v1`
- `qmd_live_event_coverage_v1`
- `qmd_flatfile_event_coverage_v1`

The lowest-latency live ML path should consume the in-memory compact event
buffer through `/snapshot/compact-events/{ticker}?limit=128` or the websocket
stream `/stream/compact-events`. ClickHouse is the durability/audit path. Raw
quote/trade persistence is intentionally optional and is not part of the default
coverage repair contract. The compact event row is the durable live equivalent
of the historical `market_sip_compact.events` training table.

## Live Bars

Bars are built asynchronously from normalized Massive quotes and trades. The
websocket ingest task hashes each ticker into one of the configured bar shards
and waits for required shard capacity instead of dropping events when a queue is
full. This lets a slow worker apply backpressure to preserve the durable event
path.

Supported default timeframes:

- `1s`
- `10s`
- `30s`
- `1m`
- `5m`
- `1h`

Each bar is aligned to the top of its timeframe using UTC event time. For
example, `1h` bars start exactly at the top of the hour, and `5m` bars start at
`:00`, `:05`, `:10`, and so on. The current open bar is kept in memory and
updated until it closes. Closed bars are emitted to the bar writer and persisted
to `live_market_bars` in batches.

The in-memory bar store is also sharded by ticker. Each shard has its own async
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

- `ema_9`, `ema_20`, `ema_50`
- `rsi_14`
- `atr_14`
- `macd_line`, `macd_signal`, `macd_histogram`
- `bollinger_mid_20`, `bollinger_upper_20`, `bollinger_lower_20`, `bollinger_std_20`
- `close_sma_20`, `volume_sma_20`
- `return_1_bar`, `price_vs_ema20_pct`, `price_vs_vwap_pct`, `trend_score`

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
persisted because the current indicator set can be recomputed from
`live_market_bars`. Set `QMD_PERSIST_INDICATORS=true` only for a specific run
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
fields.

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
terminal renders this as the `Maintenance Progress` panel.

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
uses the latest symbols from `q_live.live_market_events_v1`; if q_live has no
symbols, it falls back to the latest symbol set in the read-only
`market_sip_compact.events` table. q_live gap detection does not infer missing
data from
`min/max/count` in `live_market_events_v1`; it subtracts confirmed intervals in
`qmd_live_event_coverage_v1` from required 04:00-20:00 ET market-session
windows. Streaming intervals are confirmed only where `compact_persisted` and
`bars_persisted` rows overlap for the same run. REST repair rows are confirmed
only when recorded as `repair_completed` per gap interval. REST repair covers
the current market day plus `QMD_RECENT_LIVE_PRIOR_MARKET_DAYS` prior US market
sessions, default `3`. Any uncovered interval inside those session windows is
treated as a gap, except for intervals shorter than
`QMD_GAP_FILL_MIN_GAP_SECONDS`. Repair rows are converted to the normal fanout
path so compact events, continuity, bars, in-memory state, streams, indicators,
and scanner primitives see the same data. Raw `live_massive_trades` and
`live_massive_quotes` are excluded from this contract unless raw persistence is
explicitly enabled for a separate debug workflow.
Deeper historical event history should be read from the read-only
`market_sip_compact.events` table, which is maintained by the flatfile pipelines
up to the prior day.

At startup, when `QMD_STARTUP_MAINTENANCE_ENABLED=true`, the gateway audits the
recent `q_live.live_market_events_v1` rows directly for structural event-table
problems. The audit reports duplicate ticker ordinals, ordinal holes, and
out-of-order ticker-local rows. Time coverage is then read from
`qmd_live_event_coverage_v1`, not inferred from event-table min/max timestamps.
If recent rows are structurally sound, the gateway runs bounded Massive REST
coverage repair before opening the websocket. Startup repair uses the same
current-plus-prior-session window as recurring repair.
If committed rows have duplicate `(ticker, ordinal)` keys, the gateway records
`needs_manual_rebuild` in the coverage manifest and does not silently rewrite
existing rows. Ordinal holes and timestamp-order warnings are reported in the
manifest summary but do not block temporal REST repair.

The legacy `qmd_market_coverage_manifest_v1` table is coarse and run-scoped. It
records startup audits, repair summaries, and historical flatfile update plans.
It is not the source of truth for recent live holes. The live source of truth is
`qmd_live_event_coverage_v1`: compact-event and bar writers publish separate
confirmation rows, and QMD counts only their overlap or explicit completed
repair rows. The flatfile source of truth is
`qmd_flatfile_event_coverage_v1`: on first startup, QMD bootstraps one
2019-forward coverage row from the latest `market_sip_compact`
`events_ordinal_continuity.source_date`. Historical `market_sip_compact.events`
updates remain flatfile-only. After hours, the gateway can plan the
`download_update_events.py` command for missing historical flatfile days, using
a US equity market-session calendar so weekends and market holidays are
skipped. On a workstation host with `QMD_HISTORICAL_FLATFILE_AUTORUN=true`, it
launches that command asynchronously; otherwise it prints the command for manual
workstation execution.

## Replay Mode

Replay mode is disabled by default. When `QMD_REPLAY_ENABLED=true`, the gateway
reads raw Massive rows from ClickHouse for `QMD_REPLAY_DATE` and optional
`QMD_REPLAY_SYMBOLS`, then feeds them through the same in-memory market, bar,
indicator, and scanner primitive pipeline. Replay does not re-persist raw events.

This is intended for deterministic validation and later backtest integration.

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
```

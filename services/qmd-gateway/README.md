# QMD Gateway

Standalone Rust market-data gateway for the quote/trade regime.

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
- batch-write raw events to the app-owned ClickHouse database
- batch-write closed bars to the app-owned ClickHouse database
- optionally batch-write closed indicator rows to the app-owned ClickHouse database
- expose a documented indicator catalog for live/offline compute policy
- expose a documented signal-method catalog with explicit working and confirmation timeframes

The gateway keeps two paths separate:

```text
fast path: Massive -> memory/scanner/bars -> local app stream
persistence path: Massive -> queues -> ClickHouse batch inserts
```

ClickHouse writes must never block the live trading decision path.

## Configuration

Environment variables:

- `MASSIVE_API_KEY`
- `QMD_GATEWAY_BIND`, default `127.0.0.1:8795`
- `QMD_MASSIVE_WS_URL`, default `wss://socket.massive.com/stocks`
- `QMD_SUBSCRIBE_ALL_SYMBOLS`, default `true`
- `QMD_SUBSCRIBE_TRADES`, default `true`
- `QMD_SUBSCRIBE_QUOTES`, default `true`
- `QMD_CLICKHOUSE_URL`
- `QMD_CLICKHOUSE_DATABASE`, default `q_live`
- `QMD_CLICKHOUSE_USER`, default `default`
- `QMD_CLICKHOUSE_PASSWORD`
- `QMD_CLICKHOUSE_MAX_BATCH`, default `10000`
- `QMD_CLICKHOUSE_FLUSH_INTERVAL_MS`, default `1000`
- `QMD_EVENT_CHANNEL_CAPACITY`, default `250000`
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
- `QMD_GAP_FILL_MIN_GAP_SECONDS`, default `60`
- `QMD_GAP_FILL_MAX_PAGES_PER_SYMBOL`, default `5`
- `QMD_GAP_FILL_SYMBOLS`, optional comma-separated priority symbols
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

- `live_massive_trades`
- `live_massive_quotes`
- `live_market_bars`
- `live_market_indicators`, only when `QMD_PERSIST_INDICATORS=true`
- `qmd_gap_fill_runs`

## Live Bars

Bars are built asynchronously from normalized Massive quotes and trades. The
websocket ingest task hashes each ticker into one of the configured bar shards
and pushes events into that shard queue with `try_send`, so bar math and
ClickHouse writes do not block the live ingest loop.

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

Closed indicator rows are kept in memory by default. They are persisted to
`live_market_indicators` in batches only when `QMD_PERSIST_INDICATORS=true`,
which should be enabled only for the versioned indicator set that has been
promoted to durable storage.

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
- dropped event counters for broadcast, ClickHouse, bar, indicator, and scanner queues
- emitted bar rows
- scanner primitive counts
- gap-fill runs, failures, and written rows
- process uptime

All hot-path queue sends use non-blocking `try_send`. If a queue is full, the
gateway drops that downstream item, increments the relevant counter, and keeps
the Massive ingest loop moving.

## Session Lifecycle

The gateway keeps the Massive websocket ingest task running for live capture.
It treats 04:00-20:00 New York time on weekdays as the active streaming window:

- 04:00-09:29 ET: premarket
- 09:30-15:59 ET: regular
- 16:00-19:59 ET: aftermarket

Gap fill has two modes. If the gateway starts during premarket, regular market,
or aftermarket and `QMD_GAP_FILL_MODE` is `auto`, `session`, or
`session_catch_up`, it immediately runs a high-priority session catch-up pass.
Outside streaming hours, `auto`, `after_hours`, and `repair` run lower-priority
database repair cycles. Gap fill uses Massive REST historical trades and quotes:

- `/v3/trades/{stockTicker}`
- `/v3/quotes/{stockTicker}`

If `QMD_GAP_FILL_SYMBOLS` is set, only those symbols are checked. Otherwise the
worker discovers symbols already present in `live_massive_trades` and
`live_massive_quotes` for the current date, then fills from each symbol's latest
stored timestamp to now. This is meant for crash/restart recovery without
blocking the live ingest fast path.

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

# QMD Gateway Operations

This file explains how to run, inspect, and troubleshoot `qmd-gateway`.

## Runtime Shape

The gateway is one OS process. Inside it, Tokio runs independent async tasks. Tokio is Rust's async runtime: it lets the process do network I/O, timers, queue reads, and API responses without creating one OS thread per job.

Main tasks:

| Task | Purpose | Failure Effect |
|---|---|---|
| Massive websocket ingest | Reads `T.*` trades and `Q.*` quotes. | Live market state stops updating if disconnected. |
| Compact event writer | Batches unified compact events and emits `/stream/compact-events`. | Live ML event durability/streaming is delayed or missing. |
| Raw ClickHouse writer | Optionally batches raw trades/quotes. | Raw replay/debug durability is delayed or missing. |
| Bar workers | Build bars by ticker shard. | Bars, bar indicators, and scanner primitives lag or drop. |
| Indicator workers | Build tick and bar indicators by ticker shard. | Indicator snapshots lag or drop. |
| Scanner primitive worker | Emits Massive-only primitive candidates. | Primitive stream becomes stale. |
| Gap-fill worker | Repairs recent quote/trade gaps with Massive REST through the same event fan-out as websocket ingest. | Missing recent events remain until next repair. |
| Replay worker | Optional one-shot replay from ClickHouse raw rows. | Only active when replay env vars enable it. |
| Local API server | Exposes health, metrics, snapshots, and streams. | App cannot consume gateway data. |

## Run Commands

Install Rust on Windows:

```powershell
.\scripts\install_rust_windows.ps1
```

Check build without starting the gateway:

```powershell
.\scripts\run_qmd_gateway.ps1 -CheckOnly
```

Run the gateway:

```powershell
.\scripts\run_qmd_gateway.ps1
```

By default this builds the Rust service, starts it in the background, waits for
`/health`, then opens the Rich terminal monitor in the same terminal. Gateway
stdout/stderr are written under `.tmp/qmd-gateway/` so service logs do not
corrupt the dashboard.

The API binds before startup maintenance completes. This lets `/health` and the
terminal come up during long REST repair after a coverage reset. Massive
websocket ingest still starts only after startup maintenance returns.

The launcher imports the repo `.env` before starting the child process. The Rust
service also performs the same `.env` discovery when run directly, and it does
not overwrite values already set in the current shell.

Useful launcher options:

```powershell
.\scripts\run_qmd_gateway.ps1 -TerminalWatch AAPL,NVDA,TSLA,AMD
.\scripts\run_qmd_gateway.ps1 -TerminalRefreshSeconds 0.5
.\scripts\run_qmd_gateway.ps1 -TerminalNoScreen
.\scripts\run_qmd_gateway.ps1 -NoTerminal
```

`-NoTerminal` preserves the old direct `cargo run` behavior. In that mode, stop
the gateway with `Ctrl+C`; the API server handles graceful shutdown on `Ctrl+C`.
In monitor mode, exiting the monitor stops the background gateway process.

## Health And Snapshot Endpoints

| Endpoint | Use |
|---|---|
| `GET /health` | Basic running state, subscriptions, session phase, and market-state metrics. |
| `GET /config` | Effective configuration after env parsing. Do not expose publicly because it includes structure and presence flags. |
| `GET /metrics` | Operational counters and lag values. |
| `GET /snapshot/maintenance` | In-flight startup maintenance, gap-fill, and backfill progress state. |
| `GET /indicator-catalog` | Indicator family contract. |
| `GET /signal-catalog` | Signal method contract. |
| `GET /snapshot/scanner?limit=250` | Simple latest market-state scanner snapshot. |
| `GET /snapshot/scanner-primitives?limit=250` | Massive-only primitive candidates. |
| `GET /snapshot/ticker/AAPL` | Latest quote/trade state for one ticker. |
| `GET /snapshot/bars/AAPL?timeframe=1m&limit=500` | Recent in-memory closed bars for one ticker/timeframe. |
| `GET /snapshot/indicators/AAPL?timeframe=1m&limit=500` | Recent indicator state for one ticker/timeframe. |

## Websocket Endpoints

| Endpoint | Sends |
|---|---|
| `/stream/compact-events` | Unified compact event rows for live ML consumers. |
| `/stream/events` | Raw normalized Massive events. |
| `/stream/scanner` | Periodic scanner market-state snapshots. |
| `/stream/scanner-primitives` | Primitive candidate events as they are emitted. |
| `/stream/ticker/{ticker}` | Periodic latest ticker snapshot. |
| `/stream/bars/{ticker}?timeframe=1m&limit=500` | Periodic bar snapshot. |
| `/stream/indicators/{ticker}?timeframe=1m&limit=500` | Periodic indicator snapshot. |

## Metrics To Watch

| Metric | Meaning | Action If Rising |
|---|---|---|
| `ingest_events`, `ingest_trades`, `ingest_quotes` | Number of parsed live/replay events. | Should rise during active feed or replay. |
| `last_event_lag_ms` | Difference between now and latest event timestamp. | If large during market hours, check Massive connection and local network. |
| `massive_connect_failures` | Failed websocket connection attempts. | Check API key, network, and Massive service status. |
| `massive_disconnects` | Websocket disconnect count. | Watch for recurring disconnects or local network instability. |
| `parse_failures` | Massive payloads that could not be parsed. | Inspect recent raw payload shape; provider schema may differ. |
| `compact_event_queue_dropped` | Compact writer receiver closed before accepting an event. Queue-full no longer increments this because required paths backpressure. | Treat any increase as a service fault and inspect logs. |
| `compact_event_rejected` | Structurally invalid events dropped before compact emit/insert. | Inspect provider data quality; raw persistence can be enabled for debugging. |
| `compact_events_persisted` | Compact rows inserted into ClickHouse. | Should rise during active feed when `QMD_PERSIST_COMPACT_EVENTS=true`. |
| `clickhouse_events_dropped` | Optional raw writer receiver closed before accepting an event. | Relevant only when `QMD_PERSIST_RAW_EVENTS=true`; inspect logs if nonzero. |
| `bar_events_dropped` | Bar worker receiver closed before accepting an event. | Treat as a service fault. |
| `indicator_events_dropped` | Tick-indicator receiver closed before accepting an event. | Treat as a service fault. |
| `bar_rows_writer_dropped` | Bar writer receiver closed before accepting a closed bar. | Treat as a service fault. |
| `bar_rows_indicator_dropped` | Indicator bar receiver closed before accepting a closed bar. | Treat as a service fault. |
| `bar_rows_scanner_dropped` | Scanner primitive receiver closed before accepting a closed bar. | Treat as a service fault. |
| `bar_rows_emitted` | Closed bars produced. | Should rise by timeframe during active feed. |
| `scanner_candidates_emitted` | Scanner primitives emitted. | Useful for scanner activity rate. |
| `gap_fill_runs`, `gap_fill_failures` | Gap-fill attempts and failures. | Failures need REST/ClickHouse error review. |
| `gap_fill_rows_written` | Rows repaired by REST gap fill. | High values after restart are expected; high values every cycle mean ingestion gaps remain. |
| `gap_fill_last_duration_ms` | Last gap-fill runtime. | If large, reduce symbols/pages or move repair after hours. |

## Backpressure Behavior

Backpressure means a queue is filling faster than its consumer drains it.

Required data paths now wait for downstream capacity instead of dropping rows:
bar aggregation, tick indicators, compact-event conversion/persistence, closed
bar persistence, scanner primitive input, and optional raw persistence. This can
slow websocket processing if ClickHouse or a worker cannot keep up, but it
preserves the event path used to build durable rows.

Local UI/websocket clients remain best effort. If no app is connected, broadcast
send failures are counted, but ClickHouse insertion and in-memory processing
continue independently.

ClickHouse writers retry their current in-memory batch after insert failures.
Compact events, bars, indicators, and optional raw rows are not cleared from the
writer batch until ClickHouse confirms the insert.

## Gap Fill Modes

Gap fill uses Massive REST:

```text
/v3/trades/{ticker}
/v3/quotes/{ticker}
```

It converts repaired rows to normalized market events, feeds the same state,
stream, bar, indicator, compact-event, and optional raw-persistence queues as
websocket ingest, then records audit rows in `qmd_gap_fill_runs`.

Massive websocket supports wildcard `T.*` and `Q.*`, but these REST endpoints
require one `stockTicker` path value. The gateway therefore repairs by ticker
and cannot request all tickers for a time range in one REST call.

| Mode | When It Runs | Purpose |
|---|---|---|
| `session_catch_up` or `session` | Once at startup if current New York time is premarket, regular, or after-hours. | Quickly repair recent event gaps after a restart during a session. |
| `after_hours` or `repair` | On timer outside active streaming hours. | Repair database gaps without competing with live ingest. |
| `auto` | Session catch-up during active hours, after-hours repair outside active hours. | Default mode. |

QMD does not depend on broker/reference tables for REST repair. It keeps a
durable market-data symbol universe in `qmd_gap_fill_symbol_universe_v1`. If
the queue is empty, QMD seeds it from the latest
`QMD_GAP_FILL_UNIVERSE_MARKET_DAYS` sessions in the read-only
`market_sip_compact.events` table. During streaming hours, it starts Massive
websocket ingest immediately and adds newly observed live compact-event tickers
to the queue as `not_gap_filled`. Repair uses the queue as its symbol source,
updates each symbol to `in_progress`, then `completed`, `partial_page_limit`, or
`failed`, and later runs reuse the same queue. If required coverage gaps exist
before any ticker is available, QMD records `awaiting_live_symbols`; the
scheduled repair loop retries every `QMD_GAP_FILL_AWAITING_SYMBOLS_RETRY_MS`.
Recent REST repair covers the current market day plus
`QMD_RECENT_LIVE_PRIOR_MARKET_DAYS` prior US market sessions, skipping weekends
and common US equity market holidays. Older history should be read from
`market_sip_compact.events`.

## Startup Maintenance And Coverage

Startup maintenance is enabled by default with
`QMD_STARTUP_MAINTENANCE_ENABLED=true`. It runs before the Massive websocket
task starts. The gateway audits recent rows in the actual
`q_live.live_market_events_v1` table and checks:

- duplicate `(ticker, ordinal)` rows
- ticker-local ordinal holes
- ticker-local rows whose ordinal order disagrees with timestamp/sequence order

The structural audit is separate from time coverage. Recent time coverage is
read from `qmd_live_event_coverage_v1`. Streaming writes are counted as covered
only where `compact_persisted` and `bars_persisted` intervals for the same run
overlap. REST repair rows are counted only when they are explicitly recorded as
`repair_completed` per gap interval. If the recent event table is structurally
clean, the gateway performs bounded Massive REST repair through the normal
fan-out path before live ingest starts, so repaired rows update memory, streams,
bars, indicators, compact persistence, and optional raw persistence in the same
way as live websocket rows. The startup repair and recurring repair both use
the current market day plus configured prior market sessions, then fill missing
04:00-20:00 ET intervals. If Massive pagination reaches
`QMD_RECENT_LIVE_MAX_PAGES_PER_INTERVAL`, the symbol is recorded as
`partial_page_limit` instead of being marked clean. If the audit finds duplicate
committed `(ticker, ordinal)` rows, the gateway records
`needs_manual_rebuild` and refuses to silently rewrite existing rows. Ordinal
holes and timestamp-order warnings remain visible in the manifest summary, but
they do not block temporal REST repair.

`qmd_market_coverage_manifest_v1` is a coarse per-run manifest. It records
startup live repair checks, scheduled recent-live repair checks, and historical
flatfile update plans. It should not have one row per symbol or per file.

After-hours historical flatfile maintenance is only a planner from the gateway.
It compares historical `events_ordinal_continuity` coverage with the configured
safe lag, using a US equity market-session calendar so market holidays such as
Juneteenth are not planned as missing flatfile days. The command it prints or launches uses
`download_update_events.py` against the historical flatfile pipeline and
explicitly targets `events` plus qmd-compatible `live_market_bars` for
`1s,5s,1m,5m,1d,1w,1mo`. QMD does not insert live websocket rows into
`market_sip_compact.events`.

## Replay Mode

Replay is for validation and future backtest integration.

When `QMD_REPLAY_ENABLED=true`, the gateway reads raw rows from ClickHouse for `QMD_REPLAY_DATE` and optional `QMD_REPLAY_SYMBOLS`, orders them by timestamp, then sends them through:

```text
market state -> bars -> indicators -> scanner primitives
```

Replay does not re-write raw events. It can still write bars and optional indicators because it uses the normal bar/indicator pipeline.

Use replay in a separate run from live trading unless you are deliberately testing live-tail behavior.

## ClickHouse Tables

| Table | Required | Purpose |
|---|---:|---|
| `live_market_events_v1` | yes | Durable live compact event source for ML-serving and live replay. |
| `live_massive_trades` | optional | Raw trade source for replay/debug when `QMD_PERSIST_RAW_EVENTS=true`. |
| `live_massive_quotes` | optional | Raw quote source for replay/debug when `QMD_PERSIST_RAW_EVENTS=true`. |
| `live_market_bars` | yes | Published bars for chart/date-slice queries. |
| `bars_by_symbol_time` | yes | Same published bars ordered for per-symbol temporal windows. |
| `bars_by_time_symbol` | yes | Same published bars ordered for market-wide time snapshots. |
| `live_market_indicators` | optional | Materialized closed bar-level indicators when `QMD_PERSIST_INDICATORS=true`. |
| `qmd_gap_fill_runs` | yes if gap fill enabled | Audit trail for gap-fill attempts. |
| `qmd_market_coverage_manifest_v1` | yes if startup maintenance or historical planning is enabled | Coarse run-level live repair and historical flatfile planning manifest. |
| `qmd_live_event_coverage_v1` | yes | Fine-grained recent q_live event coverage confirmations and repair intervals. |
| `qmd_flatfile_event_coverage_v1` | yes | Historical flatfile event coverage bootstrap. |
| `qmd_gap_fill_symbol_universe_v1` | yes | Durable ticker queue and per-symbol status source for recent q_live REST repair. |

## Common Checks

Before live use:

1. `MASSIVE_API_KEY` is present.
2. `QMD_CLICKHOUSE_URL` is reachable from this machine.
3. `QMD_CLICKHOUSE_DATABASE` points to the app-owned write database.
4. `/health` returns `status: running`.
5. `/metrics` shows rising `ingest_trades` and `ingest_quotes` during active market data.
6. Drop counters stay at zero or remain explainable during bursts.
7. `live_market_events_v1` receives rows or `/stream/compact-events` emits rows.
8. `live_market_bars`, `bars_by_symbol_time`, and `bars_by_time_symbol` receive closed bars.

## Failure Triage

| Symptom | First Checks |
|---|---|
| `/health` says `api_only_missing_massive_key` | Check env loading and `MASSIVE_API_KEY`. |
| No quote/trade counts during market hours | Check Massive websocket auth, subscription channels, and network. |
| Compact rows not written | Check ClickHouse URL, user/password, database permissions, `compact_event_queue_dropped`, and `compact_event_rejected`. |
| Raw rows not written | Raw persistence is optional; check `QMD_PERSIST_RAW_EVENTS=true`, ClickHouse permissions, and `clickhouse_events_dropped`. |
| Bars are missing but raw rows arrive | Check `bar_events_dropped`, bar shard count, and configured timeframes. |
| Indicators are missing but bars arrive | Check `indicator_events_dropped`, `bar_rows_indicator_dropped`, and indicator history limits. |
| Scanner primitives are missing | Confirm bars close, then check whether current market activity meets primitive thresholds. |
| API is slow | Lower broadcast frequency, inspect websocket clients, and watch drop counters. |
| Gap fill does not run | Check `QMD_GAP_FILL_ENABLED`, `MASSIVE_API_KEY`, `QMD_GAP_FILL_MODE`, and whether the current phase allows repair. |
| Gap fill records `awaiting_live_symbols` | The durable symbol universe is empty and no websocket compact symbols have arrived yet. Once websocket symbols arrive, repair should add them to `qmd_gap_fill_symbol_universe_v1` and retry on `QMD_GAP_FILL_AWAITING_SYMBOLS_RETRY_MS`. |
| Gap fill records `no_symbols_available` | Outside streaming hours, no q_live or latest historical compact-event symbols were available for REST repair. |
| Gap fill keeps writing many rows | Live ingest may be dropping compact events, or the gateway was offline for longer than expected. |
| Startup maintenance records `needs_manual_rebuild` | Recent `q_live` committed ordinals are structurally inconsistent. Do not rely on automatic tail repair; inspect/rebuild the affected live event range. |

## Security Notes

The default bind is `127.0.0.1:8795`, which is local-only. Use a LAN bind only when another trusted machine must connect.

Do not expose the gateway API directly to the internet. It has permissive CORS because it is meant for a trusted local app environment, not a public service.

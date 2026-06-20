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
| Gap-fill worker | Repairs raw quote/trade gaps with Massive REST when raw persistence is enabled. | Missing raw rows remain until next repair. |
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

Stop it with `Ctrl+C`. The API server has graceful shutdown on `Ctrl+C`.

## Health And Snapshot Endpoints

| Endpoint | Use |
|---|---|
| `GET /health` | Basic running state, subscriptions, session phase, and market-state metrics. |
| `GET /config` | Effective configuration after env parsing. Do not expose publicly because it includes structure and presence flags. |
| `GET /metrics` | Operational counters and lag values. |
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
| `compact_event_queue_dropped` | Events not queued for compact conversion/persistence. | Increase compact queue size or improve compact writer throughput. |
| `compact_event_rejected` | Structurally invalid events dropped before compact emit/insert. | Inspect provider data quality; raw persistence can be enabled for debugging. |
| `compact_events_persisted` | Compact rows inserted into ClickHouse. | Should rise during active feed when `QMD_PERSIST_COMPACT_EVENTS=true`. |
| `clickhouse_events_dropped` | Raw events not queued for optional raw persistence. | Relevant only when `QMD_PERSIST_RAW_EVENTS=true`. |
| `bar_events_dropped` | Events not queued to bar workers. | Increase bar shards/capacity or profile bar aggregation. |
| `indicator_events_dropped` | Events not queued to tick indicators. | Increase indicator shards/capacity or reduce indicator work. |
| `bar_rows_writer_dropped` | Closed bars not queued for persistence. | Increase bar writer queue or ClickHouse throughput. |
| `bar_rows_indicator_dropped` | Closed bars not queued to bar indicators. | Increase indicator bar queue or indicator shards. |
| `bar_rows_scanner_dropped` | Closed bars not queued to scanner primitives. | Increase scanner primitive queue or simplify primitive evaluation. |
| `bar_rows_emitted` | Closed bars produced. | Should rise by timeframe during active feed. |
| `scanner_candidates_emitted` | Scanner primitives emitted. | Useful for scanner activity rate. |
| `gap_fill_runs`, `gap_fill_failures` | Gap-fill attempts and failures. | Failures need REST/ClickHouse error review. |
| `gap_fill_rows_written` | Rows repaired by REST gap fill. | High values after restart are expected; high values every cycle mean ingestion gaps remain. |
| `gap_fill_last_duration_ms` | Last gap-fill runtime. | If large, reduce symbols/pages or move repair after hours. |

## Backpressure Behavior

Backpressure means a queue is filling faster than its consumer drains it.

The gateway does not block Massive ingest when a downstream queue is full. It drops that downstream item and increments a metric. This protects live ingestion from slow ClickHouse writes, slow API clients, or heavy indicator work.

The tradeoff is explicit: it is better to lose a derived downstream item and see the counter rise than to freeze the websocket ingest loop.

## Gap Fill Modes

Gap fill uses Massive REST:

```text
/v3/trades/{ticker}
/v3/quotes/{ticker}
```

It writes repaired rows to `live_massive_trades` and `live_massive_quotes`, then records audit rows in `qmd_gap_fill_runs`.
Because it is still raw-table based, the worker is skipped unless `QMD_PERSIST_RAW_EVENTS=true`.

| Mode | When It Runs | Purpose |
|---|---|---|
| `session_catch_up` or `session` | Once at startup if current New York time is premarket, regular, or after-hours. | Quickly repair recent raw-data gaps after a restart during a session. |
| `after_hours` or `repair` | On timer outside active streaming hours. | Repair database gaps without competing with live ingest. |
| `auto` | Session catch-up during active hours, after-hours repair outside active hours. | Default mode. |

If `QMD_GAP_FILL_SYMBOLS` is set, only those tickers are checked. If it is empty, the worker discovers symbols already present in today's raw tables.

Current limitation: REST-repaired rows are persisted, but session catch-up does not yet feed those REST rows into the live in-memory bar/indicator/scanner state. Immediate scanner warm-up from REST rows is a future improvement.

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
| `live_massive_trades` | optional | Raw trade source for replay/debug/gap fill when `QMD_PERSIST_RAW_EVENTS=true`. |
| `live_massive_quotes` | optional | Raw quote source for replay/debug/gap fill when `QMD_PERSIST_RAW_EVENTS=true`. |
| `live_market_bars` | yes | Published bars built from quotes/trades. |
| `live_market_indicators` | optional | Published indicators when `QMD_PERSIST_INDICATORS=true`. |
| `qmd_gap_fill_runs` | yes if gap fill enabled | Audit trail for gap-fill attempts. |

## Common Checks

Before live use:

1. `MASSIVE_API_KEY` is present.
2. `QMD_CLICKHOUSE_URL` is reachable from this machine.
3. `QMD_CLICKHOUSE_DATABASE` points to the app-owned write database.
4. `/health` returns `status: running`.
5. `/metrics` shows rising `ingest_trades` and `ingest_quotes` during active market data.
6. Drop counters stay at zero or remain explainable during bursts.
7. `live_market_events_v1` receives rows or `/stream/compact-events` emits rows.
8. `live_market_bars` receives closed bars.

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
| Gap fill does not run | It is skipped unless `QMD_PERSIST_RAW_EVENTS=true`; compact-only gap fill is not implemented yet. |
| Gap fill keeps writing many rows | Live ingest may be dropping raw persistence, or the gateway was offline for longer than expected. |

## Security Notes

The default bind is `127.0.0.1:8795`, which is local-only. Use a LAN bind only when another trusted machine must connect.

Do not expose the gateway API directly to the internet. It has permissive CORS because it is meant for a trusted local app environment, not a public service.

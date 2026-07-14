# QMD Historical Gateway

This Rust service is the historical market-data source for Replay, Backtest,
and Backtest Debug. It reads `market_sip_compact.events_YYYY` in deterministic
`(sip_timestamp_us, ticker, ordinal)` order and mirrors QMD's compact-event,
canonical-event, and enriched-bar resource schemas. Bars are calculated from
events by the exact `qmd_core::bars` implementation used by live QMD; no
historical bar table is used.

Live trading must use `services/qmd-gateway`. This service is deliberately
read-only: it cannot connect to Massive, run live gap repair, or write live QMD
state.

## Shared Rust authority

`services/qmd-gateway` now exports its existing event, compact-event decoder,
bar, indicator, scanner, state, and API models as the `qmd_core` Rust library.
The live binary compiles against that library, and this crate depends on the
same package by path. There is no copied event or bar implementation.

Historical compact condition/indicator tokens and dense tape values are decoded
through `market_sip_compact.event_condition_token_reference` and
`market_sip_compact.ref_stock_tapes` during service preflight. Missing or
incompatible reference rows stop startup.

Run from the repository root:

```powershell
cargo run --manifest-path services\qmd_history_gateway\Cargo.toml
```

The repository launcher builds from the local Cargo cache and then starts the
service:

```powershell
.\scripts\run_qmd_history_gateway.ps1
```

The launcher is idempotent. Before building or starting another process, it
resolves `QMD_HISTORY_BIND` and checks `/health`. If the expected historical
gateway is already running and ready, it reports that state and exits
successfully. If the address belongs to another service, or the port is open
without a ready historical `/health` response, it stops with an actionable
address-conflict message instead of attempting a duplicate bind.

Configuration uses `QMD_HISTORY_CLICKHOUSE_URL`, `QMD_HISTORY_DATABASE`,
`QMD_HISTORY_TABLE_PREFIX`, `QMD_HISTORY_CLICKHOUSE_USER`,
`QMD_HISTORY_CLICKHOUSE_PASSWORD`, `QMD_HISTORY_BIND`,
`QMD_HISTORY_BATCH_SIZE`, and `QMD_HISTORY_MAX_EVENTS_PER_REQUEST`.

Defaults:

- bind: `127.0.0.1:8801`
- database: `market_sip_compact`
- yearly-table prefix: `events_`
- batch size: `25000`
- maximum events in one bar snapshot calculation: `2000000`

## API

All timestamps must be RFC3339 with an explicit timezone. Historical requests
are half-open: `start <= event_time < end`.
Supported bar timeframes are the live QMD set: `1s`, `10s`, `30s`, `1m`, `5m`,
and `1h`.

- `GET /health`
- `GET /config`
- `GET /coverage?start=...&end=...`
- `GET /coverage/latest` (latest market day with canonical event coverage)
- `GET /snapshot/compact-events/{ticker}?start=...&end=...&limit=...`
- `GET /snapshot/bars/{ticker}?start=...&end=...&timeframe=1m&limit=...` (bars plus canonical QMD bar indicators)
- `WS /stream/compact-events?start=...&end=...&tickers=AAPL,MSFT`
- `WS /stream/events?start=...&end=...&tickers=AAPL,MSFT`
- `WS /stream/bars/{ticker}?start=...&end=...&timeframe=1m`

The bar snapshot calculates its `indicators` array from the returned ordered
bars through the shared live-QMD indicator state. Replay, Backtest, and Canvas
charts therefore use the live formulas without reading or maintaining a
separate historical indicator table.

The streaming endpoints close after the requested historical window is fully
delivered. The live QMD equivalents remain open and publish newly arriving
events; the event and bar payload schemas are shared.

`/coverage` verifies selected exchange days from
`market_sip_compact.events_ordinal_continuity`, the canonical per-symbol,
per-source-day coverage authority written with the event tables. It reports
event and symbol counts plus the corresponding `events_YYYY` tables without
scanning hundreds of millions of event rows. Snapshot and stream payloads
continue to read the event tables themselves.

## Validation

```powershell
cargo test --offline --manifest-path services\qmd-gateway\Cargo.toml
cargo test --offline --manifest-path services\qmd_history_gateway\Cargo.toml
```

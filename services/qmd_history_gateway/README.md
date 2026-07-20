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
`QMD_HISTORY_TABLE_PREFIX`, `QMD_HISTORY_MACRO_BARS_TABLE`, `QMD_HISTORY_CLICKHOUSE_USER`,
`QMD_HISTORY_CLICKHOUSE_PASSWORD`, `QMD_HISTORY_BIND`,
`QMD_HISTORY_STRUCTURE_DATABASE`, `QMD_HISTORY_STRUCTURE_EVENTS_TABLE`,
`QMD_HISTORY_BATCH_SIZE`, `QMD_HISTORY_MAX_EVENTS_PER_REQUEST`,
`QMD_HISTORY_CACHE_MAX_ENTRIES`, `QMD_HISTORY_CACHE_MAX_BARS_PER_ENTRY`, and
`QMD_HISTORY_CACHE_UPDATE_CAPACITY`. Memory/concurrency controls are
`QMD_HISTORY_CACHE_MAX_BYTES`, `QMD_HISTORY_CACHE_MAX_CONCURRENT_BUILDS`,
`QMD_HISTORY_CACHE_MAX_CONCURRENT_FETCHES`, `QMD_HISTORY_FETCH_CHUNK_HOURS`,
`QMD_HISTORY_CACHE_MAX_UPDATES_PER_ENTRY`, and
`QMD_HISTORY_PRODUCT_CACHE_MAX_ROWS_PER_ENTRY`.

Defaults:

- bind: `127.0.0.1:8801`
- database: `market_sip_compact`
- yearly-table prefix: `events_`
- durable macro table: `macro_bars_by_time_symbol`
- generic-structure database/table: `q_live.qmd_structure_events_v1`
- batch size: `25000`
- maximum events in one derived calculation: `10000000`
- revision-aware derived cache entries: `256`
- maximum bars retained per derived entry: `100000`
- total derived-cache memory budget: `1 GiB`
- concurrent cold builds: `4`
- service-wide concurrent ClickHouse chunk fetches: `8`
- source fetch chunk width: `24 hours`
- maximum derived updates per entry: `500000`

Historical bar reconstruction loads at most 5,000 confirmed generic-structure
events for the requested symbol from the 90 days preceding the requested
window. The warm start is causal: only rows confirmed before the window are
eligible, and the reconstructed event-native state is sampled at each bar end
without changing semantics across chart timeframes.
- maximum canonical product rows per entry: `2000000`

## API

All timestamps must be RFC3339 with an explicit timezone. Historical requests
are half-open: `start <= event_time < end`.
Product snapshots additionally clamp their build horizon to `min(end, as_of)`;
future events never enter an as-of cache entry.
Supported bar timeframes are the live QMD set: `100ms`, `1s`, `5s`, `10s`,
`30s`, `1m`, `5m`, and `1h`.

- `GET /health`
- `GET /config`
- `GET /coverage?start=...&end=...`
- `GET /coverage/latest` (latest market day with canonical event coverage)
- `GET /snapshot/cache` (cache hits, misses, builds, entries, and evictions)
- `GET /snapshot/family-bars/{ticker}?start=...&end=...&as_of=...&resolution=1m`
- `GET /snapshot/condition-bars/{ticker}?start=...&end=...&as_of=...&resolution=1m`
- `GET /snapshot/macro-bars/{ticker}?start=...&end=...&as_of=...&timeframe=1d`
- `GET /snapshot/chart-macro-bars/{ticker}?start=...&end=...&as_of=...&timeframe=1d|1mo` (bounded chart history; monthly rows aggregate durable daily macro families)
- `GET /snapshot/compact-events/{ticker}?start=...&end=...&limit=...`
- `GET /snapshot/microstructure-forecast/{ticker}?start=...&end=...&limit=1024` (the shared deterministic 25-, 100-, and 500-event next-midpoint forecast contract plus confidence-gated unified `buy`/`sell`/`wait` action used by live QMD, strategies, and Canvas)
- `GET /snapshot/bars/{ticker}?start=...&end=...&timeframe=1m&limit=...` (bars plus canonical QMD bar indicators)
- `WS /stream/compact-events?start=...&end=...&tickers=AAPL,MSFT`
- `WS /stream/events?start=...&end=...&tickers=AAPL,MSFT`
- `WS /stream/bars/{ticker}?start=...&end=...&timeframe=1m`
- `WS /stream/indicators/{ticker}?start=...&end=...&timeframe=1m`
- `WS /stream/derived/{ticker}?start=...&end=...&timeframe=1m&emit=updates`

Bars and indicators have separate ordered streams. A cold derived build emits
each finalized bar before the bounded indicator worker calculates its forecast,
so chart price data is never held behind indicator calculation. Builds calculate
only the requested output timeframe plus the canonical 100 ms forecast grid.
Each higher-timeframe microstructure row confidence-weights the 100 ms samples
inside that bar and applies an agreement penalty to confidence.
Session-anchored cumulative Level-1 OFI and signed trade-volume delta are then
advanced from those interval-local values by the shared stateful indicator
calculator. Both start from one zero baseline at 04:00 New York time and do not
reset at the 09:30 regular-session open. The DST-aware anchor is shared by live
and historical QMD, preserving cumulative-flow and confirmation/absorption
semantics at every supported timeframe.

The chart `vwap` indicator uses the same 04:00 New York session anchor and
continues through the 09:30 regular open. It accumulates each selected
timeframe bar's `hlc3 * volume`, matching TradingView's default Session VWAP
source and extended-hours anchor semantics. The canonical bar-level `vwap`
remains the eligible trade-price notional divided by eligible volume.

`/stream/derived` supports `emit=full`, `emit=updates`, and
`emit=full_then_updates`. Incremental messages contain a monotonic sequence,
the causal finalized bar, its canonical indicator row, and the bar's event-time
`as_of`. Clients resume with `after_sequence`; `max_updates=1` implements one
Replay step. `updates_per_second=0` is unthrottled fast-forward, while a
positive value provides paced Replay output.

The bar snapshot and derived stream use the shared live-QMD bar store and
stateful indicator calculator. A source-revision and engine-version cache key
prevents redundant calculation while invalidating results after canonical
event rebuilds or QMD schema changes. Cold stream subscribers receive updates
as ClickHouse events are read; concurrent and later consumers share the same
single-flight build. Cold builds split long windows into fixed time chunks,
prefetch a bounded number concurrently under a service-wide semaphore, and
consume the chunk streams oldest-to-newest for causal indicators.

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

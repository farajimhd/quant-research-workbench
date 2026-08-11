# QMD Historical Gateway

This Rust service is the bounded market-history source for charts, Replay,
Backtest, and Backtest Debug. It plans non-overlapping reads across completed
`market_sip_compact.events_YYYY` archive coverage and recent `q_live.events`,
then exposes the remaining current-memory tail as an explicit QMD Gateway
continuation. Queryable rows retain deterministic event-time/source ordering
and mirror QMD's compact-event,
canonical-event, and enriched-bar resource schemas. Bars are calculated from
events by the exact `qmd_core::bars` implementation used by live QMD; no
historical bar table is used.

The archive watermark is the verified 20:00 America/New_York close of the last
covered market session, converted to UTC with daylight-saving rules; it is not
an assumed UTC-midnight boundary. Every emitted event is globally ordered by
`sip_timestamp_us`, ticker, and source ordinal/arrival sequence, while the
original `source_sequence` remains part of the shared compact-event contract.
Any uncovered interval is explicit, and the current live tail remains a QMD
Gateway continuation rather than a hidden physical-table read.
The application typed QMD client composes that continuation for compact-event
windows, using this service's exact current-live segment bounds and QMD
Gateway's versioned bounded page. Chart and historical Scanner continuation are
not yet composed.

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
`QMD_HISTORY_TABLE_PREFIX`, `QMD_HISTORY_DAILY_SESSION_BARS_TABLE`, `QMD_HISTORY_CLICKHOUSE_USER`,
`QMD_HISTORY_CLICKHOUSE_PASSWORD`, `QMD_HISTORY_BIND`,
`QMD_HISTORY_STRUCTURE_DATABASE`, `QMD_HISTORY_STRUCTURE_EVENTS_TABLE`,
`QMD_HISTORY_RECENT_DATABASE`, `QMD_HISTORY_RECENT_EVENT_TABLE`,
`QMD_HISTORY_RECENT_EVENT_COVERAGE_TABLE`, `QMD_HISTORY_LIVE_GATEWAY_URL`,
`QMD_HISTORY_BATCH_SIZE`, `QMD_HISTORY_MAX_EVENTS_PER_REQUEST`,
`QMD_HISTORY_CACHE_MAX_ENTRIES`, `QMD_HISTORY_CACHE_MAX_BARS_PER_ENTRY`, and
`QMD_HISTORY_CACHE_UPDATE_CAPACITY`. Memory/concurrency controls are
`QMD_HISTORY_CACHE_MAX_BYTES`, `QMD_HISTORY_CACHE_MAX_CONCURRENT_BUILDS`,
`QMD_HISTORY_CACHE_MAX_CONCURRENT_FETCHES`, `QMD_HISTORY_FETCH_CHUNK_HOURS`,
`QMD_HISTORY_CACHE_MAX_UPDATES_PER_ENTRY`, and
`QMD_HISTORY_PRODUCT_CACHE_MAX_ROWS_PER_ENTRY`. Full-market Scanner replay is
bounded by `QMD_HISTORY_SCANNER_CACHE_MAX_ENTRIES`,
`QMD_HISTORY_SCANNER_MAX_EVENTS_PER_SNAPSHOT`, and
`QMD_HISTORY_SCANNER_SHARD_COUNT`.

Defaults:

- bind: `127.0.0.1:8801`
- database: `market_sip_compact`
- yearly-table prefix: `events_`
- recent source: `q_live.events`, gated by
  `q_live.qmd_live_event_coverage_v1`
- current continuation: `http://127.0.0.1:8800`
- durable daily-session table: `daily_session_bars_by_symbol_time_v1`
  (`QMD_HISTORY_DAILY_SESSION_BARS_TABLE`); QMD derives closed 1-day and
  weekly, monthly, and yearly trade bars only from causally available completed
  daily rows. The current macro period is marked partial.
- generic-structure database/table: `q_live.qmd_structure_events_v2`
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
- full-market Scanner cache entries: `2`
- maximum events in one Scanner snapshot: `250000000`
- Scanner calculation shards: `16`

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
  reports the exact archive-plus-recent source tables selected by the source
  plan, its hash and completeness, and combined event/ticker/time bounds.
- `GET /coverage/latest` (latest market day with canonical event coverage)
- `GET /source-plan?start=...&end=...&tickers=AAPL,MSFT` (ordered archive,
  recent, gap, and current-live continuation segments; clients never choose a
  physical database)
- `GET /capability-catalog` (shared QMD Live/History computation vocabulary)
- `GET /snapshot/cache` (cache hits, misses, builds, entries, and evictions)
- `GET /snapshot/scanner-derived?start=...&end=...&as_of=...` (causal
  full-market 100 ms QMD indicator projection, active scored signals on their
  declared clocks, and the newest 20,000 lifecycle events; calculated with the
  shared live engines)
- `GET /snapshot/family-bars/{ticker}?start=...&end=...&as_of=...&resolution=1m`
- `GET /snapshot/condition-bars/{ticker}?start=...&end=...&as_of=...&resolution=1m`
- `GET /snapshot/macro-bars/{ticker}?start=...&end=...&as_of=...&timeframe=1d`
- `GET /snapshot/chart-macro-bars/{ticker}?start=...&end=...&as_of=...&timeframe=1d|1w|1mo|1y` (bounded chart history; macro rows aggregate the durable completed daily authority)
- `GET /snapshot/compact-events/{ticker}?start=...&end=...&limit=...`
- `GET /snapshot/events?start=...&end=...&tickers=AAPL,MSFT&limit=...`
  returns decoded market events, an explicit continuation cursor, and the
  source-plan hash/revision token. A continuation supplies both as
  `expected_source_plan_hash` and `expected_revision_token`; drift returns HTTP
  409 with `source_revision_conflict` and `restart_snapshot`. Replay uses this
  bounded pull contract so pausing does not leave a push stream saturated or
  require buffering an unbounded remainder of the session.
- `GET /snapshot/bars/{ticker}?start=...&end=...&timeframe=1m&limit=...` (bars plus canonical QMD bar indicators)
- `GET /snapshot/chart-bars/{ticker}?start=...&end=...&timeframe=100ms|1s|5s|10s|30s|1m|5m|1h&limit=...&stage=bars|full`
  (`stage=bars` releases bounded chronological price bars after the complete
  source window is consumed, while the same single-flight cache entry continues
  its indicator build; the backward-compatible default `stage=full` also returns
  indicators, canonical
  `market_signal_events`, structure events, and the newest 4,000 distinct
  causal, latest-session `structure_level_history` entries needed to
  reconstruct encountered-level volume profiles without projecting historical
  levels onto only the final indicator row or leaking older-session volume)
- `WS /stream/compact-events?start=...&end=...&tickers=AAPL,MSFT`
- `WS /stream/events?start=...&end=...&tickers=AAPL,MSFT`
- `WS /stream/bars/{ticker}?start=...&end=...&timeframe=1m`
- `WS /stream/indicators/{ticker}?start=...&end=...&timeframe=1m`

- `WS /stream/derived/{ticker}?start=...&end=...&timeframe=1m&emit=updates`

For `current_live` plan segments, QMD History pages the QMD Gateway
cross-market compact-event contract, fails closed if the requested window was
evicted, restores canonical event-time order, and then uses the same shared QMD
decoder and computation engines as archive/recent inputs. `SourceRevision`
therefore exposes `live_continuation_sequence` and `request_complete` separately
from `complete_for_history`: a request may be complete while still not being an
immutable durable revision suitable for a paused Replay.

Bars and indicators have separate ordered streams. A cold derived build emits
each finalized bar before the bounded indicator worker calculates its evidence,
so chart price data is never held behind indicator calculation. Builds calculate
only the requested output timeframe plus the canonical 100 ms evidence grid.
Each higher-timeframe microstructure row aggregates raw 100 ms evidence for its
diagnostic fields. Its Flow-Structure Composite values confidence-weight the
canonical 100 ms composite states inside the display bar. Timestamped scored
lifecycles are returned as `market_signal_events`, so a chart can render the
first transition inside a larger candle without waiting for that candle to close.
Versioned QMD market signals are calculated by the same Rust
`MarketSignalEngine` used by live QMD and are returned separately as
`market_signal_events`. Their `effective_at` is the causal close of the
method's declared working timeframe, so historical charts and strategies do
not wait for a larger display candle to close.
Generic Structure local swings and accepted breaks are likewise retained in a
separate chronological `structure_events` stream produced from the same ordered
eligible trades. Each supported timeframe owns its exact trade-extrema buckets,
local swings, and break direction independently of the requested display-bar
resolution. Requesting 1-second bars therefore cannot discard 100 ms or
1-second swing levels, nor collapse simultaneous timeframe events into the last
event attached to a sampled bar.
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

The Canvas backend durably materializes completed `scanner-derived` snapshots
in `q_live.canvas_historical_qmd_scanner_v1`,
`q_live.canvas_historical_qmd_signal_event_v1`, and
`q_live.canvas_historical_qmd_snapshot_meta_v1`. Completion is accepted only
when the non-empty stored indicator count matches metadata. Scanner requests
return their base universe while the first replay runs, then poll the durable
artifact; subsequent requests do not replay the session.

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

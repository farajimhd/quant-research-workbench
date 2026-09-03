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
`source_sequence` field remains part of the shared compact-event contract.
Recent `q_live` rows retain the original vendor sequence. Legacy archive tables
do not persist it, so QMD History uses their deterministic `ordinal` as the
explicit compatibility fallback; ordering remains exact, but a legacy archive
event is not promised to retain the same causation hash as its earlier live row.
Any uncovered interval is explicit, and the current live tail remains a QMD
Gateway continuation rather than a hidden physical-table read.
Live compact schema v5 in `q_live.events` preserves
`execution_timestamp_us` separately from `sip_timestamp_us`. The immutable
`market_sip_compact.events_YYYY` historical archive retains its common
16-column SIP-time schema and must not be altered by this service. Historical
adapters synthesize an unavailable execution clock (`0`) solely to satisfy the
shared in-memory wire shape; causal Scanner, indicator, VWAP, and structural
projections advance in SIP availability order. Exact retrospective execution
time is therefore available only for live rows whose source contract actually
contains it, never inferred from an archive column's presence.
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
successfully only when algorithm version, checkpoint table, and checkpoint-set
authority also match. A stale structural authority requires an explicit
restart. `-NoBuild -BinaryPath PATH` starts a clean prebuilt release without
requiring Cargo or compiling unrelated working-tree changes. If the address
belongs to another service, or the port is open without a ready historical
`/health` response, startup stops with an actionable conflict message.

Configuration uses `QMD_HISTORY_CLICKHOUSE_URL`, `QMD_HISTORY_DATABASE`,
`QMD_HISTORY_TABLE_PREFIX`, `QMD_HISTORY_DAILY_SESSION_BARS_TABLE`,
`QMD_HISTORY_INTRADAY_BASE_BARS_TABLE`, `QMD_HISTORY_CLICKHOUSE_USER`,
`QMD_HISTORY_CLICKHOUSE_PASSWORD`, `QMD_HISTORY_BIND`,
`QMD_HISTORY_STRUCTURE_DATABASE`, `QMD_HISTORY_STRUCTURE_EVENTS_TABLE`,
`QMD_HISTORY_STRUCTURE_DAILY_CHECKPOINT_TABLE`,
`QMD_STRUCTURE_CHECKPOINT_SET_ID`,
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
Generic Structure checkpoint advancement is bounded by
`QMD_HISTORY_STRUCTURE_CHECKPOINT_MAX_CONCURRENT_ADVANCEMENTS`,
`QMD_HISTORY_STRUCTURE_CHECKPOINT_MAX_EVENTS`, and
`QMD_HISTORY_STRUCTURE_CHECKPOINT_MAX_WINDOW_HOURS`. Full checkpoint-bearing
requests have a separate bounded body limit,
`QMD_HISTORY_STRUCTURE_CHECKPOINT_REQUEST_MAX_BYTES` (default `67108864`), so
large valid level books can advance without weakening the smaller default limit
on unrelated endpoints.
Historical chart reconstruction seeds one causal ticker-level Structure book,
including still-valid prior-session levels, from the preceding
`QMD_HISTORY_STRUCTURE_BOOK_LOOKBACK_DAYS` (default `180`). The seed is complete
or fails closed: `QMD_HISTORY_STRUCTURE_BOOK_MAX_SEED_EVENTS` (default
`2000000`) is an explicit resource ceiling, never a tail-truncation policy. If
persisted Structure events or a compatible daily checkpoint are unavailable,
QMD History causally rebuilds that same complete horizon from canonical tape;
there is no shorter fallback authority. Concurrent chart pages and timeframes
for the same ticker and causal boundary share one in-flight checkpoint rebuild.
The trade-only cold seed uses streamed four-hour ClickHouse chunks by default
(`QMD_HISTORY_STRUCTURE_FETCH_CHUNK_MINUTES=240`), so memory and remote response
sizes remain bounded without paying one query round trip every 30 minutes.
Completed cold seeds are written atomically beneath the configured prepared-bar
runtime root and keyed by ticker, full source revision, causal boundary, and
calculation revision. A service restart therefore restores the exact seed rather
than rebuilding the multi-month book; corrupt or mismatched artifacts fail closed.
Single-ticker deployment-gap repair evidence is read from
`QMD_HISTORY_RECENT_FOCUSED_REPAIR_TABLE` (default
`q_live.qmd_gap_fill_symbol_universe_v1`). A completed, error-free repair whose
recorded window covers the gap promotes only that ticker's interval to Recent;
it never broadens global coverage.

Defaults:

- bind: `127.0.0.1:8801`
- database: `market_sip_compact`
- yearly-table prefix: `events_`
- recent source: `q_live.events`, gated by
  `q_live.qmd_live_event_coverage_v1`
- current continuation: `http://127.0.0.1:8795`; port `8800` belongs to the
  IBKR Supervisor and is never a QMD fallback
- durable daily-session table: `daily_session_bars_by_symbol_time_v1`
  (`QMD_HISTORY_DAILY_SESSION_BARS_TABLE`); QMD derives closed 1-day and
  weekly, monthly, and yearly trade bars only from causally available completed
  daily rows. The current macro period is marked partial.
- durable intraday fast path: `intraday_base_bars_by_time_ticker`
  (`QMD_HISTORY_INTRADAY_BASE_BARS_TABLE`); bars-only historical chart pages
  use the persisted event-derived grid when the requested ticker, date, and
  resolution exist, and otherwise fall back to causal raw-event reconstruction.
- generic-structure database/table: `q_live.qmd_structure_events_v2`
- certified daily-checkpoint authority:
  `q_live.qmd_structure_daily_checkpoint_v2` set
  `canonical-tradable-20250101-20260831-v16-cert-v1`; checkpoint payload,
  split lineage, source revision, and certification chain are validated before
  a seed can be used
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
- concurrent Generic Structure checkpoint advancements: `4`
- maximum events in one checkpoint advancement: `5000000`
- maximum checkpoint advancement window: `72 hours`

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
  uses a two-stage, resource-capped continuity lookup and coalesces identical
  cold requests behind a 30-second bounded cache. It first resolves only the
  target source date, then aggregates canonical ticker counts for that date;
  it never groups the complete continuity history on a status or source-plan
  request.
- `GET /source-plan?start=...&end=...&tickers=AAPL,MSFT` (ordered archive,
  recent, scheduled-closed, gap, and current-live continuation segments;
  clients never choose a physical database). Known weekend and 20:00-04:00
  New York closures are covered-empty; possible in-session gaps remain
  uncovered and fail closed.
- `GET /capability-catalog` (shared QMD Live/History computation vocabulary)
- `GET /snapshot/cache` (cache hits, misses, builds, entries, and evictions)
- `GET /snapshot/scanner-derived?start=...&end=...&as_of=...` (causal
  full-market 100 ms QMD indicator projection, active scored signals on their
  declared clocks, and the newest 20,000 lifecycle events; calculated with the
  shared live engines)
- `POST /materialize/generic-structure-checkpoint` advances one versioned
  Generic Structure checkpoint through one explicit ticker/as-of window. It
  accepts Recent and Current-Live source tiers because those preserve the live
  arrival identity, and scheduled-closed covered-empty segments because they
  cannot mutate the cursor. Archive ordinal or a possible in-session source gap
  returns a typed conflict instead of fabricating an exact continuation. The
  response includes the advanced checkpoint, event counts, source plan, and
  source revisions before and after replay.
- `POST /materialize/generic-structure-snapshot-advance` advances a replay or
  backtest-owned checkpoint through canonical historical events and returns
  both the successor checkpoint and its point-in-time snapshot. It preserves
  the exact-live-cursor contract above while avoiding a daily-book rebuild on
  every strategy decision second.
- `POST /estimate/generic-structure-event-counts` is the Campaign v2
  loopback-only planning primitive. It reads only the canonical
  `events_ordinal_continuity` index and reports total and largest-session event
  counts for bounded ticker batches. It never scans compact-event tables and
  never substitutes for streamed event counts or checkpoint cursors.
- `POST /estimate/generic-structure-trade-counts` is the legacy raw trade-count
  estimator retained for compatibility. New checkpoint campaigns must use the
  continuity-index endpoint above.
- `POST /materialize/generic-structure-rebuild` reconstructs a checkpoint from
  a fresh shared engine over one explicit, gap-free canonical Archive + Recent
  + Current-Live window. It is an operator recovery primitive, available only
  while QMD History is bound to loopback. Replay is event-bounded and
  revision-pinned; source-plan or source-revision drift fails closed. QMD Live
  remains the only service allowed to persist and activate the result.
- `GET /snapshot/family-bars/{ticker}?start=...&end=...&as_of=...&resolution=1m`
- `GET /snapshot/condition-bars/{ticker}?start=...&end=...&as_of=...&resolution=1m`
- `GET /snapshot/macro-bars/{ticker}?start=...&end=...&as_of=...&timeframe=1d`
- `GET /snapshot/chart-macro-bars/{ticker}?start=...&end=...&as_of=...&timeframe=1d|1w|1mo|1y` (bounded chart history; macro rows aggregate the durable completed daily authority)
- `GET /snapshot/compact-events/{ticker}?start=...&end=...&limit=...`
- `GET /snapshot/events?start=...&end=...&tickers=AAPL,MSFT&limit=...`
  returns decoded market events, an explicit continuation cursor, and the
  source-plan hash/revision token. The default `revision_policy=pinned`
  continuation supplies both as
  `expected_source_plan_hash` and `expected_revision_token`; drift returns HTTP
  409 with `source_revision_conflict` and `restart_snapshot`. Replay uses this
  bounded pull contract so pausing does not leave a push stream saturated or
  require buffering an unbounded remainder of the session. A Live consumer may
  select `revision_policy=advancing` and pin only `expected_source_plan_hash`;
  newer tail revision tokens are accepted, while a changed source plan still
  requires restart.

  `complete=true` means event pagination is exhausted. It must be read together
  with `source_revision.request_complete` (no declared source gap) and
  `source_revision.complete_for_history` (no live continuation required).
  Replay and Backtest require all three conditions; an explicit-gap contract
  audit may intentionally accept only exhausted pagination while recording the
  declared gaps and `request_complete=false`.
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

Archive and recent rows pass through QMD's shared source-lineage derivation.
Decoded event metadata therefore always carries bounded correlation and
causation IDs. Recent and Live agree when their stored vendor sequence agrees;
legacy archive rows derive causation from the deterministic ordinal fallback.

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

## Standalone structural checkpoint campaign

The `structure_checkpoint_campaign` binary runs Campaign v6 directly on a
workstation. It uses the continuity index both for workload estimates and exact
per-session ordinal bounds, daily bars only to prioritize currently tradable
tickers (with one bounded raw-liquidity fallback when those bars are absent),
the canonical completed-session authority for scheduling, the shared v16
decoder/engine for event-native calculation, and the shared ClickHouse writer
for immutable daily checkpoints. QMD HTTP services are not required on that
host. One worker owns one ticker through the whole period, loads continuity and
split authority once, streams bounded `(ticker, ordinal)` ranges, keeps its book
in memory, persists every completed session including empty ticker-days, and
releases it before taking another ticker.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python scripts\run_structure_checkpoint_campaign.py `
  --checkpoint-set-id canonical-tradable-20250101-20260831-v16-cert-v1 `
  --priority-ticker SUGP `
  --priority-ticker JUNS `
  --start-date 2025-01-01 `
  --end-date 2026-08-31 `
  --liquidity-start-date 2026-08-01 `
  --liquidity-end-date 2026-08-31 `
  --runtime-dir D:\TradingML\runtimes\qmd_gateway\structure-checkpoint-campaign-v6\canonical-2025-2026 `
  --process-workers 80
```

Rerunning the identical command is the supported resume operation. The launcher
reuses the immutable universe plan and each ticker starts from its last source-
and split-compatible checkpoint in the exact named set. The
launcher detaches a durable supervisor before opening the read-only interactive
terminal. Closing the terminal does not stop the campaign. The display shows
resolved and durable progress, worker assignments,
rates, ETA, recent work, and failures. Its fixed-row refresh does not repeatedly
clear the screen or scroll. Redirected output remains plain text.
ETA uses total events estimated from the ordinal continuity summary and the
aggregate batch-level processed-event rate from every active worker over a
rolling five-minute window, rather than assuming every ticker-day costs the
same. Failed or interrupted attempts roll back their uncommitted event
contribution. Aggregate schema v1 and worker schema v7 status documents are
written atomically without blocking workers on per-session disk writes. The
aggregate is stored in `campaign-status.json`, and
Ctrl+C closes only the read-only monitor. Use `--stop-existing graceful` to
finish and certify each worker's active day before stopping, or
`--stop-existing fast` to roll back the active incomplete day at the next
ordinal-chunk boundary.

The launcher accepts 1-80 worker processes. On Windows, every shard worker uses
a current-thread runtime pinned by `--core-index` across processor groups, so
80 workers occupy 80 distinct logical processors when the host exposes them.
ClickHouse or storage saturation can still prevent linear speedup.

The launcher uses a prebuilt executable from
`D:\TradingML\runtimes\bin\structure_checkpoint_campaign_v6.exe` when Cargo is
not installed. Use `--purge-existing-checkpoints` only for an explicitly
authorized cold reset; it deletes only the named checkpoint set on its first
run. The full contract is
[`structural_checkpoint_campaign_v6.md`](../../docs/data_contracts/structural_checkpoint_campaign_v6.md).

If the parent progress window exits while the worker status files continue to
advance, do not rerun the campaign command because that can launch duplicate
workers. Reattach a dashboard to the existing immutable manifest and plan:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python scripts\run_structure_checkpoint_campaign.py `
  --monitor-existing `
  --no-build `
  --binary D:\TradingML\runtimes\bin\structure_checkpoint_campaign_v6.exe `
  --checkpoint-set-id canonical-tradable-20250101-20260831-v16-cert-v1 `
  --runtime-dir D:\TradingML\runtimes\qmd_gateway\structure-checkpoint-campaign-v5\canonical-tradable-20250101-20260831-v16-cert-v1
```

The reattached monitor reconstructs aggregate progress and ETA from the atomic
per-worker status files and reports worker-status freshness. It is strictly
read-only: only the detached supervisor writes aggregate status or finalizes
the set registry. Ctrl+C closes this monitor without stopping the independently
running workers.

At every v16 checkpoint load, the shared engine recomputes derived hold score
fields from raw causal hold and accepted-break counts before applying another
event. This repairs old checkpoint presentations and strategy evidence without
purging or replaying otherwise valid checkpoint history.

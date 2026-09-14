# Backtest computation and monitoring

Backtest decisions, allocation, order matching, and journal writes retain their
original event order. Acceleration changes preparation and presentation, not the
strategy definition, thresholds, candle completion rules, or simulated costs.

## Causal boundaries

- Resident V7 frame lookahead holds at most 8,192 immutable inputs (other paths
  retain 64). Every timeframe requests the completed-second V7 view at or before
  the current market-event cutoff. A later intrasecond frame can share that
  already-known view; its future candle is not observed.
- Preparation uses four bounded ticker lanes. Each ticker has one QMD worker
  owner and advances chronologically. Journal authority is recorded when the
  engine consumes an observation, not when preparation reads it.
- V7 cursors address snapshots by exact completed second. A later cached
  snapshot cannot satisfy an earlier request. Timestamp and catalog checks
  remain mandatory; deferred transport errors surface at consumption.
- Detector history before admission is retained. Moving admission ahead of
  observation would change later decisions and is not this optimization.
- Historical broker initialization uses the session clock. Market events and
  decision timestamps remain the order-submission clocks. Position projections
  use simulated source time; receipt/recording time remains operational metadata.

## Lossless working-set storage

Single-session V7 backtests warm a run-scoped QMD working set before playback.
Each eligible ticker retains its verified preceding checkpoint and a read-only
NumPy array of persisted canonical one-second OHLCV inputs. The prepared stream
must carry the certified structure-input clock, complete requested coverage,
and the pinned catalog identity. Input hashes and warm-up receipts are recorded.
Future rows can reside in memory, but only `bar_end <= requested cutoff` enters
the unchanged streaming kernel. No current-day ticker replay or engine eviction
occurs during this path. Multi-session runs retain the existing causal cursor
path and explicitly record that mode in their authority evidence.

Four QMD worker owners process independent tickers concurrently. Batches contain
at most 256 requests; each ticker remains chronological. Delta v2 omits unchanged
geometry and order while retaining exact snapshots for 32 requested cutoffs.
Proposal matching uses NumPy comparisons; fit equations, tie-breaking, input
order, and checkpoint serialization remain unchanged. Assignment lookup indexes
keys by ticker so state replacements remain visible without a universe scan.
The backend seals prepared level evidence into immutable JSON-compatible
containers. Subsequent snapshots reuse unchanged geometry, and deep copies of
that sealed evidence retain its identity. Mutable calculations still construct
new values. Journal writes retain full evidence but skip immediate read-back
when the caller does not use the returned rows. Deferred-capital validation
still runs whenever pending requests exist.

The resident path admits one active stream per worker and budgets 4 GiB per
worker, 16 GiB across four dedicated workers, separate from chart workers. It
measures retained objects once at preparation and samples process RSS once per
wall-clock second during playback. This is a fail-closed budget check, not an
operating-system allocation limit. Capacity failures stop execution rather than evicting state or
dropping inputs. Completed/stopped runs release their QMD streams. A QMD restart
discards private working state; resume reconstructs it from pinned inputs and
the preceding checkpoint. Reused pre-optimization source revisions must match
the prepared source clock, split identity, and canonical build identity. When
lookback and session split windows differ, resume requires exact equality of
the current-session canonical bars and the original source revision.

Warm-up sends each preceding-session seed to the backend before playback. No
current-day bar is observed during preparation. Compact detector checkpoint
format 3 uses native JSON containers and preserves shared immutable evidence;
restoration also accepts legacy formats 1 and 2 without changing the detector
contract. Equal assignment parameters share one checkpoint evidence value.
The journal stores repeated evidence by content hash and reconstructs the
complete values when read. During batching, a bounded 65,536-key insertion cache
avoids rebinding already-stored blobs. Derived band and interaction projections
are cached only on immutable rows and excluded from serialization.
Strategy projections preserve unchanged row identities even when a neighboring
level changes. Static prior-book projections are prepared during warm-up.

Large backtest evidence blobs and the assignment, detector, and controller
checkpoint sections use a versioned zlib JSON envelope when compression saves
space. The envelope verifies uncompressed length and SHA-256, and evidence
references retain their original logical hashes. Legacy plain JSON remains
readable. Broker checkpoint fields stay inline for bounded history-table SQL.
Compression never removes fields or changes simulated state.
Assignment rows reference verified parameter evidence during backtest batching;
readers hydrate the original parameters, including subsequent updates. The
checkpoint stores the complete authority ledger as one verified JSON reference.
Quiet active bands retain identical contact, departure, and attempt bookkeeping
without calculating an event context that cannot be emitted. Crossings,
contacts, near-band observations, and retained break witnesses use the complete
transition path.

Backtest SQLite journals use FULL synchronous mode with transactions bounded
by 4,096 writes or 500 ms at write boundaries. Checkpoints, publication sequence
reads, outbox reads, and close force a commit. A failed transaction rolls back
and poisons further writes, requiring recovery from the durable prefix. Live
trading retains its existing immediate FULL commits.

The older cursor path used for chart requests and multi-session runs uses the
spill mechanism described below.

Each QMD worker retains 16 resident engines and spills evicted checkpoints to a
process-private SQLite cache under the local runtime root. Checkpoint restoration
uses the existing engine integrity contract plus a SHA-256 check on the spill.
Small SHA accumulator objects remain resident. Delta encoder bases also spill,
so an eviction does not force retransmission of every level.

Completed historical session bars are acquired once per worker and ticker/day,
then indexed reads supply only `start < bar_end <= cutoff`. Fetching a completed
day is input preparation; future bars do not enter the engine. Live/incomplete
days do not use the closed-day cache. A worker restart creates a fresh namespace.
Scratch transactions use an in-memory rollback journal without crash fsync;
the namespace is discarded after process death. This does not change the
authoritative trading journal. Normal managed shutdown closes and removes the
worker's scratch directory; abandoned crash directories are never reused.

The cache has a 32 GiB SQLite budget per worker and a 50,000 spilled-session
limit. Capacity or integrity failure stops the request; it never substitutes
partial data, a different book, approximate levels, or an unverified source.
Initial cold source loading and checkpoint seeding still have a cost. Warm-cache
speed must not be advertised as full-session throughput.

## UI publication

The recent-backtests table fetches independently of setup readiness. Its backend
read uses a dedicated two-thread executor, so readiness checks and indicator
warmup cannot exhaust the worker pool serving history. The frontend defers its
initial dispatch past React's development mount/cleanup probe to avoid duplicate
history reads. Refresh and Resume retain their own pending/error states; new-run
launch checks remain mandatory.

The history isolation test holds every default preparation worker busy while
the actual history endpoint returns. Browser tests hold configuration, warmup,
or preflight requests pending and exercise history loading, Refresh, and an
intercepted Resume across 12 light/dark, scale and viewport scenarios. Three
focused tests and 12 subtests pass; the managed production build and 12-scenario
strict UI review pass with zero objective issues. Evidence is under
`D:\TradingML\runtimes\ui-review\backtest-history-independent` and
`backtest-history-final`. The before trace already showed independent frontend
mounting; the fixed dependency was the shared backend executor, not a readiness
condition hiding the table.

A broader historical Canvas/launch-contract check returned 11 passes and three
failures in unchanged paths: the Canvas projection mock and two launch-page
source-text expectations. The affected endpoint AST and launch-page source
were verified unchanged from the preceding commit. These unrelated fixtures
were not rewritten as part of the history-loading fix.

The engine captures a boundary at most once per second, with forced lifecycle
updates. The packet contains the canonical broker projection, frozen assignment
state, simulated time, and the journal's committed sequence.
During playback it freezes assignment state only for requested chart symbols;
with no viewers it copies no ticker states. A newly requested symbol waits for
the next captured boundary. Paused and terminal boundaries retain complete
assignment coverage so late readers do not require engine advancement.

A separate process reads the journal through both the timestamp and sequence
boundary, projects activity, and encodes Canvas JSON. It serializes assignments
only for requested symbols. HTTP polling reads publications; it does not invoke
broker reconciliation or trigger assignment serialization on the engine loop.

There is one active render and one replaceable pending boundary, with at most
eight cached symbols. Slow readers cannot accumulate engine work. Cancelling a
reader does not cancel publication. Monitoring failures are exposed in compact
run status without changing the engine outcome. Terminal rendering releases the
process; resident-run eviction and replacement close the publisher.

This removes the expensive Canvas work from the engine loop. Capturing a small
boundary, IPC, and serving HTTP still cost resources; it is not a claim of zero
shared-machine overhead.

## Validation and activation

The final full-market measurement used the original pinned configuration and
2,610 prepared tickers on 2026-08-21. Diagnostic run
`8c5022c3-2536-4fda-885f-367d7c2f4e3b` processed 280,701 market events and 40,086
strategy frames: 618 simulated seconds in 263.28 seconds of playback (2.35x
real time), including two periodic checkpoints totaling 48.89 seconds. Warm-up
took an additional 274.46 seconds. Final stop/checkpoint cleanup is excluded
from playback. The sampled interval after five simulated minutes ran at 5.06x;
that quieter interval is not a full-session throughput claim. A separate
opening-prefix run managed only 1.02x over 134 simulated seconds, so dense
opening bursts remain a material limit. These measurements do not establish
peak regular-session throughput or the requested consistently high speed over
many full days. Strategy processing and checkpoint persistence remain the
largest measured stages.

Detailed timing and parity artifacts are stored under
`D:\TradingML\runtimes\tests` (`v7-performance-summary.json`,
`v7-ten-minute-engine-result.json`, and `v7-decision-parity.json`). Benchmarks
use isolated runs; the user's stopped run remains unchanged.

The resident-stream validation on 2026-09-14 compared the original V7 kernel
against 865 persisted bars across eight tickers. Every prefix snapshot and the
selected checkpoint hashes matched. A full-market comparison through 04:01 ET
matched all 2,368 strategy decisions and their complete evidence, excluding
run-specific identifiers and recording timestamps. This is prefix equivalence,
not a claim of completed full-session strategy or P&L equivalence.

Tests also cover future-tail independence, unavailable seeds, changed source
bars, legacy checkpoint restoration, compressed-evidence corruption, and 500
candles of exact interaction event/state parity. The broader regression run
passed 616 tests and 19 subtests. Two existing legacy fixture tests still fail:
`HistoricalDebugFixtureTests.test_debug_fixture_runs_strategy_round_trip_to_terminal_flat_state`
and `ReplayHistoricalFetchBudgetTests.test_structural_frames_use_completed_bars_and_not_legacy_structure_or_scanner`.
Those fixtures use obsolete strategy settings and V6 respectively; the current
contracts were not relaxed to accommodate them. QMD History's locked, offline
Rust check passed.

The original stopped run's CISS and PFSA source-window edge cases were verified
through the managed QMD HTTP path: warm-up consumed zero current-session bars,
and subsequent advancement retained the original pinned source revisions.
That check did not resume or modify the user's run.

Focused tests cover exact V7 eviction/rewind and transport parity, cutoff-limited
prefetch, deferred failures, sequence-fenced publication, cancellation, terminal
publication, and exact broker checkpoint equality with monitoring on/off.
`tests/test_backtest_publication.py` exercises a real spawned rendering process
and a buy/sell round trip. The QMD Rust routing test verifies stable ownership.

A bounded real-data comparison on 2026-09-14 used SUGP, JUNS, AAPL, NVDA, TSLA,
and MSFT at 04:00:01, 04:00:30, and 04:01:00 ET on 2026-08-21, with two resident
sessions to force eviction. All 18 snapshots equalled the original service
exactly. With the upstream cache warm, the two continuation rounds took 7.53 s
originally and 4.12 s with spill restoration (about 1.83x). Including initial
worker loading, totals were 11.53 s and 8.97 s. This isolates cache work; it is
not a full-market throughput result or a measurement of the four-worker pool.

Use `scripts/services.ps1` for runtime activation. Inspect active historical and
live work before restarting the backend or QMD History. Preserve complete run
checkpoints, frozen configuration, book fingerprint, and existing journals.
Never bypass runtime-version or checkpoint-integrity checks to resume a run.
The source changes require fresh backend and QMD History processes; a frontend
refresh alone does not activate them.

## Saved review and lazy monitoring (2026-09-14)

Saved Review now uses a read-only reader that projects checkpoint identity and
broker state, without restoring source readers, detector state, assignments or
the strategy engine. Resume retains the complete restart validation and causal
execution path. Legacy manifests are projected through SQLite JSON extraction
to avoid constructing their enormous unrelated Python object trees.

For stopped run `fdeff0d5-654e-41cc-91a9-e309c290af93`, cold HTTP Review after
backend restart measured 2.75 and 2.97 seconds, versus 37.13 seconds before this
change. The first financial Canvas request measured 21 ms and 11,970 JSON bytes.
These are presentation measurements, not engine-throughput benchmarks. The run
remains stopped at 08:00:47Z with 71,347 processed events.

Backtest views follow new data by default. Update view explicitly
advances a held view; Follow latest refreshes every five seconds and pauses
when the user interacts with a panel. Hidden panels suspend requests, chart
details are requested only when needed, and unused publication interests expire
after 15 seconds. Engine publication still uses a separate process and bounded
latest-only work. Setup-only results/comparison reads no longer run when opening
an existing monitoring workspace.

Strategy activity uses filtered 200-row server pages and a six-page client cache.
Each browse scope is fenced by both causal time and journal sequence; returning
to a cached page preserves evidence state. Exact evidence remains demand-loaded.
Run-linked charts cannot expand beyond the engine cursor, even when a caller
requests a full session or future cutoff. Explicit legacy Canvas/results reads
retain their activity page; lazy monitoring defers it.

Validation includes exact saved positions, orders, executions, closed trades,
portfolio and performance equality, unchanged journal bytes, a test that forbids
execution restoration during Review, checkpoint resume regression, and the
existing monitored/unmonitored round-trip equality checks. Browser tests use
intercepted advancing status to verify held selections, single evidence reads,
page caching, sequence fences, filtering and Follow interaction. The managed
final regression passed 15 focused backend tests and two browser tests (the
saved-run browser test scrolls the activity panel into view before expecting
its lazy content). The managed
production build and 12 light/dark, 0.8/1/1.25-scale, normal/compact captures pass
with zero automated objective issues. Evidence is under
`D:/TradingML/runtimes/ui-review/backtest-lazy-final` and `backtest-lazy`.
The existing compact header clips some older controls at maximum scale; the new
view controls remain usable. Full-session causal/P&L and throughput acceptance
remain open as described above.

## Resumed-run progress and checkpoint responsiveness

The user resumed `8c5022c3-2536-4fda-885f-367d7c2f4e3b` on September 14.
Preparation rebuilt all 2,610 V7 working-set tickers and retained about 8.54 GB.
At 11:32 PDT it advanced from 2,432 to 2,560 prepared tickers while its market
clock stayed fixed. The API reported running with runtime_ready=false, which
the old UI incorrectly treated as playback. Warmup finished around 11:33 PDT.
The following 300,000-event checkpoint measured 22.78 seconds for capture plus
20.32 seconds for persistence. Over the observed 54-second interval, the market
clock advanced only 26 seconds. The earlier 2.35x bounded playback measurement
therefore does not establish sustained resumed-run throughput.

Checkpoint capture/persistence now runs in a worker while the engine awaits the
same complete, durable checkpoint before advancing. Compact status uses a frozen
boundary plus current work phase and elapsed time; its async endpoint bypasses
the shared synchronous request pool. Mutation APIs reject concurrent changes;
pause/stop only set control flags during the checkpoint. Cancellation waits for
the worker to finish before releasing the boundary. Checkpoint contents and
durability are unchanged; this fixes reporting/request stalls, not the underlying
43-second checkpoint cost. Repeated full working-set preparation and incremental
checkpoint performance remain optimization work.

The UI now recognizes resumed preparation from runtime readiness, reports ticker
counts, shows checkpoint capture/save with elapsed time, and keeps polling through
finalization. Activity follows automatically until the user interacts; a visible
paused-updates message explains held rows. Activity-only layouts use their own
held clock rather than a hidden financial panel's old timestamp/sequence.

Validation: delayed capture/persistence keeps the compact endpoint responsive
within a 200 ms test deadline and the complete saved state equals the synchronous
baseline. Browser tests cover automatic follow, evidence inspection, paging,
checkpoint phase labels and 2,432/2,610 resumed-warmup progress. Managed build and
12 theme/scale/viewport reviews passed; screenshots are under
`D:/TradingML/runtimes/ui-review/backtest-work-progress` and `backtest-work-phases`.
The user run subsequently reached stopped with a complete checkpoint at
596,100 events (08:37:06.8Z). No other active backtest was resident, so the
backend was restarted through the managed lifecycle to activate these changes.
No stop or resume command was issued by this task.

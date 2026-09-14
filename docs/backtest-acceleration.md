# Backtest computation and monitoring

Backtest decisions, allocation, order matching, and journal writes retain their
original event order. Acceleration changes preparation and presentation, not the
strategy definition, thresholds, candle completion rules, or simulated costs.

## Causal boundaries

- Frame lookahead holds at most 64 immutable inputs. Only completed one-second
  frames at or before the current market-event cutoff are prepared.
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

The engine captures a boundary at most once per second, with forced lifecycle
updates. The packet contains the canonical broker projection, frozen assignment
state, simulated time, and the journal's committed sequence.

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

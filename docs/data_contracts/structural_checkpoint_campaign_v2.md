# Structural Checkpoint Campaign v2

> Superseded by [Structural Checkpoint Campaign v3](structural_checkpoint_campaign_v3.md).

## Purpose

Campaign v2 populates immutable Generic Structure algorithm v16 daily
checkpoints without changing the event-native calculation. It is a transport,
planning, concurrency, persistence, and restart contract; it is not a second
structural-level algorithm.

## Authorities

- Compact events come from the QMD History source plan and retain the canonical
  quote/trade fields and exact per-ticker ordering identity.
- Planning counts come only from
  `market_sip_compact.events_ordinal_continuity`. They are workload hints and
  never replace streamed events, event counts, or checkpoint cursors.
- Corporate actions come from `q_live.market_stock_split_v1` as known at the
  requested causal boundary.
- The calculation is `generic-structure-v16` from the shared Rust QMD library.
- Durable output is `q_live.qmd_structure_daily_checkpoint_v1`.

No OHLCV, bar, time-bucket, price-level, or trade aggregate may substitute for
the canonical compact-event stream. Quotes, trade conditions, exact ordering,
and split boundaries affect the resulting geometry and scores.

## Planning and bounded concurrency

The planner batches ticker estimates through the continuity-index endpoint
`/estimate/generic-structure-event-counts`. It must not scan yearly compact
event tables to estimate work. Tickers are grouped by their indexed total and
largest-session event counts. Missing index estimates do not remove a ticker;
the runtime calculation remains authoritative.

Each worker owns one ticker at a time and processes that ticker's checkpoint
dates sequentially. Different tickers may run concurrently. Concurrency is
bounded by the builder, QMD Live, QMD History, and ClickHouse query limits.
Increasing worker count does not authorize unbounded ClickHouse threads or
memory.

Sparse persisted checkpoint dates do not broaden a replay request. Advancement
between them is internally segmented at the shared historical runtime's maximum
window (72 hours by default), with the checkpoint carried across every segment.
Only planned daily boundaries are persisted. Rebuild and advancement event
limits come from their respective shared runtime authorities rather than one
incompatible campaign-level override.

The workstation entry point is the standalone Rust binary
`structure_checkpoint_campaign` in the QMD History crate. It connects directly
to the configured historical and writable ClickHouse authorities; QMD Live and
QMD History HTTP services do not need to run on the workstation. The binary
reuses their shared library modules rather than copying the algorithm.

The archive is monthly partitioned and ordered by `(ticker, ordinal)`. Ticker
workers therefore preserve primary-key pruning. Hash predicates or repeated
full-market timestamp scans are prohibited unless a future physical projection
makes those reads independently prunable.

## Resume compatibility

The latest checkpoint is only a candidate resume seed. Before advancing it,
the runtime recomputes the complete source revision from `authority_start`
through that checkpoint's session boundary and requires exact equality of:

- algorithm version and ticker identity;
- source-plan hash;
- source-revision token;
- complete/gap-free source status;
- exact nonzero event cursor; and
- point-in-time split authority included in the source-revision token.

When all fields match, processing begins with the first missing session. When
events or split terms were corrected, the seed is rejected and the ticker is
rebuilt from canonical authority. Corrected split terms must never be applied
on top of geometry produced with superseded split terms.

Historical transport cursors are reset only while reading a new archive
segment. If that segment produces no successor structural cursor, the prior
nonzero exact cursor remains the checkpoint identity; if a successor exists,
it replaces the prior cursor. Sparse or inactive symbols therefore retain a
valid unchanged book without manufacturing an event or losing provenance.

Existing checkpoints for different tickers may end on different dates. Resume
is consequently per ticker, not one global campaign date. A restart may safely
resubmit units: compatible completed units return `already_current`, while only
missing or invalidated units are calculated.

## Persistence and progress

A checkpoint is persisted only after the requested source window is complete
and its revision is unchanged across calculation. Identity includes ticker,
session date, algorithm version, source-plan hash, source-revision token, exact
cursor, authority start, and payload.

The standalone builder writes its progress report atomically under the runtime
root. Report schema v3 exposes queued, active, completed, already-current,
unavailable, failed, and dependency-blocked units, processed event totals,
recent completions, exact active ticker/session assignments, and failures. The
interactive terminal renders a stable one-second dashboard from this same
state. It clears once at startup, then updates fixed rows in place without
full-screen refresh flicker and restores the cursor on every exit path.
Redirected output emits a plain snapshot every 15 seconds without ANSI control
sequences. The report is operational evidence, not checkpoint
authority; ClickHouse compatibility checks remain authoritative after a missing
or stale report.

## Validation gate

Before raising production concurrency or launching a full-universe campaign:

1. Compare a bounded panel against the single-ticker daily authority.
2. Require exact checkpoint JSON, cursor, split lineage, source identity, level
   geometry, scores, probabilities, footprints, and timeframe state.
3. Interrupt and resume the panel and require identical output.
4. Measure ClickHouse rows/bytes, query memory, worker RSS, events per second,
   checkpoint rate, and retry rate.
5. Increase concurrency only while throughput improves within the explicit
   workstation resource budget.

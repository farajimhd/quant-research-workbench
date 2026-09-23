# Data lifecycle and persistence

## Authority

Use a new configured ClickHouse database. Proposed name: `arte`.
This is a design name. No database is created or renamed by the documentation change.
Production components do not query the old app's event tables, checkpoints, or caches.
They do not call `download_update_events` or read flatfiles.
Historical data comes from REST into the new authority.

Database roles separate ingestion, live journals, backtest output, and read-only UI.
The new roles have no write permission on legacy databases.
Operational tables use explicit `live_market_ssd` storage. Verify table policy and
actual part placement before writers start. An unavailable policy blocks startup.

## Historical acquisition

1. Resolve point-in-time instrument identity and requested session ranges.
2. Fetch trades and quotes through bounded REST workers.
3. Follow provider pagination to completion, including equal-timestamp boundaries.
4. Normalize and insert idempotently. Keep acquisition progress outside source code.
5. Validate identity, counts, ordering, schema capabilities, and conflicts.
6. Publish interval coverage only after verification.
7. Build the required bars and historical V7 seed.
8. Publish the complete dependency-readiness manifest.

Retries resume the failed stage. Derived failure does not require reacquiring an
already certified source interval. Certification reports exactly which checks ran.
It does not claim absolute provider completeness without corresponding evidence.

Coverage is keyed by instrument, channel, interval, source revision and contract.
Track pending, fetching, verifying, certified, empty-certified, and failed states.
A failed query is not an empty interval. A date maximum is not coverage proof.

## Gap repair

The maintenance process owns repair. The live process supplies suspected intervals.
Use bounded overlap around the last durable cursor and the live handover boundary.
Reconcile identities and corrections. Do not infer loss from non-contiguous sequences.

Repair source data, bars, indicator state, detector state, and V7 dependencies.
Use the preceding certified state and replay forward. Buffer live arrivals during
handover. Deduplicate overlap and preserve the original live receipt metadata.

Catch-up updates state without placing expired historical orders. Enable new exposure
only after source, derived state, broker state, and freshness gates pass.
Missing official LULD or another non-repairable input blocks its dependent feature.
Trade/quote REST coverage does not imply historical LULD coverage.

## Session close

Full historical V7 computation starts after the configured complete source session.
The session calendar defines this boundary, including holidays and early closes.
Do not hardcode regular close as the end of extended trading.

Reconcile the completed session through REST. Then finalize historical V7 and publish
the next-session seed. A later correction creates a new generation. Do not hot-swap
an active session's seed. Keep overnight resident memory, but verify its seed identity
before using it in the next session.

## Persistence and retention

Backtest preparation reads pinned bars, indicators, historical level books,
scanner and Watchlist products from this database. Maintenance builds missing
required products from certified REST events, validates them, and publishes a
dependency-readiness manifest before the run starts. It builds only requested
products and retains short rolling bar history plus any generation pinned by a
run or recovery checkpoint. Bar-based historical level construction and
streaming live level updates share tested semantics but have separate source
and availability contracts.

Fixed-cadence Boolean products such as signal streams and Watchlists use sparse
known/value transitions under a published complete coverage record. That record
pins the source bar generation, computation contract, exact transition count,
and streaming digest. Readback expands the changes to the complete 100 ms grid.
An absent transition means carry forward only after the digest and coverage
are verified. Unknown remains distinct from false. Event-cadence products need
a separate event-evaluation record; they cannot use this sparse inference.
The shared event-cadence Boolean builder now records an evaluation digest for
every trade or quote boundary, including unchanged values, while retaining
only state transitions in memory. It seals only against an independently
supplied expected boundary count and source digest. The caller must derive
that expectation from a verified event source or causal playback ledger;
the builder's own observations are not independent coverage evidence.
The source digest includes the scheduler's trade-eligibility result, so a
different eligibility policy cannot reuse a source certificate silently.
ARTE now has a separate ClickHouse schema for sparse event transitions and an
immutable product header. The publisher checks the explicit SSD policy and
actual part placement, compares retry rows, verifies all transition rows,
then publishes the header. Readback requires the pinned product and source
authority hashes. The schema has not been applied, connected readback has not
been tested, and the external source authority still must certify the ledger.
For historical and recorded-live playback, a separate source ledger now
observes every shared scheduler boundary. It certifies only after the playback
is complete, its admitted-event count matches, its prepared source is pinned
by the run manifest and source catalogue, and the full interval is covered.
The event-Boolean producer must seal against this proof. Publication requires
the proof type and rejects a different scope, definition, event count, digest,
authority hash, or certification time. This is conditional on the catalogue's
underlying source authority being independently certified; it does not prove
provider completeness or live feed health. Live-stream certification remains
unimplemented.
A run-level producer remains unfinished.

ClickHouse is the sole durable store for backtest spools, run evidence, and
operational logs. Use compact typed records and measured compression. SQLite
files are not an allowed fallback, temporary spool, or recovery authority.

| Product | Policy |
|---|---|
| Compact events | Retain configured historical coverage and pinned source generations |
| Live receipt metadata | Retain the recorded-live audit window and pinned incident runs |
| Required completed bars | Short rolling backtest/continuation window; pin active runs |
| Indicators | Persist continuation state and decision evidence; avoid all-value histories |
| Fixed-cadence Boolean products | Sparse changes and complete pinned coverage; retain products required by active runs |
| Historical V7 levels | Changed versions and required fit inputs; retain referenced seeds |
| Streaming V7 | Separate audit/recovery retention; never a daily historical seed |
| Trading journals | Durable run, decision, command, fill and reconciliation evidence |
| Prepared arrays | Regenerable, bounded caches; no sole authoritative copy |

Retention durations are mandatory profile settings, not hidden defaults.
Expiry must respect checkpoint dependencies, active runs, and audit pins.
Delete nothing required to reproduce an accepted run or resume a retained seed.

## Nonblocking market persistence

Use bounded batches and queues. Track accepted, durable, pending and failed counts.
On sustained failure, disarm exposure increases before the buffer is exhausted.
Never silently drop accepted events. Record an unresolved range if intake must stop.

RAM buffering cannot guarantee receipt durability through a power failure. REST can
repair market payloads but cannot recover original local receive timestamps. Such a
range must be marked incomplete for recorded-live validation.

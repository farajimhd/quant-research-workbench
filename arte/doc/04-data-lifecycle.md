# Data lifecycle and persistence

## Authority

Use the existing configured ClickHouse `arte` database for ARTE persistence.
At the original design baseline its name was only proposed; the user now
reports that the database exists. Documentation does not certify its current
tables, coverage, schema compatibility, or part placement.

Certified `market_sip_compact.events_YYYY` may supply read-only historical
trades and quotes. ARTE must not call the parent app or its Python
`download_update_events` at runtime. The ARTE distribution must implement
the required flatfile digestion semantics in Rust and ClickHouse. The
importer's destination remains an open design decision; no existing yearly
table write is authorized by this document. REST supplies recent missing
coverage and gap repair. WebSocket supplies Live.

The old prohibition on reading legacy events is superseded. The prohibition
on runtime parent services, direct flatfile reads by consumers, and unapproved
legacy writes remains. Backtest, strategy, V7, bars, charts, and repair
consumers read only pinned certified ClickHouse products. Only the ingestion
authority may open raw flatfiles while acquiring and verifying a source day.

Database roles separate ingestion, live journals, backtest output, and read-only UI.
ARTE consumer roles have no write permission on legacy databases. A separate
ingestion-role proposal is required if the importer is ever approved to append
to yearly tables.
Operational tables use explicit `live_market_ssd` storage. Verify table policy and
actual part placement before writers start. An unavailable policy blocks startup.

## Historical acquisition

### Select a certified source

1. Resolve point-in-time instrument identity, session, channels, and required
   fields from the run's dependency plan.
2. Inspect certified yearly compact coverage and the delayed-trade reporting
   revision. Reuse compatible rows read-only; an uncertified day or unknown
   required capability is not ready.
3. For missing or outage-affected source days, schedule ARTE's Rust/ClickHouse
   flatfile digestion when a complete source file is available. The imported
   generation, destination, identity mapping, and publication time must be
   pinned. The write target must be approved before implementation.
4. Use bounded REST workers for recent missing intervals and same-day repair.
   Follow pagination to completion, including equal-timestamp boundaries.
5. Reconcile overlapping sources by event identity, payload revision, and
   knowledge time. Preserve WebSocket receipt evidence separately.
6. Normalize and insert idempotently where insertion is required. Keep
   acquisition progress outside source code. Validate identity, counts,
   ordering, schema capabilities, and conflicts.
7. Publish interval coverage only after verification. Build or select required
   bars, indicators, Boolean products, and historical V7 checkpoints.
8. Publish the complete dependency-readiness manifest.

The original REST-only eight-stage plan is retained in steps 1, 4–8 for its
bounded pagination, validation, and publication requirements. REST is no longer
the sole historical source.

### Flatfile importer decision still required

ARTE will reimplement the required `download_update_events` semantics in Rust
and ClickHouse. The parser, normalized columns, condition/reporting rules,
source certificates, event identity, deterministic replay order, and
restart-safe publication must be pinned independently of the Python source.
Transform and validate bulk rows in bounded ClickHouse/vectorized stages where
appropriate; Rust owns orchestration, provenance, and exact readback checks.

| Possible destination | Benefit | Required evidence before approval |
|---|---|---|
| ARTE-owned source tables | No writes to expensive existing yearly tables; new source key need not use a dense ordinal | Avoid overlap duplication, reconcile archive/ARTE/REST generations, prove Backtest and derived-builder reads |
| Append-only yearly compact tables | One existing historical archive and direct reuse of compatible market-day/V7 builders | Port exact ordinal allocation and compact codec, coordinate the sole writer, preserve existing rows/schema, prove restart-safe append and source-day certification |

Neither destination is approved yet. Do not create a writer or expand database
permissions until the user chooses after reviewing the detailed contract.
Existing yearly compact reads remain read-only in the meantime.

Retries resume the failed stage. Derived failure does not require reacquiring an
already certified source interval. Certification reports exactly which checks ran.
It does not claim absolute provider completeness without corresponding evidence.

Coverage is keyed by instrument, channel, interval, source revision and contract.
It also pins source type, certified generation, delayed-trade capability, and
the exact derived-product attempt. A source day's ingestion status is distinct
from a strategy's readiness for that day.
Track pending, fetching, verifying, certified, empty-certified, and failed states.
A failed query is not an empty interval. A date maximum is not coverage proof.

## Gap repair

The maintenance process owns repair. The live process supplies suspected intervals.
Use bounded overlap around the last durable cursor and the live handover boundary.
Reconcile identities and corrections. Do not infer loss from non-contiguous sequences.
Repair may use REST immediately and later reconcile a complete flatfile or
certified compact archive day. A stopped Live process does not make the day
unrecoverable for historical analysis. It does make original local receive
timestamps unavailable for the outage interval; historical source recovery
cannot certify recorded-live latency or reproduce missed live decisions.

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

Reconcile the completed session through the available certified source path:
REST before a complete flatfile arrives, then a flatfile/compact-source
successor when independently certified. The source cutoff and any provisional
versus final status must be explicit. Finalize historical V7 only against a
compatible complete-session certificate, then publish the next-session seed.
A later correction creates a new generation. Do not hot-swap
an active session's seed. Keep overnight resident memory, but verify its seed identity
before using it in the next session.

## Persistence and retention

Backtest preparation reads pinned bars, indicators, historical level books,
scanner and Watchlist products from this database. Maintenance builds missing
required products from certified historical events, validates them, and publishes a
dependency-readiness manifest before the run starts. It builds only requested
products and retains short rolling bar history plus any generation pinned by a
run or recovery checkpoint. Bar-based historical level construction and
streaming live level updates share tested semantics but have separate source
and availability contracts.

The existing `arte.market_day_bars_v1` and `arte.market_day_technical_v1`
products are a candidate fast Backtest authority. Their current builder reads
certified compact events and other dated reference inputs. ARTE must pin each
completed build, unit attempt, source/reporting revision, seed mode, and
calculation hash; verify coverage and `live_market_ssd` placement; and either
package the required builder/reference responsibilities or publish compatible
ARTE-owned successors. A current `arte` table does not by itself remove its
parent-code or other-database dependencies. No per-bar ClickHouse query belongs
in the Backtest hot loop.

The existing `arte.structural_levels_v7`,
`arte.structural_level_coverage_v7`, and
`arte.structural_level_builder_checkpoint_v7` contracts must be maintained.
The coverage fence and terminal builder checkpoint are distinct. The historical
levels are retrospective and become seed inputs only after their recorded
session-end availability. ARTE must check source/condition/split/algorithm
compatibility before advancing or loading a checkpoint. Streaming state does
not replace these historical rows.

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
The historical precompute runner and event-Boolean publisher can now be
composed in one pure preparation call. It returns the product ready for the
header-last ClickHouse publisher; it does not publish or start a service.
That composition compiled but has not been exercised against ClickHouse.

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

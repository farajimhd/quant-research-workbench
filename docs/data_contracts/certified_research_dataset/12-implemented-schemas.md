# Implemented compact schema foundations

[Overview](../certified_research_dataset_design.md)

**Implementation status: in progress.** These exact schemas and the strict market calculation path have bounded ClickHouse fixture coverage. Historical identity/reference producers, final publication and the full campaign controller are not yet complete.

## Current scope

The user narrowed delivery to ClickHouse common data only: no training banks/shards, labels, optimizer exports or downstream tasks. Admission uses 2026-onward US stock/conid identities with dated listing eligibility and timely removals. Earlier events are context. Temporary acquisition files and operational receipts remain ingestion/controller responsibilities.

## Inspect the schemas

Run `python -B scripts/prepare_certified_research.py schema --format sql` for exact DDL, or `--format json` for the column catalog. Every published column has an explicit lossless codec; tables use monthly/yearly or compact dictionary partitions and `live_market_ssd`.

## Relationship map

- Dataset metadata is stored once in `research_dataset_v1`. Market rows reference its UUID.
- `research_identity_v1` maps typed stable numeric keys to original canonical IDs. Assignment and collision checks are required producer responsibilities.
- Membership maps listing, security and issuer keys to dated ticker/conid eligibility.
- Facts reference entity/metric/unit keys and a provenance hash.
- Market products and checkpoint state use one listing key and immutable attempt IDs.
- Receipts certify specific attempts; physical rows alone do not publish authority.

## Exact columns

- [Identity, membership and provenance schemas](12a-schema-identity.md)
- [Market data schemas](12b-schema-market.md)
- [Facts, checkpoint and receipt schemas](12c-schema-facts-state.md)

## Validation performed

Live isolated SSD fixture tests covered DDL, exact bar round-trip, actual part placement, strict rejection counters, all eight bar frames and indicator projection. Fixtures were removed after testing. These are bounded correctness checks, not full-market throughput certification.

The next implementation slice adds attempt-scoped readback/publication, explicit empty proof, stable normalized identities, half-open membership and public-as-of fact lookup. Live tests cover receipt resume, duplicate rejection, listing/delisting boundaries, original filing values versus later revisions, and a bounded AAPL August 21 training-minute comparison against canonical events. Keeper session-loss handling has deterministic tests; live Keeper integration and full-range performance qualification remain pending.

Acquisition adapters now delegate to the real updater/reporting entry points with explicit environment selection and D-SSD paths. Source planning checks importer status, reporting coverage, frozen source hashes and the mutation barrier. Parameter-derived context distinguishes compatible predecessor state from an explicitly declared rebuild prefix. The shared V7 producer emits PM/regular/AH binary checkpoints; live fixtures verify their header/payload chain and publication readback. The full campaign controller, historical reference resolution and final dataset seal are still unfinished.

## Remaining implementation

Wire source acquisition/migration receipts; build dated normalized identities and membership; certify public-as-of SEC/short/float values; publish compatible causal checkpoints and dependency receipts; add complete run/resume/verify paths and measured representative performance. The launcher currently exposes schema inspection and read-only preflight/plan, and explicitly reports campaign execution disabled.

## Runnable producer slices

### Market and SEC facts

`producer.run_market` aggregates strict events once, publishes normalized bars and liquidity, then runs indicators and causal session checkpoints as dependent consumers. Every product has independent readback evidence. Verified resume performs no new inserts. The controller must still provide certified predecessor states, source guards and ownership; this is not yet a complete campaign launch path.

`reference_producer.run_sec` scans selected issuer fundamentals into compressed ClickHouse staging once, then publishes normalized facts and deduplicated provenance. Retained `sec_core` context supplies duration start/end dates; unmatched canonical records retain an explicit unknown-context marker. Original acceptance time is preferred; authoritative filing dates use a conservative following New York day. Conflicting clocks or values produce null conflict facts. Native currencies have distinct unit keys, without implied FX conversion.

Fact attempts become readable only through verified reference receipts. As-of lookup returns latest records per output unit, basis unit and namespace; callers can select these axes explicitly. It orders by period end and original public availability, and prefers a known context and the longest reported duration when these tie. Raw facts preserve all durations and currencies. Derived calculations select compatible periods and units explicitly rather than using the generic tie preference as a formula definition.

The [19-formula producer](06a-derived-fundamentals.md) now uses certified reported parents and runs its period/vintage selection inside ClickHouse. Fiscal-year/period and normalized currency/framework basis fields are persisted for this purpose; a small taxonomy dictionary completes their decoding. It emits change records, preserves original availability and withholds incomplete debt or unknown period contexts. Independent numeric fixtures and a real AAPL SEC→derived chain pass; the latter completed in about eight seconds in the bounded test.

Bounded live tests cover these producer paths and exact resume behavior. Full-universe throughput, SEC source-gap admission, float/short producers, historical membership and the final dataset seal remain to be implemented and qualified.

### Short volume and interest

The short-data producer now has a runnable ClickHouse-only slice: CNMS short/exempt/non-exempt/total volume and matching daily ratio; reported short interest, provider ADV and days to cover. It binds provider tickers to dated dataset membership and checks source hashes, original public clocks, count consistency and exact output readback. Unknown clocks block publication before inserts. Interest records with an authoritative publication date use the following New York day as a conservative bound. Measurement dates and ingestion clocks never substitute for release evidence. Certified receipts support resume without inserts. Reported zero volume remains valid; a zero denominator makes the ratio unavailable. Counts exceeding exact Float64 integer representation are rejected, with original values retained in provenance.

**Current data prerequisite (October 12 audit):** January 1–October 2, 2026 contains 3,143,402 short-volume rows with no public timestamps and 142,612 short-interest rows with neither public timestamps nor publication dates. `plan` now exposes these missing-clock counts. Existing values require source-version timing repair; calendar dates alone do not prove which value revision was public. Rolling short windows and qualified float ratios remain unfinished, as do the full controller and final seal.

### Historical universe

The historical universe reader now builds the 2026-onward conid anchor pool from current listing records and retained Gateway universe observations. It resolves the requested context through canonical ticker intervals, listing/delisting dates, completed ticker-history coverage, issuer rules and mapping issues. Known non-US/non-stock/OTC entries are counted outside scope; missing evidence is quarantined. It checks a frozen source fingerprint across bounded metadata shards. Provenance bodies remain server-side.

A live read for January 2025–October 2, 2026 with October 12 conid anchors resolved 4,711 intervals; unresolved inputs included 2,509 missing dated symbol mappings, 61 missing listing starts, seven historical identity mismatches, six open mapping issues and 19 conflicting conid anchors. This is an input-resolution audit, not a published membership certificate. Repair orchestration and membership publication still need wiring into the campaign controller.

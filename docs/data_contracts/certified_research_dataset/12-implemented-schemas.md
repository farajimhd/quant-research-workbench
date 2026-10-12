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

## Remaining implementation

Wire source acquisition/migration receipts; build dated normalized identities and membership; certify public-as-of SEC/short/float values; publish compatible causal checkpoints and dependency receipts; add complete run/resume/verify paths and measured representative performance. The launcher currently exposes schema inspection and read-only preflight/plan, and explicitly reports campaign execution disabled.

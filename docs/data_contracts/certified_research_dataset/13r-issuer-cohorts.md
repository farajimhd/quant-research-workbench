# Bounded issuer cohorts

[Launcher](13j-launcher-execution.md) · [Completion audit](13i-completion-audit.md)

## Delivered scoping path

`--issuer-keys` freezes an explicit sorted cohort of at most 256 normalized
issuer UInt64 keys. Preparation and publication retain all its securities,
share classes and historical listing intervals. The config and universe unit
persist the selection; stored resume cannot replace it with current CLI defaults.
Issuer selection changes the dataset contract, not the calculation recipe.
Existing unscoped config identities remain unchanged.

Selection occurs only after the **full** historical universe qualifies. It
cannot hide an unrelated rejected listing. Each cohort's universe fingerprint
binds the full source fingerprint and its issuer keys; changes anywhere in that
frozen evidence boundary invalidate reuse. No table or new data codec is needed:
these are bounded fields in the existing compressed immutable metadata.

## Complete partition census

`plan-cohorts --context-start-date YYYY-MM-DD --end-date YYYY-MM-DD` is read-only.
It partitions all admitted target issuers using configurable
`--cohort-max-issuers` (default 128) and `--cohort-max-sessions` (default 200,000).
The latter remains below the existing 250,000-session preparation bound.

The census intersects intervals with full 04:00–20:00 New York sessions using
binary search. It holds interval/day metadata, not the full listing-day graph.
Partial-day membership, competing security/issuer ownership, overlapping market
listings, or a single issuer exceeding the budget block the partition. Issuer
history is never silently split or truncated. The partition hash binds the
full source, config, actual context-day hash, counts, limits and complete groups.

The declared context is not proof that every required seed, float-price date,
source or feature is available. Normal preparation still derives and validates
those dependencies. Expanded context or graph/metadata overflow remains a blocker.

## Validation and remaining execution work

Unit tests compare the interval census with execution membership and partition
600,000 listing-days across 3,000 issuers without calling whole-universe session
materialization. A real ClickHouse fixture publishes both share classes of one
selected issuer, excludes the unrelated issuer, and reloads the frozen cohort
through the actual stored-plan path.

A controlled census measurement counted those 600,000 listing-days into 24
cohorts in 0.129 seconds with 1.87 MiB incremental traced Python memory. Fixture
interval allocation was outside that measurement. This measures metadata census
work, not production market computation, storage rates or end-to-end throughput.

`collection.Controller` now supplies the bounded orchestration core: deterministic
child dataset IDs bind the collection UUID, partition hash and member index;
frozen member plan hashes and issuer sets are checked on every load. It requires
aggregate capacity admission before any member writer, executes cohorts
sequentially (each cohort retains its existing concurrent unit execution), and
independently re-verifies every member before publishing a collection root.
Interrupted or blocked members cannot produce a whole-collection certificate.
Resume delegates immutable-plan reuse to existing child controllers and repeats
final authority verification; progress counts are never certification evidence.

Five controller tests cover successful final re-verification, interrupted/blocked
execution, changed member identities, failed authority roots, definition tampering
and capacity rejection before writers. This core has not been exercised as a
real whole-collection ClickHouse run. Its injected adapters still need durable
ClickHouse definition/seal persistence, aggregate capacity measurement and CLI
wiring. These remain required before claiming the full January-to-end universe
can run as one launch.
Until then, `plan-cohorts` explicitly reports `execution_ready=false`.

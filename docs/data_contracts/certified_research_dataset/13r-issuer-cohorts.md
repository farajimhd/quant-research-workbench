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

This is a complete cohort **scoping/planning** component, not yet the complete
collection controller. Automatic sequential cohort execution, aggregate capacity
admission, durable member plans and a final whole-collection certificate remain
required before claiming the full January-to-end universe can run as one launch.
Until then, `plan-cohorts` explicitly reports `execution_ready=false`.

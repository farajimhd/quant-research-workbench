# Reference source discovery

[First-launch preparation](13l-initial-preparation.md) · [Reference handoffs](13d-reference-handoffs.md)

`reference_discovery.discover` prepares retained ClickHouse reference inputs for
the admitted target securities. `initial_preparation.discover_and_prepare`
connects this discovery directly to context preparation and graph assembly;
callers no longer need to construct issuer/security source locators themselves.

| Input | Discovery and admission |
|---|---|
| Dated universe | Qualified Gateway intervals covering short history and target dates |
| Issuer identity | Batched CIK lookup; missing, invalid, competing or shared CIKs block |
| SEC | Complete acquisition coverage, private worker fingerprint per issuer |
| Short volume/interest | Completed coverage and original-public-time source fingerprints |
| Corporate actions/allocation | Batched current-dataset receipt discovery and independent output verification |
| Reported supply | Include an independently verified receipt when available |

Corporate and allocation authorities are mandatory. Missing scopes are returned
as explicit preparation requirements before expensive source workers start;
no empty authority is manufactured. Ambiguous receipt candidates require an
explicit scope selection rather than a choice by insertion time. Qualified
donors from another dataset produce separate import units and dependent family
handoffs. Creating authorities from missing original evidence remains unfinished.

## Removing the membership publication cycle

Fresh short-data discovery cannot depend on membership that will be published
only after the plan is assembled. `short_sql.snapshot_memberships` compiles the
same qualified dated intervals into bounded ClickHouse SQL. Metadata is
validated and grouped once for the security worker pool. Source observations
remain in ClickHouse, with the same half-open measurement-time identity match,
publication-clock rejection and source fingerprint as the published path.

A real isolated ClickHouse test compares both paths and proves identical
counts and fingerprints. It also excludes observations measured after a
delisting. The initial coordinator now extends **identity coverage** to the
short lookback, without demanding unnecessary market-event acquisition for
those extra short-history dates.

## Execution boundary

Discovery uses one bounded pool with private clients and the launch's worker
and query-memory reservations. It reports prepared metadata separately from
certified persisted products. It performs no provider request or publication.
Storage placement, coverage, identity and clock failures block the result.
Runtime handlers independently recheck the frozen source fingerprints after
membership publication, before downstream reference facts can certify.

The `launch` CLI now connects discovery, preparation and admitted execution.
It independently resolves adjustment hashes for prior V7 candidates. Creation
and acquisition of missing reference authorities, initial-state qualification
and measured production capacity remain unfinished. Existing source discovery
does not make the currently incomplete January–October inputs certifiable.

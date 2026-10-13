# First-launch preparation

[Controller](13-campaign-controller.md) · [Launcher](13j-launcher-execution.md)

`pipelines/certified_research/initial_preparation.prepare` connects read-only
preparation to `initial_plan.assemble`. It accepts acquired canonical/reporting
proofs, frozen calculation rules, issuer/security source locators and compatible
state selections. It returns one graph for market and reference execution;
it does not publish data, install metadata or certify a dataset.

| Phase | Required input | Prepared result |
|---|---|---|
| Admission | Complete SEC acquisition ranges, source/reporting proofs, selected short lookback | Reject incomplete prerequisites before preparation workers |
| Float-price context | Frozen issuer SEC scopes and hashes | Measurement dates and their required prior exchange sessions |
| Source context | Initial source days plus newly discovered price dates | Verified combined canonical/reporting epoch |
| Historical membership | Gateway identity/lifecycle evidence over the expanded range | Dated security/listing/issuer ownership |
| State preparation | Required source hashes, adjustments, compatible predecessor locators | Seed units and intervening calculation sessions |
| Price-only history | Earlier listings of target securities needed for float prices | Shared bar aggregates, without unrelated state genesis |
| Assembly | All prepared lanes and supplied reference prerequisites | Source→bars→indicator/V7/activity/price→fundamental/float/short dependency graph |

Float contexts use one bounded worker pool and private clients. Only compact
measurement-date metadata leaves ClickHouse; no events, bars or fundamental
datasets are exported. Preparation uses the campaign worker/memory reservations.
Clients close on success, failure and interruption. Progress distinguishes
prepared metadata from certified persisted products.

The final market date caps SEC float **measurement dates** separately from the
UTC availability range. This matters in winter, when the last AH boundary
extends into the next UTC date. Availability evidence remains fully included
while price context cannot extend beyond the final market session.

Initial and expanded canonical/reporting epochs are rechecked, as is the
Gateway source fingerprint after preparation. Missing or ambiguous membership,
unsupported rename transitions, incompatible state, omitted prior short
sessions, metadata overflow and changed source evidence block the returned
plan. Price demands before a proven listing retain explicit unavailable entries;
no price is invented for those dates.

## Remaining launch boundary

The lower-level coordinator accepts supplied reference source locators and
fundamental coverage bounds. [Retained reference discovery](13m-reference-discovery.md)
now supplies those locators through `discover_and_prepare`. Missing reference
authority creation/acquisition, initial
genesis/legacy checkpoint qualification and production capacity measurements
remain unfinished. `launch` now connects this preparation to first execution;
existing `run/resume` execute installed immutable plans. Missing prerequisites
block launch before a campaign definition or seal is published.

Focused tests exercise graph assembly, earlier price-only listings, source and
identity drift, missing seeds, short context and interruption. A real read-only
ClickHouse check rejects the currently uncovered October SEC range before any
worker starts. Existing bounded SEC context tests also pass. No full campaign or
provider download was run.

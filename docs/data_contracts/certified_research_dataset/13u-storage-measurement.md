# Certified persisted-storage measurement

[Capacity design](10a-capacity.md) · [Completion audit](13i-completion-audit.md)

`measure-storage --dataset-id UUID --database ISOLATED_PILOT_DATABASE` uses the
existing independent dataset verifier before and after a bounded physical-part
snapshot. `--measurement-tables` selects a comma-separated subset of normalized
tables; the default is common bars, indicators/state, liquidity, V7, activity
and facts. It writes no observations or training/optimizer artifacts.

The measurement requires that every active stored row in each selected table
belongs to this pilot dataset. Mixed datasets cannot be attributed by multiplying
whole-table compression rates by a row fraction. Logical raw row counts must
match physical part counts; changing parts or authority roots reject the sample.
At most 16 tables and 4,096 active parts are inspected. Actual disk/policy checks
remain mandatory. Historical observation rows are not fetched by the sampler;
the authority verifier still performs its normal bounded evidence checks.

Inspection, verification and measurement clients now enforce `readonly=1`
through HTTP query settings. They preserve existing SQL settings rather than
appending a second SQL `SETTINGS` clause. Write clients retain their previous
default behavior. A real ClickHouse check accepted a read with `SETTINGS final=0`
and rejected table creation; client configuration and launcher tests also pass.

The result binds dataset/root, version, table row/part counts, actual disk,
compressed/uncompressed bytes, part evidence hash and result evidence hash.
`active_parts_only=true` excludes inactive cleanup/staging/merge overhead from
this persisted snapshot. Retained attempts belonging to the pilot can still
occupy physical bytes. `projection_ready=false` is mandatory: representative
population, comparable product-unit counts, recipe compatibility, staging peaks
and merge peaks must still qualify before producing an executable pilot profile.
Shared fact-table bytes cannot be assigned to individual feature families
without a separately valid measurement design.

Five unit/launcher tests and a real ClickHouse fact-publication fixture passed.
The real fixture independently verified the campaign, measured two persisted
fact rows with positive disk bytes, and retained projection-not-ready status.
These are measurement-path checks, not representative production pilot rates.

## October 13 physical baseline

A live read-only `system.parts`/disk snapshot returned:

| Existing table | Active disk bytes, decimal GB | Compressed bytes/physical row |
|---|---:|---:|
| `arte.bars_v1` | 167.54 | 26.64 |
| `arte.indicators_v1` | 247.13 | 118.49 |
| `arte.liquidity_100ms_v1` | 241.48 | 52.82 |
| `market_sip_compact.events_2026` | 950.00 | 7.89 |

The three legacy market tables occupy about 656.15 GB. The 2026 canonical event
table contains 120,294,226,954 physical rows. Workload disk free/unreserved space
was approximately 5,003.71 GB for `live_market_ssd` and 1,502.38 GB for
`sip_raw_ssd`. These are disk-policy observations, not proof of their physical
volume grouping; grouping remains an explicit admission input.

This baseline covers each table's current physical contents, including retained
attempts/coverage outside any selected target campaign. It is not a January-to-
end estimate for the new schemas. Do not add a proportional projection from
these old tables to authorize new writers. Capacity admission refreshes current
headroom and requires compatible qualified product rates plus explicit reserves.

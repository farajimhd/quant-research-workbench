# Certified persisted-storage measurement

## Read-only whole-plan estimate

`estimate-storage --dataset-id UUID` now reads an installed immutable plan and
reports its storage demand without invoking execution or acquiring sources.
Add `--collection` for an installed collection; retained child outputs and cohort
transient allowances use the existing conservative collection admission model.

With `--capacity-profile PATH`, the report uses the same schema/recipe-compatible
qualified rates, reserves and physical-volume grouping as execution admission.
Without a profile, it returns `measurement_required`, product demand counts,
current physical inventory and `required_bytes=null`. It does not infer a new
schema's rate from old-table bytes or qualify sampled peaks automatically.

The server connection is readonly. Plan/collection definitions are rechecked
after measurement. This is a capacity report, not source or dataset certification;
no producers, downloads, repair, Keeper writer lease or data exports are started.
A bounded real stored-plan check returned the missing-measurement state correctly.

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

## Transient observation component

`transient_snapshot()` reads bounded declared staging/published table scopes
from the installation catalog. It reports staging active/inactive bytes,
inactive output bytes and current merge input/written-uncompressed counters.
Policy and actual active-part checks still apply. Unknown/duplicate scopes are
rejected. It fetches metadata only and creates no staging or output rows.

These are instantaneous observations, not maximum reservations or compressed
merge output measurements. `upper_bound_proven=false` prevents confusing an
empty/quiet sample with proof that a campaign needs zero transient headroom.
Continuous/phase-bound capture, representative activity and qualified peak-bound
conversion remain incomplete. Twelve focused capacity tests and the real
ClickHouse measurement fixture passed, including the actual merge-counter query.

`PhaseObservations` now provides bounded, thread-safe phase capture. It hashes
and retains snapshots without dropping samples; exceeding the configured bound
fails explicitly. `producer.run_market(..., storage_observer=observer.record)`
can capture before-stage, after-staging and after-publication observations in a
controlled pilot. Default production behavior is unchanged when no observer is
selected. Cleanup capture is a supported explicit phase, not automatically
instrumented throughout every reference/campaign path.

Transient v2 also checks inactive-part disk placement; inactive bytes on a backup
disk cannot be treated as SSD overhead. Fourteen focused measurement/execution
tests and the real shared-market DAG fixture passed. The real fixture observed
positive staging bytes and retained its normal shared-scan/publication/resume
checks. Automatic pilot CLI capture, all-path cleanup/reference instrumentation,
representative runs and qualified peak bounds remain incomplete.

The same optional observer is now accepted by `MarketHandlers`,
`ExecutionLaunch`/stored restoration and `saved_execution.run`. Thus controlled
pilots can use campaign-global shared aggregation rather than a separate market
implementation. Observation remains launcher-owned Python, never serialized
executable plan code. Reference/cleanup instrumentation and automatic pilot CLI
configuration remain outstanding; default runs do not collect samples.

## Bounded stored-pilot CLI capture

`run`/`resume --observe-storage` now enables market phase capture on an already
frozen plan of at most 64 units. Source, owner and capacity admission gates
remain mandatory. Before execution and at every sample, all selected
staging/dataset-bearing published tables must be empty or belong only to this
dataset. Mixed pilot tables fail closed. This is an opt-in measurement mode,
not permission to start a full campaign or expand the plan.

The final CLI summary reports sample count, observed maxima and an evidence
hash, with `upper_bound_proven=false`. It does not generate a capacity profile
or export historical data. The collector rejects negative, boolean, fractional
or missing counters even when their hashes are valid; returned/producer-owned
snapshots cannot mutate captured history. Forty focused tests cover collector,
isolation, CLI capture/cleanup and saved execution. Full reference/cleanup/merge
coverage and representative peak qualification remain outstanding.

Capture failures are sticky: partial history stays available for diagnosis but
`capture_status=failed` prevents presenting it as an uninterrupted observation
run. A reused execution that emits no new samples reports `no_samples`, not a
zero peak. CLI summaries include execution status separately from capture status;
neither a successful execution nor a sampled maximum qualifies peak bounds.

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

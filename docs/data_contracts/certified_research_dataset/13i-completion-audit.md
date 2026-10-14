# Implementation completion audit — October 13, 2026

[Controller](13-campaign-controller.md) · [Validation](13h-validation-status.md)

**The full objective remains incomplete.** The unified CLI now assembles and
executes new qualified plans, resumes stored plans, and backfills causal session
checkpoints. Bounded runnable tests pass. Missing historical authority creation,
legacy qualification and representative production capacity still prevent a
claim that the requested full dataset can be certified from current inputs.

## Requirement evidence and remaining work

| Required outcome | Current implementation evidence | Completion gap |
|---|---|---|
| One acquisition-to-certification launch | `launch`, `launch-collection`, initial preparation, typed execution, collection resume/readonly verification | Missing historical authorities still require concrete creation/acquisition paths; qualified capacity must be measured |
| Download, encode and import after resubscription | `acquisition.updater_command` invokes the existing updater; new ingestion captures original reporting evidence before its success manifest | Keep this path available; no provider calls/download tests while unsubscribed |
| Repair imported historical events | Authorized ingestion-only retained-file recovery, SSD reserve/chunking, original-clock reconciliation and native repair have bounded ClickHouse tests | Full retained-day migration is not executed here; downstream remains canonical/sidecar only, with no REST |
| Dated US-stock/conid universe | Historical interval loading, publication and execution membership | Resolve missing/ambiguous historical identities and timely eligibility changes; current pool membership alone is insufficient |
| Shared bars and parallel consumers | Shared aggregate consumers; bounded issuer census, fresh collection launch, normalized persistence and aggregate capacity guard | Representative pilot rates and production throughput/memory measurements |
| PM/regular/AH causal checkpoints | Prefix-bound generation, explicit rebuild origins, prior-AH/foreign-bar `backfill-v7`, same-state equivalence and zero-insert resume | Legacy 2025 qualification, proven listing-start origins and certified rename bridges |
| Certified fundamentals, float and short data | Certified fact receipts, SEC stage-lineage/range admission, short source handlers, float and short-pressure SQL | Missing acquisition catch-up paths; original corporate/allocation/supply/short vintages |
| Requirement-derived earlier context | Automatic issuer/security assignment, state discovery, selected frames, rolling lookbacks, SEC float-price dates and price-only prior listings | Missing historical identity/state/reference evidence remains an explicit dependency |
| Compact normalized compressed persistence | Exact installation catalog, explicit lossless codecs, workload policies and actual-part checks | Audit every additional acquisition/migration schema before enabling its writer |
| Restart-safe execution and certification | Immutable CLI plans, typed producers/verifiers, source guards and seals; bounded market/SEC/backfill fixtures resume without inserts | Chunked whole-universe loading and end-to-end acceptance using qualified production bindings/capacity |
| Measured storage and efficiency | Physical inventory and plan-bound capacity guard | Representative certified pilot rates, transient/merge peaks, physical-volume grouping and full-universe projection |
| Inputs for RL and optimization | Existing common market/reference product contracts | Final consumer feature-coverage comparison; missing required common products must be added before claiming coverage |
| No downstream preparation | Controller scope is common data and certification | Continue excluding shards, hindsight labels, exports/caches, training and optimization |

Passing a fixture does not resolve the production evidence gaps in this table.
No expensive full campaign is part of implementation validation.

## Current historical identity repair inventory

A read-only October 13 audit used context January 1, 2025 through October 2,
2026, with conid observations through October 13. Among 2,509 listings rejected
for missing dated symbol intervals, direct retained-coverage lookup found:

| Dependency | Listings |
|---|---:|
| Missing retained ticker-history coverage | 2,351 |
| Source mapping conflict | 70 |
| Inactive listing lifecycle proof | 54 |
| Covered-empty history requiring a proven symbol origin | 34 |

`universe.history_repair_inventory()` reads coverage directly by listing ID;
joining through an absent symbol interval would hide this distinction. It
returns a bounded hashed dependency inventory, not an admission certificate.
`plan-cohorts` and fresh collection preparation now expose these lanes.
Mapped `covered_empty` does not by itself prove an unchanged lifetime ticker;
inactive/current symbols cannot replace dated lifecycle evidence. No provider
request or canonical/reference mutation occurred during this audit. Counts are
snapshot-specific and must be refreshed after Gateway repair or acquisition.

## Next implementation order

The shared SEC coverage writer previously allowed an empty policy to omit the
DDL storage setting and fall back to ClickHouse default storage. It now creates
coverage on explicit `live_market_ssd` with ZSTD(1) and checks policy/actual part
placement before creation and before/after inserts. Existing misplaced tables
or parts block and require a separate verified migration; no automatic move or
old-table codec rewrite occurs. Three focused storage tests and the real SEC
lineage ClickHouse fixture passed. Other SEC acquisition writers/runtime paths
still require review before unified acquisition is enabled; no download ran.

The shared SEC `clickhouse_writer` now verifies named workload policy and active
part placement before schema setup, before cloning a source schema, after setup,
and before row insertion. Its inferred policy is explicitly `live_market_ssd`;
absent templates cannot fall back to default, and misplaced templates fail.
Three unit tests cover absent templates, backup policy, bad parts and missing
writer tables. A live read-only check passed for the current `WRITE_TABLES` set.

A broader October 13 policy census found eight separate default-policy SEC
tables: disclosure taxonomy/candidate, filing entity archive manifest, rendered
text rebuild/bundle manifests, LLM issuer review/history, and synthesis. This is
not proof that their existing parts moved. Acquisition-used manifest writers
require review and explicit restart-safe migration before enablement; unrelated
application products are not modified by this task.

The entity-archive manifest and rendered-text rebuild/bundle manifest bootstrap
paths now explicitly use `live_market_ssd` and default ZSTD(1), verifying policy
and active part placement before/after bootstrap. Their insert paths check the
target before payload publication. Existing default-policy manifests are not
altered or overwritten: they block with a verified-migration requirement.
Fourteen shared storage/manifest unit tests pass, and a real isolated ClickHouse
fixture created all three manifest schemas, preserved an entity manifest row,
and verified SSD placement. The production manifest migration and remaining SEC
stage writer/runtime audit are still incomplete; no acquisition was started.

[SEC manifest migration](13s-sec-manifest-migration.md) now provides a fixed-scope,
backup-retaining, raw-version-preserving migration command. Eight unit/regression
tests and a real cross-partition duplicate-version migration/resume fixture pass.
Production planning was read-only; the default-policy source manifests have not
been moved. Maintenance-window execution and remaining SEC stage writer/runtime
audit remain required before acquisition can be enabled.

1. Close historical identity/reference creation and acquisition gaps, including
   original public corporate/allocation/short evidence and uncovered SEC ranges.
   Existing qualified scopes can already be imported server-side across datasets.
2. Qualify legacy histories, listing-start origins and rename bridges. Reuse the
   delivered causal backfill route only after its source authorities qualify.
3. Bind capacity admission to representative certified pilot evidence and actual
   disk topology. Validate the implemented issuer-cohort planning/loading at
   production scope without lifting the 250,000-session preparation and 1,000,000-unit /
   256 MiB saved-plan bounds. Inventory ratios alone must not authorize writes.
4. Finish the common-feature comparison against current RL/optimization consumers
   and add missing required common products without downstream preparation.
5. Run bounded end-to-end acceptance with current qualified inputs and update
   this audit with requirement-specific evidence before marking the goal complete.

Source/reference qualification and bounded capacity measurement can proceed
independently where their inputs exist. Execution still waits for all required
parents; certification cannot skip an unavailable family.

See [session backfill](13p-session-checkpoint-backfill.md),
[explicit origins](13n-rebuild-origins.md), and
[retained reporting migration](13o-retained-reporting-migration.md) for the
delivered paths and their precise qualification/retention boundaries.

[SEC lineage qualification](13q-sec-stage-lineage.md) corrects the partial-run
completion bug and requalifies retained metadata without altering source records.
Original public-value evidence and actual later acquisition remain separate
dependencies; this metadata repair does not create missing fundamentals.

[Issuer cohort scoping](13r-issuer-cohorts.md) now avoids whole-universe session
materialization during the census and persists each selected issuer group through
normal launch/resume. The collection controller core and normalized ClickHouse
definition/member/seal storage now pass bounded restart and complete-membership
tests. Fresh launch/resume/readonly verification routes are connected. A real
two-child fact-publication fixture sealed, resumed without inserts and rejected
authority tampering. Representative measured rates and production-scope
acceptance remain unfinished; existing bounds have not been raised.

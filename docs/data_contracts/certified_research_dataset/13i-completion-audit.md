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
| Measured storage and efficiency | [Isolated certified persisted-byte measurement](13u-storage-measurement.md), physical inventory and plan/collection capacity guards | Representative pilot rates, transient/merge peaks, physical-volume grouping and full-universe projection |
| Inputs for RL and optimization | [Current RL V6/V18 input review](13t-consumer-input-review.md); common contracts and prior-session RVOL demand | Legacy reader bindings, consumer parity and actual historical evidence/coverage remain unproven |
| No downstream preparation | Controller scope is common data and certification | Continue excluding shards, hindsight labels, exports/caches, training and optimization |

Passing a fixture does not resolve the production evidence gaps in this table.
No expensive full campaign is part of implementation validation.

## Current historical identity repair inventory

The launcher now includes a bounded retained-history symbol interval repair.
It reuses the Gateway kernel, checks original retained payload/coverage and
alias bindings, inserts only missing intervals, and independently verifies
readback/source stability. A real isolated fixture restored two intervals and
resumed with zero inserts. Missing histories, inactive lifecycle, covered-empty
origins and historical eligibility transitions remain unresolved; the repair
does not manufacture them. See [retained symbol repair](13x-retained-symbol-repair.md).

Verified dated identity points can now supply a missing symbol origin, including
successful covered-empty histories. Point hashes, identity/venue/type consistency,
successful response fingerprints and context bounds are checked. A real fixture
persisted one origin and resumed without inserts. Such intervals remain scope
pending and cannot enter the historical tradable universe until listing/venue
evidence is independently qualified; no IPO-date backprojection occurs.

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

`historical_identity.prepare_observations()` now supplies a bounded batch
executor for the existing dated-point handler: 1–1,000 explicit unique requests,
one global worker pool, verified point reuse before provider construction, and
explicit `acquire_missing` opt-in. Newly acquired points require independent
published readback. Seven handler/batch tests passed without provider calls.
The unified CLI now accepts bounded explicit identity points on source
preparation and fresh launch, before source preparation and graph freezing.
Reuse-only is the default; missing points block market preparation. A separate
reference-acquisition opt-in does not enable market downloads. The real bounded
ClickHouse fixture reused two persisted points without inserts/provider calls.
This does not reconstruct symbol intervals or prove lifetime membership;
complete change/lifecycle evidence and automatic repair discovery remain
incomplete. See [identity prerequisite options](13v-identity-prerequisites.md).

Explicit Gateway ticker-history requests now read bounded selected inventory,
coverage and integrity metadata server-side instead of fetching the complete
catalog before selection. Readback checks scope, duplication and bounds. A live
read-only single-identity check passed without publication/provider access.
This prepares the bounded repair lane; it does not supply missing lifecycle,
venue-transition or symbol-origin evidence, or change current inactive bindings.

Historical scope transitions now fail closed instead of silently projecting a
current OTC/non-US/non-USD classification backward. The candidate query retains
an evidence bit only from dated 2026-onward tradable snapshots that independently
match USD stock type and a verified non-OTC US venue. Generic broker tradability
alone is insufficient. A currently out-of-scope listing with that historical
evidence requires a dated transition boundary, not silent exclusion or automatic
historical admission. The evidence bit is included in universe source hashes;
old universe bindings require requalification rather than silent reuse.
Two focused tests passed, and the current read-only Gateway query retained its
existing blockers without widening production admission. Interval reconstruction
and transition-evidence publication remain incomplete.

## Next implementation order

Fresh source preparation now expands its context floor from selected rolling
requirements and declared rebuild origin. Newly discovered float-price sessions
have an explicit ingestion dependency before expanded source inventory/epoch
freezing, shared by single and collection launches. Sparse source reads select
only required dates; broader legacy aggregate fingerprints cannot be silently
reused. See [source context execution](13y-source-context-execution.md).

The unified launcher now has a guarded `--acquire-sec-missing` handoff to the
complete existing SEC chain. It propagates explicit context, runtime and bounded
resource selections, then independently qualifies stage lineage and required
family coverage before derived graph creation. Actual parent dry-run, typed
option, query-default, owned-child cleanup and source-handoff tests pass. The
live default runtime mapping is outside CH's `/mnt/d/market-data/` file authority;
execution needs a verified runtime mount alias, and existing misplaced source
tables must be migrated first. No provider acquisition, mount/configuration
change or production SEC campaign was run. See [scoped SEC acquisition](13w-sec-scoped-acquisition.md).

A narrow readonly SEC runtime bind-mount helper and opt-in startup hook are now
prepared. Eleven helper/related configuration tests and shell syntax checks
passed. The helper preserves foreign/nonempty aliases and checks filesystem
identity; it does not widen the configured file root, move files or restart the
server. Actual mount activation and CH nonce readback remain unverified.

SEC bulk mirror creation, raw/replacement stages, snapshot manifest writes and
cutovers now enforce the shared explicit `live_market_ssd` authority with ZSTD(1)
defaults. Misplaced templates or existing stage parts block before drop/create
or exchange. Acceptance archive inserts and cleanup keys have the same table
and part checks; cleanup key DDL can no longer use default storage. The shared
writer audit includes inactive parts, not only active ones. Twenty-two focused
storage tests and two real bounded ClickHouse fixtures passed (bulk setup/stages,
acceptance fallback cleanup preserving the verified replacement). These checks
do not migrate production tables or complete the full SEC acquisition/runtime
audit and runtime access qualification needed before production catch-up.

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
   with real reader/parity acceptance after the reviewed primitive mapping;
   do not generate downstream banks, shards, labels or exports.
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

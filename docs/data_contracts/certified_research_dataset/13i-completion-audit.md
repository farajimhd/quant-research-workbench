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
| One acquisition-to-certification launch | `launch`, `initial_preparation`, `initial_plan.run`, `ExecutionLaunch`, stored run/resume | Missing historical authorities still require concrete creation/acquisition paths; qualified capacity must be measured |
| Download, encode and import after resubscription | `acquisition.updater_command` invokes the existing updater; new ingestion captures original reporting evidence before its success manifest | Keep this path available; no provider calls/download tests while unsubscribed |
| Repair imported historical events | Authorized ingestion-only retained-file recovery, SSD reserve/chunking, original-clock reconciliation and native repair have bounded ClickHouse tests | Full retained-day migration is not executed here; downstream remains canonical/sidecar only, with no REST |
| Dated US-stock/conid universe | Historical interval loading, publication and execution membership | Resolve missing/ambiguous historical identities and timely eligibility changes; current pool membership alone is insufficient |
| Shared bars and parallel consumers | Initial assembly joins one aggregate with independent chronological technical/V7 and activity/price consumers | Whole-universe chunked metadata planning and representative throughput/memory measurements |
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

## Next implementation order

1. Close historical identity/reference creation and acquisition gaps, including
   original public corporate/allocation/short evidence and uncovered SEC ranges.
   Existing qualified scopes can already be imported server-side across datasets.
2. Qualify legacy histories, listing-start origins and rename bridges. Reuse the
   delivered causal backfill route only after its source authorities qualify.
3. Bind capacity admission to representative certified pilot evidence and actual
   disk topology. Implement chunked whole-universe planning/loading instead of
   simply lifting the current 250,000-session preparation and 1,000,000-unit /
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

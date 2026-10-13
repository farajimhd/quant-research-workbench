# Implementation completion audit — October 13, 2026

[Controller](13-campaign-controller.md) · [Validation](13h-validation-status.md)

**The full launcher is not ready.** Producer/controller fixtures pass, but the
CLI still exposes inspection, source preparation and saved verification only.
It cannot yet assemble and publish an initial complete plan or run/resume it.

## Requirement evidence and remaining work

| Required outcome | Current implementation evidence | Completion gap |
|---|---|---|
| One acquisition-to-certification launch | `planning.program_units`, `ExecutionLaunch`, `saved_execution.run` | Initial concrete assembly/publication, qualified capacity input and CLI execution wiring |
| Download, encode and import after resubscription | `acquisition.updater_command` invokes the existing updater; new ingestion captures original reporting evidence before its success manifest | Keep this path available; no provider calls/download tests while unsubscribed |
| Repair imported historical events | Canonical sidecar reconciliation and native reporting repair have bounded ClickHouse tests | Original nanosecond/full-condition evidence is missing for affected retained days; no REST or raw-file fallback is authorized |
| Dated US-stock/conid universe | Historical interval loading, publication and execution membership | Resolve missing/ambiguous historical identities and timely eligibility changes; current pool membership alone is insufficient |
| Shared bars and parallel consumers | One aggregate stage feeds technical, V7, activity and price units | Full-universe initial graph assembly and representative throughput measurements |
| PM/regular/AH causal checkpoints | Prefix-bound causal state, restoration, discovery and seed handlers | Legacy compatible migration/backfill launch, explicit genesis and certified rename bridges |
| Certified fundamentals, float and short data | Certified fact receipts, SEC/short source handlers, float and short-pressure SQL | Completeness admission; original corporate/allocation/supply vintages; uncovered short-interest sources |
| Requirement-derived earlier context | Indicator/V7 discovery, rolling lookbacks, SEC float-price discovery and per-listing price demands | Automatic issuer/security/historical-listing assignment and top-level context expansion |
| Compact normalized compressed persistence | Exact installation catalog, explicit lossless codecs, workload policies and actual-part checks | Audit every additional acquisition/migration schema before enabling its writer |
| Restart-safe execution and certification | Immutable plans, per-unit independent verification, seals; saved SEC fixture resumes without writes | Full saved-plan path with actual canonical bindings and qualified capacity evidence |
| Measured storage and efficiency | Physical inventory and plan-bound capacity guard | Representative certified pilot rates, transient/merge peaks, physical-volume grouping and full-universe projection |
| Inputs for RL and optimization | Existing common market/reference product contracts | Final consumer feature-coverage comparison; missing required common products must be added before claiming coverage |
| No downstream preparation | Controller scope is common data and certification | Continue excluding shards, hindsight labels, exports/caches, training and optimization |

Passing a fixture does not resolve the production evidence gaps in this table.
No expensive full campaign is part of implementation validation.

## Next implementation order

1. Assemble one bounded initial **complete** plan from real source, universe,
   seed, reference and context requirements. Return structured missing-input
   reasons before publishing any plan. Preserve all producer dependencies.
2. Bind capacity admission to representative certified pilot evidence and actual
   disk topology. Inventory ratios alone must not authorize writes.
3. Expose initial execution and saved resume through the existing launcher with
   frozen resources, progress, graceful stop, and post-run verification.
4. Close historical source/identity/reference and checkpoint migration gaps.
   Missing original evidence remains a reported dependency; it is not guessed.
5. Recheck the required common feature catalog against current RL/optimization
   consumers, run bounded end-to-end validation, and update this audit with
   requirement-specific evidence before marking the goal complete.

Source/reference qualification and bounded capacity measurement can proceed
independently where their inputs exist. Execution still waits for all required
parents; certification cannot skip an unavailable family.

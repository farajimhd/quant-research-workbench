# Validation status — October 13, 2026

[Controller](13-campaign-controller.md) · [Design overview](../certified_research_dataset_design.md)

## Verified

- Latest combined opt-in ClickHouse contracts plus initial/saved execution, membership, float, program planning, seed preparation, runtime and capacity suite: **118 passed in 171.76 seconds**. This supersedes narrower checks for those selected modules; it does not qualify a production-scale campaign.
- Assembled suite without live opt-in: **111 passed, 36 skipped**.
- Opt-in bounded ClickHouse contracts and SEC suite: **81 passed in 164.53 seconds**.
- Preparation target/bounds and source-spelling checks after the latest admission-order change: **17 passed**.
- Float measurement-price discovery: **12 focused tests passed**, plus **1 bounded ClickHouse float integration test passed**. Certified SEC measurement dates can demand earlier regular closes; context-date merging is available, but automatic launcher wiring remains incomplete.
- Saved execution adapter: **9 focused tests passed**, plus **1 bounded ClickHouse SEC/derived campaign test passed**. The adapter loads the real persisted plan and resumes certified receipts without new writes; this fixture supplies source/runtime bindings and a fixture capacity certificate. It does not qualify production capacity or full acquisition-to-certification execution.
- Market preparation accepts per-listing float-price session demands and retains them in shared aggregate/source dependencies while preserving separate indicator/V7 replay starts. Missing dated membership or canonical source blocks preparation. **23 focused preparation/float/planning tests passed**; issuer-to-historical-listing assignment and automatic top-level launch assembly remain unfinished.
- Initial graph/execution adapters: **22 focused assembly/runtime/preparation tests** and **14 focused initial/saved execution/planning tests** passed. The extended bounded SEC ClickHouse test passed in **24.51 seconds**: blocked capacity caused no writes, admitted execution published the real recipe/SEC plan and seal, stored units matched by key, and saved resume issued no writes. Canonical/runtime bindings and capacity remain explicit fixture seams; no full production campaign is qualified by this check.

These suites overlap; counts should not be added. The live suite exercises isolated schema installation/placement, canonical metadata and bounded event reads, original-clock reconciliation/native repair fixtures, public-as-of reference materialization, indicators/state, causal checkpoints, dependency execution, immutable plans, receipts/seals and resume checks. Test namespaces are cleaned up. This run did not call Massive, download flatfiles, train models or start a full historical campaign.

## Still incomplete

Automatic complete-plan assembly and CLI `run/resume` orchestration; required original-vintage/source qualification and acquisition paths; legacy checkpoint migration/genesis/rename bridges; SEC completeness admission; representative full-universe throughput and qualified storage-rate measurements; full requirement-by-requirement completion audit. Initial graph and execution adapters now exist and are exercised by bounded fixtures. Fresh-campaign SEC price-context discovery is implemented and passed a read-only retained-input check; top-level wiring remains incomplete. Reference scopes currently use UTC dates while sessions and SEC measurement-age checks use New York dates. Price discovery conservatively includes an extra predecessor date at the UTC/NY boundary; full target-session reference range alignment still requires explicit integration.

Small correctness fixtures and compressed physical inventory do not qualify production-scale efficiency or capacity. No full campaign has been admitted on this evidence, and the implementation goal remains active.

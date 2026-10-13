# Validation status — October 13, 2026

[Controller](13-campaign-controller.md) · [Design overview](../certified_research_dataset_design.md)

## Verified

- Assembled suite without live opt-in: **111 passed, 36 skipped**.
- Opt-in bounded ClickHouse contracts and SEC suite: **81 passed in 164.53 seconds**.
- Preparation target/bounds and source-spelling checks after the latest admission-order change: **17 passed**.

These suites overlap; counts should not be added. The live suite exercises isolated schema installation/placement, canonical metadata and bounded event reads, original-clock reconciliation/native repair fixtures, public-as-of reference materialization, indicators/state, causal checkpoints, dependency execution, immutable plans, receipts/seals and resume checks. Test namespaces are cleaned up. This run did not call Massive, download flatfiles, train models or start a full historical campaign.

## Still incomplete

Final plan publication and `run/resume` launcher orchestration; required original-vintage/source qualification and acquisition paths; legacy checkpoint migration/genesis/rename bridges; SEC completeness admission; representative full-universe throughput and qualified storage-rate measurements; full requirement-by-requirement completion audit.

Small correctness fixtures and compressed physical inventory do not qualify production-scale efficiency or capacity. No full campaign has been admitted on this evidence, and the implementation goal remains active.

# Whole-plan storage admission

[Controller](13-campaign-controller.md) · [Design overview](../certified_research_dataset_design.md)

## What the guard counts

`capacity.plan_demands()` derives write-unit counts from every unit in the assembled execution graph. Market aggregation covers shared bars/liquidity; technical units cover indicators/state; V7 units cover checkpoints. Reference calculations, universe and identity observations retain their own measured product categories. Existing corporate/allocation/supply scope handoffs are read-only; new acquisition/import stages require separately classified write products.

Read-only prepared-source and checkpoint roots add no product writes, but **every planned unit contributes to `campaign_metadata` demand**. Its pilot must include immutable definitions/plan rows, progress and root-seal overhead. Unknown products block admission rather than being omitted. Acquisition/migration stages outside the frozen derived graph need their own qualified capacity contract; they are not silently treated as read-only.

Per-product active-unit bounds respect workers, memory reservations and the global memory budget. Summed product peaks conservatively assume overlap, while controller metadata has one scheduler-owned writer. The estimate budgets the full plan and applies no unverified resume discount. Existing persisted data is already reflected in current disk free space; this conservative projection can overestimate additional writes during resume.

## Qualification is still required

`PlanCapacityGuard` combines those demands with fresh physical ClickHouse inventory and selected `PilotRate` records. It checks compatible semantic recipe hashes, staging/merge peaks, reserves and explicit physical-volume grouping. Its certificate binds the exact controller plan hash, demand fingerprint and inventory evidence. `ExecutionLaunch` requires an admitted, intact certificate before writing.

Counts do not establish comparable pilot rates. Reference-range length, liquidity strata, payload/state size, schema/codecs and actual merge behavior must be qualified by representative measurements. Raw legacy bytes-per-row is not a substitute. Physical-volume grouping must also be verified; different ClickHouse disk names do not prove different physical volumes.

## Frozen-plan integrity

Campaign argument dictionaries remain ordinary in-memory objects. The controller compares each active unit against its frozen encoded row before producer/verifier calls, and checks the whole graph once before installation/admission. A later mutation cannot execute under the old plan hash. The active-unit check avoids a full-graph scan on each callback.

Focused tests check mandatory metadata demand, unknown-product rejection, certificate binding and mutated-argument rejection. A bounded live inventory check remains blocked without qualified rates. Representative throughput/capacity qualification and final CLI integration remain unfinished; no full campaign is admitted by these checks.

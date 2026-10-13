# Short-data materialization integrity

[Controller](13-campaign-controller.md) · [Design overview](../certified_research_dataset_design.md)

## Frozen input must match the stage

Short-data prerequisite verification and publication are separate execution units. An original source can be revised between them. `run_short()` therefore checks both the source audit and the **actual materialized input**, before publishing facts, provenance or a receipt.

The shared `short_sql.input_tuple()` defines the audit identity: event ID, provider ticker, measurement date, original quantities, original publication clock, source system/venue, source security/listing IDs, content hash, evidence reference and mapped listing key. Its JSON representation is appended to the staged provenance proof. It preserves the original integer quantities without converting them to floating-point fact values.

`staged_input_audit()` reconstructs the frozen audit hash server-side from distinct staged input records, sorted by event ID. It verifies original input count/uniqueness and the complete five-metric short-volume or three-metric short-interest expansion. A changed, missing or duplicated input or incomplete expansion blocks publication. Only counts and a hash leave ClickHouse; no raw short dataset is fetched into Python.

## Measurement and publication ranges differ

All source measurements in the audited range are staged, including observations whose original publication date falls after the requested end. This prevents a future-publication row from disappearing from the input comparison.

Publication then filters `available_at` to the requested range for both facts and their provenance. A scope can certify zero currently available facts while still checking its actual acquired measurement inputs. This does not replace the separate acquisition-coverage prerequisite.

The existing compressed `research_stage_reference_v1` stores these rows; there is no additional persistence schema or duplicated raw table. Capacity estimates must allow up to five staged facts per short-volume input or three per short-interest input, including deferred-publication rows. Published row counts alone do not measure that temporary requirement.

## Verification

A bounded live ClickHouse fixture revises an isolated source after the audit and before staging. The new input hash differs, publication raises, and no reference receipt is created. Another case checks a short-interest measurement whose publication is later than the scope: input verification succeeds and published fact count is zero. Existing original-clock, denominator, valid-zero and no-insert resume checks still pass.

No production short data was regenerated and no provider was called. The fixture does not qualify full-universe capacity or legacy certificates against this newer materialization check.

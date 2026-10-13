# Certified checkpoint seeds

[Controller](13-campaign-controller.md) · [Design overview](../certified_research_dataset_design.md)

## Implemented execution roots

`ExecutionLaunch` registers `technical_seed` and `v7_seed`. Both take exactly one argument, `dependency`: a compact receipt locator with its `state_context`. Checkpoint payloads and indicator arrays do not belong in plan arguments. These roots only read and verify existing authorities; they do not acquire data, create an initial empty state, or replay historical events.

Each handler owns a private ClickHouse client, checks dataset/source ownership before and after verification, and closes the client on failure. The seed's source date must exist in the launcher's frozen canonical/reporting context. A seed does not authorize skipping intervening exchange sessions; context planning and day rollover retain their chronological checks.

## V7 authority

`v7.verify_seed()` verifies an explicit **causal full-state** locator. Its context contains:

- `recipe_hash`, `source_hash`, `predecessor_hash` and `ticker`;
- `regular_close`, `bars_parent_hash` and `adjustment_hash`.

The reader compares recipe/source with independently supplied frozen inputs, verifies the exchange calendar, reconstructs bar and V7 receipt lineage using the actual rule contract, and independently reads the certified receipts. It then reconstructs the input-prefix and PM→regular→AH chain from certified 1-second bars before restoring the AH payload. Production requires corporate-adjustment lineage, including proof for an unchanged price scale. Returned metadata preserves that lineage.

The locator's declarations alone never establish compatibility. Retrospective completed-day books and legacy locators lacking required context need an explicit migration/admission path; this reader does not reinterpret them as causal seeds.

## Indicator authority

`IndicatorSeedHandlers` checks the frozen recipe, source and rule hashes against the locator, then independently validates its `technical_state` receipt and all eight persisted frame states. No historical bar scan is required for indicator restoration. It returns the state receipt as the seed authority; a combined indicator receipt may supply the locator through its `state_receipt` field.

## Bounded V7 discovery

`v7.discover()` selects the latest compatible AH checkpoint **within the explicitly supplied frozen day contexts**. Each day supplies `source_hash`, historical `ticker` and independently certified `adjustment_hash`; the launcher also supplies the recipe and rule contract. Missing adjustment evidence is not inferred as an unchanged scale. An explicit dataset can restrict selection.

The reader fetches bounded receipt/PM-header metadata, reconstructs expected bar and V7 lineage, and skips incompatible recipes/source epochs without fetching bars or payloads. The selected candidate passes the same `verify_seed()` check as an explicit locator. Multiple compatible candidates on the selected date block discovery rather than choosing by insertion time. More than the configured candidate limit blocks and requires a narrower context or explicit dataset; results are never silently truncated. Bounds are 128 candidates by default (at most 1,024) and 4,096 frozen context days.

The result contains the seed dependency, verified AH checkpoint, source session and inspected/incompatible candidate counts. Context planning must still replay every intervening exchange session. This implementation uses existing compressed receipts/checkpoints and adds no persisted schema or duplicated payload.

## Validation and remaining work

A bounded live ClickHouse campaign fixture verifies AH restoration, both seed handlers, unchanged stored authority and rejection of an altered V7 predecessor hash. Unit tests cover missing frozen source, cancellation and client cleanup. This demonstrates correctness on a small fixture, not production throughput.

Launch-time orchestration of discovery/adjustment evidence, legacy 2025 checkpoint compatibility/migration, genesis certification and final session-backfill CLI routing remain unfinished. Existing `load_checkpoint`/`load_from_bars` readers support PM, regular and AH; the new `v7_seed` execution root specifically restores the preceding day's AH for chronological day generation.

# Optimization inputs and required upgrades

[Overview](../certified_research_dataset_design.md) · [Previous](07-rl-inputs.md) · [Next](09-execution.md)

Understand compact tapes, history banks, cache reuse and execution assumptions.

**Status:** design requirements; existing-code facts and proposed upgrades are distinguished below. Audit figures are the October 11 snapshot, not a fresh campaign status.

## On this page

- [Optimization consumer forms and upgrades](#optimization-consumer-forms-and-upgrades)

## Optimization consumer forms and upgrades

### V6 and V7 execution assumptions

Preserve separate execution assumptions: V6 detailed broker/account replay versus V7 assumed fractional fills at next observed trade close. V7 quote features are not fill constraints. Terminal mark, missing-price and held-after-ranking behavior remain explicit.

### Prepared inputs and ranking population

Forms: compact per-session Parquet market/rank tapes; mmap clocks/identity maps/features/validity; original 149 feature channels (147 bank features plus split/session flags); 332-channel lossless history banks and elapsed-clock indices; relative-price channels and 11 quote/price/activity channels; feature-cache tiles; execution evidence for V6. Prepare full histories for the ranked-union identities before and after rank entry. Preserve price-filter-before-volume ranking and held identities after rank exit. Full-universe common data must not be reduced to a fixed top10 upstream.

### Existing cache reuse

Current cache already supports 2048-clock/four-listing compressed tiles, packed validity, source/schema/implementation hashes and restart. Integrate it as a consumer materialization, not a new event fetch. GPU may use compact resident banks/gathers while CPU uses tiles; avoid mandatory duplication when a chosen adapter does not need both. New source/eligibility/universe/feature contracts invalidate old compatibility even if file shapes match.

### Required feature and compatibility upgrades

Add certified fundamentals/short-pressure sidecar and versioned searchable channels only through an explicit catalog revision. Preserve temporal rule semantics, source masks, 121 swing combinations and quote age. History/source compression must be lossless; rank/filter parameters are in the receipt. No research financial qualification, evolutionary launch or reopened sealed set is implied by data certification.

[Overview](../certified_research_dataset_design.md) · [Previous](07-rl-inputs.md) · [Next](09-execution.md)

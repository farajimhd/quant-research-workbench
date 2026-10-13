# Frozen runtime reconstruction

[Controller](13-campaign-controller.md) · [Design overview](../certified_research_dataset_design.md)

`runtime_plan.units()` creates a `calculation_recipe` descriptor plus `condition_rules` chunks in the existing immutable campaign plan tables. Each chunk contains at most 32 rules and stays within the 8 KiB argument limit. No persistence schema, payload table or generated file is added.

The descriptor freezes the market algorithm definition, custom V7 parameters, exact original rule order/types, feature-selection hash and reference contract. The reference contract includes the fact catalog, fundamental formulas and calculation-module fingerprints. Dates and resource budgets remain in the existing saved campaign configuration.

`resolve()` validates the complete chunk set, rule hash, frame selection, calculation hash and current algorithm/dependency contracts. Missing chunks, changed selections or incompatible code fail rather than replacing values with current defaults. `runtime()` combines that verified descriptor with the saved `canonical_source` day bindings to recreate `MarketRuntime`; it opens no data clients itself.

`ExecutionLaunch.from_stored()` uses this reconstruction by default. Explicit runtime factories remain supported for controlled integrations. When a descriptor is present, launch construction checks runtime equality and admission revalidates the descriptor before writes. Registered metadata handlers verify rule chunks and the recipe as normal plan units; their persistence demand is campaign metadata only.

Plans created before this descriptor existed need an explicit immutable-plan upgrade or a separately controlled runtime factory. This change does not retroactively add metadata to old dataset definitions.

Tests preserve nondefault tick/coverage settings and 80 condition rules across chunking, reject missing chunks and changed selections, and reconstruct frozen source bindings without opening clients. A bounded live ClickHouse metadata-only fixture persists the descriptor, loads it through the saved-plan reader and verifies unchanged sealed resume. It does not certify market-data coverage. Final CLI wiring and remaining source/prerequisite qualification remain unfinished.

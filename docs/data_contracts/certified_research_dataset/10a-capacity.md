# Storage estimates and capacity admission

[Overview](../certified_research_dataset_design.md) · [Previous](10-storage.md) · [Next](11-certification-decisions.md)

Separate measured disk space, provisional estimates and unknown export costs.

## On this page

- [Capacity estimates and admission](#capacity-estimates-and-admission)

## Capacity estimates and admission

### Core estimate and exclusions

Earlier October 11 audit: existing physical bars/indicators/liquidity occupy about 611 GiB across attempts; estimated certified 36-session footprint is 304 GiB. Session/source-volume extrapolation for 189 target sessions suggests 1.6–2.0 TiB new core data and a provisional 2.3–2.8 TiB additional-space budget including working headroom/small derivatives. These estimates do not include extensive prior-context rebuilds, full RL banks, optimizer full-universe histories, expanded tiles or the January G→D staging inventory.

### Measured disk headroom

New disk check: `live_market_ssd` free 5,003,820,261,376 bytes (~4.55 TiB); `sip_raw_ssd` free 1,502,394,458,112 bytes (~1.37 TiB). Do not add the displayed capacities: configured paths report the same total backing capacity, so verify mount/device relationships and concurrent physical-space consumption before admitting budgets. Recheck immediately before launch.

### Reference-data footprint

Current reference physical footprints are small relative to market data: short volume 1.542 GiB, short interest 0.160 GiB, SEC facts 7.664 GiB, FTD 0.336 GiB, resolved float 0.012 GiB. They are physical parts, not certified target-set byte attribution.

### Consumer banks, histories and caches

Prior optimizer history benchmark: ~167 GiB/30 sessions for its ranked population; scaling to 189 is ~1.03 TiB only under comparable populations/compression. It is not an all-tradable estimate. V7 cache default 1,200 GiB is a cap, not expected size. RL base arrays require 596 bytes per actual row (8 clock + 37×4 scalars + 110×4 levels), before masks/indices/labels; 26.79 million rows implies ~14.9 GiB/day before compression. This single-day benchmark is not sufficient to project full January coverage.

### Final capacity admission

Plan measures representative dense/sparse units and calculates base tables + contexts + staging + exported banks/histories/cache + mutation/temp/merge headroom. Report compressed disk and uncompressed resident RAM separately. Select reuse/compression/export presets explicitly; fail if measured total exceeds the space floor. No old build deletion is assumed.

[Overview](../certified_research_dataset_design.md) · [Previous](10-storage.md) · [Next](11-certification-decisions.md)

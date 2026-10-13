# Canonical source spelling checks

[Controller](13-campaign-controller.md) · [Design overview](../certified_research_dataset_design.md)

Gateway membership uses normalized symbols; canonical SIP preserves source spelling. These can differ: the January 2, 2026 canonical ticker/day index contains `ABRpD`, while normalization produces `ABRPD`. The market kernel reads exact ticker strings, so blindly using the normalized name could produce empty bars for a security with real events.

`source.require_exact_symbol()` checks the compact ingestion-owned `events_ticker_day_index` before `MarketRuntime` requests execute. Multiple source names with the same uppercase form, an unmapped different spelling, or an invalid event count block. An absent index entry remains a possible genuine no-event session; the broader source/index certification contract is still required.

Explicit dated mappings are supported through `MarketSession.source_ticker` and the `program_from_universe(source_spellings={(listing_key, date): raw_ticker})` parameter. The source spelling must preserve the uppercase identity, exist exactly in that day's index and remain unambiguous. This handles case differences only; arbitrary symbol changes still require a certified identity transition. Unknown listing/session mappings fail.

Aggregation selects the raw source spelling while staging/output identity and V7 bindings retain the canonical ticker. An alias is bound into product-parent hashes using `explicit-case-source-v1`; it cannot reuse an unmapped product receipt. Unchanged/exact spellings retain their prior hash behavior. Cloned producer units preserve the mapping. V7 authority/seed/discovery context carries the source spelling so lineage can be independently reconstructed.

Read-only live checks confirmed `ABRpD` exists, unmapped `ABRPD` is rejected, and an explicit mapping reads a bounded five-event canonical sample. Focused tests verify selection, canonical identity and lineage differences; two live market/checkpoint regression fixtures passed. Automatic bulk discovery/freeze of mapping proposals remains unfinished. No retained flatfiles or provider requests were used.

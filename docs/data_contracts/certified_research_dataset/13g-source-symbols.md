# Canonical source spelling checks

[Controller](13-campaign-controller.md) · [Design overview](../certified_research_dataset_design.md)

Gateway membership uses normalized symbols; canonical SIP preserves source spelling. These can differ: the January 2, 2026 canonical ticker/day index contains `ABRpD`, while normalization produces `ABRPD`. The market kernel reads exact ticker strings, so blindly using the normalized name could produce empty bars for a security with real events.

`source.require_exact_symbol()` checks the compact ingestion-owned `events_ticker_day_index` before `MarketRuntime` requests execute. Multiple source names with the same uppercase form, a different source spelling, or an invalid event count block with an explicit mapping requirement. No event scan, retained-flatfile read or fallback alias is used. An absent index entry remains a possible genuine no-event session; the broader source/index certification contract is still required.

A read-only live check confirmed `ABRpD` exists and `ABRPD` is rejected. Unit tests cover exact/absent names, normalization ambiguity and invalid counts. Explicit dated source-symbol mapping is still to be implemented; this guard prevents the mismatch from being silently certified as an empty market day.

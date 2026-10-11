# Available data, gaps and source evidence

[Overview](../certified_research_dataset_design.md) · [Previous](01-foundations.md) · [Next](03-ingestion-universe.md)

See what the October 11 audit confirmed and what remains unverified.

**Status:** design requirements; existing-code facts and proposed upgrades are distinguished below. Audit figures are the October 11 snapshot, not a fresh campaign status.

## On this page

- [Review evidence and current gaps](#review-evidence-and-current-gaps)
- [Source pointers](#source-pointers)

## Review evidence and current gaps

### Review scope

Code reviewed: market-day SQL and reporting migration; Reference Gateway tradability, publications and float resolution; SEC as-of queries and app fundamental formulas; structural V7 campaign/stream; RL V6 source, feature bank, current label/sequence contracts; optimization V6 history and V7 inputs/cache. TASK-0220 and TASK-0223 continuity was checked. Version names require care: structural V7 is not optimization V7; `structural_checkpoint_campaign_v7.md` describes another generic-structure campaign and is not the Level Book V7 contract.

### Audit location and date

Live reads were bounded, read-only ClickHouse queries on October 11. Evidence is under `D:/TradingML/runtimes/market-data-expansion-review/20261011/`; no secrets are included. Earlier audit results below are from this conversation's October 11 investigation; new reference checks are in `design_*.json`.

### Coverage and blockers

| Authority/product | Available evidence | Consequence |
| --- | --- | --- |
| Canonical SIP | Source receipts for Jan 2–Oct 2: 189 sessions, 120,294,226,954 events, 25,995,919,023 trades | Use certified canonical events; distinguish NY session date from physical UTC partition dates |
| Reporting classification | 56 complete receipts July 1–Sep 18 in the earlier audit; sampled older rows unclassified | Audit/certify or migrate remaining days; do not mutate days that are already correct just to obtain a receipt |
| Existing core products | 36 sealed sessions July 30–Sep 18; sampled certified bars admit unknown clocks under the old policy | New strict eligibility/build lineage; recompute affected state chains and consumers |
| Universe snapshots | No V2 snapshots before July 30; gaps/carry-forward cases later; retained V1 publications do not cover January | Required dated historical reconstruction; today's graph is insufficient |
| Structural history | 2025–Sep 2026 history exists but earlier audit labels most coverage legacy-unfiltered | Compatibility audit before reuse; incompatible seeds require prior repair/rebuild |
| FINRA short volume | 3,143,402 rows, 188 dates Jan 2–Oct 1, CNMS; all publication timestamps null; 1,027,798 rows have empty security identity | Fill endpoint gap, repair dated identities and availability; dates alone do not establish certified coverage |
| Massive short interest | 142,612 rows for 11 settlement dates Jan 15–Jun 15; all publication dates/timestamps null; 23,666 empty security identities | Gap-fill later periods and certify publication timing before historical use |
| Resolved float | 60,424 rows across 9 resolution dates Sep 23–Oct 5 | Not target-period historical authority; raw float observations exist earlier and must be resolved as-of |
| SEC XBRL | Tables/columns exist; physical parts contain 57,063,756 rows / 7.664 GiB | Physical rows are not unique certified facts. Whole-table FINAL aggregate exceeded the 1 GiB read budget; all-issuer historical coverage remains unproven |
| Optimization feature cache | Current `v7/feature_cache.py` and `prepare_features.py` exist with hashed, resumable tiles | Reuse/upgrade compatibility; the older handoff saying persistence is absent is superseded by current code |

### What the audit does not prove

Reference parts checked reside on `live_market_ssd`. Raw-source completeness, dated identity coverage, publication vintages and algorithm compatibility still need per-unit certification; an aggregate row count cannot establish them.

## Source pointers

- `pipelines/market_sip/events/{market_day_sql.py,trade_reporting_flags.py}`; `scripts/backfill_trade_reporting_flags.py`.
- `pipelines/market_sip/flatfiles/download_update_events.py`; `pipelines/market_sip/events/clickhouse_build_unified_events.py` for encoded DDL.
- `services/reference_gateway/{tradability.py,tradable_snapshots.py,ticker_events.py,resolved_float.py,market_publications.py}`.
- `pipelines/reference_data/{security_dimensions.py,market_publications_historical_gap_fill.py}`; `migration/step_06_build_q_live_bridge_features.py`.
- `src/backend/query_plans/sec_fundamentals_asof_v1.py`; `src/backend/ticker_facts_service.py`.
- `research/level_book/v7/campaign.py`; `src/market_engine/streaming_level_book.py`; `src/backend/{fixed_v7_stream.py,structural_v7_seed.py}`.
- `research/rl_trading/v6/{README.md,LABEL_PREPARATION.md,TEACHER_SEQUENCE.md,features.py,source.py,reference.py,universe_scope.py,environment_source.py,luld.py,rl_training.py}`.
- `research/vectorized_backtest/v6/torch_backtest/{feature_bank.py,compact_prepare.py,history_bank.py,persisted_history.py}`; `research/vectorized_backtest/v7/{README.md,data.py,features.py,feature_cache.py,prepare_features.py}`.
- Relevant continuity: TASK-0215, TASK-0206, TASK-0208, TASK-0220, TASK-0223; October 8 optimization handoff and September 24 RL hindsight diagnosis. Historical operational notes are not fresh campaign status.

[Overview](../certified_research_dataset_design.md) · [Previous](01-foundations.md) · [Next](03-ingestion-universe.md)

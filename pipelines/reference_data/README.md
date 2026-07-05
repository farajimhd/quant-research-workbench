# Reference Data Pipeline

This package owns reference-data loads and q_live migration scripts.

## Market References

Use this loader for dense market reference tables:

```powershell
python -m pipelines.reference_data.run_load_market_references
```

## q_live Migration

The historical q_live migration scripts live in:

```text
pipelines/reference_data/migration/
```

Run migration steps by module path, for example:

```powershell
python -m pipelines.reference_data.migration.step_01_create_q_live_schema --help
```

## Ongoing Reference Gateway

Slow-changing identity/reference sync is owned by:

```text
services/reference_gateway/
```

The default executable step is a read-only audit/planner:

```powershell
python -m services.reference_gateway.main
```

It enforces the rule that any unresolved identity, exchange, conid, or mapping
issue keeps the affected security out of the tradable universe.

In `--execute` mode, the reference gateway first closes stale gateway-owned
issues whose symbols are now valid, then rebuilds `feature_tradable_universe_v1`
and `feature_scanner_static_v1` from the canonical q_live graph. When
active-ticker reconciliation discovers open mapping issues, it writes those
issues to `id_mapping_issue_v1`. When a Massive active ticker is clean, the
gateway can also insert the new issuer/security/listing/symbol graph rows. It
then rebuilds the tradable/scanner publications again. The audit remains
read-only validation; it does not directly patch `is_tradable`.

Initialize the market-publication schema after hours:

```powershell
python -m services.reference_gateway.main --ensure-market-publication-schema
```

Temporary write-database test mode:

```powershell
python -m services.reference_gateway.main --read-database q_live --test-write-database q_reference_tmp --execute --ensure-market-publication-schema
```

Historical/gap-fill market publications:

```powershell
python D:\TradingCodes\quant-research-workbench\pipelines\reference_data\market_publications_historical_gap_fill.py --start-date 2026-01-01 --end-date 2026-06-22 --read-database q_live --write-database q_live --sources finra_short_volume,massive_short_interest,sec_fails_to_deliver,reg_sho_threshold,massive_splits,massive_dividends,massive_ipos,massive_ticker_details,massive_presentation_assets,ibkr_borrow_availability,sec_country_assertions --finra-venues CNMS --output-root-win D:/market-data/prepared/reference_market_publications --presentation-asset-root-win D:/market-data/reference_gateway/artifacts/presentation_assets --resume-from-coverage --execute
```

Workstation runtime command:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\reference_data\market_publications_historical_gap_fill.py --start-date 2026-01-01 --end-date 2026-06-22 --read-database q_live --write-database q_live --sources finra_short_volume,massive_short_interest,sec_fails_to_deliver,reg_sho_threshold,massive_splits,massive_dividends,massive_ipos,massive_ticker_details,massive_presentation_assets,ibkr_borrow_availability,sec_country_assertions --finra-venues CNMS --output-root-win D:/market-data/prepared/reference_market_publications --presentation-asset-root-win D:/market-data/reference_gateway/artifacts/presentation_assets --resume-from-coverage --execute
```

Workstation temp-write smoke:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\reference_data\market_publications_historical_gap_fill.py --start-date 2026-01-01 --end-date 2026-06-22 --read-database q_live --write-database q_reference_tmp --sources finra_short_volume,massive_short_interest,sec_fails_to_deliver,reg_sho_threshold,massive_splits,massive_dividends,massive_ipos,massive_ticker_details,massive_presentation_assets,ibkr_borrow_availability,sec_country_assertions --finra-venues CNMS --output-root-win D:/market-data/prepared/reference_market_publications --presentation-asset-root-win D:/market-data/reference_gateway/artifacts/presentation_assets --resume-from-coverage --execute
```

The fill script writes compact normalized rows and
`market_reference_publication_coverage_v1` rows. It implements FINRA daily
short-sale volume, Massive short interest, SEC fails-to-deliver, NasdaqTrader
Reg SHO threshold rows, Massive corporate actions, Massive current ticker
details/share-supply/presentation assets, IBKR point-in-time borrow, and
country assertions. IBKR borrow and Massive ticker details are current-state
sources; for historical windows the script records `source_not_historical`
instead of fabricating old snapshots.

The reference gateway can run a recent coverage-aware publication fill after its
execute-mode audit. Large/manual historical fills should still use the script
directly with explicit date ranges.

Dry-run mode does not create or alter tables. If the target write database has
not been initialized, the script reports `schema_missing` and exits after
writing the run summary. Weekend FINRA windows and SEC windows with no published
file are persisted as `covered_empty` only during `--execute`, so maintenance
does not repeatedly rediscover non-publication days.

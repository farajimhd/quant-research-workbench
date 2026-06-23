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

In `--execute` mode, the reference gateway first rebuilds
`feature_tradable_universe_v1` and `feature_scanner_static_v1` from the
canonical q_live graph. When active-ticker reconciliation discovers open mapping
issues, it writes those issues to `id_mapping_issue_v1` and rebuilds the
tradable/scanner publications again. The audit remains read-only validation; it
does not directly patch `is_tradable`.

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
python D:\TradingCodes\quant-research-workbench\pipelines\reference_data\market_publications_historical_gap_fill.py --start-date 2026-01-01 --end-date 2026-06-22 --read-database q_live --write-database q_live --sources finra_short_volume,sec_fails_to_deliver --finra-venues CNMS --output-root-win D:/market-data/prepared/reference_market_publications --resume-from-coverage --execute
```

Workstation runtime command:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\reference_data\market_publications_historical_gap_fill.py --start-date 2026-01-01 --end-date 2026-06-22 --read-database q_live --write-database q_live --sources finra_short_volume,sec_fails_to_deliver --finra-venues CNMS --output-root-win D:/market-data/prepared/reference_market_publications --resume-from-coverage --execute
```

Workstation temp-write smoke:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\reference_data\market_publications_historical_gap_fill.py --start-date 2026-01-01 --end-date 2026-06-22 --read-database q_live --write-database q_reference_tmp --sources finra_short_volume,sec_fails_to_deliver --finra-venues CNMS --output-root-win D:/market-data/prepared/reference_market_publications --resume-from-coverage --execute
```

The fill script writes compact normalized rows and
`market_reference_publication_coverage_v1` rows. It currently implements FINRA
daily short-sale volume and SEC fails-to-deliver historical fills. IBKR borrow
availability is point-in-time only and should be polled into
`market_security_borrow_v1`; it should not be backfilled as if historical borrow
availability were known.

Dry-run mode does not create or alter tables. If the target write database has
not been initialized, the script reports `schema_missing` and exits after
writing the run summary. Weekend FINRA windows and SEC windows with no published
file are persisted as `covered_empty` only during `--execute`, so maintenance
does not repeatedly rediscover non-publication days.

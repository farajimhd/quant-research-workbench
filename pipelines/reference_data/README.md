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

The first executable step is a read-only audit/planner:

```powershell
python -m services.reference_gateway.main
```

It enforces the rule that any unresolved identity, exchange, conid, or mapping
issue keeps the affected security out of the tradable universe.

Initialize the market-publication schema after hours:

```powershell
python -m services.reference_gateway.main --ensure-market-publication-schema
```

Historical/gap-fill market publications:

```powershell
python D:\TradingCodes\quant-research-workbench\pipelines\reference_data\market_publications_historical_gap_fill.py --start-date 2026-01-01 --end-date 2026-06-22 --database q_live --sources finra_short_volume,sec_fails_to_deliver --finra-venues CNMS --output-root-win D:/market-data/prepared/reference_market_publications --resume-from-coverage --execute
```

Workstation runtime command:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\reference_data\market_publications_historical_gap_fill.py --start-date 2026-01-01 --end-date 2026-06-22 --database q_live --sources finra_short_volume,sec_fails_to_deliver --finra-venues CNMS --output-root-win D:/market-data/prepared/reference_market_publications --resume-from-coverage --execute
```

The fill script writes compact normalized rows and
`market_reference_publication_coverage_v1` rows. It currently implements FINRA
daily short-sale volume and SEC fails-to-deliver historical fills. IBKR borrow
availability is point-in-time only and should be polled into
`market_security_borrow_v1`; it should not be backfilled as if historical borrow
availability were known.

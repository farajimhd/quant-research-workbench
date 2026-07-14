# SEC Bulk ClickHouse Ingest Guide

Use `sec_bulk_clickhouse_ingest.py` after `sec_initial_fill_download.py` has downloaded the SEC bulk files. This stage does not use daily EDGAR `.nc.tar.gz` archives.

## Inputs

Expected files under the SSD artifact root:

```text
D:\market-data\sec_core\bulk\submissions\submissions.zip
D:\market-data\sec_core\bulk\companyfacts\companyfacts.zip
D:\market-data\sec_core\bulk\mappings\company_tickers.json
D:\market-data\sec_core\bulk\mappings\company_tickers_exchange.json
D:\market-data\sec_core\bulk\mappings\company_tickers_mf.json
```

## Tables Created

- `sec_bulk_mirror_raw_source_file_v3`
- `sec_bulk_mirror_company_v3`
- `sec_bulk_mirror_company_ticker_v3`
- `sec_bulk_mirror_submission_file_ref_v3`
- `sec_bulk_mirror_filing_v3`
- `sec_bulk_mirror_xbrl_fact_v3`
- `sec_bulk_mirror_member_manifest_v3`

The script creates the database and tables if they do not exist.

## Workstation Command

Dry run:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_bulk_clickhouse_ingest.py --dry-run --artifact-root-win D:/market-data/sec_core --output-root-win D:/market-data/prepared/sec_core
```

Small schema/parser smoke test:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_bulk_clickhouse_ingest.py --artifact-root-win D:/market-data/sec_core --output-root-win D:/market-data/prepared/sec_core --sources company_tickers,company_tickers_exchange,company_tickers_mf,submissions,companyfacts --limit-ciks 10 --batch-size 5000
```

Full bulk ingest:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_bulk_clickhouse_ingest.py --artifact-root-win D:/market-data/sec_core --output-root-win D:/market-data/prepared/sec_core --sources company_tickers,company_tickers_exchange,company_tickers_mf,submissions,companyfacts --batch-size 50000
```

## Environment

ClickHouse connection defaults:

- `SEC_CLICKHOUSE_URL`, then `QMD_CLICKHOUSE_URL`, then generic ClickHouse env defaults.
- `SEC_CLICKHOUSE_USER`, then `QMD_CLICKHOUSE_USER`.
- `SEC_CLICKHOUSE_PASSWORD`, then `QMD_CLICKHOUSE_PASSWORD`.
- `SEC_CLICKHOUSE_DATABASE`, default `sec_core`.
- `SEC_CLICKHOUSE_STORAGE_POLICY`, then `CLICKHOUSE_LIVE_STORAGE_POLICY`.

Storage paths:

- `SEC_CORE_ARTIFACT_ROOT_WIN`, default `D:/market-data/sec_core`.
- `SEC_CORE_OUTPUT_ROOT_WIN`, default `D:/market-data/prepared/sec_core`.

The script refuses `G:` and `\\DESKTOP-SAAI85T\Workstation-G\...` roots by default.

## Important Behavior

- The script inserts only SEC bulk data.
- It does not download or parse daily feed archives.
- It does not download accession `.txt` files yet.
- It stores `sec_bulk_mirror_submission_file_ref_v3` rows from parent CIK members.
- `companyfacts.zip` is exploded into one row per XBRL fact observation.
- `submissions.zip` is the complete filing-metadata authority: parent CIK members use `filings.recent`, while included `CIK##########-submissions-###.json` members use top-level filing arrays.
- Both submission member shapes write `sec_bulk_mirror_filing_v3` with `accepted_at_utc` parsed only from explicit timezone-bearing `acceptanceDateTime` values.
- Fragment manifest signatures include the fragment parser version. Parser corrections reprocess only fragment members while preserving completed parent-member work.

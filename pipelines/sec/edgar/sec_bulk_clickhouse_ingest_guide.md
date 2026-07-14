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
- `sec_bulk_mirror_snapshot_manifest_v3`

The script creates the database and tables if they do not exist.

## Workstation Command

Dry run:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_bulk_clickhouse_ingest.py --dry-run --artifact-root-win D:/market-data/sec_core --output-root-win D:/market-data/prepared/sec_core
```

Bounded schema/parser smoke test (stages and validates 10 members without replacing active tables):

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_bulk_clickhouse_ingest.py --artifact-root-win D:/market-data/sec_core --output-root-win D:/market-data/prepared/sec_core --sources submissions,companyfacts --limit-members 10 --max-threads 4 --max-memory-usage 4G
```

Full bulk ingest:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_bulk_clickhouse_ingest.py --artifact-root-win D:/market-data/sec_core --output-root-win D:/market-data/prepared/sec_core --sources company_tickers,company_tickers_exchange,company_tickers_mf,submissions,companyfacts --max-threads 32 --max-memory-usage 96G
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
- `SEC_CORE_ARTIFACT_ROOT_CH`, default `/mnt/d/market-data`.
- `SEC_CORE_OUTPUT_ROOT_WIN`, default `D:/market-data/prepared/sec_core`.
- `SEC_BULK_CLICKHOUSE_MAX_THREADS`, default `32`.
- `SEC_BULK_CLICKHOUSE_MAX_MEMORY`, default `96G`.
- `SEC_BULK_MINIMUM_ROW_RATIO`, default `0.95`.

The script refuses `G:` and `\\DESKTOP-SAAI85T\Workstation-G\...` roots by default.

## Important Behavior

- Each downloaded SEC bulk file is treated as a complete snapshot, not an append-only update.
- ClickHouse reads ZIP members directly with `file(..., 'JSONAsString')`; Python does not parse filing or fact rows.
- Every source is loaded into isolated raw and normalized staging tables. Active mirrors remain unchanged if loading or validation fails.
- Validated staging tables replace active mirrors with `EXCHANGE TABLES`; the superseded tables and temporary raw JSON are dropped only after post-cutover row checks and snapshot-manifest activation succeed.
- A replacement whose row count is below 95% of the active mirror, or whose maximum filing date regresses, is rejected by default.
- `--limit-members` is diagnostic only: it validates bounded staging data and never performs a cutover (`--limit-ciks` remains a compatibility alias).
- It does not download or parse daily feed archives.
- It does not download accession `.txt` files yet.
- It stores `sec_bulk_mirror_submission_file_ref_v3` rows from parent CIK members.
- `companyfacts.zip` is exploded into one row per XBRL fact observation.
- `submissions.zip` is the complete filing-metadata authority: parent CIK members use `filings.recent`, while included `CIK##########-submissions-###.json` members use top-level filing arrays.
- Both submission member shapes write `sec_bulk_mirror_filing_v3` with `accepted_at_utc` parsed only from explicit timezone-bearing `acceptanceDateTime` values.
- The snapshot manifest records source identity, SHA-256, member count, staged rows, active rows, status, and failure reason. The former per-member resume manifest is no longer part of the active ingestion path.
- After a successful unbounded archive refresh, the obsolete `sec_bulk_mirror_member_manifest_v3` table is dropped.
- XBRL replacement tables include `fact_id` in their ordering key so distinct observations cannot collapse under `FINAL`.

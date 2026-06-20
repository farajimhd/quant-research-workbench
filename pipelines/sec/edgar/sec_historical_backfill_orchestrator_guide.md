# SEC Historical Backfill Orchestrator

`sec_historical_backfill_orchestrator.py` runs the current SEC historical pipeline in the same order as the successful manual workflow, but with one command, one run folder, one generated PowerShell plan, and one log file per stage.

It does not insert raw SEC archives into ClickHouse. Daily `.nc.tar.gz` archives stay on disk. ClickHouse receives bulk SEC metadata, filing parent rows, document metadata, normalized filing text, skip records, timestamp repairs, and audit outputs.

## Current Stage Order

Default gap-fill stages:

```text
daily-archive-download
validate-downloaded
text-extract
text-ingest-preflight
text-ingest-execute
timestamp-repair
integrity-audit
```

Initial fill preset:

```text
bulk-download
bulk-ingest
daily-archive-download
validate-downloaded
text-extract
text-ingest-preflight
text-ingest-execute
timestamp-repair
integrity-audit
```

Optional discovery stage:

```text
archive-content-discovery
```

Use discovery only when you want archive-format diagnostics. It is not required for normal filing text extraction.

## What Each Stage Runs

| Stage | Script | Purpose | Mutates DB |
| --- | --- | --- | --- |
| `bulk-download` | `sec_initial_fill_download.py` | Downloads `submissions.zip`, `companyfacts.zip`, and ticker mapping JSON files. | No |
| `bulk-ingest` | `sec_bulk_clickhouse_ingest.py` | Inserts SEC bulk mirror tables used for CIK, ticker, submission, and XBRL data. | Yes |
| `daily-archive-download` | `sec_daily_feed_archive_download.py` | Downloads daily `.nc.tar.gz` filing-content archives for the requested period. | No |
| `validate-downloaded` | `sec_validate_downloaded_archives.py` | Validates the downloaded/reused archives selected from the latest downloader manifest. | No |
| `archive-content-discovery` | `sec_archive_content_discovery.py` | Samples archive contents for diagnostics. | No |
| `text-extract` | `sec_filing_text_extract_parts.py` | Parses archives and writes DB-ready JSONEachRow part files. | No |
| `text-ingest-preflight` | `sec_filing_text_clickhouse_file_ingest.py` | Validates ClickHouse `file()` access and row counts for the part files. | No |
| `text-ingest-execute` | `sec_filing_text_clickhouse_file_ingest.py` | Inserts `sec_filing_v2`, `sec_filing_document_v2`, `sec_filing_text_v2`, and skip rows. | Yes |
| `timestamp-repair` | `sec_acceptance_fallback_submissions_repair.py` | Repairs date-only fallback `accepted_at_utc` rows from `submissions.zip`. | Yes |
| `integrity-audit` | `sec_integrity_audit.py` | Runs read-only integrity checks after loading. | No |

## Output

Each orchestrator run writes:

```text
D:\market-data\prepared\sec_historical_backfill_orchestrator\<run_id>\
  sec_historical_backfill_orchestrator_manifest.json
  sec_historical_backfill_orchestrator_plan.ps1
  sec_historical_backfill_orchestrator_results.jsonl
  sec_historical_backfill_orchestrator_summary.md
  logs\<stage>.log
```

If a stage fails, read `logs\<stage>.log` first. The orchestrator stops at the failed stage unless `--continue-on-error` is passed.

## Plan Only

Run this before any heavy run. It writes the exact child commands without executing them.

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_historical_backfill_orchestrator.py --start-date 2019-01-01 --end-date 2026-06-17
```

## Full Initial Fill

Use this when bulk SEC source files and ClickHouse bulk mirror tables also need to be refreshed:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_historical_backfill_orchestrator.py --start-date 2019-01-01 --end-date 2026-06-17 --stages initial-fill --execute
```

## Historical Gap Fill

Use this when bulk SEC metadata already exists and you need to fill a filing-content period:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_historical_backfill_orchestrator.py --start-date 2026-06-17 --end-date 2026-06-21 --execute
```

## Text-Only Continuation

Use this when archives already exist and you only want to rebuild/load filing text parts:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_historical_backfill_orchestrator.py --start-date 2019-01-01 --end-date 2026-06-17 --stages text-extract,text-ingest-preflight,text-ingest-execute,timestamp-repair,integrity-audit --execute
```

## Load Existing Text Parts

Use this when extraction already completed and you only need ClickHouse loading:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_historical_backfill_orchestrator.py --start-date 2019-01-01 --end-date 2026-06-17 --stages text-ingest-preflight,text-ingest-execute,integrity-audit --text-manifest-json D:/market-data/prepared/sec_filing_text_parts/<run_id>/sec_filing_text_extract_manifest.json --execute
```

## Smoke Test

This checks the command chain over one archive day with small limits:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_historical_backfill_orchestrator.py --start-date 2026-06-05 --end-date 2026-06-06 --stages archive-to-text --limit-days 1 --limit-archives 1 --max-filings-per-archive 50 --text-limit-parts 10 --execute
```

## Important Arguments

- `--stages`: comma-separated stage list, or preset `default`, `initial-fill`, `archive-to-text`, `all`.
- `--execute`: required to run child scripts. Without it, the orchestrator only writes a plan.
- `--continue-on-error`: continue after failed stages.
- `--artifact-root-win`: raw SEC artifact root. Default `D:/market-data/sec_core`.
- `--daily-archive-output-root-win`: downloader manifest root. Default `D:/market-data/prepared/sec_daily_feed_archives`.
- `--text-parts-output-root-win`: extractor output root. Default `D:/market-data/prepared/sec_filing_text_parts`.
- `--parts-root-win`: Windows prefix for ClickHouse `file()` part paths. Default `D:/market-data`.
- `--parts-root-ch`: ClickHouse server prefix matching `--parts-root-win`. Default `/mnt/d/market-data`.
- `--archive-download-concurrency`: default `2`.
- `--daily-archive-request-min-interval-seconds`: default `0.2`, matching the successful archive download history.
- `--text-extract-workers`: default `4`.
- `--pending-multiplier`: default `2` for text extraction and validation queues.
- `--sample-limit`: default `1000`.
- `--limit-days`, `--limit-archives`, `--max-filings-per-archive`, `--text-limit-parts`: smoke-test limits only.
- `--timestamp-limit-rows`, `--timestamp-limit-ciks`, `--timestamp-limit-zip-entries`: timestamp repair smoke-test limits.
- `--force-download`: redownload existing SEC source/archive files.
- `--allow-g-drive`: opt in to G-drive roots. By default stage scripts avoid accidental HDD writes where they enforce that rule.

## Lessons From Manual Runs

- Daily archives were downloaded separately from SEC bulk files. The current orchestrator keeps that separation.
- Corrupt archive repair was done by deleting exact failed archives, redownloading, then validating. The orchestrator handles redownload and validation; targeted deletion remains a manual repair step because it depends on a specific failed archive summary.
- The old acceptance header/download repair path is no longer the primary path. `sec_acceptance_fallback_submissions_repair.py` is the current repair stage because it uses local `submissions.zip` and avoids millions of SEC header requests.
- `sec_filing_document_v1` and `sec_filing_text_v1` are legacy. The current text path writes to v2 tables.
- The timestamp repair insert path batches by accepted month to avoid ClickHouse's `max_partitions_per_insert_block` error.

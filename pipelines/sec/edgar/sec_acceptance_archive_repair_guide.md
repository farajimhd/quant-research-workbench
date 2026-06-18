# SEC Acceptance Archive Repair

This script repairs `q_live.sec_filing_v2` rows whose acceptance timestamp came from a daily-archive date fallback instead of an exact EDGAR acceptance time.

It is designed for the issue created by archive-derived parent rows from the filing-text extractor:

- `archive_filing_date_midnight`
- `archive_date_midnight`
- `filing_date_midnight_fallback`

It also repairs rows marked `archive_acceptance_datetime` by converting the raw EDGAR `ACCEPTANCE-DATETIME` from `America/New_York` to UTC. This avoids treating EDGAR local time as UTC.

## What It Does

1. Selects local SEC daily `.nc.tar.gz` archives.
2. Queries `q_live.sec_filing_v2 FINAL` for candidate fallback rows on each archive date.
3. Opens matching local archive members.
4. Parses `ACCEPTANCE-DATETIME` from the filing container header.
5. Converts the timestamp from EDGAR Eastern time to UTC.
6. Writes replacement `sec_filing_v2` JSONEachRow parts.
7. Writes unresolved rows to a diagnostic file.
8. Optionally inserts replacement rows into ClickHouse.

The script does not use ClickHouse mutations. It inserts replacement rows into the existing `ReplacingMergeTree` table. Query with `FINAL` to see the repaired logical row.

## Dry Run

Use this first. It writes part files and diagnostics but does not insert into ClickHouse.

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_acceptance_archive_repair.py --archive-root-win D:/market-data/sec_core/daily_archives --output-root-win D:/market-data/prepared/sec_acceptance_archive_repair --start-date 2026-06-01 --end-date 2026-06-16 --archive-workers 4
```

## Execute

After reviewing the dry-run summary, insert replacement rows:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_acceptance_archive_repair.py --archive-root-win D:/market-data/sec_core/daily_archives --output-root-win D:/market-data/prepared/sec_acceptance_archive_repair --start-date 2026-06-01 --end-date 2026-06-16 --archive-workers 4 --execute
```

## Full Historical Repair

Run this on the workstation, not the laptop:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_acceptance_archive_repair.py --archive-root-win D:/market-data/sec_core/daily_archives --output-root-win D:/market-data/prepared/sec_acceptance_archive_repair --start-date 2019-01-01 --end-date 2026-06-16 --archive-workers 4 --execute
```

## Useful Arguments

- `--repair-sources`: comma-separated `accepted_at_source` values to repair. Defaults to `archive_acceptance_datetime,archive_filing_date_midnight,archive_date_midnight,filing_date_midnight_fallback`.
- `--source-run-id`: optional filter such as `sec_text_extract_20260617_141532`.
- `--limit-archives`: smoke-test cap on selected archives.
- `--limit-candidates-per-archive`: smoke-test cap on fallback rows per archive.
- `--rows-per-part`: replacement rows per JSONEachRow part. Default `50000`.
- `--archive-workers`: concurrent archive parsing workers.
- `--skip-insert`: build parts even when `--execute` is present, but do not insert.

## Outputs

Each run writes:

```text
D:/market-data/prepared/sec_acceptance_archive_repair/<run_id>/
```

Important files:

- `sec_acceptance_archive_repair_manifest.json`
- `sec_acceptance_archive_repair_summary.md`
- `archive_results.jsonl`
- `unresolved_rows.jsonl`
- `parts/sec_filing_v2_acceptance_repair_parts/*.jsonl`

Unresolved rows should stay excluded from timestamp-sensitive market-reaction training until another source, such as SEC submissions API or accession header download, repairs them.

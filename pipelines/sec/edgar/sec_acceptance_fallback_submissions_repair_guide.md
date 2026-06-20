# SEC Fallback Acceptance Submissions Repair

This script repairs `q_live.sec_filing_v2` rows whose `accepted_at_utc` is populated but only as a date-only fallback.

Targeted sources:

- `archive_filing_date_midnight`
- `archive_date_midnight`
- `filing_date_midnight_fallback`

It scans local SEC `submissions.zip`, including both main CIK JSON files and historical `CIK##########-submissions-###.json` fragments, then inserts replacement `sec_filing_v2` rows with exact `acceptanceDateTime` where available.

The script is separate from `sec_acceptance_archive_repair.py`. The archive repair proved that daily `.nc` archives usually do not contain `ACCEPTANCE-DATETIME`; this script uses submissions bulk instead.

## Dry Run

Run this first on the workstation. It writes candidate buckets, replacement part files, and unresolved diagnostics, but does not insert into ClickHouse.

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_acceptance_fallback_submissions_repair.py --artifact-root-win D:/market-data/sec_core --output-root-win D:/market-data/prepared/sec_acceptance_fallback_submissions_repair
```

## Execute

After reviewing the dry-run summary, insert the replacement rows:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_acceptance_fallback_submissions_repair.py --artifact-root-win D:/market-data/sec_core --output-root-win D:/market-data/prepared/sec_acceptance_fallback_submissions_repair --execute
```

## Smoke Test

Use a small row cap before a full run:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_acceptance_fallback_submissions_repair.py --artifact-root-win D:/market-data/sec_core --output-root-win D:/market-data/prepared/sec_acceptance_fallback_submissions_repair_smoke --limit-rows 10000
```

## Useful Arguments

- `--fallback-sources`: comma-separated `accepted_at_source` values to repair.
- `--submissions-zip-win`: explicit path to `submissions.zip` if not under `D:/market-data/sec_core/bulk/submissions/submissions.zip`.
- `--limit-rows`: cap fallback rows for a smoke test.
- `--limit-ciks`: cap distinct CIK buckets for a smoke test.
- `--limit-zip-entries`: cap SEC submissions zip entries scanned for a smoke test.
- `--rows-per-part`: replacement rows per JSONEachRow part. Default `50000`.
- `--insert-batch-size`: rows per ClickHouse insert batch. Default `50000`.
- `--row-progress-interval`: print/write row progress during very large CIK entries. Default `10000`.
- `--status-interval-seconds`: write `scan_status.json` at least this often while rows are moving. Default `30`.
- `--skip-insert`: build parts even when `--execute` is present, but do not insert.

## Outputs

Each run writes:

```text
D:/market-data/prepared/sec_acceptance_fallback_submissions_repair/<run_id>/
```

Important files:

- `sec_acceptance_fallback_submissions_repair_manifest.json`
- `sec_acceptance_fallback_submissions_repair_summary.md`
- `scan_status.json`
- `accepted_rows.jsonl`
- `unresolved_rows.jsonl`
- `source_results.jsonl`
- `parts/filing/part_*.jsonl`

## Validation Query

After execute, validate that fallback rows decreased and new repair sources exist:

```sql
SELECT accepted_at_source, count()
FROM q_live.sec_filing_v2 FINAL
GROUP BY accepted_at_source
ORDER BY count() DESC;
```

The remaining rows with date-only fallback sources should be much smaller before using SEC filings for timestamp-sensitive market-reaction labels.

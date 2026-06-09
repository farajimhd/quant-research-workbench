# SEC Acceptance Backfill Build Guide

Use `sec_acceptance_backfill_build.py` before migration Step 7. It does not mirror all SEC bulk files into ClickHouse. It builds only the accepted-timestamp source needed for current rows in `q_live.sec_filing_v2`.

## What It Does

1. Streams current missing filing keys from ClickHouse:

```sql
SELECT cik, accession_number
FROM q_live.sec_filing_v2 FINAL
WHERE accepted_at_utc IS NULL
```

2. Scans `submissions.zip` locally.

3. Extracts only matching SEC recent filing rows.

4. Saves local diagnostics:

- `accepted_rows.jsonl`
- `not_found_keys.jsonl`
- `not_found_ciks.jsonl`
- `sec_acceptance_backfill_manifest.json`
- `sec_acceptance_backfill_summary.md`

5. With `--execute`, creates and inserts into:

```text
sec_core.sec_bulk_mirror_filing_acceptance_v1
```

The table is partitioned by `cityHash64(cik) % 64`, not by month. The source rows can span many years in one batch, and monthly partitioning can exceed ClickHouse's `max_partitions_per_insert_block` limit.

The script does not add new rows to `q_live`. Step 7 performs the q_live replacement-row backfill after this source exists.

## Required Input

```text
D:\market-data\sec_core\bulk\submissions\submissions.zip
```

If the zip is somewhere else, pass `--submissions-zip-win`.

## Workstation Commands

Dry run with a small key sample:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\sec_acceptance_backfill_build.py --limit-missing-keys 10000 --artifact-root-win D:/market-data/sec_core --output-root-win D:/market-data/prepared/sec_acceptance_backfill
```

Execute with a small key sample:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\sec_acceptance_backfill_build.py --execute --limit-missing-keys 10000 --artifact-root-win D:/market-data/sec_core --output-root-win D:/market-data/prepared/sec_acceptance_backfill
```

Full execute:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\sec_acceptance_backfill_build.py --execute --artifact-root-win D:/market-data/sec_core --output-root-win D:/market-data/prepared/sec_acceptance_backfill
```

Then run Step 7:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\migration\step_07_backfill_sec_accepted_timestamps.py --execute --output-root-win D:/market-data/prepared/q_live_migration/step_07_sec_accepted_timestamps
```

## Useful Arguments

- `--limit-missing-keys`: smoke-test cap for q_live missing keys.
- `--limit-ciks`: smoke-test cap for CIK JSON files scanned inside `submissions.zip`.
- `--batch-size`: ClickHouse insert batch size, default `50000`.
- `--stage-database`: default `sec_core`.
- `--stage-table`: default `sec_bulk_mirror_filing_acceptance_v1`.
- `--submissions-zip-win`: explicit path to `submissions.zip`.
- `--execute`: writes the narrow source table. Without it, only local files are written.

## Interpreting Results

- `accepted_rows_written`: rows found in `submissions.zip` for current q_live missing keys.
- `accepted_rows_inserted`: valid accepted rows inserted into the narrow source table. This is zero in dry-run mode.
- `remaining_missing_rows`: current q_live missing keys not found in `submissions.zip` recent filings.
- `not_found_ciks.jsonl`: CIK-level summary used to decide whether older SEC submission fragments are needed.

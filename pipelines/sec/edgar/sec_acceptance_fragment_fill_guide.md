# SEC Acceptance Fragment Fill Guide

Use `sec_acceptance_fragment_fill.py` after `sec_acceptance_backfill_build.py` and before migration Step 7.

The first pass scans `submissions.zip` recent filings. This second pass fills the remaining rows by downloading only older SEC submission fragment JSON files referenced by each CIK's `filings.files` index.

## What It Does

1. Queries q_live rows still missing `accepted_at_utc` after the first pass.

2. Anti-joins against:

```text
sec_core.sec_bulk_mirror_filing_acceptance_v1
```

3. Reads `submissions.zip` locally to find older fragment filenames for the remaining CIKs.

4. Plans only fragments whose `filingFrom` / `filingTo` range overlaps the remaining filing dates.

5. Downloads fragment JSON files from:

```text
https://data.sec.gov/submissions/<fragment-name>.json
```

6. Parses matching accessions and appends valid accepted rows to:

```text
sec_core.sec_bulk_mirror_filing_acceptance_v1
```

7. Writes local diagnostics:

- `fragment_jobs.jsonl`
- `fragment_results.jsonl`
- `accepted_rows.jsonl`
- `still_not_found_keys.jsonl`
- `still_not_found_ciks.jsonl`
- `sec_acceptance_fragment_fill_manifest.json`
- `sec_acceptance_fragment_fill_summary.md`

## Workstation Commands

Dry run with a small fragment cap:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\sec_acceptance_fragment_fill.py --limit-fragments 25 --artifact-root-win D:/market-data/sec_core --output-root-win D:/market-data/prepared/sec_acceptance_fragment_fill
```

Execute a small fragment cap:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\sec_acceptance_fragment_fill.py --execute --limit-fragments 25 --artifact-root-win D:/market-data/sec_core --output-root-win D:/market-data/prepared/sec_acceptance_fragment_fill
```

Full execute:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\sec_acceptance_fragment_fill.py --execute --artifact-root-win D:/market-data/sec_core --output-root-win D:/market-data/prepared/sec_acceptance_fragment_fill
```

Then rerun Step 7 dry-run. If candidate rows equal the staged source count and remaining missing is acceptable, run Step 7 execute.

## Useful Arguments

- `--download-workers`: concurrent fragment workers, default `8`.
- `--sec-request-min-interval-seconds`: global request interval, default `0.11`, which stays below SEC's 10 requests/second guidance.
- `--limit-fragments`: smoke-test cap.
- `--download-all-fragments-per-cik`: ignore date ranges and download every older fragment for each remaining CIK.
- `--force-redownload`: redownload fragments even if already saved.
- `--execute`: inserts matched rows into the staging table. Without it, files are downloaded/parsed and local diagnostics are written, but no ClickHouse insert happens.

## Expected Runtime

Runtime depends on how many fragment files are needed. At SEC fair-access speed, a few thousand fragments should usually finish in minutes, plus parsing and ClickHouse insert time. If the still-missing set falls back to accession-level header files later, that pass will be slower.

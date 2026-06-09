# SEC Acceptance Header Fill Guide

Use `sec_acceptance_header_fill.py` after:

1. `sec_acceptance_backfill_build.py`
2. `sec_acceptance_fragment_fill.py`

This is the final fallback for the small set of q_live filings still missing SEC accepted timestamps.

## What It Does

1. Queries q_live filings still missing `accepted_at_utc`.

2. Anti-joins against:

```text
sec_core.sec_bulk_mirror_filing_acceptance_v1
```

3. Fetches each remaining accession header:

```text
https://www.sec.gov/Archives/edgar/data/<cik>/<accession_compact>/<accession>.hdr.sgml
```

4. Parses:

```text
<ACCEPTANCE-DATETIME>YYYYMMDDHHMMSS
```

5. Converts that Eastern Time timestamp to UTC.

6. Appends valid rows to:

```text
sec_core.sec_bulk_mirror_filing_acceptance_v1
```

7. Writes local diagnostics:

- `header_jobs.jsonl`
- `header_results.jsonl`
- `accepted_rows.jsonl`
- `still_not_found_keys.jsonl`
- `sec_acceptance_header_fill_manifest.json`
- `sec_acceptance_header_fill_summary.md`

## Workstation Commands

Dry run with a small accession cap:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\sec_acceptance_header_fill.py --limit-accessions 25 --artifact-root-win D:/market-data/sec_core --output-root-win D:/market-data/prepared/sec_acceptance_header_fill
```

Execute with a small accession cap:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\sec_acceptance_header_fill.py --execute --limit-accessions 25 --artifact-root-win D:/market-data/sec_core --output-root-win D:/market-data/prepared/sec_acceptance_header_fill
```

Full execute:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\sec_acceptance_header_fill.py --execute --artifact-root-win D:/market-data/sec_core --output-root-win D:/market-data/prepared/sec_acceptance_header_fill
```

Then rerun Step 7 dry-run. If counts look correct, run Step 7 execute.

## Useful Arguments

- `--download-workers`: concurrent header workers, default `8`.
- `--sec-request-min-interval-seconds`: global request interval, default `0.11`, below SEC's 10 requests/second guidance.
- `--limit-accessions`: smoke-test cap.
- `--force-redownload`: redownload headers even if already saved.
- `--execute`: inserts matched rows into the staging table. Without it, headers are downloaded/parsed and local diagnostics are written, but no ClickHouse insert happens.

## Expected Runtime

For about 1,000 remaining accessions, expect a few minutes at the default rate limit plus network and retry time.

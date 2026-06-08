# SEC Historical Feed Download Guide

This script handles the historical SEC raw filing source that complements live SEC RSS.

It has two separate modes:

1. **Raw archive download**: save daily `.nc.tar.gz` archives only. No decompression, parsing, or header timestamp fetch.
2. **Parse/enrich**: stream `.nc` members from the compressed archive, parse SGML submission containers, and fetch small `.hdr.sgml` files for `accepted_at`.

By default, parse/enrich mode does **not** expand all `.nc` files to disk. It streams each member from the `.tar.gz`. Use `--persist-nc-files` only when you intentionally want individual `.nc` artifacts written to disk.

## Required Environment

Set a descriptive SEC user agent before production runs:

```powershell
$env:SEC_USER_AGENT="QuantResearchWorkbench SEC historical ingest your_email@example.com"
```

The script also loads `.env` from the repo through shared MLOps environment discovery. It never prints secret values.

## Script Path

Laptop repo:

```powershell
python D:\TradingCodes\quant-research-workbench\research\mlops\sec_historical_feed_download.py
```

Bounded pipeline script:

```powershell
python D:\TradingCodes\quant-research-workbench\research\mlops\sec_historical_feed_pipeline.py
```

Workstation mirror:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\sec_historical_feed_download.py
```

Workstation bounded pipeline:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\sec_historical_feed_pipeline.py
```

## What Gets Written

Compressed raw archives:

```text
<SEC_HISTORICAL_ARTIFACT_ROOT_WIN>\archives\YYYY\QTRN\YYYYMMDD.nc.tar.gz
```

Header timestamp artifacts:

```text
<archive folder>\_headers\<accession>.hdr.sgml
```

Run outputs:

```text
<SEC_HISTORICAL_OUTPUT_ROOT_WIN>\sec_feed_historical_<run_id>.jsonl
<SEC_HISTORICAL_OUTPUT_ROOT_WIN>\sec_feed_submissions_<run_id>.jsonl
<SEC_HISTORICAL_OUTPUT_ROOT_WIN>\sec_feed_documents_<run_id>.jsonl
<SEC_HISTORICAL_OUTPUT_ROOT_WIN>\sec_feed_headers_<run_id>.jsonl
```

These are JSONL outputs for inspection and schema finalization. The script does not insert into ClickHouse yet.

The bounded pipeline also stores per-day normalized data:

```text
<SEC_HISTORICAL_NORMALIZED_ROOT_WIN>\YYYY\QTRN\YYYY-MM-DD\submissions.jsonl
<SEC_HISTORICAL_NORMALIZED_ROOT_WIN>\YYYY\QTRN\YYYY-MM-DD\documents.jsonl
<SEC_HISTORICAL_NORMALIZED_ROOT_WIN>\YYYY\QTRN\YYYY-MM-DD\headers.jsonl
<SEC_HISTORICAL_NORMALIZED_ROOT_WIN>\YYYY\QTRN\YYYY-MM-DD\manifest.jsonl
```

## Raw Archive Download Only

This is the safest first pass. It downloads compressed daily archives and stops.
`--archive-concurrency` applies in this mode.
The script first reads the SEC quarterly Feed directory listings and creates jobs only for archive files that actually exist. Weekends, holidays, and missing archive dates are not guessed as failed jobs.

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\sec_historical_feed_download.py --start-date 2026-06-01 --end-date 2026-06-08 --download-only --archive-concurrency 2
```

## Bounded Download + Normalize Pipeline

Use this when you want to keep compressed archives on HDD and write normalized JSONL as each day finishes. Downloads and parsing overlap, but each archive is parsed only after its `.nc.tar.gz` download is complete.

The pipeline keeps at most roughly `--download-concurrency` archive downloads ahead of parsing, so it does not require downloading the entire historical range before normalized output starts.

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\sec_historical_feed_pipeline.py --start-date 2020-01-01 --end-date 2026-06-08 --download-concurrency 2 --header-concurrency 8 --sec-request-min-interval-seconds 0.11
```

Recommended smoke test:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\sec_historical_feed_pipeline.py --start-date 2026-06-05 --end-date 2026-06-06 --download-concurrency 1 --header-concurrency 4 --sec-request-min-interval-seconds 0.11 --limit-files-per-day 20
```

Delete compressed archives only after a day parses successfully:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\sec_historical_feed_pipeline.py --start-date 2020-01-01 --end-date 2026-06-08 --download-concurrency 2 --header-concurrency 8 --sec-request-min-interval-seconds 0.11 --delete-archive-after-parse
```

## Parse From Compressed Archives

This downloads archives if missing, streams `.nc` files from the compressed archive, parses filing/document metadata, and fetches `.hdr.sgml` for `accepted_at`.
Parse/enrich mode processes archive days one at a time so `--header-concurrency` and `--sec-request-min-interval-seconds` remain the effective SEC request controls across the run.

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\sec_historical_feed_download.py --start-date 2026-06-05 --end-date 2026-06-06 --archive-concurrency 1 --header-concurrency 8 --sec-request-min-interval-seconds 0.11
```

## Persist Extracted `.nc` Files

Use this only when you want expanded `.nc` files on disk for manual inspection or downstream artifact retention.

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\sec_historical_feed_download.py --start-date 2026-06-05 --end-date 2026-06-06 --archive-concurrency 1 --header-concurrency 8 --sec-request-min-interval-seconds 0.11 --persist-nc-files
```

## Local Smoke Test With Already Extracted Folder

This uses the folder you already extracted and parses only a small number of files.

```powershell
python D:\TradingCodes\quant-research-workbench\research\mlops\sec_historical_feed_download.py --existing-extracted-dir C:\Users\g835l\Downloads\20260605.nc.tar\20260605.nc --existing-archive-date 2026-06-05 --limit-files-per-day 20 --header-concurrency 4 --sec-request-min-interval-seconds 0.11
```

Skip SEC header fetch if you only want to validate local `.nc` parsing:

```powershell
python D:\TradingCodes\quant-research-workbench\research\mlops\sec_historical_feed_download.py --existing-extracted-dir C:\Users\g835l\Downloads\20260605.nc.tar\20260605.nc --existing-archive-date 2026-06-05 --limit-files-per-day 20 --no-header-fetch
```

## Important Behavior

- The daily archive is the content source.
- `.hdr.sgml` is the timestamp authority for `accepted_at`.
- `accepted_at_edgar_raw` is parsed as EDGAR Eastern time and converted to `accepted_at_utc`.
- The archive date is not used as the event timestamp.
- `FILING-DATE` is stored but should not be used for market-reaction labels.
- The script rate-limits SEC requests with `--sec-request-min-interval-seconds`. Use `0.11` or slower for production to stay below SEC's 10 requests/second guidance.

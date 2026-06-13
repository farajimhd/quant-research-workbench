# SEC Historical Feed Download Guide

This script handles the historical SEC raw filing source that complements live SEC RSS.

It has two separate modes:

1. **Raw archive download**: save daily `.nc.tar.gz` archives only. No decompression, parsing, or header timestamp fetch.
2. **Parse/enrich**: stage the compressed archive on SSD, keep a verified compressed archive on HDD, stream `.nc` members from the SSD archive, parse SGML submission containers, and fetch small `.hdr.sgml` files for `accepted_at`.

By default, parse/enrich mode does **not** expand all `.nc` files to disk. It streams each member from the `.tar.gz`. Use `--persist-nc-files` only when you intentionally want individual `.nc` artifacts written to disk.

The bounded pipeline is designed for HDD archival plus SSD parsing. It downloads each daily archive to `SEC_HISTORICAL_TEMP_ROOT_WIN`, asynchronously copies the compressed archive to `SEC_HISTORICAL_ARTIFACT_ROOT_WIN`, parses from the SSD copy, writes normalized JSONL to `SEC_HISTORICAL_NORMALIZED_ROOT_WIN`, then deletes the SSD temp archive after parsing and HDD copy verification succeed.

Before an archive is parsed or reused from cache, the bounded pipeline validates that the `.tar.gz` is complete and contains `.nc` members. If an existing SSD temp archive or HDD archive is truncated/corrupt, it is deleted and the pipeline fetches a fresh copy from SEC.

## Required Environment

Set a descriptive SEC user agent before production runs:

```powershell
$env:SEC_USER_AGENT="QuantResearchWorkbench SEC historical ingest your_email@example.com"
```

The script also loads `.env` from the repo through shared MLOps environment discovery. It never prints secret values.

Recommended workstation roots:

```powershell
$env:SEC_HISTORICAL_ARTIFACT_ROOT_WIN="G:/market-data/sec_edgar_feed"
$env:SEC_HISTORICAL_OUTPUT_ROOT_WIN="G:/market-data/prepared/sec_edgar_feed"
$env:SEC_HISTORICAL_NORMALIZED_ROOT_WIN="D:/market-data/sec_edgar_feed_normalized"
$env:SEC_HISTORICAL_TEMP_ROOT_WIN="D:/market-data/sec_edgar_feed_temp"
```

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

Temporary SSD archives used by the bounded pipeline:

```text
<SEC_HISTORICAL_TEMP_ROOT_WIN>\archives\YYYY\QTRN\YYYYMMDD.nc.tar.gz
```

These SSD temp archives are deleted after normalized output is written and the permanent HDD archive is verified. They are not the source of truth.

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

The bounded pipeline also stores per-day normalized data. Put this root on SSD; these are the final normalized training inputs.

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

Use this when you want to keep compressed archives on HDD and write normalized JSONL as each day finishes. Downloads, HDD copy, and parsing overlap, but each archive is parsed only after its `.nc.tar.gz` is fully staged on SSD.

The pipeline keeps at most roughly `--download-concurrency` archive downloads ahead of parsing, so it does not require downloading the entire historical range before normalized output starts.

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\sec_historical_feed_pipeline.py --start-date 2020-01-01 --end-date 2026-06-08 --download-concurrency 2 --archive-copy-concurrency 1 --header-concurrency 8 --sec-request-min-interval-seconds 0.11 --progress-interval-seconds 10 --progress-file-interval-mib 64 --progress-record-interval 500
```

If the raw archives were created by `sec_daily_feed_archive_download.py`, they live under `sec_core\daily_archives` instead of `sec_core\archives`. In that case pass `--archive-subdir daily_archives` so the parser reuses the completed archive set instead of downloading a second copy:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\sec_historical_feed_pipeline.py --start-date 2019-01-01 --end-date 2026-06-11 --artifact-root-win D:/market-data/sec_core --archive-subdir daily_archives --temp-root-win D:/market-data/sec_edgar_feed_temp --normalized-root-win D:/market-data/sec_edgar_feed_normalized --output-root-win D:/market-data/prepared/sec_edgar_feed --download-concurrency 2 --archive-copy-concurrency 1 --header-concurrency 8 --sec-request-min-interval-seconds 0.11 --progress-interval-seconds 10 --progress-file-interval-mib 64 --progress-record-interval 500
```

Recommended dry-run smoke test. This validates discovery/config without writing partial normalized data:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\sec_historical_feed_pipeline.py --start-date 2026-06-05 --end-date 2026-06-06 --download-concurrency 1 --archive-copy-concurrency 1 --header-concurrency 4 --sec-request-min-interval-seconds 0.11 --progress-interval-seconds 5 --progress-file-interval-mib 16 --progress-record-interval 500 --dry-run
```

Recommended full one-day pipeline smoke test. This parses every filing in the selected daily archive:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\sec_historical_feed_pipeline.py --start-date 2026-06-05 --end-date 2026-06-06 --download-concurrency 1 --archive-copy-concurrency 1 --header-concurrency 4 --sec-request-min-interval-seconds 0.11 --progress-interval-seconds 10 --progress-file-interval-mib 64 --progress-record-interval 500
```

Delete permanent HDD compressed archives only after a day parses successfully:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\sec_historical_feed_pipeline.py --start-date 2020-01-01 --end-date 2026-06-08 --download-concurrency 2 --archive-copy-concurrency 1 --header-concurrency 8 --sec-request-min-interval-seconds 0.11 --progress-interval-seconds 10 --progress-file-interval-mib 64 --progress-record-interval 500 --delete-archive-after-parse
```

Progress controls:

- `--progress-interval-seconds`: maximum quiet time before printing progress inside a long step. Default: `10`.
- `--progress-file-interval-mib`: byte interval for archive download/copy progress. Default: `64`.
- `--progress-record-interval`: `.nc` member or header-record interval for validation, parsing, and header fetch progress. Default: `500`.
- `--download-progress-bars` / `--no-download-progress-bars`: enable or disable tqdm archive download bars in text fallback mode. Rich layout always uses the structured matrix cell for downloads. Default: disabled, to avoid tqdm/log interleaving when Rich is unavailable.
- `--progress-layout auto|rich|text`: `auto` uses a Rich two-panel console when Rich is installed. The top panel is a fixed worker-slot matrix: each download worker has one row, and the fixed columns are `Download`, `Validate`, `Copy`, `Parse`, and `Headers`. The bottom panel holds ordered logs. Use `text` for plain console output.
- `--progress-log-lines`: number of log lines retained in the Rich log panel. Default: `24`.
- `--progress-panel-rows`: fixed height for the top Rich progress panel. Default: `12`; increase it if you run many concurrent days and want more visible rows.
- `--progress-screen` / `--no-progress-screen`: `--progress-screen` is the default and pins the Rich layout to a fixed live screen so validation and download log messages cannot push the matrix upward. Use `--no-progress-screen` only when you specifically want normal terminal scrollback.
- `--header-max-retries`: retry count for `.hdr.sgml` accepted-time probes only. Default: `1`; archive downloads still use `--max-retries`.
- `--header-retry-base-seconds`: retry backoff base for `.hdr.sgml` probes only. Default: `0.5`.
- `--header-failure-breaker-threshold`: stop scheduling more header probes for a day after this many retryable header failures and no successes. Default: `4`; set `0` to disable.

The fixed five-column progress matrix requires Rich in the active Python environment. If Rich is not installed and `--progress-layout auto` is used, the script falls back to throttled text progress and disables tqdm download bars by default so progress lines do not corrupt each other. Install Rich in the workstation environment when you want the matrix:

```powershell
conda run -n ml4t python -m pip install rich
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

This uses the folder you already extracted and parses every `.nc` file in it.

```powershell
python D:\TradingCodes\quant-research-workbench\research\mlops\sec_historical_feed_download.py --existing-extracted-dir C:\Users\g835l\Downloads\20260605.nc.tar\20260605.nc --existing-archive-date 2026-06-05 --header-concurrency 4 --sec-request-min-interval-seconds 0.11
```

Skip SEC header fetch if you only want to validate local `.nc` parsing:

```powershell
python D:\TradingCodes\quant-research-workbench\research\mlops\sec_historical_feed_download.py --existing-extracted-dir C:\Users\g835l\Downloads\20260605.nc.tar\20260605.nc --existing-archive-date 2026-06-05 --no-header-fetch
```

## Important Behavior

- The daily archive is the content source.
- In the bounded pipeline, SSD temp archives are working files and HDD archives are the retained compressed source-of-truth artifacts.
- Final normalized JSONL files should be written on SSD. Keep the compressed `.nc.tar.gz` archive on HDD and set `SEC_HISTORICAL_NORMALIZED_ROOT_WIN` to an SSD path such as `D:/market-data/sec_edgar_feed_normalized`.
- Existing archive files are integrity-checked before reuse. Corrupt temp/HDD archives are removed and redownloaded.
- The runnable historical ingest scripts do not expose a per-day filing cap. Every `.nc` filing in each selected daily archive is parsed so normalized output is complete for that day.
- The bounded pipeline reports every active archive date through the same fixed stages: `Download`, `Validate`, `Copy`, `Parse`, and `Headers`.
- With the Rich layout, `--download-concurrency 5` creates five fixed worker rows. Each row keeps the same five stage columns while its assigned archive day moves through the pipeline.
- Stages with a known total, such as byte downloads and copies, show a bounded progress bar. Stages whose total is discovered while running, such as archive validation, show a stable running cell with elapsed time and the latest count.
- The lower Rich panel is reserved for messages, ordered oldest to newest within the retained log window.
- Header timestamp probes have their own retry policy. This prevents repeated `.hdr.sgml` failures from consuming the same retry budget intended for large archive downloads.
- `.hdr.sgml` is the timestamp authority for `accepted_at`.
- Some SEC feed entries do not expose a separate `.hdr.sgml` sidecar even though the filing directory is listed. Those rows are retained and marked with `timestamp_fetch_status="unavailable_404"` rather than counted as request failures.
- `accepted_at_edgar_raw` is parsed as EDGAR Eastern time and converted to `accepted_at_utc`.
- Normalized submission rows include `event_time_utc`, `event_time_source`, `event_time_quality`, and `market_label_eligible`. Only rows with `event_time_quality="exact_sec_acceptance"` and `market_label_eligible=true` should be used for market-reaction labels.
- The archive date is not used as the event timestamp.
- `FILING-DATE` is stored but should not be used for market-reaction labels.
- The script rate-limits SEC requests with `--sec-request-min-interval-seconds`. Use `0.11` or slower for production to stay below SEC's 10 requests/second guidance.

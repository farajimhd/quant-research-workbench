# SEC Initial Fill Source Download Guide

Use `sec_initial_fill_download.py` to fetch the raw SEC source artifacts needed before building the `sec_core` database. This script only downloads files and writes manifests. It does not parse files and does not insert into ClickHouse.

## What It Downloads

Bulk sources:

- `submissions.zip`: filing history and accepted timestamps.
- `companyfacts.zip`: XBRL company facts.
- `company_tickers.json`: CIK, ticker, and company-name mapping.
- `company_tickers_exchange.json`: CIK, ticker, exchange, and company-name mapping.
- `company_tickers_mf.json`: mutual fund CIK, series, class, and ticker mapping.

Fallback-only daily archives:

- `YYYYMMDD.nc.tar.gz` SEC daily filing-content archives from `Archives/edgar/Feed/YYYY/QTRn/`.
- Do not download these for the normal historical backfill. The default path is bulk metadata first, then selected accession `.txt` files.

## Recommended Roots

For workstation runs, keep raw SEC artifacts on an SSD-backed market-data folder:

```powershell
$env:SEC_CORE_ARTIFACT_ROOT_WIN="D:/market-data/sec_core"
$env:SEC_CORE_OUTPUT_ROOT_WIN="D:/market-data/prepared/sec_core"
$env:SEC_USER_AGENT="QuantResearchWorkbench SEC initial fill your_email@example.com"
```

`SEC_CORE_ARTIFACT_ROOT_WIN` stores raw downloaded SEC inputs. `SEC_CORE_OUTPUT_ROOT_WIN` stores manifests and run summaries.

## Script Paths

Laptop source of truth:

```powershell
python D:\TradingCodes\quant-research-workbench\pipelines\sec\edgar\sec_initial_fill_download.py
```

Workstation mirror:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\pipelines\sec\edgar\sec_initial_fill_download.py
```

## Safe Dry Run

Use this first to confirm paths and planned URLs without downloading:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\pipelines\sec\edgar\sec_initial_fill_download.py --dry-run --artifact-root-win D:/market-data/sec_core --output-root-win D:/market-data/prepared/sec_core
```

## Phase 1A: Download Bulk Sources

Run this first. It downloads the required SEC bulk inputs for company identity, ticker mapping, accepted timestamps, and XBRL facts:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\pipelines\sec\edgar\sec_initial_fill_download.py --artifact-root-win D:/market-data/sec_core --output-root-win D:/market-data/prepared/sec_core --download-concurrency 2 --sec-request-min-interval-seconds 0.11 --progress-layout auto
```

Expected raw output:

```text
D:\market-data\sec_core\bulk\submissions\submissions.zip
D:\market-data\sec_core\bulk\companyfacts\companyfacts.zip
D:\market-data\sec_core\bulk\mappings\company_tickers.json
D:\market-data\sec_core\bulk\mappings\company_tickers_exchange.json
D:\market-data\sec_core\bulk\mappings\company_tickers_mf.json
```

Expected manifest output:

```text
D:\market-data\prepared\sec_core\sec_initial_fill_sources_<run_id>.jsonl
D:\market-data\prepared\sec_core\sec_initial_fill_summary_<run_id>.json
```

## Fallback Only: Download Daily Filing Archives

Do not run this for normal historical backfill. Use it only for a narrow fallback/debug date range when selected accession `.txt` retrieval cannot answer a specific reconciliation problem. The script first discovers available SEC archive days from the quarterly directory listings, so weekends and holidays are not treated as failed downloads.

Small smoke test:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\pipelines\sec\edgar\sec_initial_fill_download.py --sources none --include-daily-archives --start-date 2026-06-05 --end-date 2026-06-06 --artifact-root-win D:/market-data/sec_core --output-root-win D:/market-data/prepared/sec_core --download-concurrency 1 --sec-request-min-interval-seconds 0.11 --progress-layout auto
```

Avoid full historical archive downloads. They are very large and not the selected strategy for this project. If you intentionally need a bounded fallback range, keep the date range small:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\pipelines\sec\edgar\sec_initial_fill_download.py --sources none --include-daily-archives --start-date 2026-06-05 --end-date 2026-06-06 --artifact-root-win D:/market-data/sec_core --output-root-win D:/market-data/prepared/sec_core --download-concurrency 1 --sec-request-min-interval-seconds 1.0 --progress-layout auto
```

Expected daily archive output:

```text
D:\market-data\sec_core\daily_archives\YYYY\QTRN\YYYYMMDD.nc.tar.gz
```

## Download Everything In One Run

This downloads bulk sources plus daily archives. This is not recommended for the current SEC historical strategy; use it only for an intentional archive-mirroring experiment:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\pipelines\sec\edgar\sec_initial_fill_download.py --sources all --include-daily-archives --start-date 2026-06-05 --end-date 2026-06-06 --artifact-root-win D:/market-data/sec_core --output-root-win D:/market-data/prepared/sec_core --download-concurrency 1 --sec-request-min-interval-seconds 1.0 --progress-layout auto
```

## Arguments

- `--artifact-root-win`: raw artifact root. Defaults to `SEC_CORE_ARTIFACT_ROOT_WIN`, then `SEC_HISTORICAL_ARTIFACT_ROOT_WIN`, then `D:/market-data/sec_core`.
- `--output-root-win`: manifest/report root. Defaults to `SEC_CORE_OUTPUT_ROOT_WIN`, then `SEC_HISTORICAL_OUTPUT_ROOT_WIN`, then `D:/market-data/prepared/sec_core`.
- `--allow-g-drive`: opt-in override for G-drive paths. By default the script refuses `G:` and `\\DESKTOP-SAAI85T\Workstation-G\...` roots before any download starts.
- `--sources`: comma-separated bulk sources. Valid values are `submissions`, `companyfacts`, `company_tickers`, `company_tickers_exchange`, `company_tickers_mf`, `all`, and `none`.
- `--include-daily-archives`: enables daily `.nc.tar.gz` archive download.
- `--start-date`: inclusive daily archive start date.
- `--end-date`: exclusive daily archive end date.
- `--limit-days`: smoke-test cap after archive-day discovery.
- `--download-concurrency`: concurrent download workers. The global SEC request limiter still applies.
- `--sec-request-min-interval-seconds`: minimum spacing between SEC request starts. Use `0.25` or slower for large daily archive backfills. `0.11` is the theoretical 10 requests/second boundary and may still be too aggressive when many downloads are active.
- `--request-timeout-seconds`: socket timeout for each request.
- `--max-retries`: retry count for retryable HTTP/network failures.
- `--retry-base-seconds`: exponential backoff base for retries.
- `--max-429-before-stop`: number of SEC HTTP 429 responses allowed before stopping queued work. Default: `1`.
- `--stop-on-429` / `--continue-on-429`: default is `--stop-on-429`. Use `--continue-on-429` only for a deliberate retry experiment.
- `--progress-interval-seconds`: progress print interval for large streaming downloads.
- `--progress-layout`: `auto`, `rich`, or `text`. `auto` uses Rich when installed and falls back to text otherwise.
- `--progress-log-lines`: retained message lines in the Rich message panel.
- `--progress-refresh-per-second`: Rich live-render refresh rate.
- `--progress-screen` / `--no-progress-screen`: keep the Rich progress display in a fixed terminal screen or let it scroll in normal output. Fixed screen is the default.
- `--force`: redownload existing artifacts.
- `--dry-run`: write a planned manifest without downloading.

## Important Behavior

- Existing files are reused unless `--force` is passed.
- Every completed or reused file is hashed with SHA-256 and recorded in the manifest.
- Large files are streamed to unique per-attempt partial names and atomically moved into place only after the download completes. This avoids collisions with stale or locked `.part` files from earlier interrupted runs.
- A failed download removes its own partial file when possible and records the failure in the manifest.
- SEC HTTP 429 is treated as a run-level throttle event by default. The script stops queued work, removes partial files for stopped workers, records stopped rows in the manifest, writes summary status `stopped`, and exits with code `2`.
- A normal request failure still records `failed`, writes summary status `failed`, and exits with code `1`.
- The downloader uses a bounded queue. It submits at most `--download-concurrency` active jobs, then schedules the next source only after a worker finishes. If SEC returns 429, unscheduled sources are marked `stopped_before_start` instead of creating thousands of cancelled futures.
- With Rich installed, the top panel reports overall source count, active workers, elapsed time, completed/reused/failed counts, and total completed bytes.
- With Rich installed, each active worker gets a fixed row showing source, status, byte progress, size, rate, attempt number, elapsed time, and message.
- The lower Rich panel is reserved for retry, completion, and summary messages.
- If Rich is not installed and `--progress-layout auto` is used, the script falls back to plain text completion and retry messages.
- This script intentionally does not parse `.zip` or `.nc.tar.gz` files. Parsing and database insertion are Phase 3.

## 429 Recovery

If SEC returns 429, wait before restarting. The manifest tells you which files were already `downloaded` or `reused`; rerunning without `--force` skips those files and resumes the remaining sources.

Recommended restart after a 429:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\pipelines\sec\edgar\sec_initial_fill_download.py --sources none --include-daily-archives --start-date 2026-06-05 --end-date 2026-06-06 --artifact-root-win D:/market-data/sec_core --output-root-win D:/market-data/prepared/sec_core --download-concurrency 1 --sec-request-min-interval-seconds 1.0 --progress-layout auto
```

Older interrupted runs may leave `*.part` files in the raw artifact tree. New runs ignore those stale partials. Delete them only after confirming no downloader process is running.

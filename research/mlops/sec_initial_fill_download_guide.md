# SEC Initial Fill Source Download Guide

Use `sec_initial_fill_download.py` to fetch the raw SEC source artifacts needed before building the `sec_core` database. This script only downloads files and writes manifests. It does not parse files and does not insert into ClickHouse.

## What It Downloads

Bulk sources:

- `submissions.zip`: filing history and accepted timestamps.
- `companyfacts.zip`: XBRL company facts.
- `company_tickers.json`: CIK, ticker, and company-name mapping.
- `company_tickers_exchange.json`: CIK, ticker, exchange, and company-name mapping.
- `company_tickers_mf.json`: mutual fund CIK, series, class, and ticker mapping.

Optional daily archives:

- `YYYYMMDD.nc.tar.gz` SEC daily filing-content archives from `Archives/edgar/Feed/YYYY/QTRn/`.

## Recommended Roots

For workstation runs, keep raw SEC artifacts on the HDD-backed market-data folder:

```powershell
$env:SEC_CORE_ARTIFACT_ROOT_WIN="G:/market-data/sec_core"
$env:SEC_CORE_OUTPUT_ROOT_WIN="G:/market-data/prepared/sec_core"
$env:SEC_USER_AGENT="QuantResearchWorkbench SEC initial fill your_email@example.com"
```

`SEC_CORE_ARTIFACT_ROOT_WIN` stores raw downloaded SEC inputs. `SEC_CORE_OUTPUT_ROOT_WIN` stores manifests and run summaries.

## Script Paths

Laptop source of truth:

```powershell
python D:\TradingCodes\quant-research-workbench\research\mlops\sec_initial_fill_download.py
```

Workstation mirror:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\sec_initial_fill_download.py
```

## Safe Dry Run

Use this first to confirm paths and planned URLs without downloading:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\sec_initial_fill_download.py --dry-run --artifact-root-win G:/market-data/sec_core --output-root-win G:/market-data/prepared/sec_core
```

## Phase 1A: Download Bulk Sources

Run this first. It downloads the required SEC bulk inputs for company identity, ticker mapping, accepted timestamps, and XBRL facts:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\sec_initial_fill_download.py --artifact-root-win G:/market-data/sec_core --output-root-win G:/market-data/prepared/sec_core --download-concurrency 2 --sec-request-min-interval-seconds 0.11
```

Expected raw output:

```text
G:\market-data\sec_core\bulk\submissions\submissions.zip
G:\market-data\sec_core\bulk\companyfacts\companyfacts.zip
G:\market-data\sec_core\bulk\mappings\company_tickers.json
G:\market-data\sec_core\bulk\mappings\company_tickers_exchange.json
G:\market-data\sec_core\bulk\mappings\company_tickers_mf.json
```

Expected manifest output:

```text
G:\market-data\prepared\sec_core\sec_initial_fill_sources_<run_id>.jsonl
G:\market-data\prepared\sec_core\sec_initial_fill_summary_<run_id>.json
```

## Phase 1B: Download Daily Filing Archives

Run this when you are ready to fetch historical filing-content archives. The script first discovers available SEC archive days from the quarterly directory listings, so weekends and holidays are not treated as failed downloads.

Small smoke test:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\sec_initial_fill_download.py --sources none --include-daily-archives --start-date 2026-06-05 --end-date 2026-06-06 --artifact-root-win G:/market-data/sec_core --output-root-win G:/market-data/prepared/sec_core --download-concurrency 1 --sec-request-min-interval-seconds 0.11
```

Full historical archive download from 2020 through today:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\sec_initial_fill_download.py --sources none --include-daily-archives --start-date 2020-01-01 --end-date 2026-06-09 --artifact-root-win G:/market-data/sec_core --output-root-win G:/market-data/prepared/sec_core --download-concurrency 2 --sec-request-min-interval-seconds 0.11
```

Expected daily archive output:

```text
G:\market-data\sec_core\daily_archives\YYYY\QTRN\YYYYMMDD.nc.tar.gz
```

## Download Everything In One Run

This downloads bulk sources plus daily archives. Use it only when you intentionally want both phases in one run:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\sec_initial_fill_download.py --sources all --include-daily-archives --start-date 2020-01-01 --end-date 2026-06-09 --artifact-root-win G:/market-data/sec_core --output-root-win G:/market-data/prepared/sec_core --download-concurrency 2 --sec-request-min-interval-seconds 0.11
```

## Arguments

- `--artifact-root-win`: raw artifact root. Defaults to `SEC_CORE_ARTIFACT_ROOT_WIN`, then `SEC_HISTORICAL_ARTIFACT_ROOT_WIN`, then `G:/market-data/sec_core`.
- `--output-root-win`: manifest/report root. Defaults to `SEC_CORE_OUTPUT_ROOT_WIN`, then `SEC_HISTORICAL_OUTPUT_ROOT_WIN`, then `G:/market-data/prepared/sec_core`.
- `--sources`: comma-separated bulk sources. Valid values are `submissions`, `companyfacts`, `company_tickers`, `company_tickers_exchange`, `company_tickers_mf`, `all`, and `none`.
- `--include-daily-archives`: enables daily `.nc.tar.gz` archive download.
- `--start-date`: inclusive daily archive start date.
- `--end-date`: exclusive daily archive end date.
- `--limit-days`: smoke-test cap after archive-day discovery.
- `--download-concurrency`: concurrent download workers. The global SEC request limiter still applies.
- `--sec-request-min-interval-seconds`: minimum spacing between SEC requests. Use `0.11` or slower for production.
- `--request-timeout-seconds`: socket timeout for each request.
- `--max-retries`: retry count for retryable HTTP/network failures.
- `--retry-base-seconds`: exponential backoff base for retries.
- `--progress-interval-seconds`: progress print interval for large streaming downloads.
- `--force`: redownload existing artifacts.
- `--dry-run`: write a planned manifest without downloading.

## Important Behavior

- Existing files are reused unless `--force` is passed.
- Every completed or reused file is hashed with SHA-256 and recorded in the manifest.
- Large files are streamed to `<target>.part` and atomically moved into place only after the download completes.
- A failed download removes its partial file and records the failure in the manifest.
- This script intentionally does not parse `.zip` or `.nc.tar.gz` files. Parsing and database insertion are Phase 3.

# SEC Historical Backfill Orchestrator

`sec_historical_backfill_orchestrator.py` runs the SEC historical stages that exist today in the order we have used them manually. It does not replace the stage scripts; it builds and executes the same commands, writes a manifest, and records phase results.

The orchestrator currently covers work through archive validation/content discovery. It does not yet build normalized SEC filing text parts, because that extractor has not been implemented.

## Covered Phases

Default phases:

```text
bulk-download
acceptance-recent
acceptance-fragment
acceptance-header
acceptance-date-fallback
q-live-accepted-backfill
daily-archive-download
validate-downloaded
```

Optional expensive phase:

```text
archive-content-discovery
```

## Important Behavior

- Without `--execute`, the orchestrator writes a timestamped manifest and PowerShell plan only.
- With `--execute`, it runs each phase command in sequence.
- It stops at the first failed phase unless `--continue-on-error` is passed.
- Daily archive phases require `--start-date` and `--end-date`; `--end-date` is exclusive.
- The raw archives remain on disk. No full SEC archive or full raw document payload is inserted into ClickHouse.
- Archive download/validation defaults match the successful workstation history: archive download concurrency `2`, archive request interval `0.2`, request timeout `60`, max retries `8`, retry base `30`, `--continue-on-429`, max 429 count `20`, validation/discovery pending multiplier `1`, and sample limit `1000`.

## Observed Workstation Archive Order

The saved PowerShell history confirms this archive-side sequence:

```text
sec_daily_feed_archive_download.py
sec_archive_content_discovery.py
sec_delete_failed_archives.py
sec_daily_feed_archive_download.py
sec_validate_downloaded_archives.py
sec_delete_failed_archives.py
sec_daily_feed_archive_download.py
sec_validate_downloaded_archives.py
```

The orchestrator covers the repeatable forward stages: archive download, targeted validation, and optional content discovery. Failed-archive deletion remains a targeted repair step because it depends on the exact failed `archive_summary.jsonl` from a discovery or validation run.

## Dry-Run Plan

Use this first. It writes the exact commands that would run:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\pipelines\sec\edgar\sec_historical_backfill_orchestrator.py --start-date 2019-01-01 --end-date 2026-06-17
```

Output:

```text
D:\market-data\prepared\sec_historical_backfill_orchestrator\<run_id>\sec_historical_backfill_orchestrator_manifest.json
D:\market-data\prepared\sec_historical_backfill_orchestrator\<run_id>\sec_historical_backfill_orchestrator_plan.ps1
```

## Execute Current Full Flow

This reproduces the current historical setup through archive download and targeted validation:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\pipelines\sec\edgar\sec_historical_backfill_orchestrator.py --execute --start-date 2019-01-01 --end-date 2026-06-17
```

## Execute Archive-Only Flow

Use this when SEC bulk metadata and accepted timestamps are already populated and you only want the daily archive period:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\pipelines\sec\edgar\sec_historical_backfill_orchestrator.py --execute --start-date 2019-01-01 --end-date 2026-06-17 --phases daily-archive-download,validate-downloaded
```

## Execute With Content Discovery

Content discovery can be very slow for a large period. Use it deliberately:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\pipelines\sec\edgar\sec_historical_backfill_orchestrator.py --execute --start-date 2026-06-01 --end-date 2026-06-17 --phases daily-archive-download,validate-downloaded,archive-content-discovery
```

## Smoke Test

Small end-to-end smoke without touching the full archive history:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\pipelines\sec\edgar\sec_historical_backfill_orchestrator.py --execute --start-date 2026-06-05 --end-date 2026-06-06 --phases daily-archive-download,validate-downloaded,archive-content-discovery --limit-days 1 --limit-archives 1 --max-filings-per-archive 50
```

## Useful Arguments

- `--phases`: comma-separated phase list, or `all`.
- `--execute`: actually run the phase commands.
- `--continue-on-error`: continue after a phase failure.
- `--artifact-root-win`: default `D:/market-data/sec_core`.
- `--core-output-root-win`: default `D:/market-data/prepared/sec_core`.
- `--daily-archive-output-root-win`: default `D:/market-data/prepared/sec_daily_feed_archives`.
- `--archive-validation-output-root-win`: default `D:/market-data/prepared/sec_downloaded_archive_validation`.
- `--archive-discovery-output-root-win`: default `D:/market-data/prepared/sec_archive_content_discovery`.
- `--bulk-download-concurrency`: default `2`.
- `--archive-download-concurrency`: default `2`.
- `--archive-validation-workers`: default `4`.
- `--archive-discovery-workers`: default `4`.
- `--sec-request-min-interval-seconds`: default `0.11` for small SEC API/header requests.
- `--daily-archive-request-min-interval-seconds`: default `0.2` for large daily archive downloads.
- `--daily-archive-request-timeout-seconds`: default `60`.
- `--daily-archive-max-retries`: default `8`.
- `--daily-archive-retry-base-seconds`: default `30`.
- `--daily-archive-max-429-before-stop`: default `20`.
- `--archive-pending-multiplier`: default `1`.
- `--archive-sample-limit`: default `1000`.
- `--validation-status`: default `downloaded`; use `reused` if you deliberately want to validate reused manifest rows.

## Export Workstation PowerShell History

Run this on the workstation to export commands related to SEC, q_live migration, and Benzinga:

```powershell
$historyPath = (Get-PSReadLineOption).HistorySavePath; $out = "D:/market-data/prepared/workstation_powershell_history_sec_q_live_news.txt"; Get-Content $historyPath | Select-String -Pattern "sec_|q_live|news_benzinga|benzinga_news|market-data|ClickHouse|clickhouse" | ForEach-Object { $_.Line } | Set-Content -Path $out -Encoding utf8; Write-Host "history=$out"
```

If you want the complete history file:

```powershell
$historyPath = (Get-PSReadLineOption).HistorySavePath; Copy-Item $historyPath D:/market-data/prepared/workstation_powershell_history_full.txt -Force; Write-Host "history=D:/market-data/prepared/workstation_powershell_history_full.txt"
```

`Get-History` only shows the current PowerShell session. `PSReadLine` history is the useful persistent file.

# SEC Daily Feed Archive Download

This script downloads only SEC EDGAR daily Feed archives:

```text
https://www.sec.gov/Archives/edgar/Feed/YYYY/QTRN/YYYYMMDD.nc.tar.gz
```

It does not decompress, parse, fetch headers, or write to ClickHouse. The next script should parse these archives and populate `q_live.sec_filing_text_v1`.

## What It Saves

Archives are saved under:

```text
{artifact-root}/daily_archives/YYYY/QTRN/YYYYMMDD.nc.tar.gz
```

The default artifact root is:

```text
D:/market-data/sec_core
```

Manifests and run summaries are saved under:

```text
D:/market-data/prepared/sec_daily_feed_archives
```

## Date Range

If `--start-date` and `--end-date` are omitted, the script downloads all available archives from:

```text
2019-01-01 through today
```

`--end-date` is exclusive. If you need q_live-based range inference later, pass `--infer-from-clickhouse`.

## Smoke Tests

Dry-run the first five available archive days from the default 2019-to-today range:

```powershell
python D:\TradingCodes\quant-research-workbench\research\mlops\sec_daily_feed_archive_download.py --dry-run --limit-days 5 --output-root-win D:/market-data/prepared/sec_daily_feed_archives
```

Download five archive days with the default 429-tolerant settings:

```powershell
python D:\TradingCodes\quant-research-workbench\research\mlops\sec_daily_feed_archive_download.py --limit-days 5 --output-root-win D:/market-data/prepared/sec_daily_feed_archives
```

Download an explicit date range:

```powershell
python D:\TradingCodes\quant-research-workbench\research\mlops\sec_daily_feed_archive_download.py --start-date 2026-06-01 --end-date 2026-06-06 --output-root-win D:/market-data/prepared/sec_daily_feed_archives
```

## Full Backfill

Download all available daily archives from 2019-01-01 through today:

```powershell
python D:\TradingCodes\quant-research-workbench\research\mlops\sec_daily_feed_archive_download.py --output-root-win D:/market-data/prepared/sec_daily_feed_archives
```

Workstation path:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\sec_daily_feed_archive_download.py --output-root-win D:/market-data/prepared/sec_daily_feed_archives
```

## Resume Behavior

Rerun the same command to resume. Existing archives are hashed and marked `reused` unless `--force` is passed.

## Important Arguments

- `--download-concurrency`: concurrent archive downloads. Default `1` for SEC friendliness.
- `--sec-request-min-interval-seconds`: global minimum delay between SEC requests. Default `1.0`.
- `--max-retries`: per-file retry count. Default `8`.
- `--retry-base-seconds`: exponential backoff base. Default `30`.
- `--request-timeout-seconds`: per socket operation timeout. Default `30`.
- `--continue-on-429`: default. Retries throttled files without stopping the whole run.
- `--stop-on-429`: optional stricter mode that stops scheduling new downloads after enough 429 responses.
- `--force`: redownload existing files.
- `--limit-days`: smoke-test cap after archive discovery.
- `--allow-g-drive`: allow artifact/output roots on G:. Disabled by default.

## Recommended Settings

The default settings are intentionally conservative because daily feed archives can be multi-GB:

```powershell
python D:\TradingCodes\quant-research-workbench\research\mlops\sec_daily_feed_archive_download.py --output-root-win D:/market-data/prepared/sec_daily_feed_archives
```

If this runs for a while without 429 responses, try `--download-concurrency 2`. I would not go above `2` for these large archives unless SEC stays stable for multiple hours.

If a run hit 429 and stopped before this update, rerun the same command. Completed archives are reused.

## Stopping Safely

Press `Ctrl+C` once. The script stops scheduling new archives, cancels queued work, asks active workers to stop, writes interrupted rows to the manifest, and exits without waiting for the full remaining archive queue. Rerun the same command to continue; completed archives are reused and partial `.part` files are cleaned up on the next worker stop.

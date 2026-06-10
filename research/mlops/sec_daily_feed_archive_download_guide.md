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

Download five archive days:

```powershell
python D:\TradingCodes\quant-research-workbench\research\mlops\sec_daily_feed_archive_download.py --limit-days 5 --download-concurrency 4 --output-root-win D:/market-data/prepared/sec_daily_feed_archives
```

Download an explicit date range:

```powershell
python D:\TradingCodes\quant-research-workbench\research\mlops\sec_daily_feed_archive_download.py --start-date 2026-06-01 --end-date 2026-06-06 --download-concurrency 4 --output-root-win D:/market-data/prepared/sec_daily_feed_archives
```

## Full Backfill

Download all available daily archives from 2019-01-01 through today:

```powershell
python D:\TradingCodes\quant-research-workbench\research\mlops\sec_daily_feed_archive_download.py --download-concurrency 4 --output-root-win D:/market-data/prepared/sec_daily_feed_archives
```

Workstation path:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\sec_daily_feed_archive_download.py --download-concurrency 4 --output-root-win D:/market-data/prepared/sec_daily_feed_archives
```

## Resume Behavior

Rerun the same command to resume. Existing archives are hashed and marked `reused` unless `--force` is passed.

## Important Arguments

- `--download-concurrency`: concurrent archive downloads. Default `4`.
- `--sec-request-min-interval-seconds`: global minimum delay between SEC requests. Default `0.11`.
- `--stop-on-429`: default. Stops scheduling new downloads after SEC returns HTTP 429.
- `--continue-on-429`: do not stop the whole run after a 429.
- `--force`: redownload existing files.
- `--limit-days`: smoke-test cap after archive discovery.
- `--allow-g-drive`: allow artifact/output roots on G:. Disabled by default.

## Recommended Settings

Start conservatively:

```powershell
python D:\TradingCodes\quant-research-workbench\research\mlops\sec_daily_feed_archive_download.py --download-concurrency 4 --sec-request-min-interval-seconds 0.11 --output-root-win D:/market-data/prepared/sec_daily_feed_archives
```

If this runs without 429 responses, increase `--download-concurrency` to `8`. Keep `--sec-request-min-interval-seconds 0.11` unless you intentionally want to run slower.

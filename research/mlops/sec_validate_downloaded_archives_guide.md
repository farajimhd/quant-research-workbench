# SEC Downloaded Archive Validation

Use this after re-running `sec_daily_feed_archive_download.py` to validate only newly downloaded SEC daily archives. It avoids repeating the full `sec_archive_content_discovery.py` scan over every historical archive.

The validator:

- Reads a downloader manifest JSONL.
- Selects rows where `status == "downloaded"` by default.
- Opens only those selected `.nc.tar.gz` archives.
- Uses the same archive scanner as content discovery.
- Writes `archive_summary.jsonl`, `aggregate_summary.json`, `document_samples.jsonl`, and `selected_downloader_rows.jsonl`.
- Exits with code `2` if any selected archive fails.

## Smoke Test

Validate one downloaded archive:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\sec_validate_downloaded_archives.py --manifest-jsonl D:/market-data/prepared/sec_daily_feed_archives/sec_daily_feed_archives_20260615_163812.jsonl --expected-count 68 --limit-archives 1 --archive-workers 1
```

## Validate The Redownloaded Set

Validate the latest run that redownloaded 68 archives:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\sec_validate_downloaded_archives.py --manifest-jsonl D:/market-data/prepared/sec_daily_feed_archives/sec_daily_feed_archives_20260615_163812.jsonl --expected-count 68 --archive-workers 4 --pending-multiplier 1 --sample-limit 1000
```

When running from a different machine against a shared archive folder, remap manifest paths:

```powershell
python D:\TradingCodes\quant-research-workbench\research\mlops\sec_validate_downloaded_archives.py --manifest-jsonl \\DESKTOP-SAAI85T\Workstation-D\market-data\prepared\sec_daily_feed_archives\sec_daily_feed_archives_20260615_163812.jsonl --manifest-artifact-root-win D:/market-data/sec_core/daily_archives --archive-root-win \\DESKTOP-SAAI85T\Workstation-D\market-data\sec_core\daily_archives --expected-count 68 --limit-archives 1 --archive-workers 1
```

If this finishes with `failed_archives=0`, we do not need to rerun the 41-hour full discovery before moving to normalized filing text extraction.

## Important Arguments

- `--manifest-jsonl`: downloader manifest to validate. If omitted, the latest manifest under `--downloader-output-root-win` is used.
- `--manifest-artifact-root-win`: optional root prefix recorded in manifest `artifact_path` values.
- `--archive-root-win`: optional actual root to read archives from when remapping manifest paths.
- `--status`: downloader status to select. Default is `downloaded`.
- `--expected-count`: aborts if the selected row count differs from this value.
- `--archive-workers`: archive-level worker processes.
- `--pending-multiplier`: queued archive jobs per worker. Keep this at `1` for large archives.
- `--limit-archives`: smoke-test cap after manifest filtering.
- `--max-filings-per-archive`: optional per-archive cap; `0` scans all filings.
- `--sample-limit`: representative document samples retained in the output.
- `--hash-archives`: optional SHA-256 prefix calculation. Leave off for normal validation.

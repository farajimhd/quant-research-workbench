# SEC EDGAR Historical Runbook

This runbook documents the current archive recovery and validation flow. The next implementation after validation is normalized filing text extraction.

## Current State

The full archive discovery found truncated daily archives. Full discovery is not incremental and took about 41 hours, so replacement checks must use targeted validation.

Last full discovery:

```text
run_root: D:/market-data/prepared/sec_archive_content_discovery/20260613_195823
archives: 1,858
failed_archives: 66
wall_seconds: 148,931.817
```

Last redownload:

```text
manifest: D:/market-data/prepared/sec_daily_feed_archives/sec_daily_feed_archives_20260615_163812.jsonl
summary: D:/market-data/prepared/sec_daily_feed_archives/sec_daily_feed_archives_summary_20260615_163812.json
status: ok
downloaded: 68
reused: 1,792
```

Previous targeted validation:

```text
run_root: D:/market-data/prepared/sec_downloaded_archive_validation/20260615_222736
archives: 68
failed_archives: 19
error: EOFError('Compressed file ended before the end-of-stream marker was reached')
```

Latest delete/redownload cycle observed on the workstation share:

```text
delete_report: D:/market-data/prepared/sec_archive_failed_archive_delete/20260616_040725/failed_archive_delete_report.json
deleted_count: 19
error_count: 0

redownload_summary: D:/market-data/prepared/sec_daily_feed_archives/sec_daily_feed_archives_summary_20260616_040809.json
redownload_manifest: D:/market-data/prepared/sec_daily_feed_archives/sec_daily_feed_archives_20260616_040809.jsonl
downloaded: 20
reused: 1,841
status: ok
```

The validation run selected those 20 downloaded archives and completed cleanly:

```text
run_root: D:/market-data/prepared/sec_downloaded_archive_validation/20260616_135437
selected_archive_count: 20
completed_archives: 20
parse_errors: 0
status: ok
```

Do not start another validation of the same manifest unless the archive files change.

## Delete Remaining Failed Archives

Run on the workstation after a validation run reports failures. Update the paths and expected count to the latest failed validation run.

Preferred module command:

```powershell
python -m pipelines.sec.edgar.sec_delete_failed_archives --archive-summary-jsonl D:/market-data/prepared/sec_downloaded_archive_validation/<validation_run>/archive_summary.jsonl --source-archive-root-win D:/market-data/sec_core/daily_archives --archive-root-win D:/market-data/sec_core/daily_archives --expected-count <failed_archive_count> --execute
```

Existing workstation compatibility command:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\sec_delete_failed_archives.py --archive-summary-jsonl D:/market-data/prepared/sec_downloaded_archive_validation/<validation_run>/archive_summary.jsonl --source-archive-root-win D:/market-data/sec_core/daily_archives --archive-root-win D:/market-data/sec_core/daily_archives --expected-count <failed_archive_count> --execute
```

If a local workstation delete raises `PermissionError(13, 'Access is denied')`, first test a simple one-file Python delete. If Windows still denies it, rerun from an elevated terminal with:

```powershell
--windows-fix-acl --windows-take-ownership
```

Only use ACL repair on the exact failed-file list, never as a recursive folder operation.

## Redownload Missing Archives

Run without `--force`.

Preferred module command:

```powershell
python -m pipelines.sec.edgar.sec_daily_feed_archive_download --artifact-root-win D:/market-data/sec_core --output-root-win D:/market-data/prepared/sec_daily_feed_archives --download-concurrency 2
```

Existing workstation compatibility command:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\sec_daily_feed_archive_download.py --artifact-root-win D:/market-data/sec_core --output-root-win D:/market-data/prepared/sec_daily_feed_archives --download-concurrency 2
```

Why no `--force`: existing valid archives are reused, and only missing files are downloaded.

## Validate Only Downloaded Archives

Use the latest downloader manifest. Set `--expected-count` to the `downloaded` count in the latest summary.

Preferred module command:

```powershell
python -m pipelines.sec.edgar.sec_validate_downloaded_archives --manifest-jsonl D:/market-data/prepared/sec_daily_feed_archives/<latest_manifest>.jsonl --expected-count <downloaded_count> --archive-workers 4 --pending-multiplier 1 --sample-limit 1000
```

Existing workstation compatibility command:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\sec_validate_downloaded_archives.py --manifest-jsonl D:/market-data/prepared/sec_daily_feed_archives/<latest_manifest>.jsonl --expected-count <downloaded_count> --archive-workers 4 --pending-multiplier 1 --sample-limit 1000
```

Success condition:

```text
failed_archives=0
parse_errors=0
```

Only after this is clean should we build normalized SEC filing text extraction.

For the completed 20-archive validation, these files contain the final status:

```text
D:/market-data/prepared/sec_downloaded_archive_validation/20260616_135437/aggregate_summary.json
D:/market-data/prepared/sec_downloaded_archive_validation/20260616_135437/archive_summary.jsonl
```

Success was confirmed by `aggregate_summary.json` and by all 20 rows in `archive_summary.jsonl` having status `ok`.

## Why We Do Not Rerun Full Discovery

`sec_archive_content_discovery.py` scans every archive and every filing. In the current setup it does not know which archives were already validated. For recovery loops, the targeted validator gives the same gzip/tar parse confidence for only the newly downloaded archives.

## Issue History

| Symptom | Cause | Fix |
| --- | --- | --- |
| Full discovery found 66 corrupt archives. | Incomplete gzip streams from daily SEC feed downloads. | Delete exact failed archives, redownload without `--force`, validate downloaded subset. |
| First delete attempt failed on backup share. | Share/NTFS did not grant delete rights even though files were readable/writable. | Add exact-file delete report and optional ACL repair mode. |
| Validator could not find `D:\...` files from laptop. | Manifest paths were workstation-local. | Add manifest-root/archive-root remapping. |
| 68 redownloads still had 19 corrupt archives. | Some replacement downloads were also truncated. | Repeat targeted delete/redownload/validate loop for only those 19. |

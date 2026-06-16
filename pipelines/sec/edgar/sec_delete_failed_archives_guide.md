# SEC Failed Archive Delete

Use this after `sec_archive_content_discovery.py` marks daily SEC archive files as failed, usually because a downloaded `.nc.tar.gz` ended early and must be re-downloaded.

The script is intentionally conservative:

- It reads `archive_summary.jsonl`.
- It selects rows where `status == "failed"`.
- It maps the discovery path, usually `D:\market-data\sec_core\daily_archives\...`, to the target archive root you pass.
- It refuses to delete paths outside `--archive-root-win`.
- It is dry-run by default. Add `--execute` only after reviewing the dry-run count.
- It writes a JSON summary and JSONL row-level audit report.

## Delete The Failed G Backup Files

Dry-run first:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\sec_delete_failed_archives.py --discovery-run-root D:/market-data/prepared/sec_archive_content_discovery/20260613_195823 --archive-root-win G:/market-data/sec_core/daily_archives --expected-count 66
```

Execute:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\sec_delete_failed_archives.py --discovery-run-root D:/market-data/prepared/sec_archive_content_discovery/20260613_195823 --archive-root-win G:/market-data/sec_core/daily_archives --expected-count 66 --execute
```

If all rows fail with `PermissionError(13, 'Access is denied')`, run the terminal as Administrator and use the ACL repair mode. This only changes permissions for the exact 66 failed archive files selected from `archive_summary.jsonl`; it does not recursively modify the archive folders.

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\sec_delete_failed_archives.py --discovery-run-root D:/market-data/prepared/sec_archive_content_discovery/20260613_195823 --archive-root-win G:/market-data/sec_core/daily_archives --expected-count 66 --execute --windows-fix-acl --windows-take-ownership
```

## Delete From D Instead

Only use this if you intentionally need to remove failed archives from the SSD archive root:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\sec_delete_failed_archives.py --discovery-run-root D:/market-data/prepared/sec_archive_content_discovery/20260613_195823 --archive-root-win D:/market-data/sec_core/daily_archives --expected-count 66 --execute
```

## Important Arguments

- `--discovery-run-root`: the discovery run folder containing `archive_summary.jsonl`.
- `--archive-summary-jsonl`: explicit summary file path; overrides `--discovery-run-root`.
- `--source-archive-root-win`: root recorded by the discovery run. Default is `D:/market-data/sec_core/daily_archives`.
- `--archive-root-win`: local archive root to delete from, usually `G:/market-data/sec_core/daily_archives` on the workstation.
- `--expected-count`: aborts if the failed row count differs from the expected number.
- `--execute`: actually deletes files. Without it, the script only reports what it would delete.
- `--windows-fix-acl`: after a delete permission failure, clears the read-only flag, grants the current Windows user full control on that file, and retries deletion.
- `--windows-take-ownership`: with `--windows-fix-acl`, takes ownership of that file before granting rights. Run from an elevated terminal.

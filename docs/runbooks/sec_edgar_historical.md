# SEC EDGAR Historical Runbook

This runbook documents the current archive recovery, validation, and normalized filing text flow.

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
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\pipelines\sec\edgar\sec_delete_failed_archives.py --archive-summary-jsonl D:/market-data/prepared/sec_downloaded_archive_validation/<validation_run>/archive_summary.jsonl --source-archive-root-win D:/market-data/sec_core/daily_archives --archive-root-win D:/market-data/sec_core/daily_archives --expected-count <failed_archive_count> --execute
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
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\pipelines\sec\edgar\sec_daily_feed_archive_download.py --artifact-root-win D:/market-data/sec_core --output-root-win D:/market-data/prepared/sec_daily_feed_archives --download-concurrency 2
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
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\pipelines\sec\edgar\sec_validate_downloaded_archives.py --manifest-jsonl D:/market-data/prepared/sec_daily_feed_archives/<latest_manifest>.jsonl --expected-count <downloaded_count> --archive-workers 4 --pending-multiplier 1 --sample-limit 1000
```

Success condition:

```text
failed_archives=0
parse_errors=0
```

Only after this is clean should normalized SEC filing text extraction run.

For the completed 20-archive validation, these files contain the final status:

```text
D:/market-data/prepared/sec_downloaded_archive_validation/20260616_135437/aggregate_summary.json
D:/market-data/prepared/sec_downloaded_archive_validation/20260616_135437/archive_summary.jsonl
```

Success was confirmed by `aggregate_summary.json` and by all 20 rows in `archive_summary.jsonl` having status `ok`.

## q_live SEC Table Lineage

`q_live.sec_filing_v2` is the filing-level parent table. It was migrated from `trading_dashboard_dev.sec_filing_v1`, then repaired by the accepted-timestamp backfill workflow. Current `FINAL` checks show:

```text
sec_filing_v2 logical rows: 8,531,118
missing accepted_at_utc: 0
duplicate (cik, accession_number): 0
accepted_at_utc range: 1994-01-04 00:00:00.000000000 to 2026-05-20 16:16:29.000000000
2019+ filings: 4,016,857
2019+ filings with primary_document: 4,016,857
```

`q_live.sec_filing_document_v1` is currently provisional. It was created by migration step 6 from `sec_filing_v2.primary_document`, not from daily archive `<DOCUMENT>` blocks.

Observed fingerprint:

```text
source_run_id: step_06_bridge_features_20260609_161534
extraction_status: metadata_only
description: primary_document_from_sec_filing_metadata
document_name equals sec_filing_v2.primary_document for all rows
document_url equals sec_filing_v2.primary_document_url for all rows
sequence_number = 1 for all rows
document_type equals sec_filing_v2.form_type for all rows
documents per accession: exactly 1
```

Therefore `sec_filing_document_v1` must not be used as the document source of truth for training. The normalized text extractor should parse the downloaded daily archives and write real `<DOCUMENT>` block metadata to `sec_filing_document_v2`.

`q_live.sec_filing_text_v1` exists but has zero rows and is superseded for archive-derived extraction by `sec_filing_text_v2`. The extractor stores clean LLM-ready body text only. Prompt headers should be assembled later by joining text rows to filing/document metadata.

Structured XBRL/fact/frame data already exists in `q_live`, so XBRL sidecars should be skipped by the text extractor and handled through structured SEC features instead.

## Extract SEC Filing Text

Run the read-only integrity audit first:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_integrity_audit.py --archive-root-win D:/market-data/sec_core/daily_archives
```

Create the v2 SEC document/text schema:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_text_v2_schema.py --execute
```

Then rerun the audit with v2 tables required:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_integrity_audit.py --archive-root-win D:/market-data/sec_core/daily_archives --require-v2-tables
```

Completed setup on 2026-06-16:

```text
schema_run: D:/market-data/prepared/sec_text_v2_schema/20260616_180125
post_schema_audit: D:/market-data/prepared/sec_integrity_audit/20260616_180132
post_schema_audit_status: fail=0, warn=5
created_empty_tables: sec_filing_document_v2, sec_filing_text_v2, sec_filing_document_skip_v1
```

Smoke extraction command:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_filing_text_extract_parts.py --archive-root-win D:/market-data/sec_core/daily_archives --output-root-win D:/market-data/prepared/sec_filing_text_parts_smoke --start-date 2025-01-02 --end-date 2025-01-03 --archive-workers 1 --max-filings-per-archive 25 --sample-limit 20 --progress-every 1
```

Full extraction command:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_filing_text_extract_parts.py --archive-root-win D:/market-data/sec_core/daily_archives --output-root-win D:/market-data/prepared/sec_filing_text_parts --start-date 2019-01-01 --end-date 2026-06-17 --archive-workers 4 --pending-multiplier 2 --sample-limit 1000 --progress-every 1
```

Preflight the generated part files through ClickHouse:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_filing_text_clickhouse_file_ingest.py --manifest-json D:/market-data/prepared/sec_filing_text_parts/<run_id>/sec_filing_text_extract_manifest.json --parts-root-win D:/market-data --parts-root-ch /mnt/d/market-data --preflight-only
```

Load after preflight succeeds:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_filing_text_clickhouse_file_ingest.py --manifest-json D:/market-data/prepared/sec_filing_text_parts/<run_id>/sec_filing_text_extract_manifest.json --parts-root-win D:/market-data --parts-root-ch /mnt/d/market-data --execute
```

Laptop smoke completed on 2026-06-16 before parent-row generation:

```text
extract_run: D:/market-data/prepared/sec_filing_text_parts_smoke/20260616_181844
archives: 1
filings: 25
document_rows: 40
text_rows: 8
skip_rows: 32
loader_preflight: passed
```

The `20260616_182541` full extract manifest should not be loaded because it skipped archive filings missing from `q_live.sec_filing_v2`. Rerun extraction with the updated extractor, which writes `sec_filing_v2` parent parts before child document/text/skip parts.

Parent-row smoke completed on 2026-06-17:

```text
extract_run: D:/market-data/prepared/sec_filing_text_parts_parent_smoke/20260617_141028
archives: 1
filings: 25
missing_parent_rows_written: 4
document_rows: 62
text_rows: 12
skip_rows: 50
errors: 0
loader_preflight: passed
```

## Why We Do Not Rerun Full Discovery

`sec_archive_content_discovery.py` scans every archive and every filing. In the current setup it does not know which archives were already validated. For recovery loops, the targeted validator gives the same gzip/tar parse confidence for only the newly downloaded archives.

## Issue History

| Symptom | Cause | Fix |
| --- | --- | --- |
| Full discovery found 66 corrupt archives. | Incomplete gzip streams from daily SEC feed downloads. | Delete exact failed archives, redownload without `--force`, validate downloaded subset. |
| First delete attempt failed on backup share. | Share/NTFS did not grant delete rights even though files were readable/writable. | Add exact-file delete report and optional ACL repair mode. |
| Validator could not find `D:\...` files from laptop. | Manifest paths were workstation-local. | Add manifest-root/archive-root remapping. |
| 68 redownloads still had 19 corrupt archives. | Some replacement downloads were also truncated. | Repeat targeted delete/redownload/validate loop for only those 19. |

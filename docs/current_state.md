# Current Pipeline State

Last updated: 2026-06-16 after scanning the workstation shares from the laptop.

This file records the working state that should be used before proposing new SEC, news, live-trading, or gateway work. It is intentionally operational: run IDs, paths, known issues, and the next command matter more than old plans.

## Active Workstreams

| Workstream | Current State | Next Action |
| --- | --- | --- |
| Live trading app | UI and broker/data separation work exists, but historical data cleanup temporarily took priority. | Resume after historical news and SEC ingestion are stable. Keep live trading code separate from semi-auto modules. |
| `qmd-gateway` | Rust service exists for live Massive trades/quotes, bars, indicators, signal catalog, and ClickHouse batching. | Later: run integration tests against live Massive and ClickHouse. |
| Benzinga historical news | Enriched news has been normalized into legacy single-table JSONEachRow parts. The target ClickHouse table is not present yet. | Preflight ClickHouse `file()` access, then insert into `q_live.benzinga_news_normalized_v1`. |
| SEC daily archives | Full discovery found corrupt archives. The latest 20-archive redownload validation completed cleanly. | Move to normalized SEC filing text extraction. |
| SEC normalized text | Not implemented yet. `q_live.sec_filing_text_v1` exists but currently has zero rows. | Build extractor only after archive validation is clean. |

## Verified Benzinga News State

Latest normalized news run:

```text
run_root: D:/market-data/prepared/benzinga_news_normalized_rows/20260611_011906
manifest: D:/market-data/prepared/benzinga_news_normalized_rows/20260611_011906/benzinga_news_normalized_manifest.json
run_id: 20260611_011906
format: JSONEachRow
target: q_live.benzinga_news_normalized_v1
rows_written: 2,512,931
part_files: 46
part_bytes: 12,185,155,246
interrupted: false
normalization_errors: 32
```

Important quality flags from that run:

```text
title_only: 627,505
short_body: 728,666
external_text: 210,737
external_artifact_missing: 133,901
pdf_link: 12,925
pdf_text: 5,762
pdf_artifact_missing: 6,218
```

The output is a legacy 42-column single-table format. It is not the newer split event/text/url/attachment table layout. The ingest script has been updated to accept this legacy manifest contract.

ClickHouse verification from the laptop on 2026-06-16:

```text
q_live.benzinga_news_normalized_v1: table not found
q_live.benzinga_news_file_ingest_manifest_v1: table not found
```

So the normalized files exist on disk, but they have not been loaded into `q_live`.

The structure audit file exists at:

```text
D:/market-data/prepared/benzinga_news_normalized_rows/20260611_011906/normalized_structure_audit.json
```

Important audit facts:

```text
unique_canonical_news_id: 2,512,931
duplicate_canonical_news_id: 0
unique_raw_payload_hash: 2,512,931
duplicate_raw_payload_hash: 0
unique_text_hash: 2,478,389
duplicate_text_hash: 34,542
```

The audit also reports non-ASCII/mojibake examples in text fields. This is not a blocker for loading the current legacy corpus, but it is a known quality issue to address in the future canonical news migration.

## Verified SEC State

Full archive discovery run:

```text
run_root: D:/market-data/prepared/sec_archive_content_discovery/20260613_195823
archives_scanned: 1,858
failed_archives: 66
wall_seconds: 148,931.817
note: full discovery is not incremental and should not be rerun for a small replacement set.
```

Redownload run after deleting corrupt archives:

```text
manifest: D:/market-data/prepared/sec_daily_feed_archives/sec_daily_feed_archives_20260615_163812.jsonl
summary: D:/market-data/prepared/sec_daily_feed_archives/sec_daily_feed_archives_summary_20260615_163812.json
status: ok
date_range: 2019-01-01 through 2026-06-16 exclusive
archives: 1,860
reused: 1,792
downloaded: 68
bytes_total: 2,611,156,866,635
wall_seconds: 11,482.975
```

Targeted validation of the 68 downloaded archives:

```text
run_root: D:/market-data/prepared/sec_downloaded_archive_validation/20260615_222736
archives: 68
completed_archives: 68
failed_archives: 19
parse_errors: 0
members: 197,872
filings: 197,853
documents: 1,671,551
wall_seconds: 5,032.903
```

The remaining 19 failures are all:

```text
EOFError('Compressed file ended before the end-of-stream marker was reached')
```

Those 19 failures were handled by the latest delete/redownload loop below. Do not rerun the full 41-hour discovery unless the whole archive corpus changes.

Latest confirmed delete/redownload loop:

```text
delete_run: D:/market-data/prepared/sec_archive_failed_archive_delete/20260616_040725/failed_archive_delete_report.json
deleted_count: 19
deleted_bytes: 19,860,029,440
error_count: 0

redownload_summary: D:/market-data/prepared/sec_daily_feed_archives/sec_daily_feed_archives_summary_20260616_040809.json
manifest: D:/market-data/prepared/sec_daily_feed_archives/sec_daily_feed_archives_20260616_040809.jsonl
status: ok
date_range: 2019-01-01 through 2026-06-17 exclusive
archives: 1,861
reused: 1,841
downloaded: 20
bytes_total: 2,640,695,005,932
wall_seconds: 3,597.054
```

The 20 downloaded dates are:

```text
2020-04-22, 2020-04-28, 2020-05-06, 2021-03-26, 2023-02-28,
2023-03-01, 2023-05-01, 2023-05-08, 2023-05-09, 2023-05-10,
2023-05-11, 2023-05-15, 2023-11-02, 2024-08-07, 2025-08-04,
2026-04-07, 2026-04-09, 2026-05-12, 2026-05-28, 2026-06-15
```

Latest validation run detected on the workstation share:

```text
run_root: D:/market-data/prepared/sec_downloaded_archive_validation/20260616_135437
manifest: D:/market-data/prepared/sec_downloaded_archive_validation/20260616_135437/validation_manifest.json
selected_archive_count: 20
status: complete
archives: 20
completed_archives: 20
parse_errors: 0
filings: 83,781
documents: 892,766
archive_bytes: 49,398,168,737
wall_seconds: 3,570.021
```

All 20 archive summaries are `ok`. The SEC archive recovery loop is clean now.

Current `q_live` SEC table state from ClickHouse:

```text
q_live.sec_filing_v2 rows: 16,307,827
q_live.sec_filing_v2 rows missing accepted_at_utc: 7,776,709
q_live.sec_filing_v2 accepted_at_utc range: 1994-01-04 00:00:00.000000000 to 2026-05-20 16:16:29.000000000
q_live.sec_filing_document_v1 rows: 8,417,763
q_live.sec_filing_text_v1 rows: 0
```

This means SEC filing/document metadata exists in `q_live`, but normalized filing text has not been populated yet.

## Issues Encountered And Resolutions

| Issue | Cause | Resolution |
| --- | --- | --- |
| SEC full discovery took about 41 hours. | It scans every archive and every filing; it is not incremental. | Added `sec_validate_downloaded_archives.py` to validate only manifest rows with `status == downloaded`. |
| 66 SEC archives were truncated. | Daily `.nc.tar.gz` downloads ended before gzip stream completion. | Delete only failed archive paths, rerun downloader without `--force`, then validate only downloaded rows. |
| Deleting backup files on `G:` failed from the laptop share. | Share/NTFS permissions allowed read/write but not delete. | Added `sec_delete_failed_archives.py` with audited exact-file delete and optional Windows ACL repair mode. |
| SEC validator smoke failed from laptop. | Downloader manifests used workstation-local `D:\...` paths. | Added archive-root remapping options to validator. |
| 68 replacement archives still had 19 truncated files. | Some redownloads also produced incomplete gzip streams. | Repeat delete/redownload/targeted-validate loop for only the remaining failures. |
| Benzinga ClickHouse preflight rejected manifest columns. | Normalized output was the older 42-column single-table contract; script expected newer 34-column event table. | Updated `news_benzinga_clickhouse_file_ingest.py` to honor legacy manifest columns and structure. |
| `research/mlops` is overloaded. | Operational pipelines, research utilities, migration scripts, and runbooks all accumulated in one folder. | SEC, Benzinga, reference-data, and q_live migration workflows now live under `pipelines/`; old SEC/Benzinga wrappers are archived. |

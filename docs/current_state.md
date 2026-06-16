# Current Pipeline State

Last updated: 2026-06-16.

This file records the working state that should be used before proposing new SEC, news, live-trading, or gateway work. It is intentionally operational: run IDs, paths, known issues, and the next command matter more than old plans.

## Active Workstreams

| Workstream | Current State | Next Action |
| --- | --- | --- |
| Live trading app | UI and broker/data separation work exists, but historical data cleanup temporarily took priority. | Resume after historical news and SEC ingestion are stable. Keep live trading code separate from semi-auto modules. |
| `qmd-gateway` | Rust service exists for live Massive trades/quotes, bars, indicators, signal catalog, and ClickHouse batching. | Later: run integration tests against live Massive and ClickHouse. |
| Benzinga historical news | Enriched news has been normalized into legacy single-table JSONEachRow parts. | Preflight ClickHouse `file()` access, then insert into `q_live.benzinga_news_normalized_v1`. |
| SEC daily archives | Full discovery found corrupt archives. A targeted re-download/validation loop is active. | Finish deleting/re-downloading the remaining failed archives, then validate only downloaded files. |
| SEC normalized text | Not implemented yet. Target schema is documented. | Build extractor only after archive validation is clean. |

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

They should be deleted, redownloaded, then validated with the targeted validator. Do not rerun the full 41-hour discovery unless the whole archive corpus changes.

## Issues Encountered And Resolutions

| Issue | Cause | Resolution |
| --- | --- | --- |
| SEC full discovery took about 41 hours. | It scans every archive and every filing; it is not incremental. | Added `sec_validate_downloaded_archives.py` to validate only manifest rows with `status == downloaded`. |
| 66 SEC archives were truncated. | Daily `.nc.tar.gz` downloads ended before gzip stream completion. | Delete only failed archive paths, rerun downloader without `--force`, then validate only downloaded rows. |
| Deleting backup files on `G:` failed from the laptop share. | Share/NTFS permissions allowed read/write but not delete. | Added `sec_delete_failed_archives.py` with audited exact-file delete and optional Windows ACL repair mode. |
| SEC validator smoke failed from laptop. | Downloader manifests used workstation-local `D:\...` paths. | Added archive-root remapping options to validator. |
| 68 replacement archives still had 19 truncated files. | Some redownloads also produced incomplete gzip streams. | Repeat delete/redownload/targeted-validate loop for only the remaining failures. |
| Benzinga ClickHouse preflight rejected manifest columns. | Normalized output was the older 42-column single-table contract; script expected newer 34-column event table. | Updated `news_benzinga_clickhouse_file_ingest.py` to honor legacy manifest columns and structure. |
| `research/mlops` is overloaded. | Operational pipelines, research utilities, migration scripts, and runbooks all accumulated in one folder. | New docs define the target repository organization before moving files. |


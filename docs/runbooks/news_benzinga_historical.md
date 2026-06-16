# Benzinga Historical News Runbook

This runbook documents the path actually used for the current historical Benzinga corpus. It differs from the future split-table canonical design.

## Current Stage

Historical news has already been downloaded, enriched, and normalized into JSONEachRow part files.

Current normalized run:

```text
run_root: D:/market-data/prepared/benzinga_news_normalized_rows/20260611_011906
manifest: D:/market-data/prepared/benzinga_news_normalized_rows/20260611_011906/benzinga_news_normalized_manifest.json
target_database: q_live
target_table: benzinga_news_normalized_v1
rows_written: 2,512,931
parts: 46
part_bytes: 12,185,155,246
format: JSONEachRow
```

The next step is ClickHouse file-ingest preflight, then insert.

ClickHouse verification from the laptop on 2026-06-16 found that these tables do not exist yet:

```text
q_live.benzinga_news_normalized_v1
q_live.benzinga_news_file_ingest_manifest_v1
```

So the normalized JSONEachRow files are present on disk, but the current corpus has not been loaded into ClickHouse.

## Preflight

Preferred module command:

```powershell
python -m pipelines.news.benzinga.news_benzinga_clickhouse_file_ingest --manifest-json D:/market-data/prepared/benzinga_news_normalized_rows/20260611_011906/benzinga_news_normalized_manifest.json --parts-root-win D:/market-data --parts-root-ch /mnt/d/market-data --preflight-only
```

Existing workstation compatibility command:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\pipelines\news\benzinga\news_benzinga_clickhouse_file_ingest.py --manifest-json D:/market-data/prepared/benzinga_news_normalized_rows/20260611_011906/benzinga_news_normalized_manifest.json --parts-root-win D:/market-data --parts-root-ch /mnt/d/market-data --preflight-only
```

What this checks:

- the manifest is complete and not interrupted;
- every part file exists and matches expected byte size;
- ClickHouse can read each file via `file()`;
- each `file()` row count matches the manifest row count.

If this fails, do not insert. Fix path mapping or manifest/table contract first.

## Insert

Run only after preflight succeeds.

Preferred module command:

```powershell
python -m pipelines.news.benzinga.news_benzinga_clickhouse_file_ingest --manifest-json D:/market-data/prepared/benzinga_news_normalized_rows/20260611_011906/benzinga_news_normalized_manifest.json --parts-root-win D:/market-data --parts-root-ch /mnt/d/market-data --execute
```

Existing workstation compatibility command:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\pipelines\news\benzinga\news_benzinga_clickhouse_file_ingest.py --manifest-json D:/market-data/prepared/benzinga_news_normalized_rows/20260611_011906/benzinga_news_normalized_manifest.json --parts-root-win D:/market-data --parts-root-ch /mnt/d/market-data --execute
```

The script creates:

```text
q_live.benzinga_news_normalized_v1
q_live.benzinga_news_file_ingest_manifest_v1
```

It uses `CLICKHOUSE_LIVE_STORAGE_POLICY` if present.

## Post-Insert Validation

After insert, validate:

```sql
SELECT count() FROM q_live.benzinga_news_normalized_v1;

SELECT
    min(published_at_utc),
    max(published_at_utc),
    countDistinct(provider_article_id),
    count()
FROM q_live.benzinga_news_normalized_v1;

SELECT provider_article_id, count()
FROM q_live.benzinga_news_normalized_v1
GROUP BY provider_article_id
HAVING count() > 1
ORDER BY count() DESC
LIMIT 20;
```

Expected first-pass count is about `2,512,931`, unless the table already contains earlier test rows.

## Important Issue History

| Symptom | Cause | Fix |
| --- | --- | --- |
| Preflight raised `manifest columns do not match current Benzinga news table contract`. | The output is a legacy 42-column single-table manifest, while the script expected the newer 34-column event table. | `news_benzinga_clickhouse_file_ingest.py` now honors manifest `clickhouse_columns` and `clickhouse_structure` for legacy output. |
| It was tempting to rerun normalization. | The normalized parts already exist and are expensive to rebuild. | Start from the manifest above and use file ingest. |
| Existing docs describe split event/text/url/attachment tables. | That is the future canonical shape, not this completed run. | Load current run into `benzinga_news_normalized_v1`; later convert/split with a controlled migration. |
| Structure audit reports non-ASCII/mojibake examples. | Historical source text contains encoded characters and some extraction artifacts. | Preserve current legacy text for loading; address text repair in the future canonical migration so the raw lineage remains reproducible. |

## Future Canonical Path

For future redownloads and live gateway parity, prefer split tables:

```text
benzinga_news_event_v1
benzinga_news_text_v1
benzinga_news_url_v1
benzinga_news_attachment_v1
```

Do not mix a partially split future corpus with the already-built legacy corpus without a migration plan.

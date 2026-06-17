# Benzinga Historical News Runbook

This runbook documents the path actually used for the current historical Benzinga corpus. It differs from the future split-table canonical design.

## Current Stage

Historical news has already been downloaded, enriched, normalized into JSONEachRow part files, and loaded into ClickHouse.

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

ClickHouse verification after insertion found:

```text
q_live.benzinga_news_normalized_v1: 2,512,931 rows
q_live.benzinga_news_file_ingest_manifest_v1: 46 ok parts
```

The normalized legacy table is now the source of truth for historical Benzinga article text.

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

## Ticker Join Table

Build the ticker-time join index after the normalized table is loaded:

```powershell
python -m pipelines.news.benzinga.news_benzinga_ticker_links
```

If the dry-run/audit looks correct, execute:

```powershell
python -m pipelines.news.benzinga.news_benzinga_ticker_links --execute
```

Rerun from scratch:

```powershell
python -m pipelines.news.benzinga.news_benzinga_ticker_links --execute --rebuild
```

Expected source-derived counts for the current corpus:

```text
source_rows: 2,512,931
rows_with_tickers: 2,359,935
expected_ticker_links: 4,309,119
max_distinct_tickers: 2,649
```

No-ticker news is not inserted into the ticker table. It remains available in `q_live.benzinga_news_normalized_v1` for market-wide and macro labels.

## Date-Range Gap Fill

Use the orchestrator when filling a historical gap from source:

```powershell
python -m pipelines.news.benzinga.news_benzinga_historical_gap_fill --start-utc 2026-06-01 --end-utc 2026-06-02 --execute-db --yes
```

The orchestrator preserves the successful manual stage order and arguments. See [news_benzinga_historical_gap_fill.md](news_benzinga_historical_gap_fill.md).

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

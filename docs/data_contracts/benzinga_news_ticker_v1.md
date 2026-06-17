# `q_live.benzinga_news_ticker_v1`

This table is the ticker join index for loaded Benzinga news. It is derived from `q_live.benzinga_news_normalized_v1`; it is not the source of truth for article text.

News with no ticker stays only in `benzinga_news_normalized_v1`. That preserves market-wide and macro news without creating fake ticker rows.

## Table Shape

```sql
ENGINE = ReplacingMergeTree(updated_at_utc)
PARTITION BY toYYYYMM(published_at_utc)
ORDER BY (ticker, published_at_utc, canonical_news_id)
```

Columns:

```sql
canonical_news_id String,
provider LowCardinality(String),
provider_article_id String,
published_date Date,
published_at_utc DateTime64(9, 'UTC'),
ticker LowCardinality(String),
ticker_index UInt16,
ticker_count UInt16,
text_hash String,
content_quality_flags Array(String),
normalizer_version LowCardinality(String),
updated_at_utc DateTime64(9, 'UTC')
```

## Semantics

| Field | Meaning |
| --- | --- |
| `canonical_news_id` | Joins back to the normalized news row. |
| `provider_article_id` | Provider id retained for debugging and lineage. |
| `published_at_utc` | Publication timestamp preserved at `DateTime64(9, 'UTC')`. |
| `ticker` | Uppercase ticker extracted from the source `tickers` array. Empty values are removed. |
| `ticker_index` | One-based position after ticker normalization and deduplication. |
| `ticker_count` | Number of distinct normalized tickers attached to the article. |
| `text_hash` | Lets downstream jobs detect equivalent article text without joining the large text column. |
| `content_quality_flags` | Carries quality labels such as `title_only`, `short_body`, `external_text`, and `pdf_text`. |
| `normalizer_version` | Version of the deterministic normalizer that produced the source row. |

## Backfill Command

Dry-run/audit:

```powershell
python -m pipelines.news.benzinga.news_benzinga_ticker_links
```

Create and backfill:

```powershell
python -m pipelines.news.benzinga.news_benzinga_ticker_links --execute
```

Rerun from scratch:

```powershell
python -m pipelines.news.benzinga.news_benzinga_ticker_links --execute --rebuild
```

The script blocks insertion into a non-empty target unless `--rebuild` or `--force` is passed.

## Expected Counts

For the loaded `20260611_011906` normalized corpus, the source-derived values are:

```text
source_rows: 2,512,931
rows_with_tickers: 2,359,935
expected_ticker_links: 4,309,119
max_distinct_tickers: 2,649
```

The first backfill inserted `4,309,119` rows and audited `0` duplicate `(canonical_news_id, ticker)` links.

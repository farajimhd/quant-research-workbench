# Benzinga Per-Item News Pipeline

The operational news pipeline is now item-first. One Benzinga raw article can be processed independently, and historical gap fills can process many items in parallel.

## Why This Exists

The earlier `url_inventory -> url_fetch_plan` path was useful for discovery: it scanned a large historical corpus so we could learn which domains should be ignored, fetched, or handled specially.

That exploratory scan should not be repeated for every live item or every small gap fill. Runtime only needs the learned policy.

## Per-Item Flow

```text
Benzinga raw payload
-> base normalized row
-> URL candidates extracted from that one payload
-> compact URL policy applied in memory
-> fetch tasks and attachment rows produced
-> optional extracted text attached
-> final normalized row
-> ticker link rows
-> ClickHouse canonical write
```

The core entry point is:

```python
process_benzinga_news_item(payload, policy, options)
```

The higher-level package entry point is:

```python
from pipelines.news.benzinga.news_pipeline import BenzingaNewsPipeline

pipeline = BenzingaNewsPipeline()
processed = pipeline.process_payload(payload)
pipeline.write_many([processed], execute=True)
```

It returns:

```text
NewsPipelineResult
  normalized_row
  ticker_links
  url_resolution.url_candidates
  url_resolution.fetch_tasks
  url_resolution.attachments
  policy_version
  warnings
```

## Runtime Policy

The policy table is:

```text
q_live.news_url_policy_v1
```

The service should load the active policy into memory at startup and refresh it on a timer or explicit command. Historical and live paths should use the same policy version.

## Current Smoke Result

The item pipeline was smoke-tested against `50` raw Benzinga JSON files from the workstation share:

```text
ok: 50
failed: 0
url_candidates: 940
fetch_tasks: 36
ticker_links: 100
url_action_counts:
  ignore: 840
  fetch_html: 70
  metadata_only: 28
  resolve_redirect: 2
```

This validates the per-item URL extraction, policy application, base normalization, and ticker-link generation path without database writes.

## Next Integration

The historical gap-fill orchestrator still mirrors the successful manual scripts. The next implementation step is to replace its `url_inventory` and `url_fetch_plan` stages with this item-level policy resolver, then use batched writers for:

```text
benzinga_news_normalized_v1
benzinga_news_ticker_v1
```

The live news path should use this item pipeline directly.

## Package Runners

Two runners now use the package directly:

```powershell
python -m pipelines.news.benzinga.news_benzinga_package_gap_fill --raw-root-win D:/market-data/news-benzinga/raw --start-utc 2026-06-01 --end-utc 2026-06-02 --processes 8 --batch-size 1000
```

The command above processes already downloaded raw Benzinga JSON files concurrently. Worker processes normalize and resolve URL policy per item. The parent process batches ClickHouse writes so the database is not hit by every worker.

For missing provider data, use the provider-backed gap-fill runner:

```powershell
python -m pipelines.news.benzinga.news_benzinga_provider_gap_fill --start-utc 2026-06-01T00:00:00Z --end-utc 2026-06-02T00:00:00Z --workers 4 --execute
```

```powershell
python -m pipelines.news.benzinga.news_benzinga_live_ingest --once --lookback-minutes 15 --execute
```

The live runner polls the Massive-served Benzinga REST endpoint, sends each returned item through the same package, writes `benzinga_news_normalized_v1`, and then writes `benzinga_news_ticker_v1`. By default it skips news rows already present by `canonical_news_id`.

## Canonical Write Path

After enrichment and final normalization, the write order is:

```text
q_live.benzinga_news_normalized_v1
-> q_live.benzinga_news_ticker_v1
```

The writer validates both target tables before writing, checks that the normalized row has required fields such as `canonical_news_id`, `provider_article_id`, `published_at_utc`, `normalized_full_text`, `text_hash`, and `updated_at_utc`, and blocks an update if an existing news row has a different ticker set. This prevents stale ticker-link rows until we add a controlled ticker-link replacement mutation.

One-item dry run:

```powershell
python -m pipelines.news.benzinga.news_benzinga_item_clickhouse_upsert --raw-json D:/market-data/news-benzinga/raw/2026/06/01/benzinga_<id>.json
```

One-item execute:

```powershell
python -m pipelines.news.benzinga.news_benzinga_item_clickhouse_upsert --raw-json D:/market-data/news-benzinga/raw/2026/06/01/benzinga_<id>.json --execute
```

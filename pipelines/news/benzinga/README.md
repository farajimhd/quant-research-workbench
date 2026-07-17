# Benzinga News Pipeline

This package contains the historical Benzinga news workflow:

- raw historical API download;
- URL inventory and fetch planning;
- URL artifact download and extraction;
- normalized JSONEachRow row building;
- ClickHouse file-based preflight and ingest.
- ticker join-table backfill from loaded normalized news.
- historical date-range gap-fill orchestration.
- compact URL policy seeding and per-item pipeline smoke tests.
- one-item canonical ClickHouse upsert into normalized news and ticker-link tables.
- reusable item-level package used by live ingestion and concurrent gap fills.
- deterministic phrase-presence and causal post-news reaction reference tables.

Preferred module path:

```powershell
python -m pipelines.news.benzinga.news_benzinga_clickhouse_file_ingest --help
```

Ticker join index:

```powershell
python -m pipelines.news.benzinga.news_benzinga_ticker_links --help
```

Historical gap-fill orchestrator:

```powershell
python -m pipelines.news.benzinga.news_benzinga_historical_gap_fill --help
```

URL policy table:

```powershell
python -m pipelines.news.benzinga.news_benzinga_url_policy --help
```

Per-item pipeline smoke:

```powershell
python -m pipelines.news.benzinga.news_benzinga_item_pipeline_smoke --help
```

One-item ClickHouse upsert:

```powershell
python -m pipelines.news.benzinga.news_benzinga_item_clickhouse_upsert --help
```

Reusable package gap fill:

```powershell
python -m pipelines.news.benzinga.news_benzinga_package_gap_fill --help
```

Provider-backed historical gap fill:

```powershell
python -m pipelines.news.benzinga.news_benzinga_provider_gap_fill --help
```

Live package ingest:

```powershell
python -m pipelines.news.benzinga.news_benzinga_live_ingest --once --limit-items 0
```

News phrase/reaction reference build:

```powershell
python pipelines\news\benzinga\run_news_reaction_extract.py
python pipelines\news\benzinga\run_news_reaction_extract.py --execute
```

The first command is a read-only coverage preflight. See the
[v1 data contract](../../../docs/data_contracts/news_reaction_reference_v1.md)
for canonical-bar prerequisites, causal horizons, table grains, quality rules,
and the 2019-2025 training / 2026 holdout split.

Old `research/mlops/news_benzinga_*.py` wrappers are archived under `pipelines/archive/legacy_wrappers/research_mlops/`. Do not use them for new runs.

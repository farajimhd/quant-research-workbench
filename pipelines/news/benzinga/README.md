# Benzinga News Pipeline

This package contains the historical Benzinga news workflow:

- raw historical API download;
- URL inventory and fetch planning;
- URL artifact download and extraction;
- normalized JSONEachRow row building;
- ClickHouse file-based preflight and ingest.

Preferred module path:

```powershell
python -m pipelines.news.benzinga.news_benzinga_clickhouse_file_ingest --help
```

Old `research/mlops/news_benzinga_*.py` wrappers are archived under `pipelines/archive/legacy_wrappers/research_mlops/`. Do not use them for new runs.

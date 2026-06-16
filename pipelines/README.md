# Operational Pipelines

This folder owns historical and production-adjacent data workflows. These scripts are not model-version experiments and should not live in `research/mlops`.

## Layout

```text
pipelines/market_sip/       # Massive SIP flatfile ingest, event tables, sample cache, benchmarks
pipelines/news/benzinga/   # Benzinga download, URL inventory, enrichment, normalization, ClickHouse load
pipelines/sec/edgar/       # SEC EDGAR archive download, validation, metadata repair, text extraction prep
```

`research/mlops` is reserved for shared research utilities. Operational commands
for moved workflows should be run from their `pipelines/...` folders.

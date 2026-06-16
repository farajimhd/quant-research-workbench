# Operational Pipelines

This folder owns historical and production-adjacent data workflows. These scripts are not model-version experiments and should not live in `research/mlops`.

## Layout

```text
pipelines/news/benzinga/   # Benzinga download, URL inventory, enrichment, normalization, ClickHouse load
pipelines/sec/edgar/       # SEC EDGAR archive download, validation, metadata repair, text extraction prep
```

`research/mlops` still contains compatibility wrappers for the moved scripts so existing workstation commands keep working while guides and runtime folders are updated.

# Repository Organization Plan

The repository used to mix production services, historical ingestion scripts, research utilities, and one-off migration tools. The current direction is to separate code by runtime ownership and data domain.

## Target Layout

```text
services/
  qmd-gateway/              # live Massive trades/quotes, bars, indicators, scanner stream
  news-gateway/             # live Benzinga polling, normalization, DB write
  sec-gateway/              # live SEC feed polling and filing capture
  news-intelligence/        # model serving and news labeling APIs

pipelines/
  market_sip/               # historical quotes/trades ingest, compacting, historical bars
  news/benzinga/            # historical Benzinga download, URL inventory, enrichment, normalize, load
  sec/edgar/                # SEC archive download, validation, filing text extraction, load
  reference_data/           # symbol/listing/conid/fundamental reference material

research/
  mlops/                    # shared utilities only
  masked_event_model/vN/    # model-specific experiments and launchers

docs/
  architecture/
  runbooks/
  data_contracts/
```

## What Belongs In `research/mlops`

Keep only reusable utilities that are shared across research versions and pipelines:

- environment loading and secret redaction
- ClickHouse HTTP client helpers
- path conventions
- manifest helpers
- metric/log writing helpers
- checkpoint/W&B helpers
- seed/device helpers

Do not keep domain workflows here long term. SEC, news, SIP, and reference-data scripts should move to `pipelines/`.

## Implemented Moves

| Current Pattern | Target Folder |
| --- | --- |
| `pipelines/news/benzinga/news_benzinga_*` | `pipelines/news/benzinga/` |
| `pipelines/sec/edgar/sec_*` archive/download/validation/text scripts | `pipelines/sec/edgar/` |
| `research/mlops/clickhouse_ingest_sip_*` and quote compact builders | `pipelines/market_sip/` |
| `research/mlops/clickhouse_load_market_references.py` | `pipelines/reference_data/` |
| `pipelines/reference_data/migration/*` | `pipelines/reference_data/migration/` |

The Benzinga, SEC, reference-data, and q_live migration moves are implemented. The old `pipelines/news/benzinga/news_benzinga_*.py` and `pipelines/sec/edgar/sec_*.py` compatibility wrappers are archived under `pipelines/archive/legacy_wrappers/research_mlops/` and are no longer active command paths.

The market SIP operational move is implemented under `pipelines/market_sip/`.
The temporary `research/mlops` market-SIP wrappers have been removed; runbooks
and workstation commands should use the pipeline paths directly.

## Compatibility Rule

Do not break active workstation commands in one large move. Use a two-stage migration:

1. Move the real implementation to the target folder.
2. Leave a temporary wrapper at the old `research/mlops/...` path that imports or executes the new module.

After active historical SEC/news loads are complete and workstation runtime guides are updated, remove wrappers in a dedicated cleanup commit. This has been done for SEC, Benzinga, and market SIP wrappers; they are archived or removed, not active command paths.

## Active Scripts To Keep Working During Migration

These old paths are archived, not active:

```text
pipelines/archive/legacy_wrappers/research_mlops/news_benzinga_*.py
pipelines/archive/legacy_wrappers/research_mlops/sec_*.py
```

## Candidates For Quarantine Or Removal

Do not delete these blindly. First confirm no guide, workstation run, or output manifest still references them.

```text
pipelines/archive/legacy_workflows/sec_edgar/sec_historical_feed_pipeline.py
pipelines/archive/legacy_workflows/news_benzinga/news_benzinga_historical_ingest.py
```

The acceptance backfill helpers remain active under `pipelines/sec/edgar/` until SEC filing text extraction is validated, because they document how `accepted_at_utc` was recovered.

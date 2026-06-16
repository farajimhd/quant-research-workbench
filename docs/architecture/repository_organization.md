# Repository Organization Plan

The current repository mixes production services, historical ingestion scripts, research utilities, and one-off migration tools. The target organization should separate code by runtime ownership and data domain.

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

## Proposed Moves

| Current Pattern | Target Folder |
| --- | --- |
| `research/mlops/news_benzinga_*` | `pipelines/news/benzinga/` |
| `research/mlops/sec_*` archive/download/validation/text scripts | `pipelines/sec/edgar/` |
| `research/mlops/clickhouse_ingest_sip_*` and quote compact builders | `pipelines/market_sip/` |
| `research/mlops/clickhouse_load_market_references.py` | `pipelines/reference_data/` |
| `research/mlops/migration/*` | `pipelines/reference_data/migration/` or `pipelines/sec/edgar/migration/` depending on table ownership |

## Compatibility Rule

Do not break active workstation commands in one large move. Use a two-stage migration:

1. Move the real implementation to the target folder.
2. Leave a temporary wrapper at the old `research/mlops/...` path that imports or executes the new module and prints the new path.

After active historical SEC/news loads are complete, remove wrappers and stale scripts in a dedicated cleanup commit.

## Active Scripts To Keep Working During Migration

These paths are currently used by workstation commands and should keep wrappers if moved:

```text
research/mlops/news_benzinga_clickhouse_file_ingest.py
research/mlops/news_benzinga_build_normalized_rows.py
research/mlops/news_benzinga_raw_download.py
research/mlops/news_benzinga_url_inventory.py
research/mlops/news_benzinga_url_fetch_plan.py
research/mlops/news_benzinga_url_download.py
research/mlops/sec_daily_feed_archive_download.py
research/mlops/sec_delete_failed_archives.py
research/mlops/sec_validate_downloaded_archives.py
research/mlops/sec_archive_content_discovery.py
```

## Candidates For Quarantine Or Removal

Do not delete these blindly. First confirm no guide, workstation run, or output manifest still references them.

```text
research/mlops/sec_historical_feed_pipeline.py
research/mlops/sec_historical_feed_download.py
research/mlops/sec_initial_fill_download.py
research/mlops/sec_bulk_clickhouse_ingest.py
research/mlops/sec_acceptance_*              # likely completed migration helpers
research/mlops/news_benzinga_historical_ingest.py
research/mlops/news_benzinga_url_enrich.py   # superseded by separate URL download/extract/normalize flow
```

The acceptance backfill helpers should be kept archived until SEC filing text extraction is validated, because they document how `accepted_at_utc` was recovered.


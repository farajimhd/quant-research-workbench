# Quant Research Workbench Docs

Start here before changing historical ingestion, live services, or trading UI code.

## Current State

- [Current Pipeline State](current_state.md): verified run IDs, output paths, current blockers, and issue history.

## Architecture

- [Repository Organization Plan](architecture/repository_organization.md): target folder layout and safe migration rules.
- [Event-Based Market Engine](architecture/event_based_market_engine.md): quotes/trades based market engine design.
- [Benzinga News Normalization Pipeline](architecture/benzinga_news_normalization_pipeline.md): future canonical split-table news design.
- [Benzinga Per-Item News Pipeline](architecture/benzinga_news_item_pipeline.md): runtime item-level path for live news, efficient gap fills, and canonical ClickHouse writes.
- [Benzinga Historical Gap Fill](runbooks/news_benzinga_historical_gap_fill.md): date-range orchestrator around the successful historical news scripts.

## Runbooks

- [Market SIP Pipelines](../pipelines/market_sip/README.md): Massive SIP flatfile ingest, unified events, and event sample-cache workflows.
- [Benzinga Historical News](runbooks/news_benzinga_historical.md): current normalized news preflight and ClickHouse load path.
- [SEC EDGAR Historical](runbooks/sec_edgar_historical.md): archive recovery, targeted validation, and next text-extraction stage.

## Data Contracts

- [Benzinga Legacy Normalized Table](data_contracts/benzinga_news_normalized_v1.md): current 42-column loaded-news contract.
- [Benzinga News Ticker Join Table](data_contracts/benzinga_news_ticker_v1.md): derived ticker/time index for loaded Benzinga news.
- [News URL Policy Table](data_contracts/news_url_policy_v1.md): compact domain/action policy used by item-level news processing.
- [SEC Filing Text Pipeline](data_contracts/sec_filing_text_pipeline.md): target SEC document and text contracts.

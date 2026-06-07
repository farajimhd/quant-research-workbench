# Benzinga News Normalization Pipeline

## Purpose

The news system should use Benzinga as the canonical provider for historical and live news. The Massive general news endpoint is derivative for this project and should not be used for training data, live news state, or normalized news persistence.

The first implementation target is:

1. Download Benzinga historical news.
2. Normalize each article through the same module used by live news.
3. Persist compact normalized rows to ClickHouse.

Keyword discovery, cheap prediction, and LLM enrichment should be built after the normalized corpus exists.

## Design Principles

- Historical and live news must share one normalizer so model training and production receive the same representation.
- No-ticker, macro, geopolitical, crypto, title-only, PDF-backed, and link-only articles must be retained because they can affect market context.
- Raw artifacts are saved on disk. ClickHouse stores compact normalized data, metadata, hashes, and extraction status.
- The write database must use `CLICKHOUSE_LIVE_STORAGE_POLICY` so news lands on the SSD policy reserved for this app's live database.
- The pipeline must be resumable. Every downloaded time bucket and persisted batch needs manifest rows or JSONL reports.
- The implementation should be modular enough for live gateway code, historical redownload scripts, keyword discovery scripts, and future labeling jobs to reuse the same parsing and normalization behavior.

## Current Code Findings

### Existing `services/news-gateway`

The current Rust gateway already has useful live pieces:

- Polls the Massive-hosted Benzinga endpoint `/benzinga/v2/news`.
- Polls the derivative Massive general news endpoint `/v2/reference/news`.
- Normalizes provider payloads into `NewsArticle`.
- Extracts text from HTML, optionally fetches short article URLs, and optionally extracts PDF text.
- Writes `live_news_articles` to ClickHouse in JSONEachRow batches.
- Keeps recent state and streams compact summaries to the app.

Required changes before the new canonical flow:

- Disable and remove the derivative Massive general news path.
- Rename source values from `massive_benzinga` to `benzinga`.
- Add `CLICKHOUSE_LIVE_STORAGE_POLICY` support to all news tables.
- Split normalization into reusable modules that can be called by live polling and historical scripts.
- Replace the current broad `live_news_articles` table with a compact canonical schema for normalized Benzinga rows, or create a new versioned table and leave the old table only as a migration source.

### Existing Workstation MLOps Pattern

The workstation ingestion script at:

```text
\\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\clickhouse_ingest_sip_compact_codec.py
```

has the pattern we should reuse:

- Python launcher with visible defaults and command-line overrides.
- Environment discovery through `research.mlops.env`.
- Secret presence reporting without printing secret values.
- ClickHouse HTTP client using `X-ClickHouse-User` and `X-ClickHouse-Key`.
- Explicit storage policy argument.
- Manifest table for resumability and status tracking.
- ProcessPool for CPU/file preflight work.
- ThreadPool for concurrent ClickHouse insert jobs.
- JSONL report output with config, preflight, insert profiles, and failures.
- Clear progress printing that can be monitored while the workstation runs.

The news pipeline should follow this style rather than becoming a one-off downloader.

## Recommended Execution Shape

Use one orchestration script that performs steps 1, 2, and 3 as a streaming pipeline:

```text
time bucket downloader workers
        |
        v
raw artifact writer
        |
        v
normalization workers
        |
        v
batched ClickHouse writer
```

This is better than three independent scripts for the first production implementation because it avoids writing a full raw archive before useful rows start reaching ClickHouse. It also allows one manifest to track the bucket lifecycle end to end.

The code should still be modular internally, so separate scripts can be added later:

- `download_benzinga_historical.py` for raw-only redownload.
- `normalize_benzinga_artifacts.py` for reprocessing saved raw artifacts.
- `ingest_normalized_benzinga.py` for pushing already-normalized JSONL/Parquet batches.
- `discover_benzinga_keywords.py` for taxonomy discovery after normalized rows exist.

The first script should be:

```text
research/mlops/news_benzinga_historical_ingest.py
```

and the workstation mirror should be copied to:

```text
\\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\news_benzinga_historical_ingest.py
```

## Historical Download Strategy

Split the requested time range into fixed UTC buckets, similar to the old stock-scanner downloader:

```text
[start_utc, bucket_1_end)
[bucket_1_end, bucket_2_end)
...
```

Each bucket calls the Massive-hosted Benzinga endpoint with:

```text
published_gte
published_lte
limit
sort=published.asc
```

The downloader should support:

- configurable bucket size, for example 5 or 15 minutes for dense periods;
- configurable download concurrency;
- retry with backoff;
- saturation detection when a bucket returns `limit` rows or has a `next_url`;
- optional automatic bucket split when saturation is detected;
- raw payload artifact storage by date and provider id;
- manifest status: `discovered`, `started`, `downloaded`, `normalized`, `inserted`, `partial`, `failed`.

Only Benzinga rows are accepted. Massive general news rows are out of scope.

## Shared Normalization Contract

The normalizer should accept one Benzinga provider payload and produce one canonical row.

Core fields:

```text
provider = benzinga
provider_article_id
canonical_news_id
published_at_utc DateTime64(9)
published_raw
last_updated_at_utc Nullable(DateTime64(9))
gateway_or_download_seen_at_utc DateTime64(9)
provider_delay_ns Nullable(Int64)
title
normalized_title
teaser
body_text
external_text
pdf_text
normalized_full_text
text_hash
article_url
url_domain
author
tickers
channels
provider_tags
image_urls
has_body
is_title_only
has_external_text
has_pdf
pdf_urls
content_quality_flags
raw_artifact_path
raw_payload_hash
normalizer_version
```

Normalization should:

- parse provider timestamps at the highest available precision;
- preserve no-ticker articles;
- strip HTML deterministically;
- decode entities;
- remove repeated whitespace;
- extract and preserve links;
- download/extract PDFs when configured;
- fetch article URLs only when body text is missing or too short;
- avoid storing full raw JSON in ClickHouse unless explicitly enabled;
- store raw payloads and extracted documents on disk with hash references in ClickHouse.

## ClickHouse Persistence

The target database should be `REAL_LIVE_CLICKHOUSE_WRITE_DATABASE`.

Tables should be created with:

```text
SETTINGS storage_policy = '<CLICKHOUSE_LIVE_STORAGE_POLICY>'
```

Proposed tables:

```text
benzinga_news_normalized_v1
benzinga_news_ingest_manifest_v1
```

`benzinga_news_normalized_v1` should use a replacement key that preserves provider updates:

```text
ENGINE = ReplacingMergeTree(updated_at_utc)
PARTITION BY toYYYYMM(published_at_utc)
ORDER BY (toDate(published_at_utc), provider_article_id)
```

The table should avoid large raw blobs. Large PDF text or extracted webpage text can be truncated to a configured limit or stored as artifact files with hashes. The normalized model input text should be stored only if it is compact enough for training and downstream retrieval.

## Keyword Layer After Normalization

Keyword discovery should run after normalized Benzinga rows exist.

The discovery job should compute:

- unigram, bigram, and trigram candidates;
- total term frequency;
- document frequency;
- yearly document frequency;
- phrase scores such as TF-IDF, PMI, or log-likelihood;
- boilerplate candidates to suppress.

The output should be a reviewed and versioned taxonomy:

```text
benzinga_keyword_taxonomy_v1
```

The production normalizer or cheap prediction stage then emits:

```text
keyword_hits Array(LowCardinality(String))
event_tags Array(LowCardinality(String))
taxonomy_version LowCardinality(String)
```

Keyword matching should be deterministic and fast. LLMs can help review or group candidates offline, but live normalization should not depend on an LLM.

## Cheap Prediction Before LLM

The normalized row and keyword taxonomy enable a cheap first-stage model:

```text
deterministic event rules
+ keyword/category features
+ fast traditional ML or small neural classifier
```

Candidate models:

- logistic regression or linear SVM on keyword and n-gram features;
- LightGBM or CatBoost on categorical/count features;
- fastText-style classifier for a small text model.

The cheap stage should produce:

```text
event_type
ticker_specific_importance
market_wide_importance
expected_direction
expected_magnitude_bucket
urgency_score
confidence
llm_required
```

The LLM stage should receive only selected rows: uncertain, rare, high impact, multi-ticker, macro, FDA, capital markets, lawsuit, M&A, or rows whose market reaction conflicts with the cheap prediction.

## Implementation Plan

### Phase 1: Canonical Benzinga Scope

- Remove derivative Massive general news from the gateway configuration and docs.
- Rename source identifiers to `benzinga`.
- Keep the current live endpoint behavior but route it through the shared Benzinga normalizer.

### Phase 2: Shared Normalizer

- Create a reusable Python module for historical ingestion scripts.
- Keep Rust live gateway behavior aligned with the same contract.
- Define normalizer version and exact field semantics.
- Add deterministic text cleanup and quality flags.

Python is the right choice for the historical workstation path because it already matches the MLOps scripts, ClickHouse tooling, multiprocessing, PDF extraction libraries, and workstation run workflow. Rust remains appropriate for the low-latency live gateway.

### Phase 3: Historical Redownload + Normalize + Persist

- Add `research/mlops/news_benzinga_historical_ingest.py`.
- Use fixed time buckets and concurrent download workers.
- Write raw artifacts to disk.
- Normalize each payload.
- Insert normalized rows in ClickHouse batches.
- Maintain manifest rows and JSONL reports.
- Use `CLICKHOUSE_LIVE_STORAGE_POLICY`.
- Mirror the script to the workstation MLOps path.

### Phase 4: Keyword Discovery

- Add `discover_benzinga_keywords.py`.
- Read from `benzinga_news_normalized_v1`.
- Generate frequency and phrase reports.
- Produce candidate taxonomy JSON for review.

### Phase 5: Cheap Prediction

- Add a deterministic keyword matcher and feature builder.
- Train/evaluate a small fast classifier on market-reaction labels.
- Persist model version and prediction outputs separately from the immutable normalized news row.

## Open Decisions

- Exact raw artifact root for the fresh Benzinga redownload.
- Whether full normalized text should be stored in ClickHouse or capped with artifact references.
- Default bucket size and concurrency limits for Benzinga API rate behavior.
- Whether PDF/web extraction happens inline during historical ingest or as a second enrichment pass for rows that need it.
- Final table names once the existing `live_news_articles` migration path is chosen.

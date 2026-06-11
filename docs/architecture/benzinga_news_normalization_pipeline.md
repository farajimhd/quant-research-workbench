# Benzinga News Normalization Pipeline

## Purpose

Benzinga is the canonical news provider for historical training data and live production news state. Massive is the API host for the Benzinga subscription endpoint; the separate Massive news endpoint is not part of the canonical corpus.

The pipeline has one goal: make historical and live news produce the same compact rows so models see the same representation in training and production.

## Source Scope

Accepted:

- Benzinga REST rows from `/benzinga/v2/news`.
- Articles with no ticker.
- Macro, geopolitical, crypto, ETF, sector, PDF-backed, link-only, and title-only articles.
- Provider updates to prior articles.

Rejected:

- Rows that cannot be parsed as Benzinga provider payloads.
- Duplicate provider rows where the replacement key already has a newer version.

No topic is rejected as junk during normalization. Filtering for trading relevance happens later in keyword, cheap-model, or LLM stages.

## Artifact Rule

Raw provider payloads are saved to disk first. ClickHouse stores compact data, hashes, timestamps, quality flags, and artifact references. Large raw JSON blobs and redundant full-text copies should not be persisted into the event table.

Live and historical paths must both use:

- deterministic HTML cleanup;
- deterministic text normalization;
- stable URL extraction and URL policy labels;
- the same normalizer version convention;
- the same ClickHouse table contract.

## Canonical Tables

The canonical normalized output is split into four tables.

### `benzinga_news_event_v1`

One row per provider article and update version.

Important fields:

- `provider`: always `benzinga`.
- `provider_article_id`: Benzinga id.
- `canonical_news_id`: stable app id, usually provider plus provider id.
- `published_at_utc`: provider publication timestamp as `DateTime64(9, 'UTC')`.
- `last_updated_at_utc`: nullable provider update timestamp.
- `downloaded_at_utc`: gateway or historical downloader observation time.
- `provider_delay_ns`: `downloaded_at_utc - published_at_utc`, when measurable.
- `title`, `normalized_title`, `teaser`.
- `text_hash`: hash of normalized title, teaser, body, external text, and PDF text.
- `article_url`, `article_url_domain`, `author`.
- `tickers`, `channels`, `provider_tags`, `image_urls`.
- `has_body`, `is_title_only`, `has_external_text`, `has_pdf`.
- `content_quality_flags`.
- `external_fetch_status`, `external_fetch_error`.
- `pdf_extract_status`, `pdf_extract_error`.
- `raw_artifact_path`, `raw_payload_hash`.
- `normalizer_version`, `updated_at_utc`.

The event table intentionally does not store `body_text`, `external_text`, `pdf_text`, raw links, or raw PDF URL arrays.

### `benzinga_news_text_v1`

One row per text part:

- `text_kind`: `body`, `external`, or `pdf`.
- `text`: normalized text capped by the configured limit.
- `text_hash`, `text_chars`, `text_bytes`.
- `source_count`: number of source fragments used.

This table is the retrieval source for model prompts and training text.

### `benzinga_news_url_v1`

One row per URL discovered from the provider row or extracted content.

Important fields:

- `url_hash`, `url`, `registered_domain`.
- `url_kind`: article, source, PDF, SEC, social, image, or other policy category.
- `url_source`: where the URL came from.
- `final_action` and `resolved_action`: deterministic policy action.
- HTTP and content metadata when fetched.
- attachment and extraction references.

This table lets enrichment attach downloaded text back to the original news row without reopening every raw JSON file.

### `benzinga_news_attachment_v1`

One row per downloaded attachment or source artifact.

Important fields:

- URL identity and domain.
- artifact path and hash.
- content type, content length, HTTP status.
- extraction method and quality.
- extracted text hash and character count.
- PDF page count when known.

## Live Gateway Contract

`services/news-gateway` keeps `live_news_articles` only as a compatibility table for current UI streams. It also writes the four canonical split tables when `NEWS_BENZINGA_CANONICAL_ENABLED=true`.

The split table defaults are:

- `NEWS_BENZINGA_EVENT_TABLE=benzinga_news_event_v1`
- `NEWS_BENZINGA_TEXT_TABLE=benzinga_news_text_v1`
- `NEWS_BENZINGA_URL_TABLE=benzinga_news_url_v1`
- `NEWS_BENZINGA_ATTACHMENT_TABLE=benzinga_news_attachment_v1`

`NEWS_BENZINGA_CANONICAL_TABLE` remains a backward-compatible alias for the event table.

## Historical Pipeline

Historical redownload should use fixed UTC time buckets with concurrent download workers. The normalizer should emit DB-ready JSONL parts:

- `event_parts/benzinga_news_event_part_*.jsonl`
- `text_parts/benzinga_news_text_part_*.jsonl`
- `url_parts/benzinga_news_url_part_*.jsonl`
- `attachment_parts/benzinga_news_attachment_part_*.jsonl`

The ClickHouse load script should ingest those files with the `file()` table function or equivalent batch insert. The target database must use `CLICKHOUSE_LIVE_STORAGE_POLICY`.

## Downstream Order

1. Finish historical download and split-row normalization.
2. Insert event/text/url/attachment tables.
3. Validate row counts, duplicate keys, timestamp precision, and text coverage.
4. Run keyword inventory over normalized text.
5. Build deterministic keyword and cheap-model features.
6. Add LLM classification only after the cheap path and schema are stable.
7. Join normalized news to market snapshots for reaction-label datasets.

SEC filings should follow the same pattern later: raw artifact first, compact event/document text tables second, market-reaction labels last.

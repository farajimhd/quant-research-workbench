# `q_live.benzinga_news_normalized_v1`

This is the current legacy normalized Benzinga news contract produced by run `20260611_011906`. It is the table to load before any future split-table migration.

As of the 2026-06-16 laptop-to-workstation check, this contract exists as JSONEachRow files on disk, but `q_live.benzinga_news_normalized_v1` has not been created or loaded in ClickHouse.

## Table Shape

Engine:

```sql
ENGINE = ReplacingMergeTree(updated_at_utc)
PARTITION BY toYYYYMM(published_at_utc)
ORDER BY (published_date, provider_article_id)
```

Columns:

```sql
provider String,
provider_article_id String,
canonical_news_id String,
published_date Date,
published_at_utc DateTime64(9, 'UTC'),
published_raw String,
last_updated_at_utc Nullable(DateTime64(9, 'UTC')),
last_updated_raw String,
downloaded_at_utc DateTime64(9, 'UTC'),
provider_delay_ns Nullable(Int64),
title String,
normalized_title String,
teaser String,
body_text String,
external_text String,
pdf_text String,
normalized_full_text String,
text_hash String,
article_url String,
url_domain String,
author String,
tickers Array(String),
channels Array(String),
provider_tags Array(String),
image_urls Array(String),
links Array(String),
has_body UInt8,
is_title_only UInt8,
has_external_text UInt8,
has_pdf UInt8,
pdf_urls Array(String),
pdf_artifact_paths Array(String),
pdf_metadata_json String,
content_quality_flags Array(String),
external_fetch_status String,
external_fetch_error String,
pdf_extract_status String,
pdf_extract_error String,
raw_artifact_path String,
raw_payload_hash String,
normalizer_version String,
updated_at_utc DateTime64(9, 'UTC')
```

## Field Semantics

| Field | Meaning |
| --- | --- |
| `provider` | Always `benzinga` for this corpus. |
| `provider_article_id` | Provider article id; primary deduplication key with `published_date`. |
| `canonical_news_id` | Stable app id derived from provider and provider id. |
| `published_at_utc` | Provider publication timestamp at nanosecond ClickHouse precision. |
| `last_updated_at_utc` | Provider update timestamp when available. |
| `downloaded_at_utc` | Historical downloader observation time. |
| `provider_delay_ns` | `downloaded_at_utc - published_at_utc`, when measurable. |
| `body_text` | Normalized provider body text. |
| `external_text` | Text extracted from fetched source/citation pages. |
| `pdf_text` | Text extracted from PDF artifacts. |
| `normalized_full_text` | Combined normalized text used for downstream model features. |
| `text_hash` | Hash of normalized text material. |
| `links` | URLs found in provider body and normalized text path. |
| `pdf_metadata_json` | Compact metadata for PDF extraction, not raw PDF content. |
| `content_quality_flags` | Deterministic quality labels such as `title_only`, `short_body`, `external_text`, `pdf_text`, `external_artifact_missing`, `pdf_artifact_missing`. |
| `raw_artifact_path` | Path to raw provider JSON artifact on disk. |
| `raw_payload_hash` | Hash of raw provider payload. |
| `normalizer_version` | Version string for deterministic normalizer behavior. |

## Load Source

```text
D:/market-data/prepared/benzinga_news_normalized_rows/20260611_011906/benzinga_news_normalized_manifest.json
```

The manifest declares this schema. The ClickHouse ingest script must use the manifest schema, not the newer split-table code defaults.

The companion audit file is:

```text
D:/market-data/prepared/benzinga_news_normalized_rows/20260611_011906/normalized_structure_audit.json
```

Key audit facts:

```text
rows: 2,512,931
duplicate_canonical_news_id: 0
duplicate_raw_payload_hash: 0
duplicate_text_hash: 34,542
```

The audit reports non-ASCII/mojibake examples in text fields. Do not silently strip these during the legacy load; any repair should be a later deterministic text-normalization migration with its own version.

## Future Split Contract

The future canonical schema separates this table into:

```text
benzinga_news_event_v1
benzinga_news_text_v1
benzinga_news_url_v1
benzinga_news_attachment_v1
```

That split is not the current loaded corpus. Do not insert the 42-column files into the 34-column event table.

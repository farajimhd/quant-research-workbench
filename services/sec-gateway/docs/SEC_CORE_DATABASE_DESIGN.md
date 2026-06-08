# SEC Core Database Design

This document defines the target SEC data system for historical initial fill, gap fill, and live `sec-gateway` ingestion. The database should support training datasets, production serving, filing text search, financial statement features, and market-reaction labeling.

The target ClickHouse database name is `sec_core`.

## Goals

- Store SEC filing metadata with exact accepted timestamps.
- Store filing documents and extracted text in a queryable table.
- Store CIK-to-ticker and CIK-to-exchange mappings from official SEC files.
- Store XBRL company facts for fundamentals and financial statement features.
- Use one canonical schema for historical and live data.
- Make every canonical row traceable to a raw SEC source artifact.
- Avoid per-day filing caps or any partial-day normalized output.
- Avoid `.hdr.sgml` header downloads as the primary timestamp source.

## Official Source Inputs

### Bulk Files

- `submissions.zip`: public EDGAR filing history by filer. This is the primary source for `acceptanceDateTime`.
- `companyfacts.zip`: XBRL company facts by CIK.
- `company_tickers.json`: ticker, CIK, and EDGAR conformed company name associations.
- `company_tickers_exchange.json`: ticker, exchange, CIK, and EDGAR conformed company name associations.
- `company_tickers_mf.json`: fund CIK, series, class, and ticker mappings.

### Daily Files

- SEC daily `.nc.tar.gz` feed archives under `Archives/edgar/Feed/YYYY/QTRn/YYYYMMDD.nc.tar.gz`.
- These contain SGML `.nc` filing containers. They are the historical filing-content source and should be parsed for filing/document structure.

### Live Sources

- SEC current filings Atom feed.
- Filing detail pages and accession/document URLs discovered from the live feed.
- Submissions API lookup for the filing accession/CIK when exact accepted time is needed before the next nightly bulk refresh.

## Database Layers

The schema is split into source tracking, canonical filing metadata, filing documents/text, company identity/mapping, raw XBRL facts, and derived fundamentals.

### `sec_raw_source_file_v1`

Tracks every downloaded source artifact.

Primary purpose:
- Reproducibility.
- Gap detection.
- Reprocessing by source file.
- Audit trail for every downstream canonical row.

Suggested columns:

| Column | Type | Notes |
| --- | --- | --- |
| `source_file_id` | `String` | Stable source id, preferably SHA-based or path-based. |
| `source_kind` | `LowCardinality(String)` | `submissions_bulk`, `companyfacts_bulk`, `daily_feed_archive`, `company_tickers`, `company_tickers_exchange`, `company_tickers_mf`, `live_feed`, `filing_detail`, `filing_document`. |
| `source_url` | `String` | SEC URL or source endpoint. |
| `artifact_path` | `String` | Local path to retained raw artifact. |
| `source_date` | `Nullable(Date)` | Daily feed date when applicable. |
| `downloaded_at_utc` | `DateTime64(9, 'UTC')` | Download completion time. |
| `byte_size` | `UInt64` | Raw artifact size. |
| `sha256` | `String` | Content hash. |
| `status` | `LowCardinality(String)` | `ok`, `missing`, `failed`, `skipped_size_cap`. |
| `error` | `String` | Error detail when not `ok`. |

Recommended engine:
- `ReplacingMergeTree(downloaded_at_utc)`
- Partition by `toYYYYMM(downloaded_at_utc)`
- Order by `(source_kind, source_date, source_file_id)`

### `sec_company_v1`

One row per CIK/company identity snapshot from submissions data.

Suggested columns:

| Column | Type | Notes |
| --- | --- | --- |
| `cik` | `String` | Zero-padded 10-digit CIK. |
| `entity_name` | `String` | Current conformed name from SEC. |
| `sic` | `Nullable(String)` | SEC SIC code. |
| `sic_description` | `Nullable(String)` | SEC SIC description. |
| `ein` | `Nullable(String)` | Employer identification number when present. |
| `category` | `Nullable(String)` | SEC category field when present. |
| `fiscal_year_end` | `Nullable(String)` | SEC `fiscalYearEnd`. |
| `state_of_incorporation` | `Nullable(String)` | SEC state field when present. |
| `addresses_json` | `String` | Compact JSON for mailing/business addresses. |
| `former_names_json` | `String` | Compact JSON for prior names. |
| `source_file_id` | `String` | Source from `sec_raw_source_file_v1`. |
| `last_seen_at_utc` | `DateTime64(9, 'UTC')` | Last time this company record was loaded. |

Recommended engine:
- `ReplacingMergeTree(last_seen_at_utc)`
- Order by `(cik)`

### `sec_company_ticker_v1`

CIK/ticker/exchange/fund mappings from official SEC mapping files.

Suggested columns:

| Column | Type | Notes |
| --- | --- | --- |
| `mapping_id` | `String` | Stable key from source + CIK + ticker + optional series/class. |
| `cik` | `String` | Zero-padded 10-digit CIK. |
| `ticker` | `String` | SEC ticker text. |
| `exchange` | `Nullable(String)` | From `company_tickers_exchange.json` when available. |
| `company_name` | `String` | SEC conformed name. |
| `mapping_source` | `LowCardinality(String)` | `company_tickers`, `company_tickers_exchange`, `company_tickers_mf`. |
| `series_id` | `Nullable(String)` | Fund mapping only. |
| `class_id` | `Nullable(String)` | Fund mapping only. |
| `first_seen_at_utc` | `DateTime64(9, 'UTC')` | First time observed in this database. |
| `last_seen_at_utc` | `DateTime64(9, 'UTC')` | Last load time. |
| `is_active` | `UInt8` | Active in latest mapping snapshot. |
| `source_file_id` | `String` | Source file id. |

Recommended engine:
- `ReplacingMergeTree(last_seen_at_utc)`
- Order by `(cik, mapping_source, ticker, ifNull(series_id, ''), ifNull(class_id, ''))`

### `sec_filing_v1`

Canonical one row per accession. This is the central filing event table.

`accepted_at_utc` must come from submissions bulk/API whenever available. `.hdr.sgml` is only a fallback for missing accessions or reconciliation.

Suggested columns:

| Column | Type | Notes |
| --- | --- | --- |
| `accession_number` | `String` | Dashed SEC accession. |
| `accession_number_compact` | `String` | Accession without dashes. |
| `cik` | `String` | Zero-padded 10-digit filer CIK. |
| `company_name` | `String` | SEC company name at source time. |
| `form_type` | `LowCardinality(String)` | `8-K`, `10-Q`, `4`, `S-1`, etc. |
| `filing_date` | `Nullable(Date)` | SEC filing date. Not suitable for market labels. |
| `report_date` | `Nullable(Date)` | SEC report date/period. |
| `accepted_at_utc` | `Nullable(DateTime64(9, 'UTC'))` | Exact EDGAR accepted time converted to UTC. |
| `acceptance_datetime_raw` | `Nullable(String)` | Raw SEC `acceptanceDateTime`. |
| `accepted_at_source` | `LowCardinality(String)` | `submissions_bulk`, `submissions_api`, `hdr_sgml`, `missing`. |
| `primary_document` | `Nullable(String)` | SEC primary document name. |
| `primary_document_url` | `Nullable(String)` | URL when known. |
| `filing_detail_url` | `Nullable(String)` | SEC detail page URL. |
| `document_count` | `Nullable(UInt16)` | Document count from submissions or parsed filing. |
| `filing_size` | `Nullable(UInt64)` | Size from submissions data when present. |
| `items` | `Nullable(String)` | SEC `items` string for applicable forms. |
| `act` | `Nullable(String)` | SEC metadata. |
| `file_number` | `Nullable(String)` | SEC metadata. |
| `film_number` | `Nullable(String)` | SEC metadata. |
| `source_kind` | `LowCardinality(String)` | Primary source for row version. |
| `source_file_id` | `String` | Source id. |
| `raw_submission_json` | `String` | Compact JSON for source filing row. |
| `last_seen_at_utc` | `DateTime64(9, 'UTC')` | Load/reconciliation time. |

Recommended engine:
- `ReplacingMergeTree(last_seen_at_utc)`
- Partition by `toYYYYMM(coalesce(accepted_at_utc, toDateTime64(filing_date, 9, 'UTC')))`
- Order by `(cik, accession_number)`
- Add projections or views ordered by `(accepted_at_utc, accession_number)` for market-label joins.

### `sec_filing_document_v1`

One row per filing document.

Suggested columns:

| Column | Type | Notes |
| --- | --- | --- |
| `document_id` | `String` | Stable key from accession + sequence + document name. |
| `accession_number` | `String` | Parent accession. |
| `cik` | `String` | Parent CIK. |
| `sequence` | `UInt16` | SEC document sequence. |
| `document_name` | `String` | Filename. |
| `document_type` | `LowCardinality(String)` | `10-Q`, `EX-99.1`, `GRAPHIC`, etc. |
| `description` | `String` | SEC description. |
| `document_url` | `String` | Download URL. |
| `content_type` | `LowCardinality(String)` | Inferred or HTTP content type. |
| `byte_size` | `UInt64` | Downloaded byte size. |
| `sha256` | `String` | Raw document hash. |
| `artifact_path` | `String` | Raw document path. |
| `text_sha256` | `String` | Extracted text hash when available. |
| `text_length` | `UInt64` | Extracted text length. |
| `extraction_status` | `LowCardinality(String)` | `extracted`, `empty_text`, `metadata_only`, `download_failed`, `size_cap`, `unsupported_type`. |
| `extraction_error` | `String` | Error detail. |
| `source_file_id` | `String` | Source id for document artifact. |
| `downloaded_at_utc` | `DateTime64(9, 'UTC')` | Download/load time. |

Recommended engine:
- `ReplacingMergeTree(downloaded_at_utc)`
- Partition by `toYYYYMM(downloaded_at_utc)`
- Order by `(accession_number, sequence, document_name)`

### `sec_filing_text_v1`

Stores extracted text in ClickHouse. This table is intentionally separate from `sec_filing_document_v1` so metadata queries do not scan large text columns.

Suggested columns:

| Column | Type | Notes |
| --- | --- | --- |
| `document_id` | `String` | Same id as `sec_filing_document_v1`. |
| `accession_number` | `String` | Parent accession. |
| `cik` | `String` | Parent CIK. |
| `sequence` | `UInt16` | SEC document sequence. |
| `document_name` | `String` | Filename. |
| `document_type` | `LowCardinality(String)` | SEC document type. |
| `text` | `String` | Normalized extracted text. |
| `text_length` | `UInt64` | Character or byte length; define consistently as UTF-8 bytes. |
| `text_sha256` | `String` | Hash of normalized text. |
| `extraction_method` | `LowCardinality(String)` | `html_text`, `xml_text`, `txt`, `pdf_text`, etc. |
| `normalized_at_utc` | `DateTime64(9, 'UTC')` | Text normalization time. |

Recommended engine:
- `ReplacingMergeTree(normalized_at_utc)`
- Partition by `toYYYYMM(normalized_at_utc)`
- Order by `(accession_number, sequence, document_name)`
- Compression should be stronger than metadata tables, for example ZSTD. Exact codec should be benchmarked before bulk load.

### `sec_xbrl_fact_v1`

Canonical company facts from `companyfacts.zip` and incremental refreshes.

Suggested columns:

| Column | Type | Notes |
| --- | --- | --- |
| `fact_id` | `String` | Stable key from CIK + taxonomy + tag + unit + period + accession + dimensions. |
| `cik` | `String` | Company CIK. |
| `taxonomy` | `LowCardinality(String)` | `us-gaap`, `dei`, etc. |
| `tag` | `String` | XBRL concept tag. |
| `unit` | `LowCardinality(String)` | XBRL unit code. |
| `value` | `Float64` | Numeric value. |
| `start_date` | `Nullable(Date)` | Fact start date. |
| `end_date` | `Nullable(Date)` | Fact end date. |
| `filed_at_utc` | `Nullable(DateTime64(9, 'UTC'))` | Use `sec_filing_v1.accepted_at_utc` when joined by accession. |
| `fy` | `Nullable(UInt16)` | Fiscal year. |
| `fp` | `Nullable(String)` | Fiscal period. |
| `form_type` | `Nullable(String)` | Filing form. |
| `frame` | `Nullable(String)` | SEC frame when present. |
| `accession_number` | `Nullable(String)` | Source accession. |
| `dimensions_json` | `String` | Reserved for dimensions/segments if needed. |
| `source_file_id` | `String` | Source id. |
| `last_seen_at_utc` | `DateTime64(9, 'UTC')` | Load/reconciliation time. |

Recommended engine:
- `ReplacingMergeTree(last_seen_at_utc)`
- Partition by `toYYYYMM(coalesce(end_date, toDate('1970-01-01')))`
- Order by `(cik, taxonomy, tag, unit, end_date, accession_number)`

### `sec_fundamental_snapshot_v1`

Derived table for model features and production serving.

This table should be computed from `sec_xbrl_fact_v1`, not directly from raw JSON. It can be rebuilt when derivation rules improve.

Example fields:
- revenue TTM
- net income TTM
- assets
- liabilities
- stockholders equity
- cash and equivalents
- operating cash flow
- shares outstanding
- EPS
- book value
- debt ratios
- source accession numbers used for each value

Recommended engine:
- `ReplacingMergeTree(computed_at_utc)`
- Order by `(cik, as_of_date, snapshot_version)`

## Historical Initial Fill

1. Create `sec_core` and all tables.
2. Download and register bulk artifacts:
   - `submissions.zip`
   - `companyfacts.zip`
   - `company_tickers.json`
   - `company_tickers_exchange.json`
   - `company_tickers_mf.json`
3. Parse ticker mapping files into `sec_company_ticker_v1`.
4. Parse `submissions.zip`:
   - populate `sec_company_v1`
   - populate `sec_filing_v1`
   - use `acceptanceDateTime` as `accepted_at_utc`
5. Parse `companyfacts.zip` into `sec_xbrl_fact_v1`.
6. Download daily `.nc.tar.gz` feed archives for the target historical period.
7. Parse every `.nc` filing in each archive:
   - enrich by accession from `sec_filing_v1`
   - populate or refine `sec_filing_document_v1`
   - populate `sec_filing_text_v1`
   - do not use per-day filing limits
8. Run reconciliation:
   - missing accession in submissions
   - missing `accepted_at_utc`
   - daily feed accession not in `sec_filing_v1`
   - `sec_filing_v1` accession without downloaded content when content is expected
9. Build `sec_fundamental_snapshot_v1`.

## Gap Fill

Gap fill should run regularly and should be able to recover from missed service downtime.

Nightly:
- Redownload or refresh `submissions.zip`.
- Upsert new/changed companies and filings.
- Redownload or refresh `companyfacts.zip`.
- Upsert new/changed facts.
- Download any missing daily feed archives since the last complete feed date.
- Parse missing filing content and text.
- Recompute affected fundamental snapshots.

Intraday:
- For new live accessions, query submissions API by CIK when `accepted_at_utc` is missing from local data.
- Insert canonical filing row immediately.
- Let nightly bulk refresh reconcile any missing or corrected fields.

## Live `sec-gateway`

Live flow should write into the same canonical tables as historical fill.

1. Poll SEC current filings feed.
2. Extract CIK, accession, form type, filing date, feed update time, and detail URL.
3. Check `sec_filing_v1` by accession.
4. If missing or missing `accepted_at_utc`, query submissions API for that CIK/accession.
5. Insert or update `sec_filing_v1`.
6. Download detail page and documents asynchronously.
7. Insert `sec_filing_document_v1`.
8. Extract text and insert `sec_filing_text_v1`.
9. Keep recent filings in memory for app APIs.
10. Emit canonical filing events to downstream model/news systems.

## Timestamp Rules

- `accepted_at_utc` is the event timestamp for market-reaction labels.
- `filing_date` must never be used for intraday market-reaction labels.
- Timestamp priority:
  1. `submissions_bulk.acceptanceDateTime`
  2. submissions API `acceptanceDateTime`
  3. `.hdr.sgml` fallback
  4. missing timestamp
- Store the raw SEC timestamp in `acceptance_datetime_raw`.
- Store the source in `accepted_at_source`.

## Text Storage Rules

- Store extracted text in `sec_filing_text_v1`.
- Keep document metadata in `sec_filing_document_v1`.
- Use `text_sha256` to dedupe and validate extracted text.
- Use stronger compression for text table than metadata tables.
- Preserve artifact paths for raw document bytes even when text is stored in ClickHouse.

## Relationship To News Gateway

SEC filings should be treated as first-class news-like events for model training and production inference.

The eventual model event stream should combine:
- Benzinga/news events from `news-gateway`.
- SEC filing events from `sec-gateway`.
- Filing text from `sec_filing_text_v1`.
- Ticker/security mappings from `sec_company_ticker_v1` plus the market reference bridge.
- Market state at `accepted_at_utc`.

## Open Implementation Questions

- Exact `sec_core` storage policy name.
- Whether `sec_filing_text_v1.text` should use default compression or explicit ZSTD codec.
- Whether daily feed archives should remain on HDD while normalized tables/text live on SSD.
- Whether `sec_fundamental_snapshot_v1` should be one wide table or multiple feature-family tables.
- How to map CIK to durable security/listing ids when one CIK maps to many share classes or tickers.

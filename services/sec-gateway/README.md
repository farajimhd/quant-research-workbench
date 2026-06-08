# sec-gateway

`sec-gateway` is the live SEC filing feed service for the new quote/trade/news regime. It polls the SEC current filings Atom feed, creates canonical filing events, downloads filing documents, extracts text for supported document types, writes the output to ClickHouse, and exposes a small local API for the app backend.

The gateway only handles live SEC filing acquisition and document normalization. It does not solve ticker mapping, signal generation, portfolio logic, or model inference. Those belong in the app backend or downstream intelligence services.

## Runtime Flow

1. Poll `SEC_LATEST_FEED_URL`.
2. Parse each Atom entry into a SEC filing candidate.
3. Dedupe by `(cik, accession_number)` in memory.
4. Save the raw feed item to disk.
5. Download and save the filing detail page.
6. Extract filing metadata:
   - CIK
   - company name
   - accession number
   - form type
   - filing date
   - feed update time
   - SEC accepted time, when available from the filing detail page or accession `.txt`
7. Parse the filing detail page for document links.
8. Download up to `SEC_MAX_DOCUMENTS_PER_FILING` documents, capped by `SEC_DOCUMENT_MAX_BYTES`.
9. Extract text from HTML, XML, TXT, and similar text files.
10. Mark PDFs and image/binary files as metadata-only until a Rust-side PDF extractor is added.
11. Batch-write filing events and document rows to ClickHouse.
12. Keep recent filing summaries in memory for `/sec/recent`.

If a filing fails before it is built and queued, it is not marked as seen. The next poll can retry it.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `SEC_ARTIFACT_ROOT_WIN` | `D:/market-data/sec_live` | Root folder for raw feed items, filing detail pages, accession text, and downloaded documents. |
| `SEC_GATEWAY_BIND` | `127.0.0.1:8798` | Local HTTP API bind address. |
| `SEC_LATEST_FEED_URL` | SEC current filings Atom feed | Live filing feed URL. |
| `SEC_FEED_POLL_INTERVAL_MS` | `5000` | Poll interval for the SEC feed. |
| `SEC_REQUEST_TIMEOUT_MS` | `10000` | HTTP request timeout. |
| `SEC_DOCUMENT_MAX_BYTES` | `12000000` | Per-document download cap. Larger documents are recorded as failed/metadata-only rather than fully downloaded. |
| `SEC_MAX_DOCUMENTS_PER_FILING` | `8` | Maximum documents downloaded per filing event. |
| `SEC_RECENT_HISTORY_LIMIT` | `5000` | Number of recent summaries kept in memory. |
| `SEC_CLICKHOUSE_URL` | `QMD_CLICKHOUSE_URL`, then `http://localhost:8123` | ClickHouse HTTP endpoint. |
| `SEC_CLICKHOUSE_DATABASE` | `QMD_CLICKHOUSE_DATABASE`, then `q_live` | Output database. |
| `SEC_CLICKHOUSE_USER` | `QMD_CLICKHOUSE_USER`, then `default` | ClickHouse user. |
| `SEC_CLICKHOUSE_PASSWORD` | `NEWS_CLICKHOUSE_PASSWORD`, then `QMD_CLICKHOUSE_PASSWORD` | ClickHouse password. |
| `SEC_CLICKHOUSE_STORAGE_POLICY` | `CLICKHOUSE_LIVE_STORAGE_POLICY` | Storage policy used when creating tables. |
| `SEC_EVENT_TABLE` | `live_sec_filing_events_v1` | Filing event output table. |
| `SEC_DOCUMENT_TABLE` | `live_sec_filing_documents_v1` | Filing document output table. |
| `SEC_CLICKHOUSE_MAX_BATCH` | `1000` | Max event or document rows per insert flush. |
| `SEC_CLICKHOUSE_FLUSH_INTERVAL_MS` | `1000` | Max writer delay before flushing partial batches. |
| `SEC_WRITER_CHANNEL_CAPACITY` | `100000` | In-memory queue capacity between poller and writer. |
| `SEC_USER_AGENT` | fallback to `NEWS_SEC_USER_AGENT`, then `SEC_EDGAR_USER_AGENT` | SEC-compliant user agent. Set this before production use. |

## Local API

| Route | Output |
| --- | --- |
| `GET /health` | `{"status":"ok"}` |
| `GET /config` | Sanitized runtime configuration. Secret values are not returned. |
| `GET /sec/recent?limit=100` | Recent filing summaries from memory. |

## ClickHouse Output Schema

### `live_sec_filing_events_v1`

One row per live SEC filing event keyed by `(session_date, provider, accession_number, cik)`.

| Column | Type | Meaning |
| --- | --- | --- |
| `session_date` | `Date` | Gateway processing date in UTC. |
| `schema_version` | `UInt16` | Output schema version. Current value is `1`. |
| `provider` | `LowCardinality(String)` | Always `sec`. |
| `event_type` | `LowCardinality(String)` | Always `sec_filing`. |
| `event_id` | `String` | Stable event key: `sec:{cik}:{accession_number}`. |
| `cik` | `String` | Zero-padded 10-digit SEC CIK. |
| `company_name` | `String` | Company or reporting-owner name from the Atom title. |
| `accession_number` | `String` | SEC accession with dashes. |
| `accession_number_compact` | `String` | Accession without dashes. |
| `form_type` | `LowCardinality(String)` | SEC form type such as `8-K`, `10-Q`, `4`, or `S-1`. |
| `filing_date` | `Nullable(Date)` | Filed date from the feed, when present. |
| `accepted_at_utc` | `Nullable(DateTime64(9, 'UTC'))` | SEC acceptance timestamp, when available. |
| `feed_updated_at_utc` | `Nullable(DateTime64(9, 'UTC'))` | Atom entry update time converted to UTC. |
| `gateway_seen_at_utc` | `DateTime64(9, 'UTC')` | Time the gateway processed the event. |
| `feed_url` | `String` | Feed URL used for discovery. |
| `detail_url` | `String` | SEC filing detail page URL. |
| `primary_document` | `String` | First document name from the filing detail table. |
| `primary_document_url` | `String` | URL for the first document. |
| `document_count` | `UInt16` | Number of document links found on the filing page. |
| `parsed_document_count` | `UInt16` | Number of downloaded documents with extracted text. |
| `extraction_status` | `LowCardinality(String)` | Event-level extraction status: `partial_or_complete`, `metadata_only`, or `no_documents`. |
| `extraction_error` | `String` | Reserved event-level error field. |
| `artifact_root` | `String` | Artifact root used by this service. |
| `raw_feed_artifact_path` | `String` | Saved raw Atom entry JSON path. |
| `detail_artifact_path` | `String` | Saved SEC detail HTML path. |
| `raw_feed_json` | `String` | Raw Atom entry payload captured as compact JSON. |

### `live_sec_filing_documents_v1`

One row per downloaded SEC filing document keyed by `(session_date, event_id, sequence, document_name)`.

| Column | Type | Meaning |
| --- | --- | --- |
| `session_date` | `Date` | Gateway processing date in UTC. |
| `schema_version` | `UInt16` | Output schema version. Current value is `1`. |
| `event_id` | `String` | Parent filing event key. |
| `cik` | `String` | Parent filing CIK. |
| `accession_number` | `String` | Parent accession number. |
| `sequence` | `UInt16` | SEC document sequence number. |
| `document_name` | `String` | SEC document filename. |
| `document_type` | `LowCardinality(String)` | SEC document type from the filing detail table. |
| `description` | `String` | SEC document description from the filing detail table. |
| `document_url` | `String` | Document download URL. |
| `content_type` | `LowCardinality(String)` | Inferred content type from the filename extension. |
| `byte_length` | `UInt64` | Downloaded byte length. |
| `content_sha256` | `String` | SHA-256 hash of downloaded bytes. |
| `artifact_path` | `String` | Saved document path. |
| `text_hash` | `String` | BLAKE2 hash prefix of extracted text. |
| `extracted_text` | `String` | Normalized text for supported document types. |
| `extraction_status` | `LowCardinality(String)` | `extracted`, `empty_text`, `pdf_text_not_supported`, `download_failed`, or `artifact_write_failed`. |
| `extraction_error` | `String` | Download or file-write error text. |
| `downloaded_at_utc` | `DateTime64(9, 'UTC')` | Time the document row was created. |

## Current Limitations

- PDF text extraction is intentionally not implemented in the Rust service yet. PDF documents are saved when under the byte cap, but their text status is `pdf_text_not_supported`.
- CIK-to-tradable-ticker mapping is not done in this gateway. The app backend should join SEC CIK/company data to the market reference bridge once that bridge is finalized.
- The in-memory dedupe set is reset when the service restarts. ClickHouse uses replacing tables so replayed rows with the same keys collapse logically.

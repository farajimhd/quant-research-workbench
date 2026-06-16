# SEC Filing Text Data Contract

SEC normalized text extraction is the next stage after archive validation succeeds. This contract describes the target output that extraction scripts should produce.

The raw `.nc.tar.gz` archives remain on disk. ClickHouse should store compact metadata and normalized text, not every raw archive member.

## Existing Metadata Table: `q_live.sec_filing_v2`

`sec_filing_v2` stores filing-level metadata and exact acceptance time:

```sql
filing_id String,
accession_number String,
accession_number_compact String,
cik String,
issuer_id Nullable(String),
company_name Nullable(String),
form_type LowCardinality(String),
filing_date Nullable(Date),
report_date Nullable(Date),
accepted_at_utc Nullable(DateTime64(9, 'UTC')),
acceptance_datetime_raw Nullable(String),
accepted_at_source LowCardinality(String),
primary_document Nullable(String),
primary_document_url Nullable(String),
filing_detail_url Nullable(String),
source_file_name String,
filing_size Nullable(UInt64),
items Nullable(String),
text_status LowCardinality(String),
source_run_id String,
source_content_sha256 String,
inserted_at DateTime64(3, 'UTC')
```

The accepted timestamp backfill is already handled. Text extraction should update or reload `text_status` only through a controlled loader.

## Document Metadata Table: `q_live.sec_filing_document_v1`

One row per extracted SEC document:

```sql
document_id String,
accession_number String,
cik String,
sequence_number Nullable(UInt16),
document_name String,
document_type Nullable(String),
description Nullable(String),
document_url Nullable(String),
local_artifact_path Nullable(String),
mime_type Nullable(String),
byte_size Nullable(UInt64),
content_sha256 Nullable(String),
extraction_status LowCardinality(String),
extraction_error Nullable(String),
source_run_id String,
inserted_at DateTime64(3, 'UTC')
```

`document_id` should be deterministic, for example hash of `(cik, accession_number, sequence_number, document_name)`.

## Text Table: `q_live.sec_filing_text_v1`

One row per normalized text representation:

```sql
document_id String,
accession_number String,
cik String,
text_kind LowCardinality(String),
text String CODEC(ZSTD(6)),
text_char_count UInt64,
extraction_method LowCardinality(String),
extracted_at_utc DateTime64(3, 'UTC'),
source_run_id String,
inserted_at DateTime64(3, 'UTC')
```

Recommended `text_kind` values:

| Value | Meaning |
| --- | --- |
| `primary_document` | Clean text from the filing primary document. |
| `exhibit` | Clean text from a non-primary exhibit that has useful prose. |
| `full_filing_combined` | Optional combined text for modeling. Use only if storage budget allows. |

Recommended `extraction_method` values:

| Value | Meaning |
| --- | --- |
| `html_text_v1` | HTML/XML-ish text cleaned with deterministic tag removal and whitespace normalization. |
| `plain_text_v1` | Plain text normalized without HTML parsing. |
| `pdf_text_v1` | PDF text extraction when no better text source exists. |
| `skipped_binary` | Binary/image/unsupported payload skipped. |
| `skipped_xbrl` | XBRL sidecar skipped because structured XBRL belongs in fact/frame tables. |

## Extraction Rules

1. Parse daily `.nc.tar.gz` archives after targeted validation is clean.
2. Read each `.nc` filing container from the archive stream.
3. Extract filing header metadata and document blocks.
4. Prefer primary HTML/plain-text filing document for modeling text.
5. Extract important prose exhibits such as `EX-99.1`, merger/proxy documents, material contracts, and press releases.
6. Skip images, CSS, JavaScript, raw XBRL sidecars, schemas, ZIPs, and spreadsheets for text table purposes.
7. Extract PDF text only when the PDF is the primary meaningful text or no HTML/plain text equivalent is present.
8. Record skipped documents in `sec_filing_document_v1` with `extraction_status`, but do not create empty text rows unless needed for diagnostics.

## Normalized Part Files

The extraction script should write DB-ready JSONEachRow parts before ClickHouse load:

```text
sec_filing_document_parts/sec_filing_document_part_*.jsonl
sec_filing_text_parts/sec_filing_text_part_*.jsonl
```

Each run must write a manifest containing:

```text
run_id
source_archive_root
date_range
archive_count
document_rows
text_rows
error_count
part_files with rows and bytes
normalizer_version
loaded_env_files with secret presence only
```

## Validation Before Insert

Before loading to ClickHouse:

- no failed archives in the selected archive validation;
- part file row counts match manifest;
- no duplicate `(document_id, text_kind)` in text parts;
- no empty `accession_number` or `cik`;
- primary filing text coverage is measured by form type;
- sample `10-K`, `10-Q`, `8-K`, `6-K`, proxy, and `EX-99.1` rows are manually inspected.


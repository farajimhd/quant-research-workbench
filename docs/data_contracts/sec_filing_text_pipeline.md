# SEC Filing Text Data Contract

SEC normalized text extraction is the next stage after archive validation succeeds. This contract describes the target output that extraction scripts should produce.

As of the 2026-06-16 ClickHouse check, `q_live.sec_filing_v2`, `q_live.sec_filing_document_v1`, and `q_live.sec_filing_text_v1` exist. `sec_filing_text_v1` has zero rows, so the text contract below is still the target for the next loader.

Important lineage finding: current `q_live.sec_filing_document_v1` is not archive-derived. It was built by migration step 6 from `q_live.sec_filing_v2.primary_document` as a provisional bridge. The archive extractor must not treat the current document rows as the final document source of truth.

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

`sec_filing_v2` is the filing-level parent table. It was migrated from `trading_dashboard_dev.sec_filing_v1` in migration step 4, then repaired by the SEC acceptance-timestamp backfill scripts and migration step 7. Text extraction should not repair filing metadata implicitly; metadata repair belongs in a separate controlled step. Text extraction should update or reload `text_status` only through a controlled loader.

Current observed logical counts from `FINAL`:

```text
q_live.sec_filing_v2 rows: 8,531,118
q_live.sec_filing_v2 missing accepted_at_utc: 0
q_live.sec_filing_v2 duplicate (cik, accession_number): 0
q_live.sec_filing_document_v1 rows: 8,417,763
q_live.sec_filing_text_v1 rows: 0
```

## Document Metadata Table: `q_live.sec_filing_document_v1`

Target meaning: one row per extracted SEC document block. Current meaning: one synthetic primary-document row per filing with `primary_document`.

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

Current `sec_filing_document_v1` fingerprint:

```text
source_run_id: step_06_bridge_features_20260609_161534
extraction_status: metadata_only
description: primary_document_from_sec_filing_metadata
document_name = sec_filing_v2.primary_document for all rows
document_url = sec_filing_v2.primary_document_url for all rows
sequence_number = 1 for all rows
document_type = sec_filing_v2.form_type for all rows
document rows without filing parent: 0
filings without document rows: 113,355
documents per accession: exactly 1
```

Therefore `sec_filing_document_v1` should be rebuilt from daily archive `<DOCUMENT>` blocks or superseded by `sec_filing_document_v2`. Creating `v2` is safer until archive-derived coverage and quality are validated.

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

The `text` field should contain clean LLM-ready body text only. Do not embed filing metadata headers in the stored text. Training/export jobs can add prompt headers by joining `sec_filing_text_v1` to document and filing metadata.

Recommended `text_kind` values:

| Value | Meaning |
| --- | --- |
| `primary_document` | Clean text from the filing primary document. |
| `exhibit` | Clean text from a non-primary exhibit that has useful prose. |
| `press_release_exhibit` | Clean body text from `EX-99.1`, `EX-99`, or related press-release exhibits. |
| `material_exhibit` | Clean body text from material agreements such as useful `EX-10*` documents. |
| `proxy_document` | Clean body text from proxy or merger/proxy material. |
| `prospectus` | Clean body text from prospectus documents such as selected `424B*`, `S-1`, `F-1`, or related forms. |
| `other_text_exhibit` | Useful prose text that does not fit the categories above. |

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
8. Record skipped documents in archive-derived document metadata with `extraction_status`, but do not create empty text rows unless needed for diagnostics.
9. Store one text row per useful document block. Do not store a concatenated filing text row as the canonical table representation. Accession-level LLM inputs can be assembled at training time by query.

## Existing Structured SEC Data

XBRL and frame data already exist in `q_live`:

```text
q_live.sec_xbrl_company_fact_v1
q_live.sec_xbrl_frame_observation_v1
q_live.sec_xbrl_frame_v1
q_live.sec_xbrl_concept_v1
```

The text extractor should skip XBRL sidecars such as `EX-101.*`, schemas, labels, calculations, presentations, and raw XML that is already represented by the structured SEC/XBRL tables.

## Integrity Gates Before Extractor

Before loading normalized text, run an integrity audit that verifies:

- `sec_filing_v2 FINAL` has no duplicate `(cik, accession_number)`.
- `sec_filing_v2 FINAL` has no missing `accepted_at_utc`.
- current `sec_filing_document_v1` is treated as provisional, not as archive-derived source truth.
- archive-derived document parts have no document rows without a filing parent.
- archive-derived document parts have deterministic unique document ids.
- text parts have no rows without an archive-derived document parent.
- text parts have no duplicate `(document_id, text_kind)`.
- XBRL accession references are profiled separately and not silently assumed to join perfectly to `sec_filing_v2`.
- extraction coverage is reported by accepted year, form type, document type, and text kind.
- manual samples for `10-K`, `10-Q`, `8-K`, `6-K`, proxy, prospectus, `EX-99.1`, and `EX-10*` are inspected.

## Normalized Part Files

The extraction script should write DB-ready JSONEachRow parts before ClickHouse load:

```text
sec_filing_document_parts/sec_filing_document_part_*.jsonl
sec_filing_text_parts/sec_filing_text_part_*.jsonl
sec_filing_skip_parts/sec_filing_skip_part_*.jsonl
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

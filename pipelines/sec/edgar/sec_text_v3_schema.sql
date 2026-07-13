CREATE TABLE IF NOT EXISTS q_live.sec_filing_document_v3
(
    document_id String,
    filing_id String,
    accession_number String,
    accession_number_compact String,
    cik String,
    sequence_number UInt32,
    document_name String,
    document_type LowCardinality(String),
    document_role LowCardinality(String),
    description Nullable(String),
    document_url Nullable(String),
    source_archive_date Date,
    source_archive_member String,
    source_archive_path Nullable(String),
    file_extension LowCardinality(String),
    content_format LowCardinality(String),
    mime_type Nullable(String),
    byte_size UInt64,
    payload_char_count UInt64,
    content_sha256 String,
    text_sha256 Nullable(String),
    has_normalized_text UInt8,
    extraction_status LowCardinality(String),
    extraction_error Nullable(String),
    normalizer_version LowCardinality(String),
    source_run_id String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY cityHash64(cik) % 64
ORDER BY (cik, accession_number, sequence_number, document_id)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.sec_filing_text_v3
(
    document_id String,
    filing_id String,
    accession_number String,
    accession_number_compact String,
    cik String,
    sequence_number UInt32,
    document_name String,
    document_type LowCardinality(String),
    document_role LowCardinality(String),
    description Nullable(String),
    document_url Nullable(String),
    text_kind LowCardinality(String),
    source_archive_date Date,
    source_archive_member String,
    source_archive_path Nullable(String),
    file_extension LowCardinality(String),
    content_format LowCardinality(String),
    mime_type Nullable(String),
    source_text String CODEC(ZSTD(9)),
    source_text_char_count UInt64,
    source_text_byte_count UInt64,
    content_sha256 String,
    normalizer_version LowCardinality(String),
    source_run_id String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(source_archive_date)
ORDER BY (cik, accession_number, document_id, content_format)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.sec_filing_text_rendered_v3
(
    document_id String,
    filing_id String,
    accession_number String,
    accession_number_compact String,
    cik String,
    text_kind LowCardinality(String),
    text String CODEC(ZSTD(6)),
    text_char_count UInt64,
    text_byte_count UInt64,
    text_sha256 String,
    extraction_method LowCardinality(String),
    normalizer_version LowCardinality(String),
    quality_flags Array(String),
    source_archive_date Date,
    source_archive_member String,
    extracted_at_utc DateTime64(3, 'UTC'),
    source_run_id String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY cityHash64(cik) % 64
ORDER BY (cik, accession_number, document_id, text_kind)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.sec_filing_document_skip_v3
(
    skip_id String,
    document_id String,
    filing_id String,
    accession_number String,
    accession_number_compact String,
    cik String,
    sequence_number UInt32,
    document_name String,
    document_type LowCardinality(String),
    document_role LowCardinality(String),
    source_archive_date Date,
    source_archive_member String,
    content_format LowCardinality(String),
    file_extension LowCardinality(String),
    skip_reason LowCardinality(String),
    quality_flags Array(String),
    extraction_error Nullable(String),
    normalizer_version LowCardinality(String),
    source_run_id String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY cityHash64(cik) % 64
ORDER BY (cik, accession_number, document_id, skip_reason)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

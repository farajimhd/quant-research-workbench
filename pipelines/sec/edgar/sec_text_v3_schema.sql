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
    source_version_key String,
    source_revision_at DateTime64(3, 'UTC'),
    source_revision_rank UInt64,
    source_revision_kind LowCardinality(String),
    pac_event_id Nullable(String),
    source_run_id String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(source_revision_rank)
PARTITION BY cityHash64(cik) % 64
ORDER BY (cik, accession_number, sequence_number, document_id)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.sec_filing_entity_v3
(
    relationship_id String,
    filing_id Nullable(String),
    accession_number String,
    accession_number_compact String,
    primary_cik String,
    entity_cik String,
    entity_role LowCardinality(String),
    entity_name Nullable(String),
    source_section_ordinal UInt16,
    source_archive_date Date,
    source_archive_member String,
    source_archive_path Nullable(String),
    source_header_sha256 String,
    source_version_key String,
    source_revision_at DateTime64(3, 'UTC'),
    source_revision_rank UInt64,
    source_revision_kind LowCardinality(String),
    pac_event_id Nullable(String),
    source_run_id String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY cityHash64(accession_number) % 64
ORDER BY (accession_number, source_version_key, entity_role, entity_cik)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE VIEW IF NOT EXISTS q_live.sec_filing_entity_current_v3 AS
SELECT e.*
FROM q_live.sec_filing_entity_v3 FINAL AS e
INNER JOIN
(
    SELECT accession_number,
           argMax(source_version_key, tuple(source_revision_rank, source_version_key)) AS source_version_key
    FROM q_live.sec_filing_entity_v3 FINAL
    GROUP BY accession_number
) AS latest USING (accession_number, source_version_key);

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
    source_version_key String,
    source_revision_at DateTime64(3, 'UTC'),
    source_revision_rank UInt64,
    source_revision_kind LowCardinality(String),
    pac_event_id Nullable(String),
    source_run_id String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(source_revision_rank)
PARTITION BY cityHash64(cik) % 64
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
    source_version_key String,
    source_revision_at DateTime64(3, 'UTC'),
    source_revision_rank UInt64,
    source_revision_kind LowCardinality(String),
    pac_event_id Nullable(String),
    extracted_at_utc DateTime64(3, 'UTC'),
    source_run_id String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(source_revision_rank)
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
    source_version_key String,
    source_revision_at DateTime64(3, 'UTC'),
    source_revision_rank UInt64,
    source_revision_kind LowCardinality(String),
    pac_event_id Nullable(String),
    source_run_id String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(source_revision_rank)
PARTITION BY cityHash64(cik) % 64
ORDER BY (cik, accession_number, document_id, skip_reason)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.sec_filing_pac_event_v3
(
    pac_event_id String,
    accession_number String,
    cik String,
    correction_timestamp_raw String,
    correction_order_key UInt64,
    filing_date Nullable(Date),
    date_as_of_change Nullable(Date),
    form_type LowCardinality(String),
    action LowCardinality(String),
    filing_deleted UInt8,
    sequence_number UInt32,
    document_name String,
    document_type LowCardinality(String),
    document_deleted UInt8,
    source_archive_date Date,
    source_archive_member String,
    source_archive_path Nullable(String),
    source_content_sha256 String,
    source_run_id String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(source_archive_date)
ORDER BY (accession_number, pac_event_id)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

-- Existing v3 installations keep their current engine until an explicit cutover,
-- but receive the shared revision lineage needed by the resolver and repair tool.
ALTER TABLE q_live.sec_filing_document_v3 ADD COLUMN IF NOT EXISTS source_version_key String DEFAULT '' AFTER normalizer_version;
ALTER TABLE q_live.sec_filing_document_v3 ADD COLUMN IF NOT EXISTS source_revision_at DateTime64(3, 'UTC') DEFAULT toDateTime64(source_archive_date, 3, 'UTC') AFTER source_version_key;
ALTER TABLE q_live.sec_filing_document_v3 ADD COLUMN IF NOT EXISTS source_revision_rank UInt64 DEFAULT toUInt64(toUnixTimestamp64Milli(source_revision_at)) * 1000000 AFTER source_revision_at;
ALTER TABLE q_live.sec_filing_document_v3 ADD COLUMN IF NOT EXISTS source_revision_kind LowCardinality(String) DEFAULT 'legacy_archive_occurrence' AFTER source_revision_at;
ALTER TABLE q_live.sec_filing_document_v3 ADD COLUMN IF NOT EXISTS pac_event_id Nullable(String) AFTER source_revision_kind;

ALTER TABLE q_live.sec_filing_text_v3 ADD COLUMN IF NOT EXISTS source_version_key String DEFAULT '' AFTER normalizer_version;
ALTER TABLE q_live.sec_filing_text_v3 ADD COLUMN IF NOT EXISTS source_revision_at DateTime64(3, 'UTC') DEFAULT toDateTime64(source_archive_date, 3, 'UTC') AFTER source_version_key;
ALTER TABLE q_live.sec_filing_text_v3 ADD COLUMN IF NOT EXISTS source_revision_rank UInt64 DEFAULT toUInt64(toUnixTimestamp64Milli(source_revision_at)) * 1000000 AFTER source_revision_at;
ALTER TABLE q_live.sec_filing_text_v3 ADD COLUMN IF NOT EXISTS source_revision_kind LowCardinality(String) DEFAULT 'legacy_archive_occurrence' AFTER source_revision_at;
ALTER TABLE q_live.sec_filing_text_v3 ADD COLUMN IF NOT EXISTS pac_event_id Nullable(String) AFTER source_revision_kind;

ALTER TABLE q_live.sec_filing_text_rendered_v3 ADD COLUMN IF NOT EXISTS source_version_key String DEFAULT '' AFTER source_archive_member;
ALTER TABLE q_live.sec_filing_text_rendered_v3 ADD COLUMN IF NOT EXISTS source_revision_at DateTime64(3, 'UTC') DEFAULT toDateTime64(source_archive_date, 3, 'UTC') AFTER source_version_key;
ALTER TABLE q_live.sec_filing_text_rendered_v3 ADD COLUMN IF NOT EXISTS source_revision_rank UInt64 DEFAULT toUInt64(toUnixTimestamp64Milli(source_revision_at)) * 1000000 AFTER source_revision_at;
ALTER TABLE q_live.sec_filing_text_rendered_v3 ADD COLUMN IF NOT EXISTS source_revision_kind LowCardinality(String) DEFAULT 'legacy_archive_occurrence' AFTER source_revision_at;
ALTER TABLE q_live.sec_filing_text_rendered_v3 ADD COLUMN IF NOT EXISTS pac_event_id Nullable(String) AFTER source_revision_kind;

ALTER TABLE q_live.sec_filing_document_skip_v3 ADD COLUMN IF NOT EXISTS source_version_key String DEFAULT '' AFTER normalizer_version;
ALTER TABLE q_live.sec_filing_document_skip_v3 ADD COLUMN IF NOT EXISTS source_revision_at DateTime64(3, 'UTC') DEFAULT toDateTime64(source_archive_date, 3, 'UTC') AFTER source_version_key;
ALTER TABLE q_live.sec_filing_document_skip_v3 ADD COLUMN IF NOT EXISTS source_revision_rank UInt64 DEFAULT toUInt64(toUnixTimestamp64Milli(source_revision_at)) * 1000000 AFTER source_revision_at;
ALTER TABLE q_live.sec_filing_document_skip_v3 ADD COLUMN IF NOT EXISTS source_revision_kind LowCardinality(String) DEFAULT 'legacy_archive_occurrence' AFTER source_revision_at;
ALTER TABLE q_live.sec_filing_document_skip_v3 ADD COLUMN IF NOT EXISTS pac_event_id Nullable(String) AFTER source_revision_kind;

ALTER TABLE q_live.sec_filing_pac_event_v3 ADD COLUMN IF NOT EXISTS correction_timestamp_raw String DEFAULT '' AFTER cik;
ALTER TABLE q_live.sec_filing_pac_event_v3 ADD COLUMN IF NOT EXISTS correction_order_key UInt64 DEFAULT 0 AFTER correction_timestamp_raw;

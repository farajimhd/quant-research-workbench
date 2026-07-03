-- q_live target schema draft.
--
-- Review before execution. This file is intentionally explicit instead of
-- generated from trading_dashboard_dev, because q_live fixes identity and sync
-- problems in the source schema.
--
-- Replace {{CLICKHOUSE_LIVE_STORAGE_POLICY}} with the value of
-- CLICKHOUSE_LIVE_STORAGE_POLICY in the execution script.

CREATE DATABASE IF NOT EXISTS q_live;

-- Shared settings pattern used by every MergeTree table in this draft:
-- SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}'

CREATE TABLE IF NOT EXISTS q_live.source_run_v1
(
    run_id String,
    job_name LowCardinality(String),
    job_type LowCardinality(String),
    source_system LowCardinality(String),
    source_database Nullable(String),
    target_database String,
    status LowCardinality(String),
    started_at_utc DateTime64(3, 'UTC'),
    finished_at_utc Nullable(DateTime64(3, 'UTC')),
    source_watermark_before Nullable(String),
    source_watermark_after Nullable(String),
    rows_read UInt64,
    rows_written UInt64,
    rows_failed UInt64,
    config_json String,
    error_json String,
    code_version Nullable(String),
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(started_at_utc)
ORDER BY (job_name, started_at_utc, run_id)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.source_artifact_v1
(
    artifact_id String,
    run_id String,
    source_system LowCardinality(String),
    artifact_kind LowCardinality(String),
    source_uri String,
    local_path Nullable(String),
    source_date Nullable(Date),
    byte_size Nullable(UInt64),
    content_sha256 Nullable(String),
    status LowCardinality(String),
    error_json String,
    observed_at_utc DateTime64(3, 'UTC'),
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(observed_at_utc)
ORDER BY (source_system, artifact_kind, ifNull(source_date, toDate('1970-01-01')), artifact_id)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.sync_watermark_v1
(
    watermark_id String,
    job_name LowCardinality(String),
    source_system LowCardinality(String),
    source_object String,
    watermark_kind LowCardinality(String),
    watermark_value String,
    updated_at_utc DateTime64(3, 'UTC'),
    run_id String,
    status LowCardinality(String)
)
ENGINE = ReplacingMergeTree(updated_at_utc)
ORDER BY (job_name, source_system, source_object, watermark_kind)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.sync_validation_v1
(
    validation_id String,
    run_id String,
    check_name String,
    target_table String,
    check_status LowCardinality(String),
    severity LowCardinality(String),
    expected_value Nullable(String),
    observed_value Nullable(String),
    mismatch_count UInt64,
    details_json String,
    checked_at_utc DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(checked_at_utc)
PARTITION BY toYYYYMM(checked_at_utc)
ORDER BY (target_table, check_name, validation_id)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.ref_country_v1
(
    country_id String,
    country_code String,
    name String,
    region_code Nullable(String),
    status LowCardinality(String),
    first_seen_at_utc DateTime64(3, 'UTC'),
    last_seen_at_utc DateTime64(3, 'UTC'),
    source_run_id String,
    source_content_sha256 String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(last_seen_at_utc)
ORDER BY country_id
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.ref_asset_class_v1
(
    asset_class_id String,
    asset_class String,
    display_name String,
    status LowCardinality(String),
    source_system Nullable(String),
    first_seen_at_utc DateTime64(3, 'UTC'),
    last_seen_at_utc DateTime64(3, 'UTC'),
    source_run_id String,
    source_content_sha256 String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(last_seen_at_utc)
ORDER BY (asset_class, asset_class_id)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.ref_exchange_v1
(
    exchange_id String,
    exchange_code String,
    name String,
    acronym Nullable(String),
    mic Nullable(String),
    operating_mic Nullable(String),
    iso_country_code Nullable(String),
    exchange_type LowCardinality(String),
    status LowCardinality(String),
    supported_asset_classes Array(String),
    first_seen_at_utc DateTime64(3, 'UTC'),
    last_seen_at_utc DateTime64(3, 'UTC'),
    source_run_id String,
    source_content_sha256 String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(last_seen_at_utc)
ORDER BY (exchange_code, exchange_id)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.ref_exchange_currency_v1
(
    exchange_currency_id String,
    exchange_code String,
    currency_code String,
    relation_status LowCardinality(String),
    is_default UInt8,
    source_system LowCardinality(String),
    source_product_count UInt64,
    first_seen_at_utc DateTime64(3, 'UTC'),
    last_seen_at_utc DateTime64(3, 'UTC'),
    source_run_id String,
    source_content_sha256 String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(last_seen_at_utc)
ORDER BY (exchange_code, currency_code, exchange_currency_id)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.ref_ticker_type_v1
(
    ticker_type_id String,
    asset_class LowCardinality(String),
    provider_code String,
    name Nullable(String),
    description String,
    locale Nullable(String),
    status LowCardinality(String),
    first_seen_at_utc DateTime64(3, 'UTC'),
    last_seen_at_utc DateTime64(3, 'UTC'),
    source_run_id String,
    source_content_sha256 String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(last_seen_at_utc)
ORDER BY (asset_class, provider_code, ticker_type_id)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.id_issuer_v1
(
    issuer_id String,
    issuer_name String,
    issuer_name_normalized String,
    legal_name Nullable(String),
    branding_name Nullable(String),
    entity_type Nullable(String),
    domicile_country_code Nullable(String),
    state_of_incorporation Nullable(String),
    sic_code Nullable(String),
    sic_description Nullable(String),
    sector Nullable(String),
    industry Nullable(String),
    industry_group Nullable(String),
    website_url Nullable(String),
    investor_website_url Nullable(String),
    logo_asset_id Nullable(String),
    status LowCardinality(String),
    first_seen_at_utc DateTime64(3, 'UTC'),
    last_seen_at_utc DateTime64(3, 'UTC'),
    last_verified_at_utc Nullable(DateTime64(3, 'UTC')),
    source_run_id String,
    source_content_sha256 String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(last_seen_at_utc)
ORDER BY issuer_id
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.id_issuer_identifier_v1
(
    issuer_identifier_id String,
    issuer_id String,
    identifier_kind LowCardinality(String),
    identifier_value String,
    identifier_value_normalized String,
    source_system LowCardinality(String),
    confidence_score Float64,
    is_primary UInt8,
    valid_from_date Nullable(Date),
    valid_to_date_exclusive Nullable(Date),
    first_seen_at_utc DateTime64(3, 'UTC'),
    last_seen_at_utc DateTime64(3, 'UTC'),
    evidence_json String,
    source_run_id String,
    source_content_sha256 String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(last_seen_at_utc)
ORDER BY (identifier_kind, identifier_value_normalized, issuer_id)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.id_security_v1
(
    security_id String,
    issuer_id String,
    product_type LowCardinality(String),
    asset_class Nullable(String),
    instrument_type Nullable(String),
    security_type Nullable(String),
    security_name String,
    has_options Nullable(UInt8),
    status LowCardinality(String),
    first_seen_at_utc DateTime64(3, 'UTC'),
    last_seen_at_utc DateTime64(3, 'UTC'),
    source_run_id String,
    source_content_sha256 String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(last_seen_at_utc)
ORDER BY (issuer_id, security_id)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.id_security_identifier_v1
(
    security_identifier_id String,
    security_id String,
    identifier_kind LowCardinality(String),
    identifier_value String,
    identifier_value_normalized String,
    source_system LowCardinality(String),
    is_primary UInt8,
    valid_from_date Nullable(Date),
    valid_to_date_exclusive Nullable(Date),
    first_seen_at_utc DateTime64(3, 'UTC'),
    last_seen_at_utc DateTime64(3, 'UTC'),
    source_run_id String,
    source_content_sha256 String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(last_seen_at_utc)
ORDER BY (identifier_kind, identifier_value_normalized, security_id)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.id_listing_v1
(
    listing_id String,
    security_id String,
    exchange_code String,
    currency_code String,
    ibkr_conid Nullable(String),
    board_code Nullable(String),
    segment_name Nullable(String),
    listing_status LowCardinality(String),
    is_primary_listing UInt8,
    list_date Nullable(Date),
    delisted_date Nullable(Date),
    first_seen_at_utc DateTime64(3, 'UTC'),
    last_seen_at_utc DateTime64(3, 'UTC'),
    source_run_id String,
    source_content_sha256 String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(last_seen_at_utc)
ORDER BY (security_id, exchange_code, currency_code, listing_id)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.id_symbol_v1
(
    symbol_id String,
    listing_id String,
    source_system LowCardinality(String),
    ticker String,
    ticker_normalized String,
    display_name String,
    ticker_root Nullable(String),
    ticker_suffix Nullable(String),
    ticker_type_id Nullable(String),
    asset_type LowCardinality(String),
    instrument_type LowCardinality(String),
    security_type Nullable(String),
    status LowCardinality(String),
    primary_symbol_flag UInt8,
    first_seen_at_utc DateTime64(3, 'UTC'),
    last_seen_at_utc DateTime64(3, 'UTC'),
    source_run_id String,
    source_content_sha256 String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(last_seen_at_utc)
ORDER BY (source_system, ticker_normalized, listing_id, symbol_id)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.id_source_mapping_v1
(
    source_mapping_id String,
    source_system LowCardinality(String),
    source_entity_kind LowCardinality(String),
    source_entity_key String,
    source_identifier String,
    mapped_entity_kind LowCardinality(String),
    mapped_entity_id Nullable(String),
    mapping_status LowCardinality(String),
    confidence_score Float64,
    evidence_json String,
    resolved_at_utc DateTime64(3, 'UTC'),
    source_run_id String,
    source_content_sha256 String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(resolved_at_utc)
ORDER BY (source_system, source_entity_kind, source_entity_key, mapped_entity_kind)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.id_mapping_issue_v1
(
    mapping_issue_id String,
    source_mapping_id String,
    source_system LowCardinality(String),
    source_entity_kind LowCardinality(String),
    source_entity_key String,
    mapped_entity_kind LowCardinality(String),
    issue_type LowCardinality(String),
    issue_status LowCardinality(String),
    issue_message String,
    evidence_json String,
    opened_at_utc DateTime64(3, 'UTC'),
    resolved_at_utc Nullable(DateTime64(3, 'UTC')),
    source_run_id String,
    source_content_sha256 String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(opened_at_utc)
ORDER BY (issue_status, source_system, source_entity_kind, source_entity_key, mapping_issue_id)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.id_sec_market_bridge_v1
(
    bridge_id String,
    cik String,
    issuer_id String,
    security_id Nullable(String),
    listing_id Nullable(String),
    symbol_id Nullable(String),
    ticker Nullable(String),
    accession_number Nullable(String),
    valid_from_date Nullable(Date),
    valid_to_date_exclusive Nullable(Date),
    mapping_method LowCardinality(String),
    mapping_status LowCardinality(String),
    confidence_score Float64,
    ambiguity_status LowCardinality(String),
    evidence_json String,
    first_seen_at_utc DateTime64(3, 'UTC'),
    last_seen_at_utc DateTime64(3, 'UTC'),
    source_run_id String,
    source_content_sha256 String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(last_seen_at_utc)
ORDER BY (cik, ifNull(ticker, ''), ifNull(listing_id, ''), ifNull(accession_number, ''))
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.market_security_float_v1
(
    security_float_id String,
    symbol_id String,
    listing_id String,
    security_id String,
    source_system LowCardinality(String),
    provider_ticker String,
    effective_date Date,
    free_float Nullable(UInt64),
    free_float_percent Nullable(Float64),
    shares_outstanding Nullable(UInt64),
    float_source_tag LowCardinality(String),
    source_event_key String,
    source_evidence_ref String,
    source_run_id String,
    source_content_sha256 String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(effective_date)
ORDER BY (symbol_id, effective_date, source_system, security_float_id)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.market_security_classification_v1
(
    security_classification_id String,
    security_id String,
    classification_source LowCardinality(String),
    classification_scheme LowCardinality(String),
    classification_level LowCardinality(String),
    classification_value String,
    source_entity_key Nullable(String),
    first_seen_at_utc DateTime64(3, 'UTC'),
    last_seen_at_utc DateTime64(3, 'UTC'),
    source_run_id String,
    source_content_sha256 String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(last_seen_at_utc)
ORDER BY (security_id, classification_source, classification_scheme, classification_level, classification_value, security_classification_id)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.market_short_interest_v1
(
    short_interest_id String,
    symbol_id String,
    listing_id String,
    security_id String,
    source_system LowCardinality(String),
    source_venue Nullable(String),
    provider_ticker String,
    settlement_date Date,
    publication_date Nullable(Date),
    published_at_utc Nullable(DateTime64(3, 'UTC')),
    short_interest Nullable(UInt64),
    avg_daily_volume Nullable(UInt64),
    days_to_cover Nullable(Float64),
    source_event_key String,
    source_evidence_ref String,
    source_run_id String,
    source_content_sha256 String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(settlement_date)
ORDER BY (symbol_id, settlement_date, source_system, short_interest_id)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.market_short_volume_v1
(
    short_volume_id String,
    symbol_id String,
    listing_id String,
    security_id String,
    source_system LowCardinality(String),
    source_venue Nullable(String),
    provider_ticker String,
    trade_date Date,
    published_at_utc Nullable(DateTime64(3, 'UTC')),
    short_volume Nullable(UInt64),
    short_volume_ratio Nullable(Float64),
    total_volume Nullable(UInt64),
    exempt_volume Nullable(UInt64),
    non_exempt_volume Nullable(UInt64),
    source_event_key String,
    source_evidence_ref String,
    source_run_id String,
    source_content_sha256 String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(trade_date)
ORDER BY (symbol_id, trade_date, source_system, short_volume_id)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.market_stock_split_v1
(
    stock_split_id String,
    symbol_id String,
    listing_id String,
    security_id String,
    source_system LowCardinality(String),
    provider_ticker String,
    execution_date Date,
    split_from Float64,
    split_to Float64,
    source_event_key String,
    source_evidence_ref String,
    source_run_id String,
    source_content_sha256 String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(execution_date)
ORDER BY (symbol_id, execution_date, source_system, stock_split_id)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.market_cash_dividend_v1
(
    cash_dividend_id String,
    symbol_id String,
    listing_id String,
    security_id String,
    source_system LowCardinality(String),
    provider_ticker String,
    cash_amount Nullable(Float64),
    currency_code Nullable(String),
    declaration_date Nullable(Date),
    dividend_type Nullable(String),
    ex_dividend_date Date,
    frequency Nullable(String),
    pay_date Nullable(Date),
    record_date Nullable(Date),
    source_event_key String,
    source_evidence_ref String,
    source_run_id String,
    source_content_sha256 String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(ex_dividend_date)
ORDER BY (symbol_id, ex_dividend_date, source_system, cash_dividend_id)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.market_ipo_v1
(
    ipo_event_id String,
    symbol_id String,
    listing_id String,
    security_id String,
    source_system LowCardinality(String),
    provider_ticker String,
    issuer_name Nullable(String),
    announced_date Nullable(Date),
    listing_date Date,
    issue_start_date Nullable(Date),
    issue_end_date Nullable(Date),
    last_updated_date Nullable(Date),
    ipo_status Nullable(String),
    currency_code Nullable(String),
    final_issue_price Nullable(Float64),
    highest_offer_price Nullable(Float64),
    lowest_offer_price Nullable(Float64),
    min_shares_offered Nullable(Float64),
    max_shares_offered Nullable(Float64),
    total_offer_size Nullable(Float64),
    shares_outstanding Nullable(Float64),
    primary_exchange Nullable(String),
    security_type Nullable(String),
    security_description Nullable(String),
    us_code Nullable(String),
    isin Nullable(String),
    source_event_key String,
    source_evidence_ref String,
    source_run_id String,
    source_content_sha256 String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(listing_date)
ORDER BY (symbol_id, listing_date, source_system, ipo_event_id)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.market_security_market_snapshot_v1
(
    security_market_snapshot_id String,
    security_id String,
    listing_id String,
    symbol_id String,
    source_system LowCardinality(String),
    provider_ticker String,
    as_of_date Nullable(Date),
    observed_at_utc DateTime64(3, 'UTC'),
    market_cap Nullable(Float64),
    round_lot Nullable(UInt32),
    share_class_shares_outstanding Nullable(UInt64),
    weighted_shares_outstanding Nullable(UInt64),
    snapshot_evidence_ref String,
    source_run_id String,
    source_content_sha256 String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(observed_at_utc)
ORDER BY (symbol_id, observed_at_utc, source_system, security_market_snapshot_id)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.market_presentation_asset_v1
(
    asset_id String,
    asset_kind LowCardinality(String),
    display_name String,
    relative_path String,
    mime_type String,
    byte_size UInt64,
    content_hash_sha256 String,
    source_system Nullable(String),
    source_reference Nullable(String),
    source_file_name Nullable(String),
    status LowCardinality(String),
    first_seen_at_utc Nullable(DateTime64(3, 'UTC')),
    last_seen_at_utc Nullable(DateTime64(3, 'UTC')),
    last_verified_at_utc Nullable(DateTime64(3, 'UTC')),
    source_run_id String,
    source_content_sha256 String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
ORDER BY (asset_kind, status, asset_id)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.massive_flatfile_source_file_v1
(
    file_id String,
    provider LowCardinality(String),
    dataset_root String,
    partition_date Date,
    object_key String,
    source_etag String,
    source_last_modified_utc DateTime64(3, 'UTC'),
    source_byte_size UInt64,
    checksum_sha256 String,
    raw_file_id String,
    file_status LowCardinality(String),
    load_status LowCardinality(String),
    loaded_row_count Nullable(UInt64),
    quote_size_correction_status LowCardinality(String),
    loaded_at_utc Nullable(DateTime64(3, 'UTC')),
    source_run_id String,
    source_content_sha256 String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(partition_date)
ORDER BY (provider, dataset_root, partition_date, file_id)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.market_fails_to_deliver_v1
(
    ftd_id String,
    symbol_id Nullable(String),
    listing_id Nullable(String),
    security_id Nullable(String),
    source_system LowCardinality(String),
    provider_ticker String,
    settlement_date Date,
    cusip Nullable(String),
    fails_quantity UInt64,
    issuer_name Nullable(String),
    previous_close_price Nullable(Float64),
    source_event_key String,
    source_evidence_ref String,
    source_run_id String,
    source_content_sha256 String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(settlement_date)
ORDER BY (provider_ticker, settlement_date, source_system, ftd_id)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.market_reg_sho_threshold_v1
(
    threshold_id String,
    symbol_id Nullable(String),
    listing_id Nullable(String),
    security_id Nullable(String),
    source_system LowCardinality(String),
    provider_ticker String,
    threshold_date Date,
    listing_exchange Nullable(String),
    threshold_status LowCardinality(String),
    source_event_key String,
    source_evidence_ref String,
    source_run_id String,
    source_content_sha256 String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(threshold_date)
ORDER BY (provider_ticker, threshold_date, source_system, threshold_id)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.market_security_borrow_v1
(
    borrow_id String,
    symbol_id Nullable(String),
    listing_id Nullable(String),
    security_id Nullable(String),
    source_system LowCardinality(String),
    broker LowCardinality(String),
    provider_ticker String,
    ibkr_conid Nullable(String),
    observed_at_utc DateTime64(3, 'UTC'),
    borrow_status LowCardinality(String),
    shortable_shares Nullable(UInt64),
    lender_count Nullable(UInt32),
    indicative_borrow_rate Nullable(Float64),
    fee_rate Nullable(Float64),
    source_event_key String,
    source_evidence_ref String,
    source_run_id String,
    source_content_sha256 String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(observed_at_utc)
ORDER BY (provider_ticker, observed_at_utc, broker, borrow_id)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.market_security_country_v1
(
    country_assertion_id String,
    symbol_id Nullable(String),
    listing_id Nullable(String),
    security_id Nullable(String),
    issuer_id Nullable(String),
    provider_ticker Nullable(String),
    assertion_date Date,
    listing_country_code Nullable(String),
    issuer_legal_country_code Nullable(String),
    issuer_hq_country_code Nullable(String),
    security_issue_country_code Nullable(String),
    effective_country_code Nullable(String),
    confidence_score Float64,
    source_system LowCardinality(String),
    source_event_key String,
    source_evidence_ref String,
    source_run_id String,
    source_content_sha256 String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(assertion_date)
ORDER BY (ifNull(symbol_id, ''), assertion_date, source_system, country_assertion_id)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.market_reference_publication_coverage_v1
(
    coverage_id String,
    coverage_kind LowCardinality(String),
    source_system LowCardinality(String),
    source_object String,
    coverage_start_date Date,
    coverage_end_date Date,
    status LowCardinality(String),
    rows_read UInt64,
    rows_written UInt64,
    rows_failed UInt64,
    started_at_utc DateTime64(3, 'UTC'),
    finished_at_utc Nullable(DateTime64(3, 'UTC')),
    details_json String,
    source_run_id String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(coverage_start_date)
ORDER BY (coverage_kind, source_system, coverage_start_date, coverage_id)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.market_reference_alert_v1
(
    alert_id String,
    alert_version UInt32,
    alert_family LowCardinality(String),
    alert_group LowCardinality(String),
    alert_type LowCardinality(String),
    alert_subtype LowCardinality(String),
    severity LowCardinality(String),
    status LowCardinality(String),
    source_system LowCardinality(String),
    source_provider LowCardinality(String),
    source_table String,
    source_event_id String,
    source_event_version String,
    source_timestamp_utc DateTime64(3, 'UTC'),
    detected_at_utc DateTime64(3, 'UTC'),
    source_evidence_ref String,
    source_content_sha256 String,
    issuer_id Nullable(String),
    security_id Nullable(String),
    listing_id Nullable(String),
    symbol_id Nullable(String),
    provider_ticker Nullable(String),
    cik Nullable(String),
    accession_number Nullable(String),
    ibkr_conid Nullable(String),
    direction LowCardinality(String),
    event_status LowCardinality(String),
    impact_scope LowCardinality(String),
    time_sensitivity LowCardinality(String),
    confidence_score Nullable(Float64),
    impact_score Nullable(Float64),
    requires_recompute UInt8,
    recompute_scope LowCardinality(String),
    affects_tradability UInt8,
    requires_review UInt8,
    title String,
    message String,
    primary_label LowCardinality(String),
    secondary_labels Array(String),
    consumer_groups Array(String),
    action_flags Array(String),
    first_seen_at_utc DateTime64(3, 'UTC'),
    last_seen_at_utc DateTime64(3, 'UTC'),
    processed_at_utc Nullable(DateTime64(3, 'UTC')),
    expires_at_utc Nullable(DateTime64(3, 'UTC')),
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(detected_at_utc)
ORDER BY (alert_family, alert_group, alert_type, ifNull(symbol_id, ''), source_timestamp_utc, alert_id)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.market_reference_alert_consumer_state_v1
(
    consumer_id String,
    alert_id String,
    consumer_group LowCardinality(String),
    status LowCardinality(String),
    claimed_at_utc Nullable(DateTime64(3, 'UTC')),
    processed_at_utc Nullable(DateTime64(3, 'UTC')),
    last_error Nullable(String),
    attempt_count UInt32,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(inserted_at)
ORDER BY (consumer_id, alert_id)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.security_tradability_fact_v1
(
    tradability_fact_id String,
    issuer_id Nullable(String),
    security_id Nullable(String),
    listing_id Nullable(String),
    symbol_id Nullable(String),
    provider_ticker Nullable(String),
    effective_at_utc DateTime64(3, 'UTC'),
    observed_at_utc DateTime64(3, 'UTC'),
    is_tradable UInt8,
    block_status LowCardinality(String),
    block_reason Nullable(String),
    issue_type Nullable(String),
    issue_id Nullable(String),
    severity LowCardinality(String),
    confidence_score Nullable(Float64),
    source_system LowCardinality(String),
    source_table String,
    source_event_id String,
    source_evidence_ref String,
    source_content_sha256 String,
    source_run_id String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(effective_at_utc)
ORDER BY (ifNull(symbol_id, ''), effective_at_utc, source_system, tradability_fact_id)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.security_routing_fact_v1
(
    routing_fact_id String,
    issuer_id Nullable(String),
    security_id Nullable(String),
    listing_id Nullable(String),
    symbol_id Nullable(String),
    provider_ticker String,
    broker LowCardinality(String),
    ibkr_conid Nullable(String),
    contract_symbol Nullable(String),
    sec_type Nullable(String),
    currency_code Nullable(String),
    exchange_code Nullable(String),
    listing_exchange Nullable(String),
    routing_status LowCardinality(String),
    ambiguity_status LowCardinality(String),
    valid_from_utc DateTime64(3, 'UTC'),
    valid_to_utc Nullable(DateTime64(3, 'UTC')),
    confidence_score Nullable(Float64),
    source_system LowCardinality(String),
    source_table String,
    source_event_id String,
    source_evidence_ref String,
    source_content_sha256 String,
    source_run_id String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(valid_from_utc)
ORDER BY (provider_ticker, broker, valid_from_utc, routing_fact_id)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.security_share_supply_fact_v1
(
    share_supply_fact_id String,
    issuer_id Nullable(String),
    security_id Nullable(String),
    listing_id Nullable(String),
    symbol_id Nullable(String),
    provider_ticker Nullable(String),
    supply_metric LowCardinality(String),
    unit_code LowCardinality(String),
    value Float64,
    period_end_date Nullable(Date),
    effective_at_utc DateTime64(3, 'UTC'),
    observed_at_utc DateTime64(3, 'UTC'),
    source_priority UInt16,
    confidence_score Nullable(Float64),
    source_system LowCardinality(String),
    source_table String,
    source_event_id String,
    cik Nullable(String),
    accession_number Nullable(String),
    xbrl_taxonomy Nullable(String),
    xbrl_tag Nullable(String),
    source_evidence_ref String,
    source_content_sha256 String,
    source_run_id String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(effective_at_utc)
ORDER BY (ifNull(symbol_id, ''), supply_metric, effective_at_utc, source_priority, share_supply_fact_id)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.security_news_catalyst_fact_v1
(
    news_catalyst_fact_id String,
    canonical_news_id String,
    provider_article_id String,
    issuer_id Nullable(String),
    security_id Nullable(String),
    listing_id Nullable(String),
    symbol_id Nullable(String),
    provider_ticker Nullable(String),
    published_at_utc DateTime64(6, 'UTC'),
    observed_at_utc DateTime64(3, 'UTC'),
    catalyst_group LowCardinality(String),
    catalyst_type LowCardinality(String),
    catalyst_subtype LowCardinality(String),
    direction LowCardinality(String),
    event_status LowCardinality(String),
    urgency_score Nullable(Float64),
    impact_score Nullable(Float64),
    confidence_score Nullable(Float64),
    labels Array(String),
    source_system LowCardinality(String),
    source_table String,
    source_event_id String,
    source_evidence_ref String,
    source_content_sha256 String,
    source_run_id String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(published_at_utc)
ORDER BY (ifNull(symbol_id, ''), published_at_utc, catalyst_group, news_catalyst_fact_id)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.security_sec_filing_event_fact_v1
(
    filing_event_fact_id String,
    filing_id String,
    accession_number String,
    accession_number_compact String,
    cik String,
    issuer_id Nullable(String),
    security_id Nullable(String),
    listing_id Nullable(String),
    symbol_id Nullable(String),
    provider_ticker Nullable(String),
    form_type LowCardinality(String),
    filing_date Nullable(Date),
    report_date Nullable(Date),
    accepted_at_utc DateTime64(9, 'UTC'),
    event_group LowCardinality(String),
    event_type LowCardinality(String),
    event_status LowCardinality(String),
    text_status LowCardinality(String),
    confidence_score Nullable(Float64),
    source_system LowCardinality(String),
    source_table String,
    source_event_id String,
    source_evidence_ref String,
    source_content_sha256 String,
    source_run_id String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(accepted_at_utc)
ORDER BY (ifNull(symbol_id, ''), accepted_at_utc, form_type, filing_event_fact_id)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.security_sec_text_signal_fact_v1
(
    text_signal_fact_id String,
    document_id String,
    filing_id String,
    accession_number String,
    accession_number_compact String,
    cik String,
    issuer_id Nullable(String),
    security_id Nullable(String),
    listing_id Nullable(String),
    symbol_id Nullable(String),
    provider_ticker Nullable(String),
    accepted_at_utc Nullable(DateTime64(9, 'UTC')),
    extracted_at_utc DateTime64(3, 'UTC'),
    signal_group LowCardinality(String),
    signal_type LowCardinality(String),
    signal_subtype LowCardinality(String),
    direction LowCardinality(String),
    event_status LowCardinality(String),
    confidence_score Nullable(Float64),
    impact_score Nullable(Float64),
    evidence_span_start Nullable(UInt64),
    evidence_span_end Nullable(UInt64),
    evidence_text_sha256 Nullable(String),
    extraction_method LowCardinality(String),
    normalizer_version LowCardinality(String),
    labels Array(String),
    source_system LowCardinality(String),
    source_table String,
    source_event_id String,
    source_evidence_ref String,
    source_content_sha256 String,
    source_run_id String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(extracted_at_utc)
ORDER BY (ifNull(symbol_id, ''), extracted_at_utc, signal_group, text_signal_fact_id)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.issuer_fundamental_metric_fact_v1
(
    fundamental_metric_fact_id String,
    issuer_id Nullable(String),
    cik String,
    metric_group LowCardinality(String),
    metric_name LowCardinality(String),
    unit_code LowCardinality(String),
    value Float64,
    fiscal_year Nullable(UInt32),
    fiscal_period Nullable(String),
    period_end_date Nullable(Date),
    filed_at_utc Nullable(DateTime64(3, 'UTC')),
    recorded_at_utc DateTime64(3, 'UTC'),
    form_type Nullable(String),
    accession_number Nullable(String),
    source_priority UInt16,
    confidence_score Nullable(Float64),
    xbrl_taxonomy Nullable(String),
    xbrl_tag Nullable(String),
    source_system LowCardinality(String),
    source_table String,
    source_event_id String,
    source_evidence_ref String,
    source_content_sha256 String,
    source_run_id String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(ifNull(period_end_date, toDate('1970-01-01')))
ORDER BY (cik, metric_group, metric_name, ifNull(period_end_date, toDate('1970-01-01')), source_priority, fundamental_metric_fact_id)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.security_valuation_fact_v1
(
    valuation_fact_id String,
    issuer_id Nullable(String),
    security_id Nullable(String),
    listing_id Nullable(String),
    symbol_id Nullable(String),
    provider_ticker Nullable(String),
    valuation_metric LowCardinality(String),
    value Float64,
    currency_code Nullable(String),
    as_of_utc DateTime64(3, 'UTC'),
    input_snapshot_id Nullable(String),
    input_fundamental_metric_ids Array(String),
    confidence_score Nullable(Float64),
    source_system LowCardinality(String),
    source_table String,
    source_event_id String,
    source_evidence_ref String,
    source_content_sha256 String,
    source_run_id String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(as_of_utc)
ORDER BY (ifNull(symbol_id, ''), valuation_metric, as_of_utc, valuation_fact_id)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.security_liquidity_profile_fact_v1
(
    liquidity_profile_fact_id String,
    security_id Nullable(String),
    listing_id Nullable(String),
    symbol_id Nullable(String),
    provider_ticker String,
    profile_window LowCardinality(String),
    session_date Date,
    observed_at_utc DateTime64(3, 'UTC'),
    median_spread_bps Nullable(Float64),
    median_quote_size Nullable(Float64),
    median_trade_size Nullable(Float64),
    trade_count Nullable(UInt64),
    quote_count Nullable(UInt64),
    total_volume Nullable(UInt64),
    dollar_volume Nullable(Float64),
    volatility_bps Nullable(Float64),
    liquidity_score Nullable(Float64),
    source_system LowCardinality(String),
    source_table String,
    source_event_id String,
    source_evidence_ref String,
    source_content_sha256 String,
    source_run_id String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(session_date)
ORDER BY (provider_ticker, session_date, profile_window, observed_at_utc, liquidity_profile_fact_id)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.sec_filing_v2
(
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
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(coalesce(accepted_at_utc, toDateTime64(ifNull(filing_date, toDate('1970-01-01')), 9, 'UTC')))
ORDER BY (cik, accession_number)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.sec_filing_document_v2
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

CREATE TABLE IF NOT EXISTS q_live.sec_filing_text_v2
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

CREATE TABLE IF NOT EXISTS q_live.sec_filing_document_skip_v1
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

CREATE TABLE IF NOT EXISTS q_live.sec_xbrl_concept_v1
(
    concept_id String,
    taxonomy LowCardinality(String),
    tag String,
    concept_label Nullable(String),
    concept_description Nullable(String),
    first_observed_at_utc Nullable(DateTime64(3, 'UTC')),
    last_observed_at_utc DateTime64(3, 'UTC'),
    source_run_id String,
    source_content_sha256 String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(last_observed_at_utc)
ORDER BY (taxonomy, tag)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.sec_xbrl_company_fact_v1
(
    company_fact_id String,
    issuer_id Nullable(String),
    cik String,
    taxonomy LowCardinality(String),
    tag String,
    unit_code LowCardinality(String),
    fiscal_year Nullable(UInt32),
    fiscal_period Nullable(String),
    filed_at_utc Nullable(DateTime64(3, 'UTC')),
    period_end_date Nullable(Date),
    value Float64,
    form_type Nullable(String),
    accession_number Nullable(String),
    recorded_at_utc DateTime64(3, 'UTC'),
    source_run_id String,
    source_content_sha256 String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(ifNull(period_end_date, toDate('1970-01-01')))
ORDER BY (cik, taxonomy, tag, unit_code, ifNull(period_end_date, toDate('1970-01-01')), ifNull(accession_number, ''))
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.sec_xbrl_frame_v1
(
    frame_id String,
    taxonomy LowCardinality(String),
    tag String,
    unit_code LowCardinality(String),
    calendar_period_code String,
    recorded_at_utc DateTime64(3, 'UTC'),
    source_run_id String,
    source_content_sha256 String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(recorded_at_utc)
ORDER BY (taxonomy, tag, unit_code, calendar_period_code)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.sec_xbrl_frame_observation_v1
(
    frame_observation_id String,
    frame_id String,
    taxonomy LowCardinality(String),
    tag String,
    unit_code LowCardinality(String),
    calendar_period_code String,
    issuer_id Nullable(String),
    cik String,
    entity_name String,
    location_code Nullable(String),
    period_end_date Date,
    value Float64,
    accession_number String,
    recorded_at_utc DateTime64(3, 'UTC'),
    source_run_id String,
    source_content_sha256 String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(period_end_date)
ORDER BY (taxonomy, tag, unit_code, calendar_period_code, cik, accession_number, period_end_date)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.feature_tradable_universe_v1
(
    universe_date Date,
    symbol_id String,
    listing_id String,
    security_id String,
    issuer_id String,
    ticker String,
    exchange_code String,
    currency_code String,
    ibkr_conid Nullable(String),
    massive_ticker Nullable(String),
    product_type LowCardinality(String),
    asset_class Nullable(String),
    listing_status LowCardinality(String),
    symbol_status LowCardinality(String),
    is_tradable UInt8,
    exclusion_reason Nullable(String),
    source_run_id String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(universe_date)
ORDER BY (universe_date, ticker, listing_id)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.feature_scanner_static_v1
(
    feature_date Date,
    symbol_id String,
    listing_id String,
    security_id String,
    issuer_id String,
    ticker String,
    free_float Nullable(UInt64),
    float_bucket LowCardinality(String),
    short_interest Nullable(UInt64),
    days_to_cover Nullable(Float64),
    short_volume_ratio Nullable(Float64),
    short_pressure_label LowCardinality(String),
    market_cap Nullable(Float64),
    sector Nullable(String),
    industry Nullable(String),
    logo_asset_id Nullable(String),
    source_run_id String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(feature_date)
ORDER BY (feature_date, ticker, listing_id)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.feature_sec_event_market_bridge_v1
(
    event_id String,
    accession_number String,
    cik String,
    accepted_at_utc DateTime64(9, 'UTC'),
    form_type LowCardinality(String),
    issuer_id String,
    security_id Nullable(String),
    listing_id Nullable(String),
    symbol_id Nullable(String),
    ticker Nullable(String),
    bridge_id String,
    mapping_confidence_score Float64,
    event_label_status LowCardinality(String),
    source_run_id String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(inserted_at)
PARTITION BY toYYYYMM(accepted_at_utc)
ORDER BY (accepted_at_utc, ifNull(ticker, ''), accession_number, bridge_id)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

CREATE TABLE IF NOT EXISTS q_live.live_symbol_market_event_v1
(
    schema_version UInt16,
    event_id String,
    ticker LowCardinality(String),
    event_type LowCardinality(String),
    event_status LowCardinality(String),
    event_start_utc DateTime64(3, 'UTC'),
    event_end_utc Nullable(DateTime64(3, 'UTC')),
    source_event_ts_utc DateTime64(3, 'UTC'),
    source_event_type LowCardinality(String),
    source_conditions Array(UInt16),
    source_indicators Array(UInt16),
    severity LowCardinality(String),
    is_live_tradability_blocking UInt8,
    block_reason Nullable(String),
    evidence_json String,
    source_run_id String,
    inserted_at_utc DateTime64(3, 'UTC')
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(event_start_utc)
ORDER BY (ticker, event_type, event_start_utc, event_id)
SETTINGS index_granularity = 8192, storage_policy = '{{CLICKHOUSE_LIVE_STORAGE_POLICY}}';

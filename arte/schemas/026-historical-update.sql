-- Historical update candidate schema. Not applied before repository extraction.
-- Every table uses the required explicit SSD policy; validate actual parts too.
-- The source importer writes ARTE-owned rows. Existing yearly compact tables
-- remain read-only. Source-row is source-file identity, not a dense market ordinal.
CREATE TABLE IF NOT EXISTS arte.historical_source_events_v1
(
    source_generation FixedString(64),
    source_date Date,
    ticker LowCardinality(String),
    source_row UInt64,
    sip_timestamp_ns UInt64 CODEC(DoubleDelta, ZSTD(1)),
    participant_timestamp_ns Nullable(UInt64),
    sequence_number UInt64,
    event_meta UInt8,
    price_primary_int UInt64,
    price_secondary_int UInt64,
    size_primary Float64,
    size_secondary Float64,
    exchange_primary UInt16,
    exchange_secondary UInt16,
    condition_tokens Array(UInt16),
    correction_code Nullable(UInt8)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(source_date)
ORDER BY (ticker, source_date, sip_timestamp_ns, event_meta, source_row, source_generation)
SETTINGS storage_policy = 'live_market_ssd';

-- Publish only after raw-file identity, row counts, event keys, codec and
-- trade-reporting revision have been checked against inserted data.
CREATE TABLE IF NOT EXISTS arte.historical_source_coverage_v1
(
    source_generation FixedString(64),
    source_date Date,
    certificate_hash FixedString(64),
    trade_reporting_revision FixedString(64),
    quote_file_sha256 FixedString(64),
    trade_file_sha256 FixedString(64),
    quote_rows UInt64,
    trade_rows UInt64,
    accepted_rows UInt64,
    rejected_rows UInt64,
    source_min_ns UInt64,
    source_max_ns UInt64,
    published_at_ns UInt64
)
ENGINE = MergeTree
ORDER BY (source_date, source_generation, certificate_hash)
SETTINGS storage_policy = 'live_market_ssd';

-- Sparse transitions over a certified complete 500 ms evaluation grid.
-- This is a modeled indicator, never an official live LULD safety input.
CREATE TABLE IF NOT EXISTS arte.estimated_luld_500ms_v1
(
    build_id FixedString(64),
    attempt_id UUID,
    session_date Date,
    ticker LowCardinality(String),
    bucket_end_ns UInt64 CODEC(DoubleDelta, ZSTD(1)),
    known UInt8,
    reference_int Int64,
    lower_int Int64,
    upper_int Int64,
    sample_count UInt64,
    source_certificate_hash FixedString(64),
    policy_hash FixedString(64)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(session_date)
ORDER BY (ticker, session_date, build_id, attempt_id, bucket_end_ns)
SETTINGS storage_policy = 'live_market_ssd';

-- Historical complete-session episodes: exactly one logical halt per ID.
-- The end is retrospective. As-known queries must hide it before end_ns.
CREATE TABLE IF NOT EXISTS arte.halt_episodes_v1
(
    build_id FixedString(64),
    attempt_id UUID,
    session_date Date,
    ticker LowCardinality(String),
    episode_id FixedString(64),
    start_ns UInt64,
    end_ns Nullable(UInt64),
    reason_codes Array(UInt16),
    status_source LowCardinality(String),
    first_source_event_ns UInt64,
    last_source_event_ns UInt64,
    available_at_ns UInt64,
    source_certificate_hash FixedString(64)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(session_date)
ORDER BY (ticker, session_date, build_id, attempt_id, start_ns, episode_id)
SETTINGS storage_policy = 'live_market_ssd';

-- One published root selects exact attempts of all derived products. Unknown
-- halt source is explicit; absence of episode rows does not imply no halt.
CREATE TABLE IF NOT EXISTS arte.historical_update_publications_v1
(
    plan_hash FixedString(64),
    session_date Date,
    source_certificate_hash FixedString(64),
    bars_build_id String,
    bars_attempt_id UUID,
    indicators_attempt_id UUID,
    liquidity_attempt_id UUID,
    v7_coverage_hash FixedString(64),
    luld_attempt_id UUID,
    halt_attempt_id UUID,
    halt_coverage_state LowCardinality(String),
    output_hash FixedString(64),
    published_at_ns UInt64
)
ENGINE = MergeTree
ORDER BY (session_date, plan_hash, output_hash)
SETTINGS storage_policy = 'live_market_ssd';

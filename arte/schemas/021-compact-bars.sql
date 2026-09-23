-- Apply only after extraction, coverage-publication and storage-policy acceptance.
-- One sparse row per nonempty completed bucket. Exact atoms and scales are kept.
-- Empty buckets exist only under a published complete coverage manifest.
CREATE TABLE IF NOT EXISTS arte.compact_bars_v1
(
    provider UInt16,
    instrument UInt64,
    session UInt32,
    timeframe_ns UInt64,
    bucket_start_ns UInt64 CODEC(Delta, ZSTD(3)),
    source_generation FixedString(64),
    calculation_hash FixedString(64),
    price_scale UInt8,
    size_scale UInt8,
    open_atoms Int64,
    high_atoms Int64,
    low_atoms Int64,
    close_atoms Int64,
    volume_atoms Int64,
    notional_atoms Int128,
    trades UInt64
)
ENGINE = MergeTree
PARTITION BY (session, timeframe_ns)
ORDER BY (provider, instrument, session, timeframe_ns, source_generation, calculation_hash, bucket_start_ns)
SETTINGS storage_policy = 'live_market_ssd';

-- Publish only after exact source-certificate and complete bar-grid readback.
-- The content hash is arte.compact-bars.v1/coverage over the canonical payload.
CREATE TABLE IF NOT EXISTS arte.compact_bar_coverage_v1
(
    coverage_hash FixedString(64),
    payload_json String CODEC(ZSTD(3))
)
ENGINE = MergeTree
ORDER BY coverage_hash
SETTINGS storage_policy = 'live_market_ssd';

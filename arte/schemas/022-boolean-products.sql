-- Apply only after source-bar, producer and transition readback acceptance.
-- One row per changed known/value state at a completed fixed-cadence bucket.
-- Missing rows mean carry forward only under a matching published digest.
CREATE TABLE IF NOT EXISTS arte.boolean_transitions_v1
(
    request_hash FixedString(64),
    provider UInt16,
    instrument UInt64,
    session UInt32,
    bucket_start_ns UInt64 CODEC(Delta, ZSTD(3)),
    known UInt8,
    value UInt8
)
ENGINE = MergeTree
PARTITION BY session
ORDER BY (request_hash, bucket_start_ns)
SETTINGS storage_policy = 'live_market_ssd';

-- The payload contains the request/source identity and exact transition
-- count/digest. Publication follows immutable transition-row readback.
CREATE TABLE IF NOT EXISTS arte.boolean_coverage_v1
(
    coverage_hash FixedString(64),
    payload_json String CODEC(ZSTD(3))
)
ENGINE = MergeTree
ORDER BY coverage_hash
SETTINGS storage_policy = 'live_market_ssd';

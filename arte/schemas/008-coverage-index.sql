-- Thin discovery index. Certificate and event-batch readback remain authoritative.
-- Not executed before repository extraction.
CREATE TABLE IF NOT EXISTS arte.event_coverage_index_v1
(
    authority_hash FixedString(64),
    interval_start UInt64,
    interval_end UInt64,
    published_at_ns UInt64,
    certificate_hash FixedString(64)
)
ENGINE = MergeTree
ORDER BY (authority_hash, interval_start, interval_end, certificate_hash)
SETTINGS storage_policy = 'live_market_ssd';

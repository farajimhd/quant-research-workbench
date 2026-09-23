-- Unapplied. Install only after repository extraction and SSD preflight.
-- Content-addressed children precede a single scheduler-bound live lane root.
-- Neither table is a broker, portfolio, or feed-health authority.
CREATE TABLE IF NOT EXISTS arte.live_cut_objects_v1
(
    object_hash FixedString(64),
    payload_hex String CODEC(ZSTD(3))
)
ENGINE = MergeTree
ORDER BY object_hash
SETTINGS storage_policy = 'live_market_ssd';

CREATE TABLE IF NOT EXISTS arte.live_cut_roots_v1
(
    slot_hash FixedString(64),
    root_hash FixedString(64),
    published_at_ns UInt64 CODEC(Delta, ZSTD(3))
)
ENGINE = MergeTree
ORDER BY slot_hash
SETTINGS storage_policy = 'live_market_ssd';

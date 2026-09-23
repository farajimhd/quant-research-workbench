-- Unapplied. Install only after repository extraction and SSD preflight.
-- Child objects precede the immutable root. A root is visible only at the
-- scheduler boundary it names; this is not a broker recovery authority.
CREATE TABLE IF NOT EXISTS arte.exact_signal_objects_v1
(
    object_hash FixedString(64),
    payload_hex String CODEC(ZSTD(3))
)
ENGINE = MergeTree
ORDER BY object_hash
SETTINGS storage_policy = 'live_market_ssd';

CREATE TABLE IF NOT EXISTS arte.exact_signal_roots_v1
(
    slot_hash FixedString(64),
    root_hash FixedString(64),
    published_at_ns UInt64 CODEC(Delta, ZSTD(3))
)
ENGINE = MergeTree
ORDER BY slot_hash
SETTINGS storage_policy = 'live_market_ssd';

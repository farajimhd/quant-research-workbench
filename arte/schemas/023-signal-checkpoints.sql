-- Unapplied until ARTE extraction, durability acceptance and SSD preflight.
-- Substitute only the configured ARTE database; never a legacy database.
-- Immutable per-source/per-prefix snapshots. A conflicting duplicate slot is
-- rejected by the reader before any state may be restored.
CREATE TABLE IF NOT EXISTS arte.signal_checkpoints_v1
(
    scope_hash FixedString(64),
    next_bucket_ns UInt64 CODEC(Delta, ZSTD(3)),
    payload_hex String CODEC(ZSTD(3)),
    published_at_ns UInt64 CODEC(Delta, ZSTD(3))
)
ENGINE = MergeTree
ORDER BY (scope_hash, next_bucket_ns)
SETTINGS storage_policy = 'live_market_ssd';

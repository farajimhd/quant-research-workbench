-- Unapplied. Requires extraction, durability acceptance and live_market_ssd checks.
-- Substitute the configured ARTE database. Never target legacy databases.
CREATE TABLE IF NOT EXISTS arte.portfolio_checkpoint_chunks_v1
(
    record_hash FixedString(64),
    payload_hex String
)
ENGINE = MergeTree
ORDER BY record_hash
SETTINGS storage_policy = 'live_market_ssd';

-- Cooperative writers lease the manifest/boundary-sequence slot.
-- Readers reject conflicting duplicates; MergeTree does not enforce uniqueness.
-- Publish a root only after every referenced chunk has passed exact readback.
CREATE TABLE IF NOT EXISTS arte.portfolio_checkpoints_v1
(
    record_hash FixedString(64),
    payload_hex String
)
ENGINE = MergeTree
ORDER BY record_hash
SETTINGS storage_policy = 'live_market_ssd';

-- Unapplied. Requires extraction, durability acceptance and live_market_ssd checks.
-- Substitute the configured ARTE database. Never target legacy databases.
CREATE TABLE IF NOT EXISTS arte.execution_checkpoint_chunks_v1
(
    record_hash FixedString(64),
    payload_hex String
)
ENGINE = MergeTree
ORDER BY record_hash
SETTINGS storage_policy = 'live_market_ssd';

-- Root slot binds manifest, instrument and boundary sequence. Writers cooperate
-- through a lease; readers reject conflicting duplicate rows before merges.
-- Publish only after all chunk readbacks succeed. This is not a whole-run fence.
CREATE TABLE IF NOT EXISTS arte.execution_checkpoints_v1
(
    record_hash FixedString(64),
    payload_hex String
)
ENGINE = MergeTree
ORDER BY record_hash
SETTINGS storage_policy = 'live_market_ssd';

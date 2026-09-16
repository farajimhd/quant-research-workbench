-- Unapplied. Requires extraction, durability acceptance and live_market_ssd checks.
-- Substitute the configured ARTE database. Never target legacy databases.
CREATE TABLE IF NOT EXISTS arte.backtest_checkpoint_chunks_v1
(
    record_hash FixedString(64),
    payload_hex String
)
ENGINE = MergeTree
ORDER BY record_hash
SETTINGS storage_policy = 'live_market_ssd';

-- The slot binds manifest and boundary sequence, not the checkpoint hash.
-- Read back all chunks before publishing. Reject conflicting duplicate roots.
-- Cooperative lease ownership is required; this is not ClickHouse CAS.
CREATE TABLE IF NOT EXISTS arte.backtest_checkpoints_v1
(
    record_hash FixedString(64),
    payload_hex String
)
ENGINE = MergeTree
ORDER BY record_hash
SETTINGS storage_policy = 'live_market_ssd';

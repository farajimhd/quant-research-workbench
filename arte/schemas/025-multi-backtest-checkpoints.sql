-- Unapplied. Install only in the standalone ARTE database after extraction.
-- Validate live_market_ssd policy and actual part placement before writes.
-- Content-addressed chunks are written and read back before the slot root.
-- A stored root is not permission to resume or submit orders.
CREATE TABLE IF NOT EXISTS arte.multi_backtest_checkpoint_chunks_v1
(
    record_hash FixedString(64),
    payload_hex String CODEC(ZSTD(3))
)
ENGINE = MergeTree
ORDER BY record_hash
SETTINGS storage_policy = 'live_market_ssd';

CREATE TABLE IF NOT EXISTS arte.multi_backtest_checkpoints_v1
(
    record_hash FixedString(64),
    payload_hex String CODEC(ZSTD(3))
)
ENGINE = MergeTree
ORDER BY record_hash
SETTINGS storage_policy = 'live_market_ssd';

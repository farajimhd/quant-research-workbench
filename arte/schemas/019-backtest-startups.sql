-- Unapplied. Requires extraction, durability acceptance and live_market_ssd checks.
-- Substitute the configured ARTE database. Never target legacy databases.
-- Stable root slot pins one startup configuration per run manifest.
-- Chunks are content addressed; publish the root only after chunk readback.
CREATE TABLE IF NOT EXISTS arte.backtest_startup_chunks_v1
(
    record_hash FixedString(64),
    payload_hex String
)
ENGINE = MergeTree
ORDER BY record_hash
SETTINGS storage_policy = 'live_market_ssd';

CREATE TABLE IF NOT EXISTS arte.backtest_startups_v1
(
    record_hash FixedString(64),
    payload_hex String
)
ENGINE = MergeTree
ORDER BY record_hash
SETTINGS storage_policy = 'live_market_ssd';

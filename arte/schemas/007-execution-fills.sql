-- Not executed before repository extraction and storage acceptance.
-- object_hash is the immutable execution identity, not a payload-only hash.
-- A conflicting correction requires its own explicit workflow.
CREATE TABLE IF NOT EXISTS arte.execution_fills_v1
(
    object_hash FixedString(64),
    value_json String CODEC(ZSTD(3))
)
ENGINE = MergeTree
ORDER BY object_hash
SETTINGS storage_policy = 'live_market_ssd';

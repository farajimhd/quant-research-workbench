-- Source migration only. Not executed before repository extraction.
-- Deployment must verify schema, live_market_ssd policy and actual part placement.
CREATE TABLE IF NOT EXISTS arte.decision_journal_v1
(
    scope_hash FixedString(64),
    sequence UInt64,
    payload_json String CODEC(ZSTD(3))
)
ENGINE = MergeTree
ORDER BY (scope_hash, sequence)
SETTINGS storage_policy = 'live_market_ssd';

-- One writer owns each scope. Identical retry rows are allowed. Conflicts fail.
-- Sequence is a strategy decision cursor, not a market-event ordinal.

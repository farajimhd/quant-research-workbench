-- Unapplied. Requires extraction, durability acceptance and live_market_ssd checks.
-- Substitute the configured ARTE database. Never target legacy databases.
-- Stable slot identifies decision/action, not payload. Conflicting rows fail reads.
-- Rejection receipts do not authorize releasing reserved funds or ignoring orders.
CREATE TABLE IF NOT EXISTS arte.action_rejections_v1
(
    record_hash FixedString(64),
    payload_json String
)
ENGINE = MergeTree
ORDER BY record_hash
SETTINGS storage_policy = 'live_market_ssd';

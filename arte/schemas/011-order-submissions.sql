-- Apply only after repository extraction and durability acceptance.
CREATE TABLE IF NOT EXISTS arte.order_submissions_v1
(
    order_key FixedString(64),
    envelope_hash FixedString(64),
    payload_json String
)
ENGINE = MergeTree
ORDER BY order_key
SETTINGS storage_policy = 'live_market_ssd';

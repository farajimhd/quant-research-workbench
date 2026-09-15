-- Apply only after repository extraction and storage-policy acceptance.
-- Replace arte with the explicitly configured standalone database identifier.
CREATE TABLE IF NOT EXISTS arte.order_authorizations_v2
(
    order_key FixedString(64),
    envelope_hash FixedString(64),
    payload_json String
)
ENGINE = MergeTree
ORDER BY order_key
SETTINGS storage_policy = 'live_market_ssd';

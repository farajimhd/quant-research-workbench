-- Apply only after repository extraction and storage-policy acceptance.
-- Bind the configured ARTE database; never target legacy/default databases.
-- Immutable content-addressed records. No implicit latest-policy authority.
CREATE TABLE IF NOT EXISTS arte.trade_eligibility_policies_v1
(
    record_hash FixedString(64),
    payload_json String
)
ENGINE = MergeTree
ORDER BY record_hash
SETTINGS storage_policy = 'live_market_ssd';

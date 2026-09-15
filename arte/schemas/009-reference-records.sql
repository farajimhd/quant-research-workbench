-- Apply only after repository extraction and storage-policy acceptance.
-- Baseline database is arte. Deployment must bind the configured ARTE database.
-- Never target a legacy/default database.
CREATE TABLE IF NOT EXISTS arte.previous_close_records_v1
(
    record_hash FixedString(64),
    payload_json String
)
ENGINE = MergeTree
ORDER BY record_hash
SETTINGS storage_policy = 'live_market_ssd';

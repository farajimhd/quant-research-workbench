-- Apply only after repository extraction and durability acceptance.
CREATE TABLE IF NOT EXISTS arte.broker_initial_outcomes_v1
(
    submission_hash FixedString(64),
    record_hash FixedString(64),
    payload_json String
)
ENGINE = MergeTree
ORDER BY submission_hash
SETTINGS storage_policy = 'live_market_ssd';

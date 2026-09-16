-- Apply only after extraction and storage-policy acceptance.
-- Substitute the configured ARTE database. Never target legacy databases.
-- Cooperative writers hold the run-ID slot lease; MergeTree does not enforce
-- uniqueness. Readers reject conflicting values before background merges.
CREATE TABLE IF NOT EXISTS arte.run_manifest_chunks_v1
(
    record_hash FixedString(64),
    payload_json String
)
ENGINE = MergeTree
ORDER BY record_hash
SETTINGS storage_policy = 'live_market_ssd';

-- A root becomes visible only after all referenced chunks pass readback.
CREATE TABLE IF NOT EXISTS arte.run_manifests_v1
(
    record_hash FixedString(64),
    payload_json String
)
ENGINE = MergeTree
ORDER BY record_hash
SETTINGS storage_policy = 'live_market_ssd';

-- Candidate staging layout, not the final compact/range-query event authority.
-- Not executed. Requires repository extraction, source identity, EventStorage and
-- durability acceptance. Use only the configured independent ARTE database.
-- Payload equality shares storage; it never merges distinct event identities.
CREATE TABLE IF NOT EXISTS arte.event_payload_staging_v1
(
    object_hash FixedString(64),
    value_json String CODEC(ZSTD(3))
)
ENGINE = MergeTree
ORDER BY object_hash
SETTINGS storage_policy = 'live_market_ssd';

CREATE TABLE IF NOT EXISTS arte.event_observation_staging_v1
(
    object_hash FixedString(64),
    value_json String CODEC(ZSTD(3))
)
ENGINE = MergeTree
ORDER BY object_hash
SETTINGS storage_policy = 'live_market_ssd';

CREATE TABLE IF NOT EXISTS arte.event_batch_staging_v1
(
    object_hash FixedString(64),
    value_json String CODEC(ZSTD(3))
)
ENGINE = MergeTree
ORDER BY object_hash
SETTINGS storage_policy = 'live_market_ssd';

-- Publish batch manifests only after full payload/reference readback.
-- Readers compare distinct values before merges; conflicts must remain visible.
-- No retention/cleanup is enabled until reachability and recovery are certified.

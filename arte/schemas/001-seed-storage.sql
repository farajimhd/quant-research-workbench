-- Reviewed source migration, not executed during pre-extraction validation.
-- Before applying: verify live_market_ssd exists and excludes backup/default disks.
-- After applying: verify table schemas, policies and actual part placement.
CREATE DATABASE IF NOT EXISTS arte ENGINE = Atomic;

CREATE TABLE IF NOT EXISTS arte.seed_objects_v1
(
    object_hash FixedString(64),
    payload_json String CODEC(ZSTD(3))
)
ENGINE = MergeTree
ORDER BY object_hash
SETTINGS storage_policy = 'live_market_ssd';

CREATE TABLE IF NOT EXISTS arte.seed_manifests_v1
(
    seed_hash FixedString(64),
    manifest_json String CODEC(ZSTD(3))
)
ENGINE = MergeTree
ORDER BY seed_hash
SETTINGS storage_policy = 'live_market_ssd';

-- Physical retry rows may repeat. Readers compare distinct payloads and reject
-- conflicting immutable identities. Do not use replacement to hide conflicts.

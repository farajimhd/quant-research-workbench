-- Candidate coverage publication storage. Not executed before extraction.
-- Publish only after complete pagination and data-backed batch verification.
-- Range discovery/indexing is separate; a hash lookup is not an interval index.
CREATE TABLE IF NOT EXISTS arte.event_coverage_staging_v1
(
    object_hash FixedString(64),
    value_json String CODEC(ZSTD(3))
)
ENGINE = MergeTree
ORDER BY object_hash
SETTINGS storage_policy = 'live_market_ssd';

-- Source migration only. Never run during pre-extraction validation.
-- Immutable one-page links: no repeated whole-prefix snapshots.
CREATE TABLE IF NOT EXISTS arte.event_acquisition_progress_v1
(
    object_hash FixedString(64),
    value_json String CODEC(ZSTD(3))
)
ENGINE = MergeTree
ORDER BY object_hash
SETTINGS storage_policy = 'live_market_ssd';
-- Job ownership must persist its acknowledged head. Progress alone is not coverage.

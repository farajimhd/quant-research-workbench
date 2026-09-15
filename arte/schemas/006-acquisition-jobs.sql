-- Source migration only; not executed during restricted validation.
-- One externally fenced owner per named plan. ClickHouse is not a lease service.
CREATE TABLE IF NOT EXISTS arte.event_acquisition_jobs_v1
(
    job_hash FixedString(64),
    revision UInt64,
    head_hash FixedString(64)
)
ENGINE = MergeTree
ORDER BY (job_hash, revision)
SETTINGS storage_policy = 'live_market_ssd';
-- Revision is the acquisition page number, never a market-event ordinal.
-- Append after progress-object readback. Conflicting heads remain visible.

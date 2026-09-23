-- Event-cadence Boolean results are distinct from fixed-bar Boolean products.
-- A row exists only when the known/value state changes at a causal event.
-- An absent row is meaningful only under the immutable product header and a
-- separately verified complete event-source ledger.
CREATE TABLE IF NOT EXISTS arte.event_boolean_transitions_v1
(
    product_hash FixedString(64),
    provider UInt16,
    instrument UInt64,
    session UInt32,
    event_index UInt64 CODEC(Delta, ZSTD(3)),
    boundary_id FixedString(64),
    source_sequence UInt64 CODEC(Delta, ZSTD(3)),
    evaluated_at_ns UInt64 CODEC(Delta, ZSTD(3)),
    known UInt8,
    value UInt8
)
ENGINE = MergeTree
PARTITION BY session
ORDER BY (product_hash, event_index)
SETTINGS storage_policy = 'live_market_ssd';

-- The header pins computation, scope, source/evaluation digests, exact event
-- count and transition count. Publish only after transition-row readback.
CREATE TABLE IF NOT EXISTS arte.event_boolean_products_v1
(
    product_hash FixedString(64),
    payload_json String CODEC(ZSTD(3))
)
ENGINE = MergeTree
ORDER BY product_hash
SETTINGS storage_policy = 'live_market_ssd';

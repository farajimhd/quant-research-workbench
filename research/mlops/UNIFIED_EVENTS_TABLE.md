# Unified Events Table

This document defines the training-oriented unified event table built from
compact SIP `quotes` and `trades`.

The goal is fast event-chunk retrieval for masked event modeling and downstream
microstructure models. The table is not a raw data archive. It is a clean,
compact, ordinal-indexed training table.

## Objective

Merge clean quotes and trades into one per-ticker event stream, assign a stable
ordinal after merge/filtering, and store only the fields needed by the event
chunk data provider.

The primary training lookup should be:

```sql
SELECT *
FROM market_sip_compact.events
PREWHERE ticker = <ticker>
  AND ordinal >= <origin_ordinal - events_per_chunk + 1>
  AND ordinal <= <origin_ordinal>
ORDER BY ordinal
```

This avoids open-ended timestamp scans such as:

```sql
sip_timestamp_us <= <origin>
```

## Build Script

Build the final table with:

```powershell
python D:\TradingML\codes\masked_event_model\v4\research\mlops\run_build_unified_events.py --rebuild
```

The launcher calls:

```text
research/mlops/clickhouse_build_unified_events.py
```

Default behavior:

```text
source range: 2019-01-01 -> 2099-12-31
train index: 2019-01-01 -> 2025-12-31
validation index: 2026-01-01 -> 2099-12-31
storage policy: CLICKHOUSE_LIVE_STORAGE_POLICY
event buckets: 256
clean mode: issue_flags_zero
```

Events are built bucket-by-bucket using:

```text
cityHash64(ticker) % build_buckets
```

This keeps ordinal assignment correct per ticker while making the build
restartable through `events_build_manifest`. Buckets with latest status `ok` are
skipped on rerun. Use `--retry-failed` or `--retry-started` to revisit failed or
interrupted buckets. Use `--force-bucket-delete` only when you intentionally want
to delete a previously written bucket before retrying it.

## Storage

Use the ClickHouse storage policy from:

```text
CLICKHOUSE_LIVE_STORAGE_POLICY
```

The current reason is practical: the existing compact raw tables use a different
SSD policy, and the unified training table needs separate free SSD capacity.

## Final Table

Suggested table name:

```text
market_sip_compact.events
```

Suggested schema:

```sql
CREATE TABLE market_sip_compact.events
(
    ticker LowCardinality(String),

    -- Clean unified event position after quote/trade merge/filtering.
    ordinal UInt64,

    -- 0 = quote, 1 = trade.
    event_type UInt8,

    -- Needed for within-chunk timing features.
    sip_timestamp_us UInt64,

    -- Unified price fields.
    -- quote: primary=ask, secondary=bid
    -- trade: primary=trade price, secondary=0
    price_primary_int UInt32,
    price_secondary_int UInt32,

    -- Unified size fields.
    -- quote: primary=ask_size, secondary=bid_size
    -- trade: primary=trade_size, secondary=0
    size_primary Float32,
    size_secondary Float32,

    -- Unified exchange fields.
    -- quote: primary=ask_exchange, secondary=bid_exchange
    -- trade: primary=trade_exchange, secondary=0
    exchange_primary UInt8,
    exchange_secondary UInt8,

    -- bit0: primary price scale
    -- bit1: secondary price scale
    -- bits2-4: tape code if retained
    -- bits5-7: reserved
    event_flags UInt8,

    -- Packed dense condition ids. Interpretation depends on event_type.
    conditions_packed UInt32,

    -- Useful for maintenance, auditing, and date-range deletion.
    event_date Date
)
ENGINE = MergeTree
PARTITION BY cityHash64(ticker) % 256
ORDER BY (ticker, ordinal)
SETTINGS storage_policy = '<CLICKHOUSE_LIVE_STORAGE_POLICY>';
```

## Price Encoding

Raw compact quote/trade tables store prices as integer plus scale code.

Scale meaning:

```text
scale = 0 -> price_int / 100
scale = 1 -> price_int / 10000
```

Because the unified event table has two price slots, each row needs two scale
bits in `event_flags`.

```text
event_flags bit0 = primary price scale
event_flags bit1 = secondary price scale
```

Quote mapping:

```text
event_type = 0
price_primary_int   = ask_price_int
price_secondary_int = bid_price_int
event_flags bit0    = ask scale from quote_flags bit1
event_flags bit1    = bid scale from quote_flags bit0
```

Trade mapping:

```text
event_type = 1
price_primary_int   = price_int
price_secondary_int = 0
event_flags bit0    = trade scale from trade_flags bit0
event_flags bit1    = 0
```

Decode examples:

```sql
if(bitAnd(event_flags, 1) = 1,
   price_primary_int / 10000.0,
   price_primary_int / 100.0) AS price_primary,

if(bitAnd(bitShiftRight(event_flags, 1), 1) = 1,
   price_secondary_int / 10000.0,
   price_secondary_int / 100.0) AS price_secondary
```

## Tape And Correction

If tape is retained, store it in `event_flags` bits 2-4:

```text
event_flags bits2-4 = tape code
```

Current compact source encoding:

```text
quote_flags bits2+ = tape code
trade_flags bits1+ = tape code
```

Trade correction is not included in the first final schema because the table is
intended to be compact and aligned with the event provider representation. If a
future experiment needs correction, use one reserved flag bit range or add a
separate compact field.

## Condition Encoding

Raw compact quote/trade tables keep condition combinations as
`LowCardinality(String)`. The unified event table does not keep those raw
strings. It maps each condition code through the appropriate reference table and
packs the dense IDs into one `UInt32`.

Quote rows use quote-condition dense IDs from:

```text
market_sip_compact.ref_quote_conditions
```

Quote packing uses four 8-bit slots:

```text
bits 0-7    quote condition 1 dense_id
bits 8-15   quote condition 2 dense_id
bits 16-23  quote condition 3 dense_id
bits 24-31  quote condition 4 dense_id
```

Trade rows use trade-condition dense IDs from:

```text
market_sip_compact.ref_trade_conditions
```

Trade packing uses five 6-bit slots:

```text
bits 0-5    trade condition 1 dense_id
bits 6-11   trade condition 2 dense_id
bits 12-17  trade condition 3 dense_id
bits 18-23  trade condition 4 dense_id
bits 24-29  trade condition 5 dense_id
bits 30-31  reserved
```

Dense ID `0` means absent or unknown in every slot. The model/data provider must
interpret `conditions_packed` by `event_type`: quote rows are 4x8-bit fields;
trade rows are 5x6-bit fields. This lets trades preserve the fifth condition
without widening the row and keeps quote conditions aligned to byte boundaries.

When joining condition references, use a unique modifier map:

```sql
SELECT modifier_int, min(dense_id) AS dense_id
FROM market_sip_compact.ref_quote_conditions
GROUP BY modifier_int
```

The quote glossary has repeated modifier codes across SIP mappings, while the
raw flatfile condition field only contains the modifier code. The builder uses
the lowest dense ID per modifier as the deterministic representation.

## Field Mapping

Quote source row to unified event:

```text
ticker              -> ticker
event_type          -> 0
sip_timestamp_us    -> sip_timestamp_us
price_primary_int   -> ask_price_int
price_secondary_int -> bid_price_int
size_primary        -> ask_size
size_secondary      -> bid_size
exchange_primary    -> ask_exchange
exchange_secondary  -> bid_exchange
conditions_packed   -> 4x8-bit packed dense ids from quote conditions
event_date          -> event_date
```

Trade source row to unified event:

```text
ticker              -> ticker
event_type          -> 1
sip_timestamp_us    -> sip_timestamp_us
price_primary_int   -> price_int
price_secondary_int -> 0
size_primary        -> size
size_secondary      -> 0
exchange_primary    -> exchange
exchange_secondary  -> 0
conditions_packed   -> 5x6-bit packed dense ids from trade conditions
event_date          -> event_date
```

Quote and trade condition dense IDs must come from separate reference tables:

```text
market_sip_compact.ref_quote_conditions
market_sip_compact.ref_trade_conditions
```

The generic stock conditions API table is not sufficient here because Massive's
quote condition modifiers and trade condition modifiers are different glossary
tables with different domains.

## Dropped Fields

These fields are intentionally not stored in the final training table:

```text
participant_delta_us
sequence_number
source_file
source_date
raw issue_flags
raw quote/trade condition strings
raw quote/trade separate column names
```

`sequence_number` is still required during table construction to break ordering
ties, but it is not required for training lookup after `ordinal` is assigned.

## Filtering Rules

The training table should contain clean events only.

Common filters:

```sql
issue_flags = 0
AND ticker != ''
AND sip_timestamp_us > 0
AND sequence_number > 0
```

Quote filters:

```sql
bid_price_int > 0
AND ask_price_int > 0
AND bid_size > 0
AND ask_size > 0
AND decoded_bid_price <= decoded_ask_price
```

Trade filters:

```sql
price_int > 0
AND size > 0
```

Do not rely on `issue_flags = 0` alone. Current `issue_flags` cover structural
ingest/conversion issues, but they are not a full semantic validity proof.

## Ordinal Assignment

Ordinal must be assigned after:

1. quote/trade source filtering,
2. quote/trade union,
3. deterministic ordering.

Construction ordering:

```sql
ORDER BY sip_timestamp_us, sequence_number, event_type
```

Ordinal expression:

```sql
row_number() OVER (
  PARTITION BY ticker
  ORDER BY sip_timestamp_us, sequence_number, event_type
) AS ordinal
```

Ordinal meaning:

```text
Position in the clean unified event stream for that ticker.
```

## Issue Rows

Rows excluded from `events` should not disappear silently. Keep an issue/audit
path for later analysis, for example:

```text
market_sip_compact.events_issues
```

That table can store original compact fields, `source_kind`, and `issue_reason`
or `issue_flags`. It is not part of the baseline training provider.

## Training Query

For one sample:

```sql
SELECT
    event_type,
    sip_timestamp_us,
    price_primary_int,
    price_secondary_int,
    size_primary,
    size_secondary,
    exchange_primary,
    exchange_secondary,
    event_flags,
    conditions_packed
FROM market_sip_compact.events
PREWHERE ticker = <ticker>
  AND ordinal >= <origin_ordinal - events_per_chunk + 1>
  AND ordinal <= <origin_ordinal>
ORDER BY ordinal
```

Expected row count:

```text
events_per_chunk
```

If fewer rows are returned, the sampled origin is invalid for that chunk length.

## Open Decisions

The first production build should decide:

```text
events table name
partition count, currently suggested as 256
whether tape stays in event_flags bits2-4
whether trade correction is dropped or packed
whether events_issues is built immediately or in a later audit pass
```

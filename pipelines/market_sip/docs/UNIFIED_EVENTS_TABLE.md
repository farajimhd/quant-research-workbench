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
python D:\TradingML\codes\masked_event_model\v4\pipelines\market_sip\events\run_build_unified_events.py --rebuild
```

Use `--rebuild` when moving from the older ticker-at-a-time builder to this
daily continuity builder. The script refuses to append if `events` already has
rows but `events_ordinal_continuity` is empty, because that state cannot preserve
ordinal correctness.

`--rebuild` is intentionally destructive. It drops and recreates the events,
build-manifest, continuity, train-index, and validation-index tables. For these
explicit rebuild drops, the script passes ClickHouse drop-size settings:

```text
max_table_size_to_drop = 0
max_partition_size_to_drop = 0
```

This is required because ClickHouse protects large tables from accidental drops
by default. The override is only used in the `--rebuild` table-drop path.

The launcher calls:

```text
pipelines/market_sip/events/clickhouse_build_unified_events.py
```

Default behavior:

```text
source range: 2019-01-01 -> 2099-12-31
build unit: one source event_date at a time
train index: 2019-01-01 -> 2025-12-31
validation index: 2026-01-01 -> 2099-12-31
storage policy: CLICKHOUSE_HISTORICAL_STORAGE_POLICY
storage partition mode: toYYYYMM(event_date)
max_partitions_per_insert_block: 1024
row policy: structural filter plus numeric zero-sanitization
drop trade correction codes: 7,8,10,11
```

Events are built one source date at a time. Each day job reads all structurally
placeable, sanitized quote/trade rows for that `event_date`, merges quotes and
trades per ticker, assigns that day's ticker-local row number, adds the prior
`next_ordinal` from `events_ordinal_continuity`, writes rows to `events`, and
records status in `events_build_manifest` with `ticker='__ALL__'`.

After a day is written, the builder appends one continuity row per ticker that
had events that day. This continuity step uses the same sanitized daily
quote/trade union plus the prior continuity offset. It must not scan the growing
`events` table by `event_date`, because `events` is ordered by `(ticker,
ordinal)` and that date scan becomes slower as the table grows.

The continuity table is the carry-forward state:

```text
market_sip_compact.events_ordinal_continuity
```

It stores:

```text
ticker
build_step
source_date
event_count
next_ordinal
last_ordinal
first_sip_timestamp_us
last_sip_timestamp_us
```

`build_step` is the stable chronological source-date index. The next day joins
to `argMax(next_ordinal, build_step)` per ticker, so every ticker keeps one
continuous ordinal stream across days.

After all requested days finish, the builder recreates the train and validation
sampling rows from `events_ordinal_continuity`, not from the completed `events`
table. The continuity table is much smaller and already carries the per-day
ticker event counts and ordinal ranges needed for split metadata.

The physical ClickHouse table partitioning is month based:

```text
toYYYYMM(event_date)
```

This keeps the number of active ClickHouse parts bounded during daily appends
while preserving a single continuous event sequence per ticker through
`ORDER BY (ticker, ordinal)`. Source days with latest status `ok` are skipped
on rerun. Use `--retry-failed` or `--retry-started` to revisit failed or
interrupted days. Use `--force-day-delete` only when you intentionally want to
delete a previously written day before retrying it.

The launcher still sets:

```text
--max-partitions-per-insert-block 1024
```

This remains harmless with monthly partitioning and keeps explicit
`--partition-mode ticker_hash` experiments from hitting ClickHouse's default
100-partition insert-block limit.

Progress output includes:

```text
day_step
current day
completed / skipped / failed / remaining
percent complete
elapsed time
days per minute
ETA
```

Ctrl+C is handled. The active day is marked `interrupted` in
`events_build_manifest` when the Python process receives the interrupt, and a
`run_interrupted` row is appended to the JSONL report. To resume after Ctrl+C,
use:

```powershell
python D:\TradingML\codes\masked_event_model\v4\pipelines\market_sip\events\run_build_unified_events.py --retry-started --force-day-delete
```

`--force-day-delete` is required for interrupted/started day retries so the
script deletes rows for that event date and the matching continuity rows before
rebuilding it. This avoids duplicate rows if ClickHouse had already committed
part or all of the interrupted insert.

Retry deletes use synchronous ClickHouse mutations (`mutations_sync = 2`) before
the day is rebuilt.

## Storage

Use the ClickHouse storage policy from:

```text
CLICKHOUSE_HISTORICAL_STORAGE_POLICY
```

Historical training data must not share the live QMD SSD policy. QMD live
tables use `CLICKHOUSE_LIVE_STORAGE_POLICY`; historical `market_sip_compact`
tables use `CLICKHOUSE_HISTORICAL_STORAGE_POLICY`.

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

    -- bit 0 event type, bit 1 primary price scale, bit 2 secondary price scale,
    -- bits 3-5 tape, bits 6-7 reserved.
    event_meta UInt8,

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

    -- Explicit condition/indicator token ids. Unknown or absent tokens are 0.
    condition_token_1 UInt8,
    condition_token_2 UInt8,
    condition_token_3 UInt8,
    condition_token_4 UInt8,
    condition_token_5 UInt8,

    -- Useful for maintenance, auditing, and date-range deletion.
    event_date Date
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(event_date)
ORDER BY (ticker, ordinal)
SETTINGS storage_policy = '<CLICKHOUSE_HISTORICAL_STORAGE_POLICY>';
```

## Split Index Tables

The builder recreates these tables when `--rebuild` is used:

```text
market_sip_compact.train_2019_to_2025
market_sip_compact.validation_2026
```

Each ticker writes its split rows immediately after its event rows are inserted.
The schema is:

```sql
(
    ticker LowCardinality(String),
    split_start_date Date,
    split_end_date Date,
    context_events UInt32,
    split_event_count UInt64,
    valid_origin_count UInt64,
    first_ordinal UInt64,
    last_ordinal UInt64,
    max_valid_ordinal UInt64,
    first_sip_timestamp_us UInt64,
    last_sip_timestamp_us UInt64,
    built_at DateTime DEFAULT now()
)
```

`first_ordinal` and `last_ordinal` are the first and last event ordinals inside
that split's timestamp/date range for the ticker. Every event in the split can
be used as an origin ordinal:

```text
first_ordinal <= origin_ordinal <= max_valid_ordinal
```

`max_valid_ordinal` is `last_ordinal`. `valid_origin_count` is equal to
`split_event_count`.

The split index is derived from `events_ordinal_continuity`:

```text
split_event_count = sum(event_count) over source_date range
first_ordinal = min(last_ordinal - event_count + 1) over source_date range
last_ordinal = max(last_ordinal) over source_date range
first_sip_timestamp_us = min(first_sip_timestamp_us) over source_date range
last_sip_timestamp_us = max(last_sip_timestamp_us) over source_date range
```

Older continuity tables may not have `first_sip_timestamp_us`; the builder adds
the column if missing and falls back to the first available daily
`last_sip_timestamp_us` for index metadata. This timestamp is descriptive
metadata only; sampling uses ticker and ordinal.

For each sampled origin, the loader should request:

```text
[origin_ordinal - context_events + 1, origin_ordinal]
```

If fewer than `context_events` rows are returned because the origin is near the
beginning of a ticker's event stream, the loader left-pads the missing events
with the all-zero/empty event representation.

The build scripts validate this schema after table creation. If a table with
legacy `event_type`, `event_flags`, `conditions_packed`, or `condition_tokens_packed` columns already exists, the run
fails and requires a fresh table or `--rebuild`.

## Event Metadata And Condition Tokens

The unified event row stores event type, price-scale flags, and tape in one
byte:

```text
event_meta UInt8
bit 0      event type: 0 quote, 1 trade
bit 1      primary price scale: 0 cents, 1 ten-thousandths
bit 2      secondary price scale: 0 cents, 1 ten-thousandths
bits 3-5   tape
bits 6-7   reserved
```

Condition/indicator ids are stored as explicit bytes:

```text
condition_token_1 UInt8
condition_token_2 UInt8
condition_token_3 UInt8
condition_token_4 UInt8
condition_token_5 UInt8
```

Unknown or absent tokens are `0`. Overflow and unknown-token counts are audit
metrics emitted by build logs; they are not persisted as per-row model
features.

Bit layout:

```text
bits  0-7    token_0
bits  8-15   token_1
bits 16-23   token_2
bits 24-31   token_3
bits 32-39   token_4
bits 40-42   token_count, capped at 5
bit  43      token_overflow, raw token count was greater than 5
bit  44      unknown_token_seen, a non-empty raw code did not join to the token reference
bit  45      primary_price_scale
bit  46      secondary_price_scale
bits 47-49   tape_code
bits 50-51   condition_pack_kind
bits 52-55   reserved
bits 56-63   pack_version, currently 1
```

Token IDs come from the unified dense token reference:

```text
market_sip_compact.event_condition_token_reference
```

Token ID `0` means absent or unknown. The reference table assigns stable IDs to
quote conditions, trade conditions, trade corrections, and quote indicators, but
the event table only packs fields available in production-time live data. Trade
corrections are used for filtering historical rows, not as model input tokens.
The builder joins only rows where `is_join_canonical = 1`, so repeated glossary
codes cannot multiply event rows.

`condition_pack_kind`:

```text
0 = no condition payload
1 = quote conditions + quote indicators
2 = trade conditions
3 = reserved
```

Price scale bits keep the raw compact source scale:

```text
scale = 0 -> price_int / 100
scale = 1 -> price_int / 10000
```

Decode examples:

```sql
if(bitAnd(bitShiftRight(event_meta, 1), 1) = 1,
   price_primary_int / 10000.0,
   price_primary_int / 100.0) AS price_primary,

if(bitAnd(bitShiftRight(event_meta, 2), 1) = 1,
   price_secondary_int / 10000.0,
   price_secondary_int / 100.0) AS price_secondary
```

Quote rows pack the first four quote condition tokens and the first quote
indicator token. `token_overflow` is set when more raw quote
conditions/indicators exist than the five available slots.

Trade rows pack the first five trade condition tokens. `token_overflow` is set
when raw trade conditions exceed five slots.

Historical trade rows whose correction code is configured for dropping are
excluded before event construction. The default dropped correction codes are
`7,8,10,11`:

```text
07 = original trade later marked erroneous
08 = original trade later cancelled
10 = cancel record
11 = error record
```

Raw quote/trade condition strings are not stored in `events`; validation against
flatfiles must reconstruct `event_meta` and `condition_token_1..5` through the
same reference table and builder expressions.

## Field Mapping

Quote source row to unified event:

```text
ticker              -> ticker
event_meta          -> event type 0 plus ask/bid scale and tape bits
sip_timestamp_us    -> sip_timestamp_us
price_primary_int   -> ask_price_int
price_secondary_int -> bid_price_int
size_primary        -> ask_size
size_secondary      -> bid_size
exchange_primary    -> ask_exchange
exchange_secondary  -> bid_exchange
condition_token_1..5 -> first four quote conditions plus first quote indicator
event_date          -> event_date
```

Trade source row to unified event:

```text
ticker              -> ticker
event_meta          -> event type 1 plus trade scale and tape bits
sip_timestamp_us    -> sip_timestamp_us
price_primary_int   -> price_int
price_secondary_int -> 0
size_primary        -> size
size_secondary      -> 0
exchange_primary    -> exchange
exchange_secondary  -> 0
condition_token_1..5 -> first five trade conditions
event_date          -> event_date
```

Condition token IDs must come from the unified reference table:

```text
market_sip_compact.event_condition_token_reference
```

The older individual reference tables still describe the source domains, but
the event table uses the single dense-token table so every token slot is an
8-bit ID with one decoding path.

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

## Filtering And Sanitization Rules

The event table should preserve structurally placeable quote/trade rows so
condition and indicator signals are not lost. Rows are filtered only when they
cannot be assigned a reliable ticker-local position in the stream.

Common filters:

```sql
ticker != ''
AND sip_timestamp_us > 0
AND sequence_number > 0
```

Quote numeric sanitization:

```sql
if bid or ask price is nonpositive, precision-clipped, or crossed:
    bid/ask price fields = 0
    bid/ask price-scale bits = 0
if bid or ask size is nonpositive:
    that size field = 0
```

Trade numeric sanitization:

```sql
if trade price is nonpositive or precision-clipped:
    trade price field = 0
    trade price-scale bit = 0
if trade size is nonpositive:
    trade size field = 0
```

Historical trade correction codes configured for dropping are still excluded
before event construction. The default dropped correction codes are `7,8,10,11`.

## Ordinal Assignment

Ordinal must be assigned after:

1. quote/trade structural filtering and numeric sanitization,
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
    event_meta,
    sip_timestamp_us,
    price_primary_int,
    price_secondary_int,
    size_primary,
    size_secondary,
    exchange_primary,
    exchange_secondary,
    condition_token_1,
    condition_token_2,
    condition_token_3,
    condition_token_4,
    condition_token_5
FROM market_sip_compact.events_YYYY
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

Production storage now uses one physical event table per year:

```text
market_sip_compact.events_2019
market_sip_compact.events_2020
...
```

Operational commands may still pass `--events-table events`; ingestion and
downstream builders treat that as a logical name and route to `events_YYYY`.
Use a concrete table name only for isolated scratch/test tables.

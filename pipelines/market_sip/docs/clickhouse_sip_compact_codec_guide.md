# ClickHouse SIP Compact Codec Ingest Guide

This guide documents the validated compact ClickHouse representation for Massive SIP quote and trade flatfiles.

The production ingest script is:

```powershell
python pipelines\market_sip\ingest\clickhouse_ingest_sip_compact_codec.py
```

The schema and insert expressions are shared with the benchmark/validation scripts through:

```text
pipelines/market_sip/benchmarks/clickhouse_compact_schema_codec_benchmark.py
```

## Production Ingest

Default production settings:

- Database: `market_sip_compact`
- Quote table: `quotes`
- Trade table: `trades`
- Manifest table: `ingest_manifest`
- Default concurrency: `12`
- Default per-insert ClickHouse threads: `4`
- Default Polars source preflight workers: inherited from `SIP_INGEST_PREFLIGHT_PROCESSES`
- Default source root on Windows: discovered from the shared flatfile config, normally `D:\market-data\flatfiles\us_stocks_sip`
- Default ClickHouse file root: discovered from env, normally `/mnt/d/market-data/flatfiles/us_stocks_sip`
- Storage policy: discovered from `CLICKHOUSE_HISTORICAL_STORAGE_POLICY` first

Example for the first production chunk:

```powershell
python D:\TradingML\codes\masked_event_model\v4\pipelines\market_sip\ingest\clickhouse_ingest_sip_compact_codec.py --database market_sip_compact --start-date 2025-01-01 --end-date 2026-06-06 --insert-concurrency 12 --max-threads 4
```

Example for the next older chunk:

```powershell
python D:\TradingML\codes\masked_event_model\v4\pipelines\market_sip\ingest\clickhouse_ingest_sip_compact_codec.py --database market_sip_compact --start-date 2024-01-01 --end-date 2024-12-31 --insert-concurrency 12 --max-threads 4
```

The script is resumable. It writes one row per source file attempt to `ingest_manifest`. Files with latest status `ok` are skipped. Files with latest status `failed` or `started` are skipped unless `--retry-failed` or `--retry-started` is provided.

The script validates the existing target table schema before ingesting. If a stale table exists, for example from an older schema where price integers were widened to `UInt64`, the script stops with a clear error. Use a fresh table/database or migrate/drop the stale table before ingesting.

The script also validates that `event_date` is materialized from `sip_timestamp_us` in UTC. This matters because the ClickHouse server timezone may be different from UTC, and a server-local materialized date can partition late-session or after-hours rows into the wrong day/month.

Before inserting, the script loads manifest status in bulk, skips files whose latest status is already `ok`, then runs a Polars streaming/lazy source preflight for each pending CSV file. The preflight extracts:

- `expected_rows`
- `expected_min_sip_timestamp`
- `expected_max_sip_timestamp`

Those values are written to `ingest_manifest` and to the JSONL report. After ClickHouse finishes each insert, the script compares ClickHouse query-log `read_rows` and `written_rows` against `expected_rows`. If row counts differ, the job is marked failed. Use `--preflight-processes 0` only for debugging when you explicitly want to skip this reconciliation.

The preflight snapshot is written to the JSONL report before inserts start. Manifest `discovered` and `started` rows are written with bulk `INSERT ... VALUES` batches, not one row at a time.

## Tables

Quote table:

```sql
quotes
(
    ticker LowCardinality(String),
    sip_timestamp_us UInt64 CODEC(DoubleDelta, ZSTD(1)),
    participant_delta_us Int32 CODEC(T64, ZSTD(1)),
    sequence_number UInt32 CODEC(T64, ZSTD(1)),
    bid_price_int UInt32 CODEC(T64, ZSTD(1)),
    ask_price_int UInt32 CODEC(T64, ZSTD(1)),
    bid_size UInt32 CODEC(T64, ZSTD(1)),
    ask_size UInt32 CODEC(T64, ZSTD(1)),
    bid_exchange UInt8,
    ask_exchange UInt8,
    conditions LowCardinality(String),
    indicators LowCardinality(String),
    quote_flags UInt8,
    issue_flags UInt16,
    event_date Date MATERIALIZED toDate(toTimeZone(fromUnixTimestamp64Micro(toInt64(sip_timestamp_us)), 'UTC'))
)
PARTITION BY toYYYYMM(event_date)
ORDER BY (ticker, sip_timestamp_us, sequence_number)
```

Trade table:

```sql
trades
(
    ticker LowCardinality(String),
    sip_timestamp_us UInt64 CODEC(DoubleDelta, ZSTD(1)),
    participant_delta_us Int32 CODEC(T64, ZSTD(1)),
    sequence_number UInt32 CODEC(T64, ZSTD(1)),
    price_int UInt32 CODEC(T64, ZSTD(1)),
    size Float32 CODEC(ZSTD(1)),
    exchange UInt8,
    conditions LowCardinality(String),
    trade_flags UInt8,
    issue_flags UInt16,
    event_date Date MATERIALIZED toDate(toTimeZone(fromUnixTimestamp64Micro(toInt64(sip_timestamp_us)), 'UTC'))
)
PARTITION BY toYYYYMM(event_date)
ORDER BY (ticker, sip_timestamp_us, sequence_number)
```

## Price Compacting

Prices are stored as a `UInt32` integer plus one scale bit.

Scale rule:

- scale bit `0`: price has cent precision. Store `round(price * 100)`.
- scale bit `1`: price needs 1e-4 precision. Store `round(price * 10000)`.
- scale bit `1` is used when `0 < price < 1`.
- scale bit `1` is also used when a price above or equal to `$1` has non-zero precision beyond cents and `round(price * 10000)` fits in `UInt32`.

This preserves validated sub-cent trade prices while keeping the price column at 32 bits. If an extremely high price above `$429,496.7295` has sub-cent precision, the row falls back to cent scale and sets a precision-clipped issue bit. That rare case is explicitly flagged instead of widening every price to 64 bits.

Quote flags:

- bit 0: bid price scale, `0 = cents`, `1 = 1e-4`
- bit 1: ask price scale, `0 = cents`, `1 = 1e-4`
- bits 2-3: tape code, stored as `raw_tape - 1`

Trade flags:

- bit 0: trade price scale, `0 = cents`, `1 = 1e-4`
- bits 1-2: tape code, stored as `raw_tape - 1`
- bits 3-6: correction code, clamped to `0..15`

Decode quote prices:

```sql
if(bitAnd(quote_flags, 1) = 1, bid_price_int / 10000.0, bid_price_int / 100.0) AS bid_price,
if(bitAnd(bitShiftRight(quote_flags, 1), 1) = 1, ask_price_int / 10000.0, ask_price_int / 100.0) AS ask_price
```

Decode trade price:

```sql
if(bitAnd(trade_flags, 1) = 1, price_int / 10000.0, price_int / 100.0) AS price
```

## Timestamp Compacting

Raw SIP timestamps are nanoseconds. The compact tables store microseconds:

```sql
sip_timestamp_us = intDiv(sip_timestamp, 1000)
```

Participant timestamp is stored as a signed microsecond delta from SIP time:

```sql
participant_delta_us = intDiv(participant_timestamp - sip_timestamp, 1000)
```

The stored delta is clipped to `Int32`. Rows whose original delta exceeds `Int32` range are marked in `issue_flags`.

Recover participant time approximately:

```sql
sip_timestamp_us + participant_delta_us AS participant_timestamp_us
```

TRF timestamps are intentionally not stored because the downloaded data showed poor availability/reliability.

`event_date` is the UTC date derived from `sip_timestamp_us`:

```sql
toDate(toTimeZone(fromUnixTimestamp64Micro(toInt64(sip_timestamp_us)), 'UTC'))
```

Do not use server-local date materialization here. On a workstation whose ClickHouse timezone is not UTC, `toDate(fromUnixTimestamp64Micro(...))` can silently create a local date and shift rows into the previous day.

## Sizes

Quote sizes are stored as `UInt32`:

```sql
bid_size = toUInt32(toFloat64OrZero(bid_size))
ask_size = toUInt32(toFloat64OrZero(ask_size))
```

Trade size is stored as `Float32 CODEC(ZSTD(1))`:

```sql
size = toFloat32OrZero(size)
```

This is intentional. Massive trade sizes can be fractional. Earlier integer conversion turned fractional sizes into `0`; validation confirmed `Float32` preserves the tested rows.

`T64` is not used for trade size because it is an integer-oriented codec. Quote sizes are integer-like and use `UInt32 CODEC(T64, ZSTD(1))`; trade size can be fractional, so it remains `Float32` with `ZSTD(1)` compression.

## Conditions And Indicators

Quote `conditions` and `indicators` are stored as `LowCardinality(String)`.

Trade `conditions` are stored as `LowCardinality(String)`.

This keeps ingestion simple and preserves the raw condition combinations. Dense categorical IDs are created later for model-specific training representations.

For the unified training event table, condition strings are not kept as raw
strings. They are mapped through separate quote/trade reference tables and packed
into one `UInt32`:

```text
quote event: 4 quote-condition dense IDs, 8 bits each
trade event: 5 trade-condition dense IDs, 6 bits each, bits 30-31 reserved
```

The condition domains are intentionally separate:

```text
market_sip_compact.ref_quote_conditions
market_sip_compact.ref_trade_conditions
```

The event row's `event_type` determines how to decode `conditions_packed`. The
unified event builder joins through a unique `modifier_int -> dense_id` map using
`min(dense_id)` per modifier. This avoids row multiplication from repeated quote
glossary modifier codes.

## Issue Flags

Quote `issue_flags`:

- bit 0: `bid_price <= 0`
- bit 1: `ask_price <= 0`
- bit 2: `bid_size <= 0`
- bit 3: `ask_size <= 0`
- bit 4: participant delta exceeded `Int32` range before clipping
- bit 5: bid price had sub-cent precision but could not fit 1e-4 scale in `UInt32`
- bit 6: ask price had sub-cent precision but could not fit 1e-4 scale in `UInt32`

Trade `issue_flags`:

- bit 0: `price <= 0`
- bit 1: `size <= 0`
- bit 2: participant delta exceeded `Int32` range before clipping
- bit 3: trade price had sub-cent precision but could not fit 1e-4 scale in `UInt32`

Rows are not dropped by this ingest. Problematic rows are preserved and flagged so downstream model-data builders can decide whether to filter, mask, or learn robustly from them.

## Query Examples

Quote chart query:

```sql
SELECT
    ticker,
    toTimeZone(fromUnixTimestamp64Micro(toInt64(sip_timestamp_us)), 'UTC') AS sip_time_utc,
    sip_timestamp_us,
    sequence_number,
    if(bitAnd(quote_flags, 1) = 1, bid_price_int / 10000.0, bid_price_int / 100.0) AS bid_price,
    if(bitAnd(bitShiftRight(quote_flags, 1), 1) = 1, ask_price_int / 10000.0, ask_price_int / 100.0) AS ask_price,
    bid_size,
    ask_size,
    bid_exchange,
    ask_exchange,
    bitAnd(bitShiftRight(quote_flags, 2), 3) + 1 AS tape,
    conditions,
    indicators,
    issue_flags
FROM market_sip_compact.quotes
WHERE ticker = 'AAPL'
  AND event_date BETWEEN toDate('2025-01-01') AND toDate('2025-01-31')
ORDER BY sip_timestamp_us, sequence_number
LIMIT 100000
```

Trade chart query:

```sql
SELECT
    ticker,
    toTimeZone(fromUnixTimestamp64Micro(toInt64(sip_timestamp_us)), 'UTC') AS sip_time_utc,
    sip_timestamp_us,
    sequence_number,
    if(bitAnd(trade_flags, 1) = 1, price_int / 10000.0, price_int / 100.0) AS price,
    size,
    exchange,
    bitAnd(bitShiftRight(trade_flags, 1), 3) + 1 AS tape,
    bitAnd(bitShiftRight(trade_flags, 3), 15) AS correction,
    conditions,
    issue_flags
FROM market_sip_compact.trades
WHERE ticker = 'AAPL'
  AND event_date BETWEEN toDate('2025-01-01') AND toDate('2025-01-31')
ORDER BY sip_timestamp_us, sequence_number
LIMIT 100000
```

Ingest status query:

```sql
SELECT
    kind,
    status,
    count() AS attempts,
    uniqExact(source_date, source_file) AS files,
    max(updated_at) AS last_update
FROM market_sip_compact.ingest_manifest
GROUP BY kind, status
ORDER BY kind, status
```

Latest status per file:

```sql
SELECT *
FROM
(
    SELECT
        *,
        row_number() OVER (
            PARTITION BY kind, source_date, source_file
            ORDER BY updated_at DESC
        ) AS rn
    FROM market_sip_compact.ingest_manifest
)
WHERE rn = 1
ORDER BY kind, source_date, source_file
```

## Validation

After ingesting a test chunk, validate random raw rows against ClickHouse decoded rows:

```powershell
python D:\TradingML\codes\masked_event_model\v4\pipelines\market_sip\clickhouse_compact_schema_validate_sample.py --database market_sip_compact --quote-table quotes --trade-table trades --quote-date 2026-05-15 --trade-date 2026-05-15 --sample-size 1000
```

Expected result for a valid schema is:

- quote mismatches: `0`
- trade mismatches: `0`
- missing rows: `0`
- duplicate key rows: `0`

## Fixing A Stale Server-Local `event_date`

Older compact tables may have this stale expression:

```sql
event_date Date MATERIALIZED toDate(fromUnixTimestamp64Micro(toInt64(sip_timestamp_us)))
```

That expression uses the ClickHouse server timezone. If the server timezone is not UTC, existing partitions and future inserts will use the wrong date semantics.

Use the UTC rebuild script before inserting more data into such a database:

```powershell
python D:\TradingML\codes\masked_event_model\v4\pipelines\market_sip\clickhouse_fix_compact_event_date.py --database market_sip_compact --copy --validate --max-threads 24
```

The script:

- creates UTC shadow tables: `quotes_event_date_utc_rebuild` and `trades_event_date_utc_rebuild`
- copies the old tables month by month
- records copy status in `event_date_fix_manifest`
- validates source/shadow row counts
- validates that shadow `event_date` exactly matches UTC-derived `sip_timestamp_us`
- does not replace the production tables unless `--swap` is provided

After copy/validation completes cleanly, promote the corrected tables and remove the stale local-date backups:

```powershell
python D:\TradingML\codes\masked_event_model\v4\pipelines\market_sip\clickhouse_fix_compact_event_date.py --database market_sip_compact --validate --swap --drop-stale-backups-after-swap
```

The swap first validates the UTC shadow tables, renames the old tables to timestamped backups, promotes the UTC shadow tables to `quotes` and `trades`, validates the promoted production tables, and then drops the stale backups when `--drop-stale-backups-after-swap` is provided. Use this flag for the large production dataset so stale server-local-date tables are not kept on disk.

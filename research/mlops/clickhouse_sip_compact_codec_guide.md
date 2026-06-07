# ClickHouse SIP Compact Codec Ingest Guide

This guide documents the validated compact ClickHouse representation for Massive SIP quote and trade flatfiles.

The production ingest script is:

```powershell
python research\mlops\clickhouse_ingest_sip_compact_codec.py
```

The schema and insert expressions are shared with the benchmark/validation scripts through:

```text
research/mlops/clickhouse_compact_schema_codec_benchmark.py
```

## Production Ingest

Default production settings:

- Database: `market_sip_compact`
- Quote table: `quotes_canonical`
- Trade table: `trades_canonical`
- Manifest table: `compact_ingest_manifest`
- Default concurrency: `12`
- Default per-insert ClickHouse threads: `4`
- Default source root on Windows: discovered from the shared flatfile config, normally `D:\market-data\flatfiles\us_stocks_sip`
- Default ClickHouse file root: discovered from env, normally `/mnt/d/market-data/flatfiles/us_stocks_sip`
- Storage policy: discovered from `CLICKHOUSE_HISTORICAL_STORAGE_POLICY` first

Example for the first production chunk:

```powershell
python D:\TradingML\codes\masked_event_model\v4\research\mlops\clickhouse_ingest_sip_compact_codec.py --database market_sip_compact --start-date 2025-01-01 --end-date 2026-06-06 --insert-concurrency 12 --max-threads 4
```

Example for the next older chunk:

```powershell
python D:\TradingML\codes\masked_event_model\v4\research\mlops\clickhouse_ingest_sip_compact_codec.py --database market_sip_compact --start-date 2024-01-01 --end-date 2024-12-31 --insert-concurrency 12 --max-threads 4
```

The script is resumable. It writes one row per source file attempt to `compact_ingest_manifest`. Files with latest status `ok` are skipped. Files with latest status `failed` or `started` are skipped unless `--retry-failed` or `--retry-started` is provided.

The script validates the existing target table schema before ingesting. If a stale table exists, for example from an older schema where price integers were `UInt32`, the script stops with a clear error. Use a fresh table/database or migrate/drop the stale table before ingesting.

## Tables

Quote table:

```sql
quotes_canonical
(
    ticker LowCardinality(String),
    sip_timestamp_us UInt64 CODEC(DoubleDelta, ZSTD(1)),
    participant_delta_us Int32 CODEC(T64, ZSTD(1)),
    sequence_number UInt32 CODEC(T64, ZSTD(1)),
    bid_price_int UInt64 CODEC(DoubleDelta, ZSTD(1)),
    ask_price_int UInt64 CODEC(DoubleDelta, ZSTD(1)),
    bid_size UInt32 CODEC(T64, ZSTD(1)),
    ask_size UInt32 CODEC(T64, ZSTD(1)),
    bid_exchange UInt8,
    ask_exchange UInt8,
    conditions LowCardinality(String),
    indicators LowCardinality(String),
    quote_flags UInt8,
    issue_flags UInt16,
    event_date Date MATERIALIZED toDate(fromUnixTimestamp64Micro(toInt64(sip_timestamp_us)))
)
PARTITION BY toYYYYMM(event_date)
ORDER BY (ticker, sip_timestamp_us, sequence_number)
```

Trade table:

```sql
trades_canonical
(
    ticker LowCardinality(String),
    sip_timestamp_us UInt64 CODEC(DoubleDelta, ZSTD(1)),
    participant_delta_us Int32 CODEC(T64, ZSTD(1)),
    sequence_number UInt32 CODEC(T64, ZSTD(1)),
    price_int UInt64 CODEC(DoubleDelta, ZSTD(1)),
    size Float32,
    exchange UInt8,
    conditions LowCardinality(String),
    trade_flags UInt8,
    issue_flags UInt16,
    event_date Date MATERIALIZED toDate(fromUnixTimestamp64Micro(toInt64(sip_timestamp_us)))
)
PARTITION BY toYYYYMM(event_date)
ORDER BY (ticker, sip_timestamp_us, sequence_number)
```

## Price Compacting

Prices are stored as an integer plus one scale bit.

Scale rule:

- scale bit `0`: price has cent precision. Store `round(price * 100)`.
- scale bit `1`: price needs 1e-4 precision. Store `round(price * 10000)`.
- scale bit `1` is used when `0 < price < 1` or when a price above or equal to `$1` has sub-cent precision.

This preserves validated sub-cent trade prices while keeping ordinary cent-priced rows compact.

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

## Sizes

Quote sizes are stored as `UInt32`:

```sql
bid_size = toUInt32(toFloat64OrZero(bid_size))
ask_size = toUInt32(toFloat64OrZero(ask_size))
```

Trade size is stored as `Float32`:

```sql
size = toFloat32OrZero(size)
```

This is intentional. Massive trade sizes can be fractional. Earlier integer conversion turned fractional sizes into `0`; validation confirmed `Float32` preserves the tested rows.

## Conditions And Indicators

Quote `conditions` and `indicators` are stored as `LowCardinality(String)`.

Trade `conditions` are stored as `LowCardinality(String)`.

This keeps ingestion simple and preserves the raw condition combinations. Dense categorical IDs can be created later for model-specific training representations.

## Issue Flags

Quote `issue_flags`:

- bit 0: `bid_price <= 0`
- bit 1: `ask_price <= 0`
- bit 2: `bid_size <= 0`
- bit 3: `ask_size <= 0`
- bit 4: participant delta exceeded `Int32` range before clipping

Trade `issue_flags`:

- bit 0: `price <= 0`
- bit 1: `size <= 0`
- bit 2: participant delta exceeded `Int32` range before clipping

Rows are not dropped by this ingest. Problematic rows are preserved and flagged so downstream model-data builders can decide whether to filter, mask, or learn robustly from them.

## Query Examples

Quote chart query:

```sql
SELECT
    ticker,
    fromUnixTimestamp64Micro(toInt64(sip_timestamp_us)) AS sip_time,
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
FROM market_sip_compact.quotes_canonical
WHERE ticker = 'AAPL'
  AND event_date BETWEEN toDate('2025-01-01') AND toDate('2025-01-31')
ORDER BY sip_timestamp_us, sequence_number
LIMIT 100000
```

Trade chart query:

```sql
SELECT
    ticker,
    fromUnixTimestamp64Micro(toInt64(sip_timestamp_us)) AS sip_time,
    sip_timestamp_us,
    sequence_number,
    if(bitAnd(trade_flags, 1) = 1, price_int / 10000.0, price_int / 100.0) AS price,
    size,
    exchange,
    bitAnd(bitShiftRight(trade_flags, 1), 3) + 1 AS tape,
    bitAnd(bitShiftRight(trade_flags, 3), 15) AS correction,
    conditions,
    issue_flags
FROM market_sip_compact.trades_canonical
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
FROM market_sip_compact.compact_ingest_manifest
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
    FROM market_sip_compact.compact_ingest_manifest
)
WHERE rn = 1
ORDER BY kind, source_date, source_file
```

## Validation

After ingesting a test chunk, validate random raw rows against ClickHouse decoded rows:

```powershell
python D:\TradingML\codes\masked_event_model\v4\research\mlops\clickhouse_compact_schema_validate_sample.py --database market_sip_compact --quote-table quotes_canonical --trade-table trades_canonical --quote-date 2026-05-15 --trade-date 2026-05-15 --sample-size 1000
```

Expected result for a valid schema is:

- quote mismatches: `0`
- trade mismatches: `0`
- missing rows: `0`
- duplicate key rows: `0`


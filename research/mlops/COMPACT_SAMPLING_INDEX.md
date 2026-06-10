# Compact SIP Sampling Index

The compact sampling index is a small ClickHouse table used by training
providers to sample ticker streams without event-volume bias.

## Default Split Tables

```text
market_sip_compact.train_2019_to_2025
market_sip_compact.validation_2026
```

Each table contains one row per eligible ticker:

```text
ticker
event_count
first_sip_timestamp_us
last_sip_timestamp_us
min_valid_ordinal
max_valid_ordinal
```

The default split ranges are:

```text
train:      2019-01-01 -> 2025-12-31
validation: 2026-01-01 -> 2099-12-31
```

The validation end date is intentionally open-ended so newly inserted 2026+
data is included when the index is rebuilt.

## Sampling Contract

The training data provider should:

1. Select a ticker uniformly from the chosen index table.
2. Sample one `origin_ordinal` uniformly between:

   ```text
   min_valid_ordinal <= origin_ordinal <= max_valid_ordinal
   ```

3. Resolve that ordinal to an origin event in the ticker-local unified
   quote/trade stream.
4. Query the last `events_per_chunk` events ending at that origin, inclusive.
5. Encode those events into the fixed v4 byte representation:

   ```text
   header_uint8: [B, 14]
   events_uint8: [B, 128, 16]
   ```

The index does not materialize origin rows and does not materialize overlapping
chunks.

## Clean Modes

The index builder supports:

```text
structural
issue_flags_zero
```

`structural` is the default. It excludes only impossible key rows:

```text
ticker != ''
sip_timestamp_us > 0
sequence_number > 0
```

`issue_flags_zero` additionally requires:

```text
issue_flags = 0
```

The data provider must use the same clean predicate as the index. If the clean
policy changes, rebuild the index.

## Build

Dry-run:

```powershell
python -m research.mlops.run_build_compact_sampling_index --dry-run
```

Build or refresh both default tables:

```powershell
python -m research.mlops.run_build_compact_sampling_index
```

Drop and recreate both tables before filling:

```powershell
python -m research.mlops.run_build_compact_sampling_index --rebuild
```

The builder writes a JSONL report under:

```text
D:/market-data/prepared/clickhouse_sip_ingest/compact_sampling_index
```

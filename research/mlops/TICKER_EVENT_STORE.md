# Ticker Compact Event Store

This pipeline builds reusable compact quote/trade event rows for masked event models without materializing overlapping chunks.

## Layout

```text
prepared/us_stocks_sip/ticker_compact_events_v1/
  events/
    year_month=2025-01/
      fragment_bucket=0000/
        bucket_id=0/*.parquet
        bucket_id=128/*.parquet
  _index/
    availability.parquet
    schema.json
    parts/year_month=2025-01/fragment_bucket=0000/bucket=0524_00000000.parquet
  _state/
    derive/*.SUCCESS.json
    compact/*.SUCCESS.json
    index/*.SUCCESS.json
  _tmp_fragments/
```

Final event files are sorted by:

```text
ticker, sip_timestamp, sequence_number, event_type
```

## Row Schema

Each row is one quote or trade event. Values are compact and model-ready, but not anchored to a context window.

```text
ticker
session_date
year_month
sip_timestamp
sequence_number
event_type              # quote=0, trade=1
price_main_1e4          # quote ask or trade price, scaled by 10000
price_aux_1e4           # quote spread, scaled by 10000; trade=0
size_1_bucket           # quote bid size bucket or trade size bucket
size_2_bucket           # quote ask size bucket; trade=0
small_size_1
small_size_2
exchange_1_id           # quote bid exchange or trade exchange
exchange_2_id           # quote ask exchange; trade=0
tape_id
condition_1_id
condition_2_id
condition_3_id
condition_4_id
correction_code
bucket_id
fragment_bucket_id
```

## Stages

`derive` reads raw daily CSV files, validates/filter invalid rows through the existing canonical rules, computes compact columns, and writes temporary fragments partitioned by month and coarse fragment bucket. Fragment buckets keep each worker from opening 1024 partition writers at once.

`compact` reads fragments for each `(year_month, fragment_bucket_id)`, sorts them by bucket/ticker/time, and writes final bucket partitions atomically. `fragment_bucket_id` is only a coarse compaction partition used to keep memory and writer fanout bounded; `bucket_id` remains the stable ticker hash bucket used by loaders.

`index` builds per-ticker availability metadata used by loaders for sampling.

## Commands

Build all stages:

```powershell
python -m research.mlops.run_build_ticker_event_store --stage all --rebuild
```

Run only compaction after fragments exist:

```powershell
python -m research.mlops.run_build_ticker_event_store --stage compact
```

Run only index after final event files exist:

```powershell
python -m research.mlops.run_build_ticker_event_store --stage index
```

The builder is restart-safe through `_state/*.SUCCESS.json` fingerprints. Re-running skips completed compatible work unless `--rebuild` is supplied.

# Compact Event Sample Cache

This cache decouples ClickHouse sampling from GPU training. ClickHouse is used to
materialize immutable sample shards on SSD; training reads only local shard files.

## Record Format

Each stored sample is one fixed-width byte record:

```text
sample_bytes = 2062
header      = 14 bytes
events      = 128 events * 16 bytes = 2048 bytes
```

The on-disk record is:

```text
[header_uint8(14)][events_uint8(128,16)]
```

The cache is not tied to training batch size. A shard is a flat stream of samples,
and the training loader slices that stream into whatever batch size is requested.

## Shards

Default shard size is 16 GiB. With 2062 bytes per sample, that is roughly:

```text
16 GiB / 2062 ~= 8.33M samples
```

Each shard has:

```text
train/shard_000000.samples.bin
train/shard_000000.samples.json
```

The metadata JSON stores sample count, byte size, format version, and SHA-256.

## Cache Layout

```text
D:\market-data\prepared\event_sample_cache\cache_YYYYMMDD_HHMMSS\
  manifest.json
  train\
    shard_000000.samples.bin
    shard_000000.samples.json
  validation\
    shard_000000.samples.bin
    shard_000000.samples.json
  train_audit_samples.jsonl
  validation_audit_samples.jsonl
```

The audit JSONL stores only a small sample of origins:

```text
split, shard_index, sample_index_in_shard, ticker, origin_ordinal, origin_timestamp_ns
```

This keeps the cache compact while still allowing exact byte-level validation
against ClickHouse for sampled records.

## Build

Use the Python launcher:

```powershell
python D:\TradingML\codes\masked_event_model\v4\research\mlops\run_build_event_sample_cache.py
```

The default first build is intentionally modest:

```text
train_cache_gib=128
validation_cache_gib=4
shard_size_gib=16
builder_micro_batch_samples=65536
origins_per_span=512
workers=8
```

Scale with overrides after the path is validated:

```powershell
python D:\TradingML\codes\masked_event_model\v4\research\mlops\run_build_event_sample_cache.py --train-cache-gib 4096 --validation-cache-gib 32 --workers 16
```

The builder still queries ClickHouse in efficient span bundles. The
`builder_micro_batch_samples` parameter controls query bundle output size, not
training batch size.

The high-throughput default intentionally creates many adjacent windows per
sampled span:

```text
builder_micro_batch_samples = 65,536
origins_per_span = 512
random origin_stride = 1..16
```

This keeps the cache format unchanged while greatly reducing ClickHouse query
overhead per stored sample. Training still shuffles samples within shards.

## Validate

Run fast structural checks plus sampled ClickHouse audit checks:

```powershell
python D:\TradingML\codes\masked_event_model\v4\research\mlops\run_validate_event_sample_cache.py --cache-root D:\market-data\prepared\event_sample_cache\cache_YYYYMMDD_HHMMSS
```

Add `--verify-sha256` only when needed; it rereads full shards and is slower.

Validation checks:

- shard sizes match metadata
- sampled records decode into valid header/event tensors
- sampled event presence/header flags are sane
- audit records can be re-queried from ClickHouse and byte-compared

## Train

v4 training supports:

```text
--data-source sample_cache
--sample-cache-root <cache folder>
```

Example:

```powershell
python D:\TradingML\codes\masked_event_model\v4\run_train.py --sample-cache-root D:\market-data\prepared\event_sample_cache\cache_YYYYMMDD_HHMMSS --epochs 2 --batch-size 4096
```

Changing `--batch-size` does not require rebuilding the cache.

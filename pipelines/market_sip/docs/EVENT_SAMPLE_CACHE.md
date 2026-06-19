# Compact Event Sample Cache

This cache decouples ClickHouse sampling from GPU training. ClickHouse is used to
materialize immutable sample shards on SSD; training reads only local shard files.

## v1 Record Format

Each v1 stored sample is one fixed-width byte record:

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

## v2 Labeled Record Format

v2 keeps the same `x` record but adds a paired `y` record containing future
chunks. The default label horizon is eight 128-event chunks:

```text
x_sample_bytes = 2062
y_sample_bytes = 8 * 2062 = 16,496
sample_bytes_on_disk = 18,558
```

The two files are written separately but share the same row order:

```text
train/shard_000000.x.bin
train/shard_000000.y.bin
train/shard_000000.json
```

Row `i` in `.x.bin` and row `i` in `.y.bin` are always the paired sample. The
metadata stores both byte sizes, both SHA-256 digests, and `label_chunks`.

For each origin ordinal `t`:

```text
x = events[t-127 : t] inclusive, encoded as one header + 128 events
y = events[t+1 : t+1024] split into eight 128-event encoded chunks
```

Sampling bounds reserve the full future horizon inside the selected split. A
training sample close to the train/validation boundary is rejected unless all
1024 future label events still belong to the training index range. The same
rule applies to validation.

## Shards

Default shard size is 16 GiB. For v1, with 2062 bytes per sample, that is roughly:

```text
16 GiB / 2062 ~= 8.33M samples
```

Each shard has:

```text
train/shard_000000.samples.bin
train/shard_000000.samples.json
```

For v2, the same 16 GiB target produces fewer samples because every sample also
stores the eight future chunks:

```text
16 GiB / 18,558 ~= 925k paired samples
```

The metadata JSON stores sample count, byte size, format version, and SHA-256.

## Cache Layout

```text
D:\market-data\prepared\event_sample_cache\cache_YYYYMMDD_HHMMSS\
  manifest.json
  train\
    shard_000000.samples.bin
    shard_000000.samples.json
    shard_000000.x.bin
    shard_000000.y.bin
    shard_000000.json
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
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\sample_cache\run_build_event_sample_cache.py
```

For labeled v2 caches, use:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\sample_cache\run_build_event_sample_cache_v2.py
```

To run the full v2 cycle in one command, use:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\sample_cache\run_event_sample_cache_v2_cycle.py
```

That command builds the v2 cache, validates finalized shards, and then audits
sampled records against the raw compact `quotes` and `trades` tables.

Before a large run, execute a small end-to-end smoke cycle:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\sample_cache\run_event_sample_cache_v2_cycle.py --smoke
```

Smoke mode uses tiny cache targets:

```text
train_cache_gib=0.05
validation_cache_gib=0.02
shard_size_gib=0.05
workers=2
builder_micro_batch_samples=4096
origins_per_span=64
validation_clickhouse_checks=5
raw_audit_checks=5
```

The v2 launcher passes:

```text
cache_version=2
label_chunks=8
train_cache_gib=2720
validation_cache_gib=64
```

Those v2 defaults reserve at most about 3 decimal TB on SSD because the builder
arguments are in GiB:

```text
2720 GiB + 64 GiB = 2784 GiB ~= 2.99 TB
```

The v1 launcher remains intentionally modest:

```text
train_cache_gib=128
validation_cache_gib=4
shard_size_gib=16
builder_micro_batch_samples=65536
origins_per_span=512
workers=8
```

Scale v1 with overrides after the path is validated:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\sample_cache\run_build_event_sample_cache.py --train-cache-gib 4096 --validation-cache-gib 32 --workers 16
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
overhead per stored sample. Training reads shards in shard-index order, shuffles
the full loaded shard once in memory, and then forms mini-batches by contiguous
slices from that shuffled array. The final incomplete mini-batch in each shard is
dropped by default so every optimizer step sees the configured batch size.

Progress logs include both total and rolling-rate ETA:

```text
rate_recent=.../s eta_recent_hours=...
rate_total=.../s  eta_total_hours=...
```

The rolling ETA uses the last `eta_recent_window` completed microbatches and is
usually more useful after warm-up.

If no microbatch completes for `heartbeat_seconds`, the builder prints a
heartbeat line and writes progress JSON with the current pending-worker count.

## Validate

Run fast structural checks plus sampled ClickHouse audit checks:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\sample_cache\run_validate_event_sample_cache.py --cache-root D:\market-data\prepared\event_sample_cache\cache_YYYYMMDD_HHMMSS
```

Add `--verify-sha256` only when needed; it rereads full shards and is slower.

To validate finalized shards while the builder is still running, skip audit
checks because audit samples are written when the split closes:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\sample_cache\run_validate_event_sample_cache.py --allow-partial --splits train --audit-clickhouse-checks 0
```

Validation checks:

- shard sizes match metadata
- sampled records decode into valid header/event tensors
- for v2, sampled label chunks decode into valid header/event tensors
- sampled event presence/header flags are sane
- audit records can be re-queried from ClickHouse and byte-compared
- for v2, both `x` and `y` are byte-compared against ClickHouse re-encoding

## Raw Source Audit

The normal validator byte-compares audit samples against the `events` table. To
also verify that the final sample bytes trace back to the compact raw
`quotes`/`trades` tables, run:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\sample_cache\run_audit_event_sample_cache_against_raw.py --cache-root D:\market-data\prepared\event_sample_cache\cache_YYYYMMDD_HHMMSS --checks 25
```

This audit uses the `*_audit_samples.jsonl` metadata, so it can trace only the
sampled audit rows in existing caches. For each audited sample it:

- reads the stored sample bytes from the shard
- fetches the same ticker/ordinal window from `market_sip_compact.events`
- fetches the underlying compact `quotes` and `trades` rows for the same ticker
  and timestamp window
- rebuilds the unified event rows using the same quote/trade mapping and
  condition references
- re-encodes those raw rows into `[header_uint8, events_uint8]`
- byte-compares raw re-encoding, events-table re-encoding, and the stored shard
  record

Add `--write-decoded-jsonl` when you want a human-readable approximate decode
of the audited sample. Price fields are reconstructed from anchors and deltas;
size and time fields remain bucketed because that compression is intentionally
lossy.

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

Useful loader flags:

```text
--sample-cache-shuffle-records / --no-sample-cache-shuffle-records
--sample-cache-drop-last / --no-sample-cache-drop-last
```

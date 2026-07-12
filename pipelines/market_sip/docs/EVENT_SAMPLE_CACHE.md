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

v2 keeps the same `x` record, adds a paired `y` record containing the first
future chunks, and writes a row-aligned labels sidecar. Naming is strict:

- fields with `future` in the name are label-only and must never be used as
  model features
- fields without `future` must be computable as of the origin event and may be
  used as features

The default fetched span is:

```text
past_span   = 2048 events ending at origin t
future_span = 2048 events after origin t
```

The stored `y` bytes contain only the first two future 128-event chunks:

```text
x_sample_bytes = 2062
y_sample_bytes = 2 * 2062 = 4,124
binary_sample_bytes_on_disk = 6,186
```

The two files are written separately but share the same row order:

```text
train/shard_000000.x.bin
train/shard_000000.y.bin
train/shard_000000.labels.parquet
train/shard_000000.json
```

Row `i` in `.x.bin`, row `i` in `.y.bin`, and row `i` in
`.labels.parquet` are always the paired sample. The metadata stores both byte
sizes, both SHA-256 digests, `label_chunks`, and the label sidecar path. For
v2, `label_chunks` means stored future `y` chunks, not the full fetched future
span.

For each origin ordinal `t`:

```text
x = events[t-127 : t] inclusive, encoded as one header + 128 events
y = events[t+1 : t+256] split into two 128-event encoded chunks
labels = scalar columns derived from events[t-2047 : t+2048]
```

Sampling bounds reserve the full future horizon inside the selected split. A
training sample close to the train/validation boundary is rejected unless all
2048 future label events still belong to the training index range. The same
rule applies to validation.

The stored `x` is still only the final 128 events of the past span. The
additional past span exists so the builder can derive short-term as-of-origin
labels or features without lookahead. The future span beyond the first two
stored `y` chunks is not stored as chunk bytes; it is only used to compute
`future_*` label columns. Labels that need bars, indicators, market structure,
or other timeframes should be built in separate offline tables keyed by
ticker/timestamp and joined later; the event cache itself only uses labels that
can be derived from the fetched event span.

The labels sidecar currently includes:

```text
ticker
origin_ordinal
origin_timestamp_us

asof_* quote/trade state from the current 128-event x chunk, falling back to
the immediately previous 128-event chunk when the current chunk has no matching
quote/trade event
past_2048_* counts and elapsed time

future_H_* labels for H in 128, 256, 512, 1024, 2048
```

For future high/low labels, elapsed time is exact:

```text
future_H_high_ask_elapsed_us =
  sip_timestamp_us(first event with max ask in events[t+1:t+H]) - origin_timestamp_us

future_H_low_bid_elapsed_us =
  sip_timestamp_us(first event with min bid in events[t+1:t+H]) - origin_timestamp_us
```

The same first-occurrence tie rule is used for `future_H_max_trade_*` and
`future_H_min_trade_*`.

### v2 Stored Columns

The binary shard files are not columnar. They store fixed-width byte records,
and the logical columns are decoded from fixed offsets.

Each row of `shard_*.x.bin` is:

```text
x_header_uint8: uint8[14]
x_events_uint8: uint8[128, 16]
```

The physical byte order is:

```text
bytes 0..13      = x_header_uint8
bytes 14..2061   = x_events_uint8 flattened row-major as event[0..127][byte0..15]
```

`x_header_uint8` is the compact event-chunk header for `events[t-127:t]`.
`x_events_uint8` contains the 128 compact event rows ending at the origin
event. Each event row uses the shared 16-byte compact event representation:

```text
event_byte_00     = event type / presence flags
event_byte_01..02 = intra-window time delta bucket
event_byte_03..04 = primary price delta, ask delta, or trade delta
event_byte_05..06 = secondary price delta or spread delta
event_byte_07     = primary size bucket
event_byte_08     = secondary size bucket
event_byte_09     = small-size / tape flags
event_byte_10     = primary exchange dense id
event_byte_11     = secondary exchange dense id
event_byte_12..15 = packed condition ids
```

The exact byte-level encoder is `encode_unified_event_window` in
`research/mlops/clickhouse_events.py`. The compact representation reference is
`research/market_references/compact_market_microstructure_representation.md`.

Each row of `shard_*.y.bin` is label-only future data:

```text
y_future_chunk_0_header_uint8: uint8[14]
y_future_chunk_0_events_uint8: uint8[128, 16]
y_future_chunk_1_header_uint8: uint8[14]
y_future_chunk_1_events_uint8: uint8[128, 16]
```

The physical byte order is:

```text
chunk 0 = events[t+1:t+128]   encoded as header(14) + events(128 * 16)
chunk 1 = events[t+129:t+256] encoded as header(14) + events(128 * 16)
```

`y` is future label data. It must not be used as a model feature.

Each row of `shard_*.labels.parquet` is row-aligned with the same `x` and `y`
row. The sidecar currently has 130 columns:

```text
ticker
origin_ordinal
origin_timestamp_us

past_2048_event_count
past_2048_quote_count
past_2048_trade_count
past_2048_quote_count_ratio
past_2048_trade_count_ratio
past_2048_elapsed_us

asof_has_quote
asof_ask_price_int
asof_ask_price_scale
asof_ask_size
asof_bid_price_int
asof_bid_price_scale
asof_bid_size

asof_last_trade_has_trade
asof_last_trade_price_int
asof_last_trade_price_scale
asof_last_trade_size

future_H_elapsed_us
future_H_quote_count
future_H_trade_count
future_H_has_quote
future_H_ask_price_int
future_H_ask_price_scale
future_H_ask_size
future_H_bid_price_int
future_H_bid_price_scale
future_H_bid_size
future_H_high_ask_price_int
future_H_high_ask_price_scale
future_H_high_ask_elapsed_us
future_H_low_bid_price_int
future_H_low_bid_price_scale
future_H_low_bid_elapsed_us
future_H_max_trade_price_int
future_H_max_trade_price_scale
future_H_max_trade_elapsed_us
future_H_min_trade_price_int
future_H_min_trade_price_scale
future_H_min_trade_elapsed_us
```

`future_H_*` is expanded for `H in {128, 256, 512, 1024, 2048}`. These columns
are labels only. For quote-state labels such as `future_H_ask_*`,
`future_H_bid_*`, `future_H_high_ask_*`, and `future_H_low_bid_*`, the label
uses the latest quote in the 128-event chunk ending at horizon `H`; if that
chunk has no quote, it falls back to the immediately previous 128-event chunk.
The `asof_*` and `past_2048_*` columns are known at the origin event and may be
used as features if a later model needs scalar context.

Price labels use the compact integer/scale convention:

```text
scale = 0 -> price = price_int / 100
scale = 1 -> price = price_int / 10000
```

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

For v2, the same 16 GiB binary shard target produces fewer samples because
every sample also stores two future chunks. The labels sidecar is an additional
compressed parquet file:

```text
16 GiB / 6,186 ~= 2.78M paired samples before label sidecar overhead
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
    shard_000000.labels.parquet
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

For x-only reconstruction pretraining, use the dedicated pretraining launcher:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\sample_cache\run_build_event_sample_cache_pretrain.py
```

To build, validate, and raw-audit an x-only pretraining cache in one command,
use:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\sample_cache\run_event_sample_cache_pretrain_cycle.py
```

Before a large x-only run, execute a small end-to-end smoke cycle:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\sample_cache\run_event_sample_cache_pretrain_cycle.py --smoke
```

The pretraining launcher writes v1-compatible shards only:

```text
train/shard_000000.samples.bin
train/shard_000000.samples.json
validation/shard_000000.samples.bin
validation/shard_000000.samples.json
```

It does not write `y.bin` or `labels.parquet`. It reuses the current bundled
ClickHouse sampler/writer path with lighter x-only defaults:

```text
cache_version=1
past_span_events=128
future_span_events=0
train_cache_gib=4096
validation_cache_gib=64
shard_size_gib=16
workers=8
pending_multiplier=1
builder_micro_batch_samples=65536
origins_per_span=512
query_bundle_spans=64
```

The direct generic v1 launcher remains available:

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
pending_multiplier=1
builder_micro_batch_samples=4096
origins_per_span=64
query_bundle_spans=8
validation_clickhouse_checks=5
raw_audit_checks=5
```

The v2 launcher passes:

```text
cache_version=2
label_chunks=2
past_span_events=2048
future_span_events=2048
train_cache_gib=2720
validation_cache_gib=64
shard_size_gib=16
workers=8
pending_multiplier=1
builder_micro_batch_samples=8192
origins_per_span=128
query_bundle_spans=16
```

Those v2 defaults reserve at most about 3 decimal TB on SSD because the builder
arguments are in GiB:

```text
2720 GiB + 64 GiB = 2784 GiB ~= 2.99 TB
```

The generic v1 launcher remains intentionally modest:

```text
train_cache_gib=128
validation_cache_gib=4
shard_size_gib=16
builder_micro_batch_samples=65536
origins_per_span=512
workers=8
```

Scale generic v1 with overrides after the path is validated:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\market_sip\sample_cache\run_build_event_sample_cache.py --train-cache-gib 4096 --validation-cache-gib 32 --workers 16
```

The builder still queries ClickHouse in efficient span bundles. The
`builder_micro_batch_samples` parameter controls query bundle output size, not
training batch size.

The v1 high-throughput default intentionally creates many adjacent windows per
sampled span:

```text
builder_micro_batch_samples = 65,536
origins_per_span = 512
random origin_stride = 1..16
```

For v2, one stored sample includes the current `x` chunk, two future `y` chunks,
and a compressed labels sidecar row. The v2 launchers use smaller microbatches
and fewer spans per ClickHouse query because each query fetches the full
2048-event past span and 2048-event future span for label extraction. This
keeps progress observable.

Training reads shards in shard-index order, shuffles the full loaded shard once
in memory, and then forms mini-batches by contiguous slices from that shuffled
array. The final incomplete mini-batch in each shard is dropped by default so
every optimizer step sees the configured batch size.

Progress logs include both total and rolling-rate ETA:

```text
rate_recent=.../s eta_recent_hours=...
rate_total=.../s  eta_total_hours=...
```

The rolling ETA uses the last `eta_recent_window` completed microbatches and is
usually more useful after warm-up.

If no microbatch completes for `heartbeat_seconds`, the builder prints a
heartbeat line and writes progress JSON with the current pending-worker count.
The heartbeat also reports the oldest pending job age, which is the first thing
to check if ClickHouse is slow or saturated.

To stop a running v2 build or full cycle, press `Ctrl+C` in the terminal that
started the Python launcher. The launcher forwards the interrupt to the active
subprocess and kills it if it does not stop within the grace period. A direct
builder run also exits immediately on `Ctrl+C` after closing the current shard
writer; already finalized shards remain on disk.

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
python D:\TradingML\codes\quant_research_workbench_pipelines\run_train.py --sample-cache-root D:\market-data\prepared\event_sample_cache\cache_YYYYMMDD_HHMMSS --epochs 2 --batch-size 4096
```

Changing `--batch-size` does not require rebuilding the cache.

Useful loader flags:

```text
--sample-cache-shuffle-records / --no-sample-cache-shuffle-records
--sample-cache-drop-last / --no-sample-cache-drop-last
```

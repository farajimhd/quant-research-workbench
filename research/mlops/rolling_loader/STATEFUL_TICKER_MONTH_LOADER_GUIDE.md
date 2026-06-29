# Stateful Ticker/Month Rolling Loader Guide

This guide describes the SSD-backed rolling loader used after building the
ticker/month cache. The builder performs ClickHouse extraction and writes
reusable ticker/month packages. The loader reads those packages, materializes
event context and labels on CPU, and yields trainer batches.

## Core Contract

The loader is stateful and package-local:

1. Discover complete `(month, ticker, part)` packages from the cache manifest.
2. Build a deterministic epoch package order.
3. Load a bounded group of packages from SSD.
4. Build refs for every eligible origin in the loaded group.
5. Optionally shuffle origin refs inside the group.
6. Materialize refs in bounded CPU chunks.
7. Concatenate materialized chunks into a ready buffer.
8. Emit final trainer batches from the ready buffer.
9. Do not advance to the next group until all eligible origins in the current
   loaded group have been emitted or an explicit cap stops the epoch.

Shuffling changes ordering only. It does not drop origins. Origins are dropped
only by explicit filters such as period, ticker, hash split, sample fraction, or
`max_origins_per_epoch`.

## Dataset Plan

A dataset plan defines membership: which origins belong to a training or
validation run. The same dataset plan should be reused across model versions
when comparing experiments.

Plan identity comes from:

```text
dataset_id
cache manifest fingerprint
split
months/start/end/tickers
sample_fraction
sample_hash_modulus
sample_hash_buckets
max_origins_per_epoch
seed
```

If `dataset_id` is empty, the loader creates an automatic plan id from the cache
and selection config. For long-running experiments, set an explicit id:

```powershell
--dataset-id bench_small_201902_v1
```

## Deterministic And Replayable Randomness

Default behavior is deterministic:

```text
same cache + same config + same seed => same package order, same origin order,
same batches
```

For non-deterministic exploration, use:

```powershell
--randomize-seed --save-state-path D:\runs\loader_state.json
```

The loader creates a random seed once and records it in state. The run is random
at creation time, but repeatable later by loading the saved state:

```powershell
--load-state-path D:\runs\loader_state.json
```

## Train/Validation Splits

Use hash buckets to create stable, non-overlapping sets without saving billions
of origin ids.

Example: 95/5 split by origin identity:

```powershell
# train
--sample-hash-modulus 100 --sample-hash-buckets 5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48,49,50,51,52,53,54,55,56,57,58,59,60,61,62,63,64,65,66,67,68,69,70,71,72,73,74,75,76,77,78,79,80,81,82,83,84,85,86,87,88,89,90,91,92,93,94,95,96,97,98,99

# validation
--sample-hash-modulus 100 --sample-hash-buckets 0,1,2,3,4
```

For small benchmark sets, combine an explicit `dataset_id`, hash split, and cap:

```powershell
--dataset-id bench_small_201902_v1 `
--sample-hash-modulus 100 --sample-hash-buckets 0,1,2,3,4 `
--max-origins-per-epoch 1000000
```

## Materialization Strategy

The loader separates materialization chunk size from trainer batch size.

```text
materialize_chunk_size
  number of origins per CPU materialization task

batch_size
  final trainer batch size emitted from the ready buffer
```

If `materialize_chunk_size=0`, it uses `batch_size`.

For liquid tickers, use smaller materialization chunks to limit temporary memory:

```powershell
--batch-size 4096 --materialize-chunk-size 512
```

The ready buffer concatenates chunk outputs and emits trainer batches. This keeps
the loaded package exhausted without requiring a giant materialized tensor for
all origins at once.

## State And Checkpointing

The loader exposes:

```python
state = loader.state_dict()
loader.load_state_dict(state)
summary = loader.summary()
```

State includes:

```text
loader_state_version
dataset_plan_id
cache_manifest_fingerprint
seed
epoch
package_position
origin_cursor
emitted_batches
emitted_samples
seen_origins_this_epoch
seen_origins_total
completed_epochs
total_available_origins
planned_origins
package_count
seen_by_month
seen_by_part
```

Training should save loader state beside the model and optimizer checkpoint.
Resume with the same cache/config and `load_state_dict`.

The loader validates that loaded state matches the current dataset plan and
cache manifest fingerprint. A mismatch fails fast.

## Ordered Materialization

By default, materialization workers run concurrently but batches are emitted in
submission order. This preserves repeatability across machines and runs.

For pure throughput profiling where repeatability is not required:

```powershell
--allow-unordered-materialization
```

This may emit worker results in completion order and therefore should not be
used for fair benchmark comparisons.

## Event Outputs

Default event output is raw windows:

```text
raw_event_windows[column] -> [B, context_chunks, events_per_window]
```

By default, raw outputs suppress identity/debug columns:

```text
ticker_id
ordinal
timestamp_us
```

Use an exact allow-list when a trainer needs specific columns:

```powershell
--event-columns event_type,price_primary_int,price_secondary_int,size_primary,size_secondary,exchange_primary,exchange_secondary,event_flags,conditions_packed,session_second
```

If `event_columns` is set, the suppress list is ignored.

## Common Commands

Profile a repeatable small benchmark:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\research\mlops\rolling_loader\run_profile_ticker_month_loader.py `
  --cache-id train_201902_201907_ticker_month `
  --month 2019-02 `
  --dataset-id bench_small_201902_v1 `
  --sample-fraction 0.001 `
  --max-origins-per-epoch 1000000 `
  --batch-size 4096 `
  --materialize-chunk-size 512 `
  --batches 16 `
  --save-state-path D:\market-data\prepared\data_provider_profiles\bench_small_loader_state.json
```

Replay from a saved state:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\research\mlops\rolling_loader\run_profile_ticker_month_loader.py `
  --cache-id train_201902_201907_ticker_month `
  --month 2019-02 `
  --dataset-id bench_small_201902_v1 `
  --batch-size 4096 `
  --materialize-chunk-size 512 `
  --load-state-path D:\market-data\prepared\data_provider_profiles\bench_small_loader_state.json
```

Profile event-only pretraining:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\research\mlops\rolling_loader\run_profile_ticker_month_loader.py `
  --cache-id train_201902_201907_ticker_month `
  --month 2019-02 `
  --data-groups events `
  --event-output-mode raw_windows `
  --batch-size 4096
```

## Invariants

- A loaded package group is exhausted before advancing unless an explicit cap
  stops the epoch.
- Ordered emission is the default even with concurrent materialization workers.
- Event windows end at or before the origin.
- Event windows are checked for ordinal continuity.
- State checkpoints include both membership identity and current cursor.
- Randomized runs are replayable only if the generated state is saved.

# Stateful Ticker/Month Rolling Loader Guide

This guide describes the SSD-backed rolling loader used after building the
ticker/month cache. The builder performs ClickHouse extraction and writes
reusable ticker/month packages. The loader reads those packages, materializes
event context and labels on CPU, and yields trainer batches.

## Core Contract

The loader is stateful and package-local:

1. Discover complete `(month, ticker, part)` packages from the cache manifest.
2. Build a deterministic epoch package order.
3. Read only the origin index for a bounded group of packages.
4. Build refs for every eligible origin in the loaded group.
5. Apply period/ticker/hash/sample filters before reading large payload files.
6. Load event, label, and context payloads only for packages with selected refs.
7. Optionally shuffle origin refs inside the group. In `raw_stream` mode, keep
   each package's selected origins in ordinal order so a loaded part is consumed
   as a continuous sliding stream.
8. Materialize refs in bounded CPU chunks.
9. Concatenate materialized chunks into a ready buffer.
10. Emit final trainer batches from the ready buffer.
11. Do not advance to the next group until all eligible origins in the current
   loaded group have been emitted or an explicit cap stops the epoch.

The ready buffer carries partial leftovers across loaded groups, so loaded-group
boundaries do not create partial trainer batches. Shuffling changes ordering
only. It does not drop origins. Origins are dropped only by explicit filters
such as period, ticker, hash split, sample fraction, or `max_origins_per_epoch`.
For sparse benchmark sets, this origin-first path avoids reading large event
parquet files for packages that contribute no selected origins.

Not every event row is a valid training origin. The origin index remains the
source of truth for samples. Event rows are payload rows used to build the
requested context for each origin.

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

When only `sample_fraction` is set and no hash modulus/buckets are requested,
the loader uses a deterministic vectorized NumPy mask seeded by
`dataset_id + seed + month + ticker + part_id`. This is repeatable and much
faster than per-origin Python hashing. Use hash buckets when exact
non-overlapping train/validation membership is required.

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

State is advanced before each batch is yielded. This matters for training
checkpointing: if the trainer saves loader state immediately after receiving a
batch, resume starts after that batch. When the yielded batch exhausts the
current loaded group, `origin_cursor` can temporarily equal the selected-origin
count for that group. On resume, the loader slices to empty, advances
`package_position`, and continues without repeating the exhausted group.

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

Default event output is raw sliding streams:

```text
raw_event_stream -> [B, event_stream_length, F]
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

## Text Embedding Outputs

When `data_groups` includes embedding groups, the loader emits text tensors in
`batch.text_inputs`:

```text
ticker_news_embeddings  -> text_inputs["ticker_news"]
market_news_embeddings  -> text_inputs["market_news"]
sec_filing_embeddings   -> text_inputs["sec_filings"]
```

Each text input contains:

```text
embeddings         [B, max_items, token_chunks, text_embedding_dim]
chunk_mask         [B, max_items, token_chunks]
item_mask          [B, max_items]
item_timestamp_us  [B, max_items]
```

The current defaults are:

```text
ticker_news_max_items: 8
market_news_max_items: 16
sec_filing_max_items: 4
ticker_news_token_chunks: 2
market_news_token_chunks: 2
sec_filing_token_chunks: 8
text_embedding_dim: 1024
```

For each origin, embedding selection is as-of:

```text
embedding.timestamp_us <= origin_timestamp_us
```

The loader keeps the latest items, places each embedding row by
`token_chunk_index`, and leaves unavailable items/chunks zero-filled with false
masks. Future text rows are not eligible.

For each selected origin, the loader uses the origin row's `event_row_offset`
to gather the continuous event stream ending at that origin:

```text
start = event_row_offset - event_stream_length + 1
end = event_row_offset + 1
raw_event_stream[row] = events[start:end, selected_columns]
```

The loader checks:

```text
events[event_row_offset].ordinal == origin_ordinal
events[end - 1].ordinal - events[start].ordinal == event_stream_length - 1
```

## XBRL Outputs

When `data_groups` includes `xbrl`, the loader emits `batch.xbrl_inputs` as
model-facing tensors instead of leaving XBRL only in `external_context`.

```text
xbrl_inputs["mask"]                         [B, xbrl_max_items]
xbrl_inputs["value"]                        [B, xbrl_max_items]
xbrl_inputs["fiscal_year"]                  [B, xbrl_max_items]
xbrl_inputs["age_days"]                     [B, xbrl_max_items]
xbrl_inputs["period_end_days"]              [B, xbrl_max_items]
xbrl_inputs["taxonomy_id"]                  [B, xbrl_max_items]
xbrl_inputs["tag_id"]                       [B, xbrl_max_items]
xbrl_inputs["unit_id"]                      [B, xbrl_max_items]
xbrl_inputs["form_id"]                      [B, xbrl_max_items]
xbrl_inputs["row_kind_id"]                  [B, xbrl_max_items]
xbrl_inputs["location_id"]                  [B, xbrl_max_items]
xbrl_inputs["mapping_confidence"]           [B, xbrl_max_items]
xbrl_inputs["time_*"]                       [B, xbrl_max_items]
```

Selection is as-of:

```text
xbrl.timestamp_us <= origin_timestamp_us
```

Rows are ordered newest-first for each origin. Missing rows are zero-filled and
masked with `xbrl_inputs["mask"] == False`. Categorical ids come from the
month-level `global/category_references.parquet`; id `0` means missing or
unknown.

These checks prevent lookahead, origin/event misalignment, and ordinal gaps.
Origins that do not have enough cached lookback are filtered before payload
loading. If a selected origin later fails an alignment or continuity check, the
run fails because the cache is inconsistent.

The older modes remain available:

```text
raw_windows
  raw_event_windows[column] -> [B, context_chunks, events_per_window]

raw_flat
  raw_event_flat[column] -> [B, coverage_events]

encoded_uint8
  headers_uint8 [B, context_chunks, 14]
  events_uint8  [B, context_chunks, 128, 16]

none
  no event tensor
```

## Common Commands

Profile with the workstation defaults:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\research\mlops\rolling_loader\run_profile_ticker_month_loader.py
```

The no-arg command uses this benchmark profile:

```text
cache_id: train_201902_201907_ticker_month
month: 2019-02
dataset_id: bench_small_201902_v1
data_groups: events,intraday_labels,daily_bars,global_daily_bars,ticker_news_embeddings,market_news_embeddings,sec_filing_embeddings,xbrl
event_output_mode: raw_stream
event_stream_length: 1024
sample_fraction: 1.0
max_origins_per_epoch: 1,000,000
batch_size: 4096
batches: 16
loaded_parts_per_group: 8
read_workers: 4
materialize_workers: 16
materialize_chunk_size: 512
report_path: D:\market-data\prepared\data_provider_profiles\ticker_month_loader_full_xy_xbrl_profile.jsonl
state_path: D:\market-data\prepared\data_provider_profiles\ticker_month_loader_full_xy_xbrl_state.json
```

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
  --report-path D:\market-data\prepared\data_provider_profiles\bench_small_loader_profile.jsonl `
  --save-state-path D:\market-data\prepared\data_provider_profiles\bench_small_loader_state.json
```

If `--report-path` is omitted, the profiler still writes a JSONL summary to:

```text
D:\market-data\prepared\data_provider_profiles\ticker_month_loader_profile.jsonl
```

Use `--no-report` only when a run should not write a timing record.

Audit emitted batches against the SSD package files:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\research\mlops\rolling_loader\audit_ticker_month_loader_batches.py `
  --cache-id train_201902_201907_ticker_month `
  --month 2019-02 `
  --batch-size 1024 `
  --batches 2 `
  --samples-per-batch 4
```

The audit checks shape consistency, duplicate identities, origin/event
alignment, raw-stream values, raw-stream ordinal continuity, intraday labels,
future-bar projection, embedding as-of selection, embedding values and masks,
deterministic first-batch replay, and resume-from-state next-batch replay.

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
  --event-output-mode raw_stream `
  --batch-size 4096
```

The profiler records block-level timings in `profile_seconds`, including loader
initialization, origin reads, sample-ref filtering, payload reads, identity
materialization, raw-stream validation, event-matrix conversion, sliding gather,
label materialization, worker wait, and ready-buffer concatenation.

## Invariants

- A loaded package group is exhausted before advancing unless an explicit cap
  stops the epoch.
- Ordered emission is the default even with concurrent materialization workers.
- Event streams/windows end at or before the origin.
- Event streams/windows are checked for ordinal continuity.
- State checkpoints include both membership identity and current cursor.
- Randomized runs are replayable only if the generated state is saved.

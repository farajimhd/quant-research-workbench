# Temporal Event Model v3

`temporal_event_model/v3` trains the multimodal temporal model described in
`TRAINER_MODEL_DESIGN_GUIDE.md` on the ticker-month rolling cache.

## Main Entry Points

- `run_train.py` is the recommended launcher. It embeds workstation defaults and
  forwards any override arguments to `train.py`.
- `run_train_feb2019_large_bs512.py` launches the full large-model February
  2019 cache run with batch size 512. It derives `max_samples` from the cache
  manifests and reserves the last cached day for validation by default.
- `train.py` contains the stateful trainer, checkpointing, W&B logging, local
  JSONL metrics, Rich progress panels, and model artifact export.
- `run_profile_training.py` profiles the real training loop on the daily-index
  cache. It runs warmup and measured batches, records loader/model/memory
  timings, records the production cache path, exports model artifacts, and
  checkpoints profiler + loader state so it can resume after interruption.
- `run_sweep_training_profile.py` runs a grid of training-profile jobs across
  model presets and batch sizes, then writes consolidated CSV/JSONL results for
  throughput, memory, loss, checkpoint, audit, and modality-coverage behavior.
- `test_smoke.py` runs a small CPU shape/loss/artifact smoke test.
- `plot_dummy_batch_shapes.ipynb` creates a dummy batch, prints nested tensor
  shapes, runs a forward pass, and prints losses.
- `plot_cache_batch_inspection.ipynb` loads one real cached batch, converts it
  through the v3 adapter, and prints all input/output shapes, availability
  masks, identities, and loss metrics.
- `plot_model_diagram.ipynb` exports the same model artifacts that a run writes
  under `artifacts/model`.
- `plot_training_profile.ipynb` reads a profiler JSONL report and plots timing,
  throughput, memory, and slow loader stages.

## Default Training Command

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\research\temporal_event_model\v3\run_train.py
```

Useful overrides:

```powershell
python research\temporal_event_model\v3\run_train.py -- --months 2019-02,2019-03,2019-04 --batch-size 512 --max-samples 10000000
```

The trainer builds an explicit day schedule from the selected cache manifests.
The schedule does not need to be calendar-contiguous:

```powershell
python research\temporal_event_model\v3\run_train.py -- --training-days 2019-02-01,2020-08-12,2023-02-05
```

If `--validation-days` is omitted, the trainer reserves validation day(s) from
the discovered training schedule using `--validation-reserve-policy` and removes
them from training. The saved validation plan is deterministic unless
`--refresh-validation-plan` is passed.

For a local shape smoke:

```powershell
python research\temporal_event_model\v3\test_smoke.py
```

For a tiny trainer smoke:

```powershell
python research\temporal_event_model\v3\train.py --dummy-data --wandb-mode disabled --progress-layout text --batch-size 2 --max-samples 2 --validation-samples 2 --validation-batches 1 --d-model 32 --event-layers 1 --event-heads 4 --fusion-layers 1 --fusion-heads 4 --output-root C:\tmp\temporal_v3_train_smoke
```

For the full February 2019 large-model run on the workstation:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\research\temporal_event_model\v3\run_train_feb2019_large_bs512.py
```

That launcher uses bounded CPU-side staging:

```text
batch_size: 512
read_workers: 4
materialize_workers: 4
loaded_parts_per_group: 2
materialize_chunk_size: 128
scanner_index_cache_entries: 4
scanner_prefetch_workers: 2
```

This is intentional. A batch size 512 v3 batch is roughly 0.5 GiB of tensor
payload before model activations, optimizer state, queued loader chunks, Polars
frames, and checkpoint copies. Loading many ticker/day packages and full
materialized chunks ahead of the trainer can exhaust system RAM even when the
final batch tensor itself would fit on the GPU.

## Training Profiler

Default workstation profile command:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\research\temporal_event_model\v3\run_profile_training.py
```

The no-arg profiler defaults to:

```text
cache_root: D:/market-data/prepared/daily_index_streaming_cache/events_daily_index_2019-02
months: 2019-02
batch_size: 128
warmup_batches: 1
measured_batches: 8
read_workers: 4
materialize_workers: 8
loaded_parts_per_group: 8
d_model: 256
AMP dtype: bf16 when CUDA is available
```

The profiler writes:

```text
training_profile.jsonl
training_profile_summary.json
run_manifest.json
config.json
checkpoints/profile_checkpoint_latest.pt
artifacts/model/*
logs/fatal_error.txt, only when an exception occurs
```

Each JSONL row includes:

- loader wait and host-to-device conversion time
- forward, loss, backward, and optimizer time
- production cache encoding time and cached-token fusion/head inference time
- CPU RSS and CUDA allocated/reserved/peak memory
- loss and fast batch metrics
- detailed loader stage timings from `DailyIndexTrainingBatch.profile`, such as
  raw stream gather, label, text, XBRL, bar, corporate-action, payload load, and
  materialization wait timings

## Sample-Based Training Cadence

Training uses `samples_seen` as the primary clock. Loss is computed every batch,
but expensive summaries, prediction metrics, validation, and periodic
checkpoints are triggered by sample thresholds rather than optimizer updates. This
keeps comparisons fair when batch size changes.

Default cadences:

| Action | Default |
| --- | ---: |
| Fast batch summary | `--fast-summary-samples 25000` |
| Train prediction/cohort metric window | `--train-metric-window-samples 250000` |
| Validation | `--validation-samples 2000000` |
| Latest checkpoint | `--checkpoint-latest-samples 250000` |
| Archive checkpoint | `--checkpoint-archive-samples 2000000` |

Older checkpoint payloads may still contain a legacy `step` field, but new v3
runs use the sample clock for scheduling, logging, and checkpoint filenames.

Detailed model timing is active on the first batch and at validation/profile
points only. It logs encoder timings for event, intraday bars, daily bars,
global bars, ticker news, market news, SEC, XBRL, corporate actions, scanner,
fusion, query heads, output heads, loss, backward, optimizer, checkpoint, and
loader/materialization stages.

The profiler also runs the production API for the first measured batches by
default:

```text
encode_modality_tokens_with_timings(batch.x)
predict_from_modality_tokens_with_timings(cached_tokens)
```

Those rows are written under `production/*`. The key timing fields are:

```text
production/cache_encode_wall_seconds
production/cached_predict_wall_seconds
production/cache_encode_samples_per_second
production/cached_predict_samples_per_second
```

This measures the deployment split where expensive modality encoders refresh
only when their source context changes and the live forecast path runs fusion
plus heads from cached modality tokens.

## Model/Batch Sweep

Default workstation sweep:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\research\temporal_event_model\v3\run_sweep_training_profile.py
```

The default sweep uses a per-model batch grid instead of one shared batch list:

```text
small:  384,512,768
medium: 256,320,384
large:  128,192,256
xlarge: 64,96,128
```

The `large` and `xlarge` presets spend most extra capacity on the event
encoder and fusion transformer. Each modality now has an optional internal
output width (`--event-d-model`, `--bar-d-model`, `--text-d-model`,
`--xbrl-d-model`, `--corporate-action-d-model`, `--scanner-d-model`) and a
small adapter into `--fusion-d-model`. A value of `0` preserves the old behavior
by falling back to `--d-model`. The sweep presets keep sparse side encoders
smaller while letting events and fusion grow.

XBRL category ids are deterministic dense ids from
`training_category_reference`. The XBRL encoder uses separate embedding tables
per field (`tag_id`, `taxonomy_id`, `unit_id`, and the rest), with id `0`
reserved for missing/unknown. It does not hash or modulo category ids into a
shared table.

Useful shorter smoke:

```powershell
python research\temporal_event_model\v3\run_sweep_training_profile.py --model-batch-grid "tiny:64,128" --max-runs 2
```

To override the full grid:

```powershell
python research\temporal_event_model\v3\run_sweep_training_profile.py --model-batch-grid "tiny:512,1024;medium:128,256"
```

The sweep writes `sweep_results.csv` and `sweep_results.jsonl` under its sweep
run folder. Use it to compare samples/s, loader wait, forward/backward time,
peak memory, loss behavior, checkpointing, audit status, and whether all
requested modalities are exercised.

The CSV contains both `wall_clock_samples_per_second` and
`samples_per_second`. Use wall-clock speed to judge end-to-end feasibility;
use the steady samples/s field to compare batch behavior after warmup and
prefetch effects.

The sweep includes production-path fields in `sweep_results.csv`, including
`production_cache_encode_seconds`, `production_cached_predict_seconds`, and
`production_cached_predict_samples_per_second`.

Full training records detailed model-section timings on the first batch and
then every `detail_profile_samples` samples. The default is 250,000 samples, so
normal loss/optimizer steps are not forced through CUDA-synchronized section
timing every batch. Validation still runs a detailed timing pass when
validation is executed.

Scanner context is prefetched by default before batch materialization:

```text
--prefetch-scanner-indexes
--scanner-prefetch-workers 8
--scanner-index-cache-entries 64
```

This moves scanner parquet indexing out of the first measured training batch
and keeps a full month of daily scanner indexes resident for chronological
training.

## Production Encoder Cache Contract

Qwen is not part of the v3 model. Historical training and live production both
consume the same stored Qwen embedding tensors produced by
`text_embed_gateway`. The v3 text encoder only projects and pools those
embeddings into model tokens.

The model exposes a production-safe two-stage API:

```python
tokens = model.encode_modality_tokens(batch_x)
output = model.predict_from_modality_tokens(tokens)
```

`tokens` is a dict of `[B, fusion_d_model]` tensors with this stable order.
The raw modality encoders may use different widths internally; production
caches the post-adapter fusion-width tokens.

```text
events
ticker_intraday_bars
ticker_daily_bars
global_daily_bars
ticker_news
market_news
sec_filings
xbrl
corporate_actions
scanner_context
```

Production should cache these named modality tokens with enough metadata to
invalidate them safely: model checkpoint/config fingerprint, ticker, origin or
context state key, source cache timestamps, and modality name. When a modality
is unchanged, production can reuse its token and call
`predict_from_modality_tokens()` to run only the fusion transformer and heads.
Missing modality tokens are aligned as zero tokens, matching the masked-zero
training convention.

The profiler is restartable. It saves the model, optimizer, scaler, RNG, profiler
state, and `AsyncDailyIndexBatchLoader.state_dict()` in
`checkpoints/profile_checkpoint_latest.pt`. If the latest profile checkpoint
exists, rerunning the same command resumes unless `--fresh-start` is passed.
Resume explicitly with:

```powershell
python research\temporal_event_model\v3\run_profile_training.py --resume-checkpoint D:\TradingML\runtimes\temporal_event_model\v3\profile\<run>\checkpoints\profile_checkpoint_latest.pt
```

## Chronological Replay Loader

The default v3 loader path is chronological replay:

```text
chronological_replay: true
time_window_seconds: 1.0
ticker_cache_capacity: 15000
origin_cursor_chunk_rows: 4096
warm_all_ticker_caches: true
```

The loader walks configured cache days in timestamp order. If the selected days
are consecutive in the replay schedule, in-memory cache state is carried into
the next day. If the schedule jumps to a non-adjacent day, the loader rebuilds
state from the builder-saved lookback context before emitting origins for that
day.

Events are treated as a rolling cache, not as a fresh per-origin gather. The
day warm-up initializes resident ticker event caches from the builder-saved
prior context. During replay, the current origin event is appended before the
sample is emitted, the oldest row is dropped, and the current cache snapshot is
copied into the batch row. This mirrors production, where events arrive from
QMD and the ticker event cache advances one event at a time. If an evicted or
new ticker appears, its single-ticker cache is rebuilt from the saved package
before that origin is emitted.

Origin rows are not materialized as one full-day table. The loader keeps
per-ticker origin cursors, loads `origin_cursor_chunk_rows` rows per ticker, and
pops only the current `time_window_seconds` rows into a sorted replay window.
This avoids repeatedly scanning all origin parquet files and avoids retaining a
full day of origins in memory.

Sparse contexts follow the same production contract conceptually: ticker news,
market news, SEC embeddings, XBRL, corporate actions, daily/global bars, and
scanner state are as-of caches keyed by availability timestamp. Low-frequency
items that arrive during market close or weekends are still visible to the next
origin whose timestamp is after their availability time. Missing cache slots are
zero with mask false.

Scanner artifacts are consumed only for the active replay window and the next
window is prefetched, so scanner data does not require a month-wide blocking
load before training can start.

### Loader Telemetry

Training logs lightweight loader/cache state without scanning payloads:

- `loader/cache/*`: event rolling-cache ticker count, estimated event-cache MiB,
  ticker cache capacity, protected ticker count, evictions, day warm timing,
  origin-cursor rows/chunks/RSS deltas, payload-cache parts, ready-buffer
  samples, and materializer index-cache counts for text, labels, scanner, bars,
  XBRL, and corporate actions.
- `loader/window/*`: active replay-window refs, tickers, parts, total day refs,
  remaining day refs, and configured window seconds.
- `loader/prefetch/*`: materialization queue depth and maximum pending batches.
- `loader/state/*`: replay/checkpoint cursors such as chronological day
  position, chronological origin cursor, emitted batches, and seen samples.

These counters are derived from existing in-memory state and batch profiles, so
they do not add ClickHouse calls, parquet scans, or tensor reductions.

## Data Contract

The trainer uses `AsyncDailyIndexBatchLoader` in `raw_stream` event mode.
The v3 loader config explicitly requests this event column order:

```text
event_meta, price_primary_int, price_secondary_int, size_primary,
size_secondary, exchange_primary, exchange_secondary, condition_token_1..5,
utc/session time features
```

Labels are grouped by task:

- `scanner_inputs`: global market-leader context. The model receives top-K
  scanner leaders and origin-ticker comparison bars for `top_gainers`,
  large/mid/small/penny-volume groups across `1s`, `5s`, `30s`, and `1m`
  scanner horizons. These tensors are read from scanner artifacts built after
  the main daily-index cache. Scanner artifact indexes are shared across async
  materializer workers and bounded to 8 cached day artifacts by default, so the
  loader does not rebuild the same day index for every batch and does not retain
  an unbounded scanner history during long chronological training.
- `bar_inputs["ticker_intraday_bars"]`: backward same-session intraday context
  bars for `trade`, `quote_bid`, and `quote_ask`, aligned to the same horizon
  list as intraday labels but clipped backward to the session start.
- `bar_inputs["ticker_daily_bars"]` and `bar_inputs["global_daily_bars"]`:
  completed daily context bars with the same `trade`, `quote_bid`, and
  `quote_ask` family schema. Each family carries matching value, mask, and
  separate 9-column bar-start/age and bar-end/age time-feature tensors.
- `future_bar_values`: separate `trade`, `quote_bid`, and `quote_ask` regression heads.
- `intraday_labels`: halt/resume/news-risk/LULD and future news/SEC arrival flags.
- `corporate_action_labels`: daily corporate-action classification horizons.

Losses are unweighted by default. Each active task contributes one masked mean
term; the final loss is the mean of active task losses.

The v3 bar encoder uses one shared `BarRowEncoder` for ticker intraday bars,
ticker daily bars, global daily bars, and scanner bars. Each trade/bid/ask row
is projected from raw decoded bar values plus
`TimeFeatureEncoder(role="bar_start")` and
`TimeFeatureEncoder(role="bar_end")` embeddings. Normal bar groups add family,
bar-group, horizon/offset, and global-symbol-slot embeddings before latent
attention. Scanner uses the same row encoder and adds scanner group, rank,
top-K, ticker-id, and row-type embeddings, but still emits a separate
`scanner_context` modality token to the fusion transformer. No price
normalization or z-score is applied in the default model path.

## Run Artifacts

Every training run writes one run directory under `output-root/run-name`:

```text
artifacts/model/model_details.json
artifacts/model/model_parameters.jsonl
artifacts/model/model_summary.txt
artifacts/model/model_architecture.mmd
artifacts/model/model_architecture.md
checkpoints/checkpoint_latest.pt
metrics.jsonl
run_manifest.json
```

Optional `torchinfo` and `torchview` files are written when those packages are
installed. If not, the corresponding `*_error.txt` files explain why.

## Stopping A Run

The trainer handles console interrupts in two stages:

- first `Ctrl+C`: request graceful cancellation, close loader/prefetch workers,
  flush logs, and write `logs/interrupted.txt`;
- second `Ctrl+C`: force process exit with code `130`.

If Windows terminal control events do not reach the Python process, create an
empty stop file in either of these paths:

```text
<run_dir>/STOP
<run_dir>/logs/STOP
```

The trainer polls these files once per second and raises the same graceful
interrupt path used by `Ctrl+C`.

## Stateful Training

Checkpoints include:

- model, optimizer, scaler, and RNG state
- train and validation loader state
- file-based day schedule and training ledger snapshots
- model card payload with dataset id, period/months, sample counts, data groups,
  latest metrics, and run root

State files are written under `run_dir/state/`:

```text
day_schedule.csv
validation_plan.csv
training_ledger_latest.csv
```

The ledger is keyed by `epoch_index`, `schedule_index`, and `day`. Metrics use
`samples_seen` as the primary x-axis so runs remain comparable when batch size
changes.

Resume with:

```powershell
python research\temporal_event_model\v3\run_train.py -- --resume-checkpoint D:\TradingML\runtimes\temporal_event_model\v3\train\<run>\checkpoints\checkpoint_latest.pt
```

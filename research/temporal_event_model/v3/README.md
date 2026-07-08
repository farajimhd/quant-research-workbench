# Temporal Event Model v3

`temporal_event_model/v3` trains the multimodal temporal model described in
`TRAINER_MODEL_DESIGN_GUIDE.md` on the ticker-month rolling cache.

## Main Entry Points

- `run_train.py` is the recommended launcher. It embeds workstation defaults and
  forwards any override arguments to `train.py`.
- `train.py` contains the stateful trainer, checkpointing, W&B logging, local
  JSONL metrics, Rich progress panels, and model artifact export.
- `run_profile_training.py` profiles the real training loop on the daily-index
  cache. It runs warmup and measured batches, records loader/model/memory
  timings, exports model artifacts, and checkpoints profiler + loader state so
  it can resume after interruption.
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
python research\temporal_event_model\v3\run_train.py -- --months 2019-02,2019-03,2019-04 --batch-size 512 --max-steps 10000
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

For a one-step trainer smoke:

```powershell
python research\temporal_event_model\v3\train.py --dummy-data --wandb-mode disabled --progress-layout text --batch-size 2 --max-steps 1 --validation-samples 2 --validation-batches 1 --d-model 32 --event-layers 1 --event-heads 4 --fusion-layers 1 --fusion-heads 4 --output-root C:\tmp\temporal_v3_train_smoke
```

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
- CPU RSS and CUDA allocated/reserved/peak memory
- loss and fast batch metrics
- detailed loader stage timings from `DailyIndexTrainingBatch.profile`, such as
  raw stream gather, label, text, XBRL, bar, corporate-action, payload load, and
  materialization wait timings

## Sample-Based Training Cadence

Training uses `samples_seen` as the primary clock. Loss is computed every batch,
but expensive summaries, prediction metrics, validation, and periodic
checkpoints are triggered by sample thresholds rather than optimizer steps. This
keeps comparisons fair when batch size changes.

Default cadences:

| Action | Default |
| --- | ---: |
| Fast batch summary | `--fast-summary-samples 25000` |
| Train prediction/cohort metric window | `--train-metric-window-samples 250000` |
| Validation | `--validation-samples 2000000` |
| Latest checkpoint | `--checkpoint-latest-samples 250000` |
| Archive checkpoint | `--checkpoint-archive-samples 2000000` |

Backward-compatible hidden `--*-steps` aliases still parse, but they are
converted to sample counts as `steps * batch_size`.

Detailed model timing is active on the first batch and at validation/profile
points only. It logs encoder timings for event, intraday bars, daily bars,
global bars, ticker news, market news, SEC, XBRL, corporate actions, scanner,
fusion, query heads, output heads, loss, backward, optimizer, checkpoint, and
loader/materialization stages.

## Model/Batch Sweep

Default workstation sweep:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\research\temporal_event_model\v3\run_sweep_training_profile.py
```

Useful shorter smoke:

```powershell
python research\temporal_event_model\v3\run_sweep_training_profile.py --models tiny --batch-sizes 64,128 --max-runs 2
```

The sweep writes `sweep_results.csv` and `sweep_results.jsonl` under its sweep
run folder. Use it to compare samples/s, loader wait, forward/backward time,
peak memory, loss behavior, checkpointing, audit status, and whether all
requested modalities are exercised.

The profiler is restartable. It saves the model, optimizer, scaler, RNG, profiler
state, and `AsyncDailyIndexBatchLoader.state_dict()` in
`checkpoints/profile_checkpoint_latest.pt`. If the latest profile checkpoint
exists, rerunning the same command resumes unless `--fresh-start` is passed.
Resume explicitly with:

```powershell
python research\temporal_event_model\v3\run_profile_training.py --resume-checkpoint D:\TradingML\runtimes\temporal_event_model\v3\profile\<run>\checkpoints\profile_checkpoint_latest.pt
```

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
  the main daily-index cache.
- `bar_inputs["ticker_intraday_bars"]`: backward same-session intraday context
  bars for `trade`, `quote_bid`, and `quote_ask`, aligned to the same horizon
  list as intraday labels but clipped backward to the session start.
- `future_bar_values`: separate `trade`, `quote_bid`, and `quote_ask` regression heads.
- `intraday_labels`: halt/resume/news-risk/LULD and future news/SEC arrival flags.
- `corporate_action_labels`: daily corporate-action classification horizons.

Losses are unweighted by default. Each active task contributes one masked mean
term; the final loss is the mean of active task losses.

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

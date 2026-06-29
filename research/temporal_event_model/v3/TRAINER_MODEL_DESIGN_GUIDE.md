# Temporal Event Model v3 Trainer And Model Design

This guide defines the intended v3 trainer/model design before implementation.
It is a verification target: implementation should not start until the design is
approved.

## Goal

`temporal_event_model/v3` trains a multimodal temporal prediction model on the
ticker-month rolling cache. The model consumes raw event context, daily bar
context, precomputed Qwen text embeddings, XBRL context, and future-label
tensors. It should train end-to-end, but production inference should be able to
cache encoder outputs and run only the cheaper fusion/head path when context has
not changed.

The trainer must be stateful. A checkpoint must be sufficient to resume from
the same data position and reproduce benchmark batches across model variants.

## Data Source

The primary data source is the ticker-month cache loader:

```text
research.mlops.rolling_loader.ticker_month_dataset.AsyncTickerMonthBatchLoader
```

The loader should support deterministic and non-deterministic modes:

- deterministic benchmark mode: fixed `dataset_id`, seed, hash buckets, and
  sample limit produce the same batches across runs
- production training mode: stochastic shuffling can vary by run while still
  being checkpointable

The loader should emit all selected data groups but allow low-level selection
for pretraining or ablation, such as events-only, events+labels, text-only, or
full multimodal training.

## Batch Contract

Each batch keeps identity fields for audit and checkpoint logs:

```text
ticker                  [B]
origin_ordinal          [B]
origin_timestamp_us     [B]
source_part_key         [B]
```

Event inputs:

```text
raw_event_stream        [B, 1024, F]
raw_event_mask          [B, 1024]
raw_event_feature_names tuple[str, ...]
```

The event stream is a sliding window ending at the origin. It is not byte-encoded
by default. Event fields should include only useful model inputs, with redundant
identity columns suppressible by loader args. Identity columns remain available
in metadata for audit.

Daily bar inputs:

```text
ticker_daily_bars       [B, ticker_offsets, bar_features]
ticker_daily_bar_mask   [B, ticker_offsets]
global_daily_bars       [B, global_symbols, global_offsets, bar_features]
global_daily_bar_mask   [B, global_symbols, global_offsets]
```

Text embedding inputs should use precomputed Qwen embeddings, not token ids, for
v3 training:

```text
ticker_news_embeddings  [B, 8, 2, 1024]
ticker_news_item_mask   [B, 8]
ticker_news_chunk_mask  [B, 8, 2]

market_news_embeddings  [B, 16, 2, 1024]
market_news_item_mask   [B, 16]
market_news_chunk_mask  [B, 16, 2]

sec_filing_embeddings   [B, 4, 8, 1024]
sec_filing_item_mask    [B, 4]
sec_filing_chunk_mask   [B, 4, 8]
```

The stored embedding source is:

```text
news_text_embeddings
sec_filing_text_embeddings
```

Embeddings are written as `Array(Float32)` in ClickHouse. Loader/model code may
cast them to bf16 on GPU during training. Missing items/chunks are zero-filled
and masked false.

XBRL inputs:

```text
xbrl_value              [B, 4096]
xbrl_mask               [B, 4096]
xbrl_time_delta         [B, 4096]
xbrl_category_ids       field-specific [B, 4096]
xbrl_confidence         [B, 4096]
```

Labels:

```text
future_intraday_bars      [B, H, label_features]
future_intraday_bar_mask  [B, H]
intraday_labels           dict[str, [B, H]]
```

Primary price targets should use bid and ask fields. Redundant mid-price targets
should not be trained by default unless explicitly enabled for an ablation.

## Model Architecture

The v3 model is a set of independent encoders plus a fusion transformer and
horizon heads.

### Event Encoder

Input:

```text
raw_event_stream [B, 1024, F]
raw_event_mask   [B, 1024]
```

Design:

- numeric projection for price, size, and time features
- categorical embeddings for event type, exchanges, flags, and conditions
- temporal encoder over the 1024-event stream using a transformer or TCN
- output an event modality token and optional event summary sequence

### Bar Encoder

Input:

```text
ticker_daily_bars
global_daily_bars
```

Design:

- separate ticker and global bar encoders
- MLP projection for bar features
- small transformer or attention pooling over offsets/symbols
- output ticker-bar and global-bar modality tokens

### Text Embedding Encoder

Input:

```text
Qwen chunk embeddings + item/chunk masks + timestamps + metadata category ids
```

Design:

- project `1024 -> d_model`
- add modality, item-position, chunk-position, and age/time features
- pool chunks into item embeddings using masked attention or gated pooling
- pool items into one modality token per group:
  - ticker news
  - market news
  - SEC filings

The model does not fine-tune Qwen in v3. Qwen inference is offline and cached.

### XBRL Encoder

Input:

```text
up to 4096 XBRL rows per sample
```

Design:

- numeric projection for value, period/time features, confidence
- category embeddings for taxonomy, tag, unit, form, row kind, and location
- gated pooling or Perceiver-style latent cross-attention
- avoid full 4096-row self-attention by default
- output one XBRL modality token

### Fusion Transformer

Input modality tokens:

```text
event
ticker_daily_bars
global_daily_bars
ticker_news
market_news
sec_filings
xbrl
```

Design:

- add modality embeddings
- include missing-modality masks
- append learned horizon query tokens
- run a fusion transformer
- decode each horizon query through prediction heads

### Prediction Heads

Primary output:

```text
future bid delta by horizon
future ask delta by horizon
```

Targets should be normalized relative to the origin/as-of price, preferably in
bps or ticks, not raw price integer units.

Optional auxiliary heads:

- future event count per horizon
- future primary/secondary size sums
- label availability calibration
- spread or liquidity regime classification

Auxiliary heads should be explicitly weighted and easy to disable.

## Loss

Use mask-aware multi-horizon losses:

```text
primary_loss = masked_huber_or_mae(pred_bid_ask_delta, target_bid_ask_delta, label_mask)
```

The default objective should emphasize stable price movement prediction:

- Huber or MAE in normalized bps/tick space
- per-horizon mask
- optional horizon weights
- no loss contribution when a horizon is unavailable

Auxiliary losses:

- event count: masked Poisson, log-MAE, or Huber on log1p count
- size sums: masked Huber on log1p size
- availability: BCE only if useful for diagnostics

## Metrics

Metrics should be emitted to Rich terminal, JSONL, and W&B.

Core training metrics:

- `train/loss`
- `train/primary_loss`
- `train/aux_loss`
- `train/learning_rate`
- `train/grad_norm`
- `train/samples_seen_total`
- `train/samples_per_second`
- `train/step_seconds`
- `train/loader_wait_seconds`
- `train/gpu_step_seconds`
- `train/gpu_memory_allocated_gib`
- `train/gpu_memory_reserved_gib`

Label-derived price metrics, overall and per horizon:

- `mae_bid_bps`
- `mae_ask_bps`
- `rmse_bid_bps`
- `rmse_ask_bps`
- `median_abs_error_bid_bps`
- `median_abs_error_ask_bps`
- `sign_accuracy_bid`
- `sign_accuracy_ask`
- `directional_accuracy_any_move`
- `valid_fraction`
- `target_mean_bps`
- `target_std_bps`
- `prediction_mean_bps`
- `prediction_std_bps`
- `bias_bps`

Spread/liquidity-aware metrics:

- `mae_bid_bps_by_spread_bucket`
- `mae_ask_bps_by_spread_bucket`
- `sign_accuracy_by_spread_bucket`
- `mae_by_event_count_bucket`
- `mae_by_session_bucket`

Intraday label metrics:

- `future_event_count_mae`
- `future_event_count_log_mae`
- `future_size_primary_log_mae`
- `future_size_secondary_log_mae`
- `label_available_fraction`
- `last_event_timestamp_gap_seconds`

Input availability metrics:

- `ticker_news_available_fraction`
- `market_news_available_fraction`
- `sec_filings_available_fraction`
- `xbrl_available_fraction`
- `ticker_bars_available_fraction`
- `global_bars_available_fraction`
- `event_window_valid_fraction`

State/data accounting metrics:

- `loader/epoch`
- `loader/package_position`
- `loader/origin_cursor`
- `loader/emitted_batches`
- `loader/emitted_samples`
- `loader/seen_origins_total`
- `loader/seen_origins_this_epoch`
- `loader/cache_manifest_fingerprint`
- `loader/dataset_plan_id`

Validation metrics should mirror training metrics with `val/` prefixes and
should be computed on deterministic validation loader state.

## Stateful Trainer Contract

Each checkpoint must contain enough state to resume the same run without
changing data order:

```text
model.state_dict
optimizer.state_dict
scheduler.state_dict
scaler.state_dict
global_step
epoch
samples_seen
best_metric_state
train_loader.state_dict()
validation_loader.state_dict()
python RNG state
numpy RNG state
torch RNG state
cuda RNG state
config snapshot
dataset_id
cache_manifest_fingerprint
git commit
wandb run id
```

Resume flow:

1. Rebuild config and model.
2. Recreate train/validation loaders from config.
3. Verify cache manifest fingerprint.
4. Restore model, optimizer, scheduler, scaler.
5. Restore RNG state.
6. Restore train and validation loader states.
7. Continue from the next batch.

The trainer should expose:

- `--resume-checkpoint`
- `--warm-start-checkpoint`
- `--fresh-start`
- `--dataset-id`
- `--max-origins-per-epoch`
- deterministic hash bucket controls for train/validation/holdout

For benchmarking and hyperparameter search, use fixed dataset ids such as:

```text
temporal_v3_1m_2019_v1
```

The same dataset id, seed, period, hash buckets, and sample limit should produce
identical batches across trainer instances.

## Trainer Engineering

Reuse the v20 training engineering style where practical:

- Rich terminal panels
- W&B metrics
- JSONL metrics
- async checkpoint manager
- run manifest
- failure traceback bundle
- bf16 AMP support
- optional model compile
- model artifact export at run start
- periodic validation
- loader throughput profiling

Data loading should overlap with GPU training:

```text
background loader reads and materializes next batches
GPU trains current batch
checkpoint stores model + optimizer + loader state
```

Default precision:

```text
stored text embeddings: Float32 in ClickHouse/cache
loader CPU tensors: float32 unless memory pressure requires otherwise
GPU training: bf16 AMP by default
```

## Run Artifacts

Each run writes a single run directory. Required files:

```text
config.json
run_manifest.json
metrics.jsonl
logs/fatal_error.txt
checkpoints/
artifacts/model/model_details.json
artifacts/model/model_parameters.jsonl
artifacts/model/model_summary.txt
artifacts/model/model_summary_torchinfo.txt
artifacts/model/model_summary_training_torchinfo.txt
artifacts/model/model_architecture.md
artifacts/model/model_architecture.mmd
artifacts/model/model_architecture_torchview
artifacts/model/model_architecture_torchview_error.txt
```

If `torchinfo` or `torchview` is unavailable, the trainer must write the matching
`*_error.txt` artifact rather than silently skipping model artifacts.

The model artifact export should include:

- model config
- parameter count by module
- trainable/frozen parameter count
- input/output shape contract
- production inference path summary
- full training path summary
- Mermaid architecture diagram
- optional torchview graph

## Rich Terminal Panels

The terminal should show:

- run summary: run name, dataset id, device, precision, params
- state panel: epoch, global step, samples seen, loader cursor, checkpoint path
- loss/metrics panel: current and moving-average training metrics
- validation panel: latest validation metrics
- throughput panel: samples/s, loader wait, GPU step time, memory
- data availability panel: event/text/XBRL/bar availability fractions
- message panel: recent warnings, checkpoints, validation, audit messages

Panels should be stable and non-flickering, following the v20 Rich layout style.

## Verification Checklist

Before a real training run:

1. Loader emits all requested groups with expected shapes.
2. Text embedding tensors come from `news_text_embeddings` and
   `sec_filing_text_embeddings`, not token ids.
3. Missing text/XBRL/bar context is zero-filled and masked false.
4. Event windows are aligned to `ticker + origin_ordinal`.
5. No origin appears outside the requested train/validation period.
6. Future labels never cross invalid intraday boundaries.
7. Daily bar features use only bars available as of the origin.
8. Future daily labels use only forward bars.
9. Label masks are false when a target horizon is unavailable.
10. Checkpoint resume reproduces the exact next batch.
11. Deterministic dataset mode reproduces the same 1M-sample benchmark set.
12. Validation loader is deterministic and independent of train-loader position.
13. Model artifact files are created before training starts.
14. W&B and JSONL metrics contain the same key scalar metrics.
15. Audit can query a small set of sampled identities against ClickHouse and
    verify event rows, labels, bars, text embeddings, and XBRL context.

## Open Implementation Notes

- Text embedding tables must be available before full v3 training.
- XBRL pooling must be designed to avoid quadratic attention over 4096 rows.
- Label normalization constants should be logged in config and manifest.
- Production encoder-cache interfaces should be explicit:
  `encode_events`, `encode_bars`, `encode_text`, `encode_xbrl`, and
  `predict_from_embeddings`.

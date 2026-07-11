# Packed Market Model v1

`packed_market_model/v1` is the first block-native model family for market-event training.
It is designed around packed chronological blocks rather than expanded per-origin context tensors.

## Data Flow

```mermaid
flowchart LR
  daily["Daily-index streaming cache"] --> builder["Packed block builder"]
  builder --> cache["Packed market block cache"]
  cache --> loader["PackedMarketDataset"]
  loader --> model["PackedMarketModelV1"]
  model --> loss["Grouped loss over all origins"]
```

## Model Contract

| Input | Shape | Meaning |
|---|---:|---|
| `events` | `[T, F]` | Contiguous ticker-local event stream for the block. |
| `origin_positions` | `[M]` | Integer positions into `events`; each position is one training origin. |
| `event_feature_names` | `[F]` | Names of raw event fields. |
| `labels[name]` | `[M]` | One target per origin for each discovered label column. |

## Architecture

| Stage | Input | Output | Notes |
|---|---:|---:|---|
| Event projection | `[T, F]` | `[T, d_model]` | LayerNorm + MLP. Raw loader values stay raw; preprocessing is inside the model. |
| Position embedding | `[T]` | `[T, d_model]` | Optional learned position id within the packed stream. |
| Causal event encoder | `[T, d_model]` | `[T, d_model]` | Stack of causal depthwise-conv residual blocks. This is faster than full attention for the first version. |
| Origin gather | `[T, d_model]`, `[M]` | `[M, d_model]` | Selects model state at each origin without rebuilding windows. |
| Label heads | `[M, d_model]` | `{label: [M]}` | One head per discovered label column. |

## Loss Groups

Labels are grouped by name:

| Group | Name Pattern | Loss |
|---|---|---|
| `price` | `price`, `open`, `high`, `low`, `close`, `bid`, `ask`, `trade` | Huber |
| `event_count` | `count`, `num_`, `event_count` | Huber |
| `event_size` | `size`, `volume`, `notional` | Huber |
| `event_state` | `flag`, `halt`, `luld`, `condition`, `is_` | BCE |
| `external_arrival` | `news`, `sec`, `arrival` | BCE |
| `corporate_action` | `split`, `dividend`, `corporate` | BCE |
| `regression` | fallback | Huber |

Loss groups are averaged without task weights to avoid the instability seen in prior AMP/bf16 runs.

## Scheduler

The trainer uses a sample-clock cosine scheduler:

- `learning_rate=1e-3`
- `scheduler=cosine`
- `scheduler_eta_min=1e-6`
- `scheduler_cycle_samples=1_024_000`
- after every `scheduler_decay_cycles=100` cosine cycles, peak LR is multiplied by `0.95`

All logs, metrics, and checkpoints are keyed by samples seen, not steps.

## Run Commands

Build packed cache first:

```powershell
python -m research.mlops.packed_market.builder `
  --source-cache-root D:\market-data\prepared\daily_index_streaming_cache\events_daily_index_2019-02 `
  --output-root D:\market-data\prepared\packed_market_block_cache `
  --cache-id packed_events_daily_index_2019-02 `
  --months 2019-02
```

Train:

```powershell
python -m research.packed_market_model.v1.train `
  --cache-root D:\market-data\prepared\packed_market_block_cache\packed_events_daily_index_2019-02 `
  --months 2019-02
```

Smoke:

```powershell
python -m research.packed_market_model.v1.train --dummy-data --max-blocks 2 --max-samples 128 --wandb-mode disabled --compile-model false
```

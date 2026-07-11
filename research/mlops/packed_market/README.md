# Packed Market Block Cache

This package is the shared data layer for models that consume packed chronological market blocks directly.
It replaces the slow path that materialized one full context tensor per origin before training.

## Core Contract

The cache stores each ticker/month as:

| Artifact | Shape / Semantics | Purpose |
|---|---:|---|
| `events.parquet` | `[T, event_features + identity]` | One contiguous ticker-local event stream for the month. Events are sorted by timestamp and ordinal and are not duplicated per origin. |
| `origins.parquet` | `[M, origin_identity + origin_event_index]` | Eligible origins and their pointer into the event stream. |
| `labels_intraday.parquet` | `[M, label_columns]` | Labels aligned one row per origin where available. |
| `block_manifest.json` | index ranges | Defines the event slice and origin slice consumed by one training block. |

The trainer reads:

```text
events:           [T_block, F]
origin_positions: [M_block]
labels:           one tensor per label, [M_block]
```

The model runs the event encoder once over `events`, gathers hidden states at `origin_positions`, and computes the loss over all origins in the block.

## Build Command

```powershell
python -m research.mlops.packed_market.builder `
  --source-cache-root D:\market-data\prepared\daily_index_streaming_cache\events_daily_index_2019-02 `
  --output-root D:\market-data\prepared\packed_market_block_cache `
  --cache-id packed_events_daily_index_2019-02 `
  --months 2019-02 `
  --workers 16
```

Smoke test with one ticker:

```powershell
python -m research.mlops.packed_market.builder `
  --source-cache-root D:\market-data\prepared\daily_index_streaming_cache\events_daily_index_2019-02 `
  --output-root D:\market-data\prepared\packed_market_block_cache `
  --cache-id smoke_packed_events_2019-02 `
  --months 2019-02 `
  --tickers AAPL `
  --max-packages 1 `
  --overwrite
```

## Builder Rules

- The builder uses the daily-index streaming cache as the source of truth.
- It deduplicates event rows by ordinal within a ticker/month.
- It joins origins to `events.event_index`; origins without a matching event are dropped and reported through package status.
- It stores events once per ticker/month and creates block manifests as views over that event stream.
- `context-events=1024` means each block includes up to 1023 prior events before its first origin.

## Loader Rules

- `PackedMarketDataset.iter_blocks()` yields `PackedMarketBlock`.
- The dataset state includes `block_index`, `epoch`, emitted blocks/origins, and a cache fingerprint.
- Resume refuses to load a state if the packed cache fingerprint changed.

## Why This Exists

The previous materialized batch path repeated the same event context thousands of times. This cache keeps the chronological stream packed and lets the model consume indexed origin positions directly, which is the practical path for large-scale training and production replay.

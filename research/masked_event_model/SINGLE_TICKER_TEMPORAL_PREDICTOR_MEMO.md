# Single-Ticker Temporal Predictor Memo

## Decision

Use a single-ticker temporal predictor as the next model after the event encoder.

The model should be trained across all tickers with shared weights, but each training
sample represents one ticker at one decision point. In production, inference should
still be batched across all tickers that need an update. This gives us the simpler
learning problem of a ticker-local model without forcing one GPU call per ticker.

## Current Stage

We are currently working on stage 1:

1. Event encoder
   - Input: one compact event chunk for one ticker.
   - Current chunk shape: `header_uint8=[14]`, `events_uint8=[128, 16]`.
   - Output: one compact embedding for that chunk, for example `[32]`.
   - Training objective: masked event reconstruction.
   - Later use: discard the decoder and reuse only the encoder.

The next stage is stage 2:

2. Single-ticker temporal predictor
   - Input: a sequence of event-encoder embeddings for one ticker.
   - Output: future event chunk(s), future encoded event bytes, or downstream price/movement targets.
   - Training is across all tickers, but each sample is ticker-local.

## Why Single-Ticker First

The scanner-based whole-market model is not the right first higher-level model.
It introduces dynamic ticker counts, missing or idle tickers, large memory use,
hard labels, difficult batching, and expensive cross-ticker attention.

The recommended first predictor is:

```text
one sample = one ticker at one origin time
x = last K event-chunk embeddings for that ticker
y = the future chunk or future targets for that same ticker
```

Production should batch many ticker samples together:

```text
batch = all tickers that updated since the last cycle
run one or more GPU batches
```

This keeps production efficient while keeping the learning task controlled.

## Data Unit

The base training unit should be a ticker-local temporal sample.

For a ticker `T` and origin chunk index `c`:

```text
sample_id
ticker_id
origin_timestamp_us
origin_ordinal
context_chunk_ids = [c-K+1, ..., c]
target_chunk_ids = [c+1, ..., c+H]
```

The model input after event encoding is:

```text
x_embeddings: [K, embedding_dim]
```

For example:

```text
K = 16
embedding_dim = 32
x_embeddings = [16, 32]
```

The raw chunk backing each embedding is still:

```text
header_uint8: [14]
events_uint8: [128, 16]
```

So before encoding, one sample with `K=16` refers to:

```text
input_chunk_headers: [16, 14]
input_chunk_events:  [16, 128, 16]
```

After encoder inference:

```text
input_embeddings: [16, 32]
```

## Recommended Target

The cleanest next target is the next future event chunk in the same compact
representation, because it is aligned with the event-encoder objective.

For horizon `H` future chunks:

```text
y_chunk_headers: [H, 14]
y_chunk_events:  [H, 128, 16]
```

For an MVP:

```text
H = 1
y = next event chunk
```

For a richer temporal predictor:

```text
H = 4, 8, or 16
y = next H event chunks
```

The decoder can predict future bytes/bits using the same compact representation
used by the event encoder. This avoids asking the higher-level model to predict
with more precision than the data representation itself contains.

## Stored Dataset Shape

A practical dataset should store metadata plus chunk references first. It should
not duplicate event bytes unnecessarily unless training throughput requires a
precomputed cache.

Recommended index row:

```text
split
ticker_id
ticker
origin_timestamp_us
origin_ordinal
context_start_ordinal
context_end_ordinal
target_start_ordinal
target_end_ordinal
context_chunk_count
target_chunk_count
```

If we store precomputed embeddings:

```text
x_embeddings_float16: [K, embedding_dim]
```

If we store target chunks:

```text
y_header_uint8: [H, 14]
y_events_uint8: [H, 128, 16]
```

The index should keep ticker and timestamp metadata for sampling, debugging,
auditing, and later joins. These fields do not have to be model inputs.

## Sampling

Training should sample ticker-local timelines, not scanner states.

Recommended first sampling policy:

1. Choose a ticker uniformly from eligible tickers.
2. Choose a valid origin chunk for that ticker uniformly.
3. Load the previous `K` chunks and next `H` chunks.
4. Encode the `K` context chunks using the frozen or trainable event encoder.
5. Train the temporal predictor on `[K, embedding_dim] -> future target`.

This avoids biasing entirely toward the most liquid tickers while still allowing
liquid tickers to contribute many distinct origin points over time.

## Production Path

Production should not run one GPU inference per ticker.

Recommended production flow:

1. Maintain rolling event chunks per active ticker.
2. When a ticker updates, refresh only that ticker's latest chunk embedding.
3. Build predictor inputs for all tickers that need prediction.
4. Run one or more batched GPU inferences.
5. Publish predictions per ticker.

This keeps inference proportional to updated tickers, not the whole market.

## Cross-Ticker Information

The first temporal predictor should stay ticker-local. Cross-ticker information
can be added later as external context rather than making the first model a full
scanner model.

Useful later additions:

- SPY/QQQ/IWM event embeddings.
- Sector or industry embeddings.
- Market time/session features.
- Cross-sectional rank features.
- Prior day/week/month high-low summaries.
- A second-stage scanner or ranking model over ticker-level outputs.

## Summary

The next dataset should be built around ticker-local temporal samples:

```text
x = K past event-encoder embeddings for one ticker
y = next H compact event chunks or derived future targets for the same ticker
metadata = ticker, origin timestamp, origin ordinal, split, chunk ids
```

This gives a clean bridge from the event encoder to a practical production model:
single-ticker learning, shared weights across the market, and batched inference
over updated tickers.

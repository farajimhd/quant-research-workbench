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

The first implementation does not need a new raw event store if ClickHouse block
queries are fast enough. The source of truth should remain:

```text
market_sip_compact.events
```

For one ticker, a single ordered range query can return a long contiguous event
timeline:

```sql
WHERE ticker = '<ticker>'
  AND ordinal BETWEEN <start_ordinal> AND <end_ordinal>
ORDER BY ordinal
```

The data loader can then create many rolling-window samples from that one block.
This amortizes ClickHouse query cost over many samples and avoids duplicating raw
event windows on SSD.

A practical dataset index can still store metadata and references for sampling,
debugging, and reproducibility. It should not duplicate event bytes unless online
block queries become the bottleneck.

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

## Online Block Loader

The preferred first data-provider design for the temporal predictor is an online
block loader:

1. Pick a ticker and an ordinal range.
2. Query a large contiguous block from `market_sip_compact.events`.
3. Keep that block in memory.
4. Generate many rolling-window samples from the block.
5. While the GPU trains on the current block-derived batches, prefetch the next
   block in a background worker.

For a block with `B_events` rows:

```text
event_block: [B_events, event_row_fields]
```

For a sample origin `t` inside that block:

```text
x_raw_span = events[t - x_len + 1, ..., t]
y_raw_span = events[t + 1, ..., t + y_len]
```

With:

```text
N = 128
K_max = 16
H = 1
x_len = (K_max + 1) * N = 2176
y_len = H * N = 128
```

The loader can decide at training time how to slice `x_raw_span` into context
chunks. This keeps stride, overlap, and `K` experimental instead of hard-coding
them into a stored dataset.

Important separate parameters:

```text
events_per_chunk = 128
context_stride_events = configurable
target_stride_events = configurable
origin_sampling_stride_events = configurable
```

`context_stride_events` controls the spacing between chunks inside one sample.
`origin_sampling_stride_events` controls how densely origins are sampled for
training. Production can still update every event even if training samples are
subsampled.

## Optional Raw Timeline Store

If ClickHouse block queries become a bottleneck, the fallback is a ticker
timeline store, not per-origin raw spans.

Preferred unit:

```text
ticker-month parquet
```

Rows should be ordered compact unified events:

```text
ordinal
sip_timestamp_us
event_type
price_primary_int
price_secondary_int
size_primary
size_secondary
exchange_primary
exchange_secondary
event_flags
conditions_packed
event_date
```

This store should include enough carry context around month boundaries so origins
near boundaries can still build full context and target spans. It is optional and
should only be built if online ClickHouse block loading cannot keep up.

## Embedding Cache

After an event encoder checkpoint is selected, the most valuable cache is an SSD
embedding cache, not a duplicated raw event cache.

Key:

```text
encoder_version
schema_version
ticker
chunk_end_ordinal
events_per_chunk
```

Value:

```text
embedding_float16: [embedding_dim]
chunk_end_timestamp_us
```

For example, with:

```text
embedding_dim = 32
float16 = 2 bytes
```

one embedding is roughly:

```text
64 bytes + metadata
```

This is much smaller than repeatedly storing raw event spans. The temporal
predictor can then train mostly from:

```text
x_embeddings: [K, embedding_dim]
```

instead of repeatedly running the event encoder.

Embedding stride should be configurable:

```text
embedding_stride = 1, 4, 8, ...
```

Stride `1` is closest to production because it has one embedding per event
origin. Larger strides reduce storage and precompute time. Missing fresh
production embeddings can be computed online for recently updated tickers.

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

Implementation priority:

1. Benchmark ClickHouse block-query loading from `market_sip_compact.events`.
2. Build the temporal predictor loader on top of contiguous event blocks.
3. After choosing an encoder checkpoint, materialize an SSD embedding cache.
4. Only build a raw ticker timeline store if online block queries are too slow.

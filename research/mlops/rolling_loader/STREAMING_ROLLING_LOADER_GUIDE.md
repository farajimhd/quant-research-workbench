# Streaming Rolling Loader Guide

This guide defines the practical training implementation for the stateful
rolling context design described in
`research/mlops/data/STATEFUL_ROLLING_CONTEXT_DESIGN.md`.

The original design remains the source of truth for semantics:

- training and production share the same cache and sample-index logic
- caches are warm-loaded as-of the replay start
- caches advance chronologically
- samples are lightweight stable-id indices before final materialization
- training uses raw trainable payloads, while production can use cached
  embeddings
- model-facing batches follow the shared `research.mlops.data` contract

The change in this guide is operational, not semantic. The original design is
hard to implement efficiently by issuing many small database queries. For
historical training, this loader instead streams larger blocks from ClickHouse,
brings data to memory, processes it with vectorized CPU operations, and feeds
ready batches to the trainer.

## Goal

The main goal is to keep the GPU training loop supplied with model-ready
batches while the CPU side performs data loading, replay, cache updates, and
batch materialization concurrently.

The loader should prepare training data with these properties:

- no per-sample ClickHouse queries
- no massive `ticker IN (...)` query strings for all tickers
- no timestamp-window scans against the ticker/ordinal ordered event table
- high-frequency events streamed from ClickHouse in large date blocks
- tokenized low-frequency context loaded as cache updates, not raw text
- cache retention controlled only by the cache configuration
- sample-index creation performed from rolling cache state
- batch construction performed from in-memory state using vectorized CPU work
- asynchronous prefetch so GPU training and CPU data prep overlap

The three-day load window is an I/O prefetch window. It is not the semantic
retention policy. Caches decide what stays resident and what is evicted.

## Relationship To The Original Stateful Design

The input and output contract is the same as the stateful design. The difference
is how historical training obtains source rows efficiently.

Original stateful idea:

```text
initialize caches at start timestamp
advance chronologically
update caches when source data arrives
create stable sample indices from current cache state
materialize batches from ids at the final collator/materializer step
```

Streaming training implementation:

```text
initialize caches at start timestamp
stream 3-day source blocks from ClickHouse
convert blocks to in-memory Arrow/Polars/NumPy structures
replay rows chronologically through the same caches
create the same stable sample indices
materialize the same RollingTrainingBatch contract
prepare future batches on CPU while GPU trains on current batches
```

The streaming loader should therefore be judged by two requirements:

1. It must preserve the stateful no-lookahead cache semantics.
2. It must make the historical training path fast enough by changing I/O and
   CPU processing strategy.

It is not a separate model contract and not a separate sampling concept.

## Source Table Shape

The event source table is currently optimized for ticker/ordinal reads:

```sql
PARTITION BY toYYYYMM(event_date)
ORDER BY (ticker, ordinal)
```

This means the fastest high-frequency read shape is a date-bounded sequential
scan ordered by `(ticker, ordinal)`:

```sql
SELECT
    ticker,
    ordinal,
    event_type,
    sip_timestamp_us,
    price_primary_int,
    price_secondary_int,
    size_primary,
    size_secondary,
    exchange_primary,
    exchange_secondary,
    event_flags,
    conditions_packed
FROM market_sip_compact.events
PREWHERE event_date >= toDate({block_start})
  AND event_date < toDate({block_end})
ORDER BY ticker, ordinal
```

The table is not optimized for all-ticker microsecond timestamp windows. A
timestamp-ordered replay view can be built in memory after each bulk load.

## Data Classes

### High-Frequency Events

High-frequency market events are loaded in large date blocks, initially three
days at a time:

```text
block_start = D
block_end   = D + 3 days
```

Rows are loaded into an Arrow/Polars block, converted to the compact event row
format used by the caches, and replayed in timestamp order.

### Tokenized Context Updates

Large sparse context is already tokenized. The loader should fetch only the
training-ready token payloads and metadata needed by the model:

- ticker news tokens
- global news tokens
- SEC filing text tokens
- XBRL numeric context, if enabled by the model/configuration

Raw text should not be loaded or processed in this loader.

### Daily Macro And Global Bars

Only `1d` bars are used. Do not load `1w`, `1mo`, or `1y` bars for this path.

Daily macro/global bars are cheap enough to load once at initialization for the
full training date range plus the required lookback. They should be held in
memory and queried by as-of logic:

```text
bar_end_us <= sample_event_us
```

This prevents lookahead leakage when future bars have already been preloaded.

## Initialization Flow

Initialization should fully prepare the cache state as-of the replay start.

### Step 1: Resolve Configuration

Resolve:

- `start_timestamp_us`
- training end timestamp or date range
- high-frequency event cache size
- news lookback and item limits
- SEC filing lookback and item limits
- XBRL lookback and item limits
- daily macro/global bar symbols
- three-day streaming block size

### Step 2: Initialize Empty Caches

Create all cache objects before replay starts:

- per-ticker high-frequency event caches
- per-ticker ticker-news token caches
- per-ticker SEC token caches
- per-ticker XBRL caches, if enabled
- global-news token cache
- daily macro/global bar store

Ticker-specific caches can be created lazily when a ticker first appears in a
streamed block, but the cache construction rule must be deterministic.

### Step 3: Warm High-Frequency Event Caches

Load only the required high-frequency tail before `start_timestamp_us`.

For each ticker that has events before the start:

```text
warm_count = configured high-frequency cache coverage
end = latest ordinal at or before start_timestamp_us
start = end - warm_count + 1
```

The current event table is good at this query shape:

```sql
PREWHERE ticker IN request_tickers
WHERE ordinal >= per_ticker_start
  AND ordinal <= per_ticker_end
ORDER BY ticker, ordinal
```

The loaded rows are pushed into event caches. Warmup should not emit training
samples.

### Step 4: Initialize Token Context As-Of State

For each tokenized context type, load the configured as-of state before
`start_timestamp_us`:

- newest `ticker_news_items` per ticker within `news_lookback_days`
- newest `global_news_items` within `news_lookback_days`
- newest `sec_filing_items` per ticker within `sec_lookback_days`
- newest XBRL rows per ticker within `xbrl_lookback_days`, if enabled

Push these payloads into the corresponding caches. They become available for
samples at the replay start.

### Step 5: Load Full Daily Macro/Global Bars

Load `1d` macro/global bars for the full training period plus lookback:

```sql
SELECT ...
FROM macro_bars_by_time_symbol
WHERE timeframe = '1d'
  AND bar_end >= {macro_start}
  AND bar_end <= {training_end}
ORDER BY sym, bar_end
```

Keep the result in memory. During batch construction, use as-of selection with
`bar_end_us <= origin_timestamp_us`.

## Streaming Loop

After initialization, the loader advances through time using three-day bulk
loads.

```text
cursor = start_timestamp_us

while cursor < training_end:
    block_start = date floor of cursor
    block_end = block_start + 3 days

    load high-frequency event block
    load tokenized context update block
    normalize blocks
    merge events and context by timestamp
    replay merged stream into caches
    emit vectorized training batches

    cursor = next unprocessed block boundary
```

The next block may overlap by timestamp if needed for deterministic replay, but
the caches must be responsible for deduplication and retention.

## Concurrent Training Pipeline

The streaming loader should run as a CPU-side producer for the GPU training
loop. It should not wait for the GPU to finish a step before preparing the next
batch.

Recommended pipeline:

```text
CPU source loader thread/process:
  stream next 3-day event/context blocks from ClickHouse
  normalize Arrow/Polars blocks
  hand normalized blocks to replay workers

CPU replay/materialization workers:
  replay rows through rolling caches in timestamp order
  create RollingSampleIndex records
  materialize RollingTrainingBatch objects
  enqueue ready batches

GPU training loop:
  dequeue ready batch
  move tensors to device
  run forward/backward/optimizer step
```

The queues should be bounded:

```text
source_block_queue_size = 1 or 2
ready_batch_queue_size = 2 to 8
```

Bounded queues prevent CPU prefetch from consuming unbounded memory while still
allowing overlap. When the GPU is slower than data prep, the ready-batch queue
fills and CPU workers naturally backpressure. When data prep is slower than the
GPU, profiler output should show the GPU waiting on the batch queue.

The CPU pipeline should be deterministic when configured for reproducibility:

- block boundaries are deterministic
- source ordering is deterministic
- tie-break rules are deterministic
- shuffle policies use explicit RNG state
- resume state records replay cursor, per-ticker cursors, sample count, batch
  count, and RNG state

The GPU loop should receive only completed `RollingTrainingBatch` objects. It
should not directly manipulate source DataFrames, ClickHouse clients, or cache
mutation state.

## Vectorized CPU Processing

The loader should use Polars/Arrow for bulk operations where they reduce Python
loop overhead:

- loading ClickHouse Arrow results
- adding dense ticker ids
- sorting a block by `sip_timestamp_us`
- grouping or filtering loaded context updates
- building candidate origin tables
- computing time features for whole arrays
- gathering token/category/numeric payloads for a batch

The rolling caches can still use compact NumPy-backed arenas/rings internally
when that is the faster or simpler representation. Polars is not required to be
the final cache storage format. The important rule is that expensive per-row
Python materialization should be delayed or avoided until the final batch
assembly boundary.

The intended data path is:

```text
ClickHouse Arrow block
  -> Polars/Arrow normalization and ordering
  -> compact cache updates
  -> RollingSampleIndex table/list
  -> vectorized batch materialization
  -> RollingTrainingBatch
```

This preserves the original stable-id cache design while making the training
implementation practical for historical data volume.

## High-Frequency Block Handling

### Step 1: Load Block

Load all event rows in the three-day date interval. Do not send a full ticker
list to ClickHouse.

### Step 2: Normalize

Convert the Arrow/Polars result into a standard in-memory representation:

```text
events_df:
  row_id
  ticker
  ticker_id
  ordinal
  sip_timestamp_us
  compact event fields
```

The loaded table is in `(ticker, ordinal)` order. This is ideal for per-ticker
cache filling.

### Step 3: Build Replay Order

Build a timestamp-ordered view for replay:

```python
replay_df = events_df.sort("sip_timestamp_us")
```

The physical event data should not be duplicated unnecessarily. Prefer row ids
or indices into the original block.

## Token Context Block Handling

Load tokenized context updates for the same three-day interval:

```text
timestamp_us >= block_start_us
timestamp_us < block_end_us
```

Normalize each context row into a cache update:

```text
context_updates_df:
  timestamp_us
  kind
  ticker_id or global key
  payload/token arrays
  source identity fields
```

Sort by timestamp. Context updates are pushed into caches only when their
timestamp is reached during replay. This preserves no-lookahead semantics.

## Merge And Replay

The block replay stream merges:

- high-frequency event rows
- ticker news token updates
- global news token updates
- SEC filing token updates
- XBRL updates, if enabled

Replay order is timestamp ascending. If timestamps tie, context update ordering
must be deterministic. A conservative tie-break is:

```text
timestamp_us, item_kind_priority, ticker, source_id
```

For each stream item:

```text
if item is context update:
    loader.push_external(...)

if item is event:
    loader.push_event(...)
    evaluate whether this event can emit a sample pointer
```

The loader never emits samples from warmup rows before `start_timestamp_us`.

## Cache Retention

The three-day block does not decide what is dropped.

Each cache applies its own retention policy after updates are pushed:

```text
event cache:
  keep configured high-frequency event count per ticker

ticker news token cache:
  keep configured item/chunk count and lookback

global news token cache:
  keep configured item/chunk count and lookback

SEC token cache:
  keep configured filing/chunk count and lookback

XBRL cache:
  keep configured item count and lookback

daily macro/global bars:
  loaded once and held for as-of lookup
```

This lets the loader use three-day I/O blocks without turning three days into a
semantic context limit.

## Sample Eligibility

An event origin is eligible only when:

- replay timestamp is at or after `start_timestamp_us`
- ticker event cache has enough high-frequency history
- required token context caches have been initialized
- sample stride rules allow this origin
- the ticker is not excluded by the training universe rules

Eligible origins should be collected into a micro-batch candidate table rather
than materialized immediately one by one.

## Output Contract

The streaming loader must not expose Polars DataFrames as its training API.
Polars and Arrow are internal loading and vectorization tools only.

The public output must match the shared `research.mlops.data` rolling contract:

- training emits `RollingTrainingBatch`
- production emits `RollingProductionBatch`
- both are keyed by the same `RollingSampleIndex` list
- model versions should not need loader-specific code paths

Each sample row represents one ticker at one selected origin event:

```text
ticker
origin_ordinal
origin_timestamp_us
```

`origin_timestamp_us` is the only absolute timestamp that should be exposed as a
model-facing identity field. Other event/context timestamps are transformed into
the shared time-feature convention.

### Training Batch Groups

The target training batch contains these model-facing groups:

| Group | Key Pattern | Shape Policy |
| --- | --- | --- |
| sample identity | `ticker`, `origin_ordinal`, `origin_timestamp_us` | `[B]` |
| origin time | `time_features[*]` | `[B]` |
| market chunks | `headers_uint8` | `[B, C, 14]` |
| market chunks | `events_uint8` | `[B, C, 128, 16]` |
| market chunk time | `chunk_time_features[*]` | `[B, C]` |
| ticker macro bars | `ticker_macro_bars` | `[B, macro_timeframes, 9]` |
| ticker macro mask | `ticker_macro_bar_mask` | `[B, macro_timeframes]` |
| global market bars | `global_market_bars` | `[B, global_symbols, macro_timeframes, 9]` |
| global market mask | `global_market_bar_mask` | `[B, global_symbols, macro_timeframes]` |
| legacy ticker/session dict | `macro_features[*]` | `[B]` |
| legacy global dict | `global_features[*]` | `[B]` |
| sample input availability | `input_availability[*]` | `[B]` boolean masks |
| ticker news tokens | `text_inputs["ticker_news"][*]` | `[B, 32, 2, 1024]` for token arrays |
| market news tokens | `text_inputs["market_news"][*]` | `[B, 64, 2, 1024]` for token arrays |
| SEC filing tokens | `text_inputs["sec_filings"][*]` | `[B, 16, 8, 1024]` for token arrays |
| XBRL fundamentals | `xbrl_inputs[*]` | `[B, 512]` per attribute |
| future macro labels | `future_macro_bars` | `[B, label_timeframes, 5]` |
| future intraday labels | `future_intraday_bars` | `[B, intraday_label_horizons, 5]` |
| legacy labels dict | `labels[*]` | usually `[B]` |
| audit context | `external_context` | source metadata, not primary model input |

The legacy ticker/session dict keeps quote state as
`session_last_bid`, `session_last_ask`, `session_last_bid_size`, and
`session_last_ask_size`. It does not persist midpoint or spread columns; those
values are deterministic from bid and ask and should be derived by a consumer
only when explicitly needed.

`C = len(context_lags)`. The current shared data package default is `C = 27`:

- 16 dense recent chunks
- 11 sparse long-history chunks with lags up to the farthest configured lag

Invalid market event windows should be filtered before materialization. The
model-facing batch should not rely on a context mask to hide invalid raw event
chunks.

Optional contexts stay zero-filled when absent. The corresponding
`input_availability` mask, for example `sec_filings_available` or
`xbrl_available`, tells the trainer whether that zero-filled group represents
missing source data for that sample.

### Production Batch Groups

`RollingProductionBatch` uses the same sample indices, identity fields,
time features, macro/global features, text inputs, XBRL inputs, and audit
context. It replaces raw market chunk bytes with cached market-encoder outputs:

```text
market_embeddings[B, C, D]
market_mask[B, C]
```

The streaming training loader should therefore build sample indices in a way
that production can reuse directly.

### Time Features

Every model-facing timestamp except `origin_timestamp_us` must use the shared
time-feature convention:

```text
time_delta_seconds
time_delta_seconds_log1p_signed
time_age_seconds_log1p
time_utc_second_of_day_sin/cos
time_utc_day_of_week_sin/cos
time_utc_day_of_year_sin/cos
time_years_since_2000
```

This applies to:

- origin time features
- market chunk origin times
- ticker news
- market news
- SEC filings
- XBRL rows

For the ticker/month SSD cache path, every stored event row must derive
cache-time event features from `timestamp_us` during cache construction. These
are the absolute calendar/session features that do not depend on a later sample
origin, such as UTC second-of-day, UTC day-of-week, UTC day-of-year,
years-since-2000, session second, session progress, and regular-hours/session
flags.

Origin-relative event-window features are computed only when a batch is
materialized because the same event row can be referenced by many origin events.
Those include `origin_timestamp_us - event_timestamp_us`, signed/log deltas, and
age features.

Raw source timestamps can remain in `external_context` for audits and debugging.

### Macro And Global Bars

The contract exposes structured 9-field bars:

```text
open, high, low, close, volume,
dollar_volume, trade_count, quote_count, vwap
```

For this streaming loader path, the source policy is to load `1d` bars and
construct the configured as-of windows from completed daily bars plus current
session state from events. Do not load independent `1w`, `1mo`, or `1y` bar rows
unless a future model explicitly requires them.

Ticker and global bar features must obey:

```text
bar_timestamp <= origin_timestamp_us
```

Future bar labels are separate targets, not context features.

### Text And XBRL Inputs

Ticker news, market news, and SEC filing text inputs come from token tables.
The streaming loader should fetch token ids, attention masks, masks, dense
category ids, and source metadata required by the shared contract. It should not
load or tokenize raw text.

XBRL inputs are structured attribute arrays under `xbrl_inputs`, with shape
`[B, xbrl_max_items]` per attribute. Category values should use dense ids from
`training_category_reference`, with `0` reserved for missing or unknown.

### Labels

Labels are part of the training batch and must remain separate from features:

- future macro labels use `future_macro_bars`
- future intraday labels use `future_intraday_bars`
- legacy compatibility values remain under `labels`

Features are selected with:

```text
timestamp <= origin_timestamp_us
```

Labels are selected strictly after the origin according to their label horizon.

## Vectorized Batch Building

Batch construction should work from a set of origin events:

```text
origins_df:
  origin_row_id
  ticker_id
  origin_ordinal
  origin_timestamp_us
```

Then compute vectorized indices:

```text
event cache positions
window start positions
window end positions
context cache ids
daily macro/global as-of row ids
future label row ids
```

High-frequency windows are gathered from per-ticker event caches. Token context
payloads are gathered by cache ids. Daily macro/global bars are gathered by
as-of index where `bar_end_us <= origin_timestamp_us`. Future labels are
gathered from in-memory future event/bar indexes after the origin.

When `batch_size` eligible origins are available:

```text
build RollingSampleIndex list
materialize RollingTrainingBatch or RollingProductionBatch
yield batch
```

The emitted batch must match the shared data package contract.

## Memory Model

Memory is split into three categories:

1. transient three-day Arrow/Polars blocks
2. persistent rolling caches
3. optional preloaded daily macro/global bar tables

After a block has been replayed, the transient block can be released. Caches
continue forward and evict only according to their own limits.

## Operational Notes

- Use three-day blocks first because they are large enough for ClickHouse
  throughput and small enough to reason about memory.
- Profile one day, three days, and five days before changing the block size.
- Prefer Arrow/Polars for bulk loading diagnostics.
- Prefer compact NumPy arrays for final cache storage if Polars object/list
  overhead becomes significant.
- Keep raw text out of this path; token tables are the training source.
- Full-load only `1d` macro/global bars unless a future model explicitly uses
  other timeframes.

## Profiler Entry Point

Use the streaming profiler to measure the implemented training path:

```powershell
python research\mlops\rolling_loader\run_streaming_training_profile.py
```

Default profiler settings:

```text
start_utc: 2019-01-05T00:00:00Z
days: 3
block_days: 3
warmup_days: 3
batch_size: 4096
batches: 4
sample_stride_events: 1
macro_timeframes: 1d
label_timeframes: 1d
q_live_contexts: ticker_news, market_news, sec_filings, xbrl
max_threads: 8
max_memory_usage: 80G
shutdown_timeout_seconds: 2.0
output_root: D:/market-data/prepared/data_provider_profiles/streaming_rolling_loader_training
```

Important profiler outputs:

```text
profile_events.jsonl
  low-level stage timing and RSS deltas:
  category reference fetch/load
  full 1d macro/global bar fetch/load
  initial token context fetch/load
  event block Arrow/Polars query
  Polars-to-NumPy ticker grouping
  event cache append
  per-context token/XBRL fetches
  context cache load
  ready index build
  batch materialization
  processed-tail trimming

batch_profiles.jsonl
  one row per emitted RollingTrainingBatch:
  batch shape summary
  materialized payload MiB
  queue wait time
  shared engine materialization metrics

summary.json
  aggregate throughput, memory, payload size, stage totals, and resolved args
  status is "interrupted" when Ctrl-C stops the profiler before completion
```

Useful variants:

```powershell
python research\mlops\rolling_loader\run_streaming_training_profile.py --days 1 --batches 2
python research\mlops\rolling_loader\run_streaming_training_profile.py --days 5 --block-days 5 --batches 8
python research\mlops\rolling_loader\run_streaming_training_profile.py --skip-xbrl
python research\mlops\rolling_loader\run_streaming_training_profile.py --simulate-gpu-seconds 0.05
python research\mlops\rolling_loader\run_streaming_training_profile.py --shutdown-timeout-seconds 5
```

## Materialized Shard Cache

For repeated training runs, the streaming loader can build SSD shards of fully
materialized `RollingTrainingBatch` samples:

```powershell
python research\mlops\rolling_loader\run_build_materialized_cache.py --one-shard
```

The builder uses the same streaming source path, but instead of yielding to a
trainer it writes complete sample tensors to disk. Shard sizing is sample-count
based:

```text
target_shard_bytes = shard_size_gib * 1024^3
estimated_sample_bytes = measured from the first materialized builder batch
raw_target_samples = floor(target_shard_bytes / estimated_sample_bytes)
shard_samples = floor(raw_target_samples / 4096) * 4096
```

This keeps shard sizes near 16 GiB while preserving 4096-sample training
alignment. The builder defaults to reduced text payloads:

```text
ticker_news_items: 8
market_news_items: 16
sec_filing_items: 4
```

Each shard is written as a temporary directory and atomically renamed after all
tensor files are flushed. Per-shard metadata includes tensor paths, shapes,
dtypes, byte sizes, SHA256 hashes, origin timestamp ranges, and human-readable
UTC dates.

Concurrency is block-local and ticker-partitioned:

```text
single writer:
  load ClickHouse block
  update rolling event/context caches
  build ready ticker blocks

worker pool:
  split active ready ticker blocks by estimated sample count
  materialize vectorized builder batches for each ticker partition

single writer:
  merge completed slices in deterministic order
  append complete materialized samples to 4096-aligned shards
  finalize shards with tmp -> final rename
  trim processed cache tails
```

Cache mutation and shard finalization stay single-threaded. Workers receive
read-only cache snapshots for their ticker partitions, which avoids concurrent
updates to the same ticker state.

By default the materialized builder derives concurrency-related caps from
`--workers`:

```text
max_pending_tasks = workers
ready_sample_cap = align_up(workers * builder_batch_size, sample_multiple)
```

For example, `--workers 64` with the default `builder_batch_size=4096` and
`sample_multiple=4096` creates a default `ready_sample_cap` of `262144`, giving
the block enough ready samples to keep 64 materialization workers active when
the data is available. These values remain overrideable for memory-constrained
runs.

## Anti-Patterns

Avoid these patterns in the training loader:

- one ClickHouse query per replay window
- one ClickHouse query per training sample
- all-ticker `ticker IN (...)` arrays with thousands of symbols
- per-ticker ordinal arrays for a whole market month
- timestamp-window scans against the current `(ticker, ordinal)` event table
- loading raw text when tokenized context tables are available
- dropping data because it left the three-day I/O block instead of because a
  cache retention policy evicted it

## Implementation Target

The final implementation should look like:

```text
initialize:
  create caches
  warm high-frequency event caches
  warm token context caches as-of start
  load full 1d macro/global bars
  start bounded source and batch queues
  start CPU loader/materialization workers

CPU loader/materialization loop:
  stream 3-day event block
  stream 3-day token context block
  normalize loaded data
  sort or index by timestamp
  replay into caches in timestamp order
  collect eligible origins
  build batches vectorized
  enqueue RollingTrainingBatch
  release transient block
  advance

GPU training loop:
  dequeue RollingTrainingBatch
  move tensors to device
  train step
  repeat until source exhausted and queues are drained
```

This keeps the guide-aligned rolling-cache behavior while using ClickHouse in
the fastest way supported by the current event table layout. The loader's public
contract remains the original stateful rolling data contract; streaming and
vectorized processing are implementation details used to make historical
training feasible.

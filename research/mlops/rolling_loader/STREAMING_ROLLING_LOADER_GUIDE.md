# Streaming Rolling Loader Guide

This guide defines the target formulation for the next rolling-loader
implementation. It keeps the existing rolling-cache idea, but changes the data
access pattern so ClickHouse is used for large aligned scans and all rolling
logic happens in memory.

## Goal

The loader should prepare training data with these properties:

- no per-sample ClickHouse queries
- no massive `ticker IN (...)` query strings for all tickers
- no timestamp-window scans against the ticker/ordinal ordered event table
- high-frequency events streamed from ClickHouse in large date blocks
- tokenized low-frequency context loaded as cache updates, not raw text
- cache retention controlled only by the cache configuration
- batch construction performed from in-memory state

The three-day load window is an I/O prefetch window. It is not the semantic
retention policy. Caches decide what stays resident and what is evicted.

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
```

High-frequency windows are gathered from per-ticker event caches. Token context
payloads are gathered by cache ids. Daily macro/global bars are gathered by
as-of index where `bar_end_us <= origin_timestamp_us`.

When `batch_size` eligible origins are available:

```text
materialize_training_batch(origins)
yield batch
```

The emitted batch should match the existing model-facing tensor contract.

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

loop:
  stream 3-day event block
  stream 3-day token context block
  normalize loaded data
  sort or index by timestamp
  replay into caches in timestamp order
  collect eligible origins
  build batches vectorized
  release transient block
  advance
```

This keeps the guide-aligned rolling-cache behavior while using ClickHouse in
the fastest way supported by the current event table layout.

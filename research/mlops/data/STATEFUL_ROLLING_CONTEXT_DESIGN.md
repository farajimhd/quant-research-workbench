# Stateful Rolling Context Data Loader

## Core Idea

The data loader should behave like the live market system, not like a stateless
batch query builder.

At any point in time, the model sees the market through a set of rolling caches:
recent market-event chunks, ticker news, global news, SEC filings, XBRL facts,
ticker bars, and global market bars. These caches are updated only when new
source data arrives. Most low-frequency data does not change for many event
origins, so it should not be rematerialized for every sample.

Training and production should use the same cache and sample-index logic. The
difference is payload type:

- Training caches hold raw trainable inputs, token ids, compact event chunks,
  category ids, and numeric rows so encoders can be trained or fine-tuned.
- Production caches hold encoder outputs whenever possible, so inference only
  gathers embeddings and runs the prediction model.

The training data loader is stateful. It can initialize at a timestamp by
loading enough prior data from the database to warm every cache, then advance
chronologically. It can resume from a timestamp/cursor by rebuilding caches from
the database rather than saving large raw cache payloads.

## Design Goals

- Match production sampling semantics during training.
- Avoid repeating low-frequency context materialization for every event sample.
- Keep fixed memory bounds for every cache.
- Support deterministic training resume from timestamp, cursors, and RNG state.
- Allow encoder fine-tuning in training while avoiding encoder recomputation in
  production.
- Keep model batches compact by passing stable cache item ids until the final
  collator/materializer step.

## Loader Parameters

The loader must be constructed from explicit parameters. These parameters define
how the initial cache is rebuilt, how much data each cache can hold, how samples
are emitted, and how training can resume deterministically.

### Time and Universe Parameters

| Parameter | Meaning |
| --- | --- |
| `start_timestamp_us` | UTC timestamp where replay or live inference begins. All caches are warm-loaded as-of this timestamp before samples are emitted. |
| `end_timestamp_us` | Optional UTC timestamp where a training/evaluation replay stops. Production leaves this unset. |
| `event_date` | Optional date shard used for efficient historical replay queries. It should be derived from timestamps, not used as the source of truth for no-lookahead logic. |
| `ticker_universe` | Explicit ticker list, universe table name, or universe query. The loader only maintains per-ticker caches for this universe. |
| `global_symbols` | Symbols used for global market context, for example `SPY`, `QQQ`, `IWM`, and `DIA`. |
| `timezone` | Timezone used only for session/calendar feature derivation. Source timestamps remain UTC. |

### Event Cache Parameters

| Parameter | Default | Meaning |
| --- | ---: | --- |
| `events_per_chunk` | 128 | Number of events in one market-structure chunk. |
| `short_context_chunks` | 16 | Number of dense recent chunk embeddings/indices. |
| `short_context_stride_chunks` | 1 | Stride between dense recent context chunks. |
| `long_context_lags` | `(32, 48, 72, 108, 162, 243, 365, 548, 822, 1233, 1850)` | Sparse older chunk lags retained for longer history. |
| `max_context_lag` | derived | Maximum lag from dense and sparse context. |
| `event_warmup_events` | derived | Minimum prior events to query during initialization. Usually `max_context_lag + events_per_chunk - 1`. |
| `event_cache_events_per_ticker` | derived | Runtime raw-event cache size per ticker. It must be at least `event_warmup_events` plus enough headroom for newly appended live events before trimming. |
| `chunk_cache_size_per_ticker` | derived | Number of chunk ids or embeddings retained per ticker. It must cover all context lags plus pending samples. |

The event cache should be warm at `start_timestamp_us`. If sufficient prior
events exist, the first emitted sample at or after `start_timestamp_us` can use
full context immediately.

### Low-Frequency Cache Size Parameters

| Parameter | Default | Meaning |
| --- | ---: | --- |
| `global_news_cache_size` | 64 | Latest global market-news items as-of the origin. |
| `ticker_news_cache_size` | 32 | Latest ticker-related news items per ticker. |
| `sec_filing_cache_size` | 16 | Latest SEC filing text items per ticker. |
| `xbrl_cache_size` | 512 | Latest XBRL rows per ticker. |
| `ticker_macro_bar_cache_size` | derived | Number of ticker bar states retained. Should cover all configured as-of macro horizons. |
| `global_market_bar_cache_size` | derived | Number of global market bar states retained for each global symbol. |
| `arena_retention_samples` | derived | Number of pending samples or batches whose referenced cache payloads must remain available before garbage collection. |

These sizes bound memory. They also define the tensor shapes used by the final
collator when raw training payloads are materialized.

### Text and Category Parameters

| Parameter | Default | Meaning |
| --- | ---: | --- |
| `text_tokenizer_model` | `Qwen/Qwen3-0.6B` | Tokenizer/model family used to produce text token ids. |
| `text_max_tokens` | 1024 | Token width per text chunk. |
| `news_token_chunks` | 2 | Maximum token chunks per news item. |
| `market_news_token_chunks` | 2 | Maximum token chunks per global news item. |
| `sec_token_chunks` | 8 | Maximum token chunks per SEC text item. |
| `news_max_channels` | 8 | Maximum news channel category ids per item. |
| `news_max_provider_tags` | 16 | Maximum provider tag category ids per item. |
| `news_max_quality_flags` | 8 | Maximum news quality flag ids per item. |
| `sec_max_quality_flags` | 8 | Maximum SEC text quality flag ids per item. |
| `category_reference_table` | `training_category_reference` | Dense category-id mapping table. Id `0` is reserved for missing or unknown. |

Category tables are loaded during initialization and can be refreshed only at
well-defined dataset boundaries. Changing category ids mid-run would make
resume and model interpretation ambiguous.

### Bar and Label Parameters

| Parameter | Meaning |
| --- | --- |
| `macro_bars_table` | Source table for historical ticker and global macro bars. |
| `macro_timeframes` | Timeframes used as as-of features, for example `1d`, `1w`, `1mo`, `1y`. |
| `label_timeframes` | Timeframes used for future macro labels. |
| `intraday_label_horizons` | Short future bar horizons such as `100ms`, `250ms`, `1s`, `5s`, through multi-hour horizons. |
| `bar_feature_keys` | As-of bar fields. Current design uses `open`, `high`, `low`, `close`, `volume`, `dollar_volume`, `trade_count`, `quote_count`, `vwap`. |
| `future_bar_feature_keys` | Future label fields. Current design uses `open`, `close`, `high`, `low`, `volume`. |

### Replay, Sampling, and Batching Parameters

| Parameter | Meaning |
| --- | --- |
| `sample_stride_events` | Event-origin stride for historical replay. Production usually emits when a ticker update creates a ready sample. |
| `batch_size` | Number of sample indices gathered before training materialization. |
| `shuffle_policy` | Whether samples are emitted chronologically, shuffled within a day/block, or scheduled by ticker. |
| `seed` | Base seed for deterministic sampling/shuffle behavior when reproducibility is requested. |
| `repeatable_randomness` | If true, every resume/replay must reproduce the same sample order and stochastic choices. If false, resumes remain stateful but new epochs can get fresh randomness. |
| `max_ready_samples` | Optional cap for profiling or smoke tests. |

### Resume Parameters

| Parameter | Meaning |
| --- | --- |
| `resume_timestamp_us` | Timestamp from which caches should be rebuilt. Usually equal to the next unprocessed origin timestamp. |
| `per_ticker_last_processed_ordinal` | Cursor map used to continue without replaying already-consumed samples. |
| `rng_state` | Serialized RNG state for deterministic replay. |
| `sample_count` | Number of samples emitted so far. |
| `batch_count` | Number of batches emitted so far. |
| `source_watermarks` | Last consumed timestamp or source id for events, news, SEC, XBRL, and bars. |
| `pending_sample_ids` | Optional set of already-assembled samples that must be emitted after resume. |

Resume state should be small. Raw event arrays and low-frequency payload arrays
should be rebuilt from the database using these parameters and cursors.

### Payload Mode Parameters

| Parameter | Training Value | Production Value |
| --- | --- | --- |
| `market_payload_mode` | raw compact chunks or chunk ids | market encoder embeddings |
| `text_payload_mode` | token ids and metadata ids | text encoder embeddings |
| `xbrl_payload_mode` | numeric rows and category ids | XBRL encoder embeddings |
| `bar_payload_mode` | numeric bar rows | bar encoder embeddings |

This separation is critical. Training keeps raw inputs when encoders are
trainable. Production stores embeddings to minimize repeated encoder inference.

## Cache Categories

### High-Frequency Event Cache

The event cache is per ticker and updates whenever a market event arrives.

Training payload:

- raw compact event rows
- rolling 128-event chunk ids
- chunk origin ordinal and timestamp
- enough prior events to satisfy the largest context lag

Production payload:

- latest compact events needed to build new chunks
- rolling 128-event chunk ids
- cached market-encoder embeddings for each completed chunk

Important rule: during normal initialization, the event cache is warm-loaded
from prior database data. The condition "if ticker has enough events" only
matters for true cold starts, newly listed tickers, long inactive tickers, or
intentional cold-cache tests.

### Global News Cache

One global cache holds the latest market-wide news items as-of the current
origin timestamp.

Default size:

- latest 64 market news items

Training payload:

- token ids
- attention masks
- dense metadata category ids
- source timestamp features

Production payload:

- text encoder embeddings
- source timestamp features or their encoded representation

### Per-Ticker News Cache

Each ticker has a bounded cache of its latest ticker-related news.

Default size:

- latest 32 ticker news items

Training payload:

- token ids
- attention masks
- dense metadata category ids
- source timestamp features

Production payload:

- text encoder embeddings
- source timestamp features or their encoded representation

### SEC Filing Text Cache

Each ticker has a bounded cache of its latest SEC filing text items.

Default size:

- latest 16 SEC text items

Training payload:

- token ids
- attention masks
- form/text-kind/quality category ids
- accepted timestamp features

Production payload:

- text encoder embeddings
- accepted timestamp features or their encoded representation

### XBRL Cache

Each ticker has a bounded cache of its latest XBRL rows.

Default size:

- latest 512 XBRL items

Training payload:

- numeric XBRL value
- fiscal year and period-end features
- accepted timestamp features
- dense category ids for taxonomy, tag, unit, form, row kind, and location
- mapping confidence

Production payload:

- XBRL encoder embeddings
- timestamp features or their encoded representation

### Ticker Macro Bar Cache

Each ticker has cached bar context. Bars should be updated only when the as-of
state changes.

Training payload:

- numeric bar fields
- time features

Production payload:

- bar encoder embeddings

### Global Market Bar Cache

Global symbols, such as broad market ETFs or indices, have cached bar context.

Training payload:

- numeric global bar fields
- time features

Production payload:

- bar encoder embeddings

## Stable IDs vs Ring Slots

Samples should never point directly at mutable ring-buffer slots.

Use stable item ids:

```text
sample.ticker_news_ids = [101, 102, ...]
sample.xbrl_ids = [5001, 5002, ...]
sample.event_chunk_ids = [90001, 90002, ...]
```

Do not use mutable slot positions:

```text
sample.ticker_news_slots = [0, 1, ...]  # unsafe
```

Ring slots can be overwritten. Stable ids preserve the exact context visible at
the sample origin. The cache can keep a bounded latest-id ring, while an arena
or item store holds id-to-payload mappings until no pending sample references
them.

## Initialization

Given a start timestamp:

1. Resolve the active ticker universe.
2. Query enough prior events for each ticker to satisfy:
   - `events_per_chunk`
   - maximum context lag
   - any required carryover window
3. Build event caches and rolling chunk ids.
4. Load low-frequency context rows with `timestamp <= start_timestamp`:
   - global news
   - ticker news
   - SEC filings
   - XBRL rows
   - ticker macro bars
   - global market bars
5. Fill each bounded cache to its configured limit.
6. Set replay/live cursors.
7. Begin sample generation or inference.

This means production can start inference immediately after warmup when enough
historical context exists.

## Chronological Advance

The loader advances in time order.

At each step:

1. Append newly available market events to the per-ticker event cache.
2. Build new 128-event chunks when new chunk origins are available.
3. In production, encode each new chunk immediately and store its embedding.
4. Append newly available low-frequency context rows to their caches.
5. Evict oldest items from each bounded latest-id ring.
6. Create sample indices from current cache state.
7. Let the collator/materializer gather payloads by stable id.

## Training Samples

A training sample should be a lightweight immutable index, not a dense tensor.

It should contain:

- ticker
- origin timestamp
- origin ordinal
- market event chunk ids for all short and long context windows
- global news item ids
- ticker news item ids
- SEC filing item ids
- XBRL item ids
- ticker macro bar state ids or values
- global market bar state ids or values
- label/future target references

The final training collator converts these ids into tensors for the model.

## Production Samples

A production sample uses the same index shape, but most ids point to cached
embeddings instead of raw inputs.

It should contain:

- market chunk embedding ids
- ticker news embedding ids
- global news embedding ids
- SEC embedding ids
- XBRL embedding ids
- ticker bar embedding ids
- global bar embedding ids
- current sample time features

The prediction model gathers embeddings and runs inference. Encoders are run
only when a new source item enters a cache.

## Resume and Reproducibility

The loader should not persist large raw event caches. Event caches can be
reconstructed from the database as long as the resume state has the current
timestamp and cursors.

Persist:

- current replay timestamp
- current event date
- per-ticker last processed ordinal or timestamp
- active ticker universe
- RNG state
- ticker scheduling/shuffle state
- sample count and batch count
- low-frequency cache watermarks
- pending sample ids if a batch is already assembled

Rebuild from database:

- event cache
- rolling event chunk ids
- global news cache
- ticker news cache
- SEC filing cache
- XBRL cache
- ticker macro bars
- global market bars

For deterministic resume, the source tables must be stable for the replay
range. If source tables changed, the rebuilt caches may differ from the original
run.

## Training vs Production Payloads

| Cache | Training Payload | Production Payload |
| --- | --- | --- |
| Market events | compact event chunks | market encoder embeddings |
| Ticker news | token ids + metadata ids | text encoder embeddings |
| Global news | token ids + metadata ids | text encoder embeddings |
| SEC filings | token ids + metadata ids | text encoder embeddings |
| XBRL | numeric rows + category ids | XBRL encoder embeddings |
| Ticker bars | numeric bar rows | bar encoder embeddings |
| Global bars | numeric bar rows | bar encoder embeddings |

## Why This Replaces Dense External Materialization

The earlier profiler path materialized low-frequency context as dense per-sample
tensors. That repeats the same SEC/XBRL/news context many times when the cache
state has not changed.

The stateful design changes the work from:

```text
for every sample:
    rebuild text tensors
    rebuild XBRL tensors
    rebuild bar tensors
```

to:

```text
when new source item arrives:
    add it to cache once

for every sample:
    store ids to the current cache state

at collator time:
    gather ids into tensors only for the batch
```

This is both faster and closer to production.

## Open Implementation Decisions

- How long the cache arena keeps old payloads after latest-id rings evict them.
- Whether training batches should materialize text/XBRL tensors immediately or
  keep ids until a model-specific collator runs.
- How to represent bar state ids when bars are cheap numeric features but may
  later be encoded by a separate bar encoder.
- How to coordinate source-table changes with deterministic replay.
- Whether to precompute encoder embeddings for frozen encoders during later
  training phases.

# Direct Ticker Streaming Loader

This is the first-class data path for `packed_market_model`.

The loader streams ticker/month event ranges directly from ClickHouse and emits packed blocks to the GPU. It does **not** use the old daily-index cache, materialized cache, indexed cache, or any batch-shard cache.

## Goal

Feed the model with packed ticker-local blocks fast enough that the model becomes the bottleneck:

```text
ClickHouse events_YYYY + context tables
  -> ticker stream workers
  -> ready packed block queue
  -> GPU trainer
```

The loader does not build repeated event windows. The model receives:

```text
events:           [T, F]
origin_positions: [M]
labels:           [M, label_count]
```

`origin_positions` point into `events`.

## Worker Abstraction

Each worker owns one ticker/month stream at a time.

```text
worker_00 -> ticker=AAPL month=2019-02 ordinal chunks in order
worker_01 -> ticker=MSFT month=2019-02 ordinal chunks in order
worker_02 -> ticker=illiquid ticker, then next ticker, ...
```

Within one worker:

1. Load the next ticker/month plan.
2. Fetch one ordinal block from ClickHouse.
3. Build one packed training block in memory.
4. Push the block to the ready queue.
5. Release memory that is no longer needed.
6. Continue to the next ordinal block for the same ticker.

Across workers, blocks are consumed by the trainer as soon as they are ready. Global ordering across tickers is not required for this training path, but each ticker stream remains ordinal ordered.

## Planning

The planner reads `events_ticker_day_index`:

```sql
SELECT
  ticker,
  formatDateTime(toStartOfMonth(source_date), '%Y-%m') AS month,
  sum(event_count) AS event_count,
  min(first_ordinal) AS first_origin_ordinal,
  max(last_ordinal) AS last_origin_ordinal,
  min(first_sip_timestamp_us) AS first_timestamp_us,
  max(last_sip_timestamp_us) AS last_timestamp_us
FROM market_sip_compact.events_ticker_day_index
WHERE source_date >= month_start
  AND source_date < month_end
GROUP BY ticker, month
ORDER BY event_count DESC, ticker
```

The plan unit is `TickerMonthPlan`.

## Block Boundaries

Block size is based on origin count and memory, not wall-clock time:

```text
target_origin_count_per_block = 65,536
event_context_rows            = 1,024
future_event_guard_rows       = 262,144
ready_queue_blocks            = 8
```

For one origin block:

```text
origin_start_ordinal -> origin_end_ordinal
```

The worker fetches:

```text
fetch_start_ordinal = max(1, origin_start_ordinal - (event_context_rows - 1))
fetch_end_ordinal   = origin_end_ordinal + future_event_guard_rows
```

The model input uses only rows:

```text
fetch_start_ordinal <= ordinal <= origin_end_ordinal
```

Rows after `origin_end_ordinal` are label support only and are never exposed as model input. This prevents lookahead.

## Vectorized Processing

For one ticker, events are already sorted by ordinal. The worker converts the fetched Arrow table to Polars/NumPy and uses vectorized operations:

| Task | Method |
|---|---|
| `origin_event_index` | `searchsorted(event_ordinals, origin_ordinals)` |
| future horizons | `searchsorted(event_timestamp_us, origin_timestamp_us + horizon_us)` |
| event counts | end index minus origin index |
| size sums | prefix sums |
| last future prices | gather `end_index - 1` where available |
| masks | horizon is valid only when required support rows exist |

There are no per-origin Python loops in the hot path.

## Worker Memory Management

Each worker has its own bounded memory manager.

Rules:

- A worker keeps only the current ticker/month plan and the current ordinal block.
- After a block is emitted, future-support rows are released unless they are needed as left context for the next block.
- If memory exceeds the worker limit, the oldest cached context segment is evicted.
- The ready queue is bounded; if the GPU is behind, workers block instead of letting memory grow.

The intended steady-state memory is:

```text
worker memory ~= current fetched Arrow/Polars block + temporary label arrays
global memory ~= ready_queue_blocks * packed block size
```

## Shared Contexts

The first implementation focuses on event stream and event-derived intraday labels because this is the bottleneck being tested.

Sparse contexts are handled by the same abstraction:

```text
ticker news embeddings
market news embeddings
SEC embeddings
XBRL
corporate actions
daily/global bars
scanner
```

They should be read as sorted streams and attached by refs:

```text
end_index   = searchsorted(context_timestamp_us, origin_timestamp_us, side='right')
start_index = max(0, end_index - max_items)
```

Missing context is represented by a short/empty ref range. Padding and masks are applied when the model gathers context.

Scanner is market-wide and cannot be computed inside one ticker worker. It is
handled by a ClickHouse sidecar:

```text
events_YYYY
  -> run-scoped 1s scanner bars in ClickHouse
  -> global loader fetches completed scanner seconds
  -> model receives scanner context by as-of time
```

Rules:

- base scanner bars are `1s`
- default sidecar window is `15 minutes`
- rows are persisted in `market_sip_compact.packed_scanner_sidecar_bars`
  under a `run_id`
- ticker workers still calculate ticker-local intraday labels outside
  ClickHouse
- the scanner fetch never reads the current in-progress second

The completed-bar rule is:

```text
bar_end_timestamp_us <= floor(origin_timestamp_us, 1s)
```

If an origin lands inside `[10:00:04.000, 10:00:05.000)`, the global scanner
fetch can use bars ending at `10:00:04.000` and earlier, never the bar ending at
`10:00:05.000`.

## Concurrency

```text
planner thread
  -> ticker/month plan queue

N ticker stream workers
  -> ClickHouse fetch
  -> vectorized block processing
  -> ready block queue

scanner sidecar worker
  -> builds 15-minute 1s market scanner windows in ClickHouse
  -> fetches completed scanner bars for block origin ranges

trainer
  -> consumes ready blocks as GPU batches
```

Recommended first workstation defaults:

```text
ticker_workers      = 24
ready_queue_blocks  = 8
clickhouse_threads  = 4 per query
max_active_queries  = 24
target_origins      = 65,536
```

The correct tuning target is:

```text
ready_queue_blocks remains > 0 while GPU is training
```

If the ready queue is usually full, the model is the bottleneck. If the queue is empty, ClickHouse fetch or CPU processing is the bottleneck.

## Resume and Reproducibility

The loader records:

```text
plan_index
worker_id
ticker
month
origin_start_ordinal
origin_end_ordinal
emitted_sequence_id
```

The ready order can be nondeterministic across workers. For exact reproducibility, run with:

```text
ticker_workers = 1
shuffle_plans = false
```

For normal training, nondeterministic ready order is acceptable and improves GPU utilization.

## Profiling Metrics

The loader and trainer must log:

```text
plan_seconds
event_fetch_seconds
event_rows_per_second
process_seconds
label_seconds
ready_queue_wait_seconds
trainer_wait_seconds
gpu_forward_seconds
gpu_backward_seconds
block_origin_count
block_event_count
worker_peak_memory_mib
process_rss_gib
```

These metrics decide whether direct streaming is sufficient or whether a narrow packed cache is still needed.

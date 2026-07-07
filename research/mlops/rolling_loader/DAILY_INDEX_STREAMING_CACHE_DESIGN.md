# Daily-Indexed Streaming Cache Builder Design

This guide defines the replacement cache-builder architecture for the rolling
loader package.

The goal is to build reusable ticker/month SSD cache packages by streaming
ordered source ranges out of ClickHouse, processing them in memory with
vectorized CPU code, and writing source-aligned cache files. The builder must be
clear, retryable, auditable, and efficient on the workstation.

This design intentionally avoids a hidden global planner. The source of truth
for market-event work is the daily ticker index:

```text
market_sip_compact.events_ticker_day_index
```

Each row in that table is a directly understandable unit of work:

```text
one ticker
one source_date
one ordinal range
one expected event row count
```

The same pipeline abstraction is used for events, labels, macro bars, text
embeddings, SEC embeddings, XBRL, and corporate actions.

## Design Name

Use this name in code, manifests, logs, and docs:

```text
daily_index_streaming_cache
```

Long form:

```text
Daily-Indexed, Modality-Pipelined Streaming Cache Builder
```

## Core Principles

1. Daily index rows are the unit of truth.
2. Cache storage is ticker/month based.
3. Fetches use efficient source-table predicates.
4. Processing is vectorized in memory.
5. Writes are bounded, atomic, and manifest-backed.
6. Every modality uses the same fetch/process/write pipeline shape.
7. Terminal panels show fixed worker rows based on configured worker counts.
8. Training and validation splits are not baked into the cache.
9. The builder must fail on integrity issues instead of writing questionable
   data.

## Inputs

### Event Daily Index

The event planner reads:

```text
events_ticker_day_index
```

Required columns:

| Column | Meaning |
| --- | --- |
| `ticker` | Uppercase ticker symbol. |
| `source_date` | New York local market source/session date. |
| `event_count` | Number of compact event rows for this ticker/date. |
| `first_ordinal` | First per-ticker event ordinal for the date. |
| `last_ordinal` | Last per-ticker event ordinal for the date. |
| `next_ordinal` | Expected next ordinal after the date. |
| `first_sip_timestamp_us` | First event timestamp in UTC microseconds. |
| `last_sip_timestamp_us` | Last event timestamp in UTC microseconds. |
| `build_step` | Event ingest build step for audit/reproducibility. |
| `built_at` | Index build timestamp. |

### Other Source Tables

Other modalities use their own source tables but follow the same pattern:

| Modality | Source identity | Time key | Cache key |
| --- | --- | --- | --- |
| Events | `ticker + ordinal` | `timestamp_us` | `ticker/month` |
| Intraday labels | `ticker + origin_ordinal` | `origin_timestamp_us` | `ticker/month` |
| Macro/daily bars | `ticker + bar_date + family` | bar date/time | `ticker/month` |
| News embeddings | source news id/chunk id | availability timestamp | ticker/global month |
| SEC embeddings | CIK/accession/document/chunk id | accepted timestamp | ticker/month |
| XBRL | fact identity | availability timestamp | ticker/month |
| Corporate actions | action id/type/date | ex/effective timestamp | ticker/month |

All source timestamps stored in cache metadata must be UTC. Any New York session
logic must be explicit and never replace the UTC identity fields.

## Output Layout

There is no train/validation split in the cache. The downstream loader chooses
periods and sample plans.

Recommended layout:

```text
cache_root/
  manifest.json
  month=YYYY-MM/
    global/
      manifest.json
      market_news_embeddings/
        part_000000.parquet
      global_macro_bars/
        part_000000.parquet
    ticker_hash=XX/
      ticker=ABC/
        manifest.json
        daily_index.parquet
        events/
          part_000000.parquet
          part_000001.parquet
        origins/
          part_000000.parquet
        intraday_labels/
          part_000000.parquet
        macro_bars/
          part_000000.parquet
        news_embeddings/
          part_000000.parquet
        sec_embeddings/
          part_000000.parquet
        xbrl/
          part_000000.parquet
        corporate_actions/
          part_000000.parquet
        context_indices/
          part_000000.parquet
```

Files are immutable. Writers must write to a temporary path first and then
rename atomically after the file and sidecar metadata are complete.

## Pipeline Abstraction

Each modality owns one pipeline:

```text
Plan -> Fetch Queue -> Fetch Workers -> Process Queue -> Process Workers -> Write Queue -> Write Workers -> Manifest/Audit
```

The implementation should expose a common interface:

```text
ModalityAdapter
  name
  plan_jobs()
  fetch(job)
  process(fetched_payload)
  write(processed_payload)
  audit_written_payload()
```

Core runtime components:

| Component | Responsibility |
| --- | --- |
| `PipelineCoordinator` | Starts/stops all modality pipelines, tracks global limits, handles graceful shutdown. |
| `ModalityPipeline` | Owns queues and workers for one modality. |
| `WorkerState` | Fixed terminal/reporting state for one worker slot. |
| `QueueState` | Queue depth, row count, byte estimate, oldest job age. |
| `CacheManifestWriter` | Writes root/month/ticker manifests and successful part metadata. |
| `RichPipelineReporter` | Non-blinking terminal layout with fixed rows per configured worker count. |
| `AuditRecorder` | Writes audit results and fails the build on strict integrity violations. |

## Global Safety Limits

Per-modality worker counts are not enough. The coordinator must enforce global
limits so the builder cannot overwhelm ClickHouse, RAM, or the SSD.

Recommended parameters:

| Parameter | Purpose |
| --- | --- |
| `--max-active-clickhouse-queries` | Upper bound across all fetch workers. |
| `--max-fetched-queue-gib` | Backpressure before fetch workers submit more rows. |
| `--max-processed-queue-gib` | Backpressure before process workers emit more write payloads. |
| `--max-rss-gib` | Process-wide soft RSS limit. |
| `--max-active-writers` | Global writer concurrency cap. |
| `--shutdown-timeout-seconds` | Time allowed for graceful flush after Ctrl+C. |

If a queue exceeds its configured byte budget, upstream workers should block.
They should not keep fetching and hope the writer catches up.

## Event Planning

### Daily Work Unit

The base event job is one row from `events_ticker_day_index`:

```text
EventDailyUnit:
  month
  source_date
  ticker
  event_count
  first_ordinal
  last_ordinal
  next_ordinal
  first_sip_timestamp_us
  last_sip_timestamp_us
```

This unit is used for:

- fetch identity
- progress accounting
- retries
- daily audit
- per-ticker/month completion checks

### Ticker/Month Group

The cache is written per ticker/month. Daily units are grouped by:

```text
month = YYYY-MM from source_date
ticker = ticker
```

For a ticker/month group:

```text
origin_first_ordinal = first_ordinal of the first available daily unit
origin_last_ordinal  = last_ordinal of the last available daily unit
```

### Past Event Context

The first origins in a month need prior events. The builder therefore fetches
some events before `origin_first_ordinal`.

Use explicit parameters:

```text
event_context_rows = 1024 by default for the first implementation
event_context_guard_rows = 0 by default
```

`event_context_rows` is the total event-window length available to the loader
for the first origin. It includes the current origin event. With
`event_context_rows=1024`, the first origin needs:

```text
1023 prior events + current origin event
```

The fetch start is therefore:

```text
fetch_start_ordinal =
  max(1, origin_first_ordinal - event_context_rows + 1 - event_context_guard_rows)

fetch_end_ordinal = origin_last_ordinal
```

For example:

```text
origin_first_ordinal = 50,000
event_context_rows = 1,024
event_context_guard_rows = 0

fetch_start_ordinal = 48,977
fetch_end_ordinal   = origin_last_ordinal
```

The first origin window can then use:

```text
48,977 ... 49,999 = 1,023 prior events
50,000            = current origin event
```

Rows are tagged:

```text
ordinal < origin_first_ordinal  -> context_only = true
ordinal >= origin_first_ordinal -> origin-eligible month row
```

The loader should use the configured `event_context_rows` as the total event
window length. `event_context_guard_rows` is optional extra prior history for
diagnostics or future experiments and defaults to zero.

### Cross-Month and Cross-Year Context

The first day of a month can require event context from the previous month or
previous year. That is expected.

The semantic fetch is:

```sql
WHERE ticker = {ticker}
  AND ordinal BETWEEN {fetch_start_ordinal} AND {fetch_end_ordinal}
ORDER BY ordinal
```

Physical implementation may split it into two or more fetches if that is more
efficient with yearly event tables:

```text
context fetch: prior range
month fetch: origin range
merge in memory by ordinal
```

After merging, the event stream must be strictly ordered by ordinal.

### Huge Daily Units

If one `events_ticker_day_index` row is too large for memory, split only that
daily unit by ordinal:

```text
split_start = first_ordinal
split_end   = min(last_ordinal, split_start + max_fetch_event_rows - 1)
```

This is a memory safety rule, not a hidden planner. The retry identity must
still include the original ticker/date plus the split ordinal range.

## Event Fetch

Fetch workers should do only ClickHouse reads. They should not process or write.

Recommended event query shape:

```sql
SELECT
    ticker,
    ordinal,
    timestamp_us,
    event_meta,
    price_primary_int,
    price_secondary_int,
    size_primary,
    size_secondary,
    exchange_primary,
    exchange_secondary,
    condition_token_1,
    condition_token_2,
    condition_token_3,
    condition_token_4,
    condition_token_5,
    conditions_packed
FROM {events_source}
WHERE ticker = {ticker}
  AND ordinal BETWEEN {start_ordinal} AND {end_ordinal}
ORDER BY ordinal
```

Fetch output should be Arrow or Polars. Python row iteration is not acceptable
for the hot path.

## Event Process

Event processing should be vectorized with Polars and/or NumPy.

Required event process steps:

1. Verify ticker is constant.
2. Verify ordinals are strictly increasing after merge.
3. Verify no duplicate ordinal exists.
4. Add `context_only`.
5. Add UTC absolute time features from `timestamp_us`.
6. Preserve raw compact event fields without model-specific normalization.
7. Build origin rows from daily units:
   - `ticker`
   - `origin_ordinal`
   - `origin_timestamp_us`
   - `source_date`
   - `origin_row_position`
8. Build range metadata:
   - context ordinal bounds
   - origin ordinal bounds
   - part ordinal bounds

The process worker must not encode event chunks. Event-window materialization is
a loader responsibility, constrained by the cached event lookback capacity.

## Event Write

Writes are ticker/month scoped.

Recommended event part metadata:

| Field | Meaning |
| --- | --- |
| `cache_version` | Builder schema/version. |
| `month` | `YYYY-MM`. |
| `ticker` | Uppercase ticker. |
| `part_id` | Monotonic part id within ticker/month. |
| `row_count` | Number of rows in the part. |
| `context_row_count` | Rows with `context_only=true`. |
| `origin_row_count` | Rows with `context_only=false`. |
| `ordinal_min` | First ordinal in part. |
| `ordinal_max` | Last ordinal in part. |
| `timestamp_min_us` | First timestamp in part. |
| `timestamp_max_us` | Last timestamp in part. |
| `source_daily_units` | List or hash of daily units covered. |
| `query_ids` | ClickHouse query ids used to fetch the rows. |
| `schema_fingerprint` | Data schema fingerprint. |

`CHUNK_SIZE` controls the max number of event rows per saved event part. The
writer accumulates rows for a ticker/month and flushes when the buffer reaches
`CHUNK_SIZE`. At ticker/month completion, it flushes the final partial part.

## Intraday Labels

Intraday labels are keyed by origin:

```text
ticker
origin_ordinal
origin_timestamp_us
```

The first implementation can compute labels from the processed event stream for
the same ticker/day/month when available. If labels need future events beyond
the current month, the label planner must request a future context extension
explicitly and tag those rows as label-only context.

Label outputs should remain source-aligned and horizon-aligned:

```text
intraday_labels/
  part_000000.parquet
```

Recommended columns:

```text
ticker
origin_ordinal
origin_timestamp_us
horizon_us
trade_open
trade_close
trade_high
trade_low
trade_size_sum
trade_event_count
bid_open
bid_close
bid_high
bid_low
bid_size_sum
bid_event_count
ask_open
ask_close
ask_high
ask_low
ask_size_sum
ask_event_count
condition_halt_pause_flag
condition_resume_flag
condition_news_risk_flag
condition_luld_limit_state_flag
future_news_available_flag
future_sec_available_flag
available
```

The label process step must be vectorized. Per-origin Python loops over future
windows are not acceptable for large runs.

## Macro and Daily Bars

Macro/daily bars use the same pipeline shape but have different source units.

Plan units:

```text
ticker
month
required_bar_start_date
required_bar_end_date
```

The builder should save only the lookback and forward range required by cache
parameters, not the full historical bar stream.

Bars should use consistent families:

```text
trade
quote_bid
quote_ask
```

For each family, preserve non-redundant raw bar values:

```text
open
close
high
low
size_sum
event_count
start_timestamp_us
end_timestamp_us
```

Absolute UTC time features may be stored alongside bars or in an aligned
sidecar file. They must remain row-aligned.

## Text, SEC, XBRL, and Corporate Context

Sparse context modalities use availability/as-of time.

The cache should store source rows plus enough historical context before the
month so the loader can build as-of contexts for the first origin in the month.

Recommended sparse-context planning formula:

```text
context_start_timestamp_us = month_start_timestamp_us - modality_lookback_window
origin_start_timestamp_us  = month_start_timestamp_us
origin_end_timestamp_us    = month_end_timestamp_us
```

For each row, store:

```text
ticker or global key
source identity fields
availability_timestamp_us
embedding or numeric payload
absolute UTC time features
source metadata needed for audit
```

Do not duplicate sparse context per origin in the cache. The loader materializes
as-of context using the source rows and context indices.

## Modality Parameters

Each modality has independent worker counts:

```text
--event-fetch-workers
--event-process-workers
--event-write-workers

--label-fetch-workers
--label-process-workers
--label-write-workers

--macro-fetch-workers
--macro-process-workers
--macro-write-workers

--news-fetch-workers
--news-process-workers
--news-write-workers

--sec-fetch-workers
--sec-process-workers
--sec-write-workers

--xbrl-fetch-workers
--xbrl-process-workers
--xbrl-write-workers

--corporate-fetch-workers
--corporate-process-workers
--corporate-write-workers
```

Defaults should reflect workload severity. Events and labels are heavy. Sparse
contexts are usually lighter.

Suggested first-run defaults on the workstation:

| Modality | Fetch | Process | Write |
| --- | ---: | ---: | ---: |
| Events | 16 | 16 | 8 |
| Intraday labels | 8 | 24 | 8 |
| Macro bars | 4 | 8 | 4 |
| News embeddings | 4 | 4 | 4 |
| SEC embeddings | 4 | 4 | 4 |
| XBRL | 4 | 8 | 4 |
| Corporate actions | 2 | 4 | 2 |

The actual implementation should allow disabling modalities:

```text
--data-groups events,intraday_labels,macro_bars,news,sec,xbrl,corporate_actions
```

## Rich Terminal Design

The terminal must use fixed worker rows. If a modality has 16 fetch workers,
its panel must reserve 16 fetch worker rows or a stable compact equivalent.
Rows should not appear/disappear during the run.

Required panels:

1. Summary
2. Events
3. Intraday Labels
4. Macro Bars
5. News Embeddings
6. SEC Embeddings
7. XBRL
8. Corporate Actions
9. Messages / Errors

Each modality panel should show fetch, process, and write state side by side:

```text
Events
Worker  Fetch                         Process                       Write                         Current
F01     8.2M/11.0M rows  220k/s       -                             -                             2019-09 AAPL
F02     idle                          -                             -                             -
P01     -                             4.1M/8.2M rows  310k/s        -                             2019-09 MSFT
W01     -                             -                             1.3GB/2.0GB  420MB/s          AAPL part_0002
```

Summary panel should show:

```text
period
cache_id
data_groups
elapsed
ETA from this run only
total expected event rows
fetched rows
processed rows
written rows
queue depths
active ClickHouse queries
RSS GiB
errors
last completed ticker/date
```

The reporter must preserve last-known useful values instead of flickering
between numbers and zeros.

## Progress Accounting

Event progress is based on `events_ticker_day_index`:

```text
expected_event_rows = sum(event_count)
expected_daily_units = count(index rows)
```

Report:

```text
daily_units_fetched / expected_daily_units
event_rows_fetched / expected_event_rows
event_rows_processed / expected_event_rows
event_rows_written / expected_event_rows
```

Sparse modalities use source row counts and bytes:

```text
source_rows_fetched
source_rows_processed
source_rows_written
```

ETA must be computed from work completed during the current process lifetime,
not from historical cache completion.

## Audit Requirements

### Event Audits

For every ticker/day:

1. Fetched origin rows equal `event_count`.
2. First origin ordinal equals `first_ordinal`.
3. Last origin ordinal equals `last_ordinal`.
4. Origin timestamps are within daily index bounds.
5. Ordinals are strictly increasing.
6. No duplicate ordinal exists.

For every ticker/month initial context:

1. Context rows, if available, end at `origin_first_ordinal - 1`.
2. Context row count is:

```text
min(event_context_rows - 1 + event_context_guard_rows, origin_first_ordinal - 1)
```

For every ticker/month:

1. Event parts cover the full fetched ordinal range.
2. Part boundaries do not lose or duplicate rows.
3. Origin rows match the union of daily index rows.
4. Manifest row counts match physical file row counts.

### Label Audits

For sampled origins:

1. Label identity matches an origin row.
2. Future windows start strictly after the origin timestamp.
3. Intraday horizons do not cross the allowed session boundary unless the label
   definition explicitly says they may.
4. Price and size fields are decoded consistently with event scale metadata.
5. Availability flags for news/SEC use future horizon intervals, not current
   context.

### Sparse Context Audits

For sampled origins:

1. Context rows have `availability_timestamp_us <= origin_timestamp_us`.
2. Future context leakage is impossible by construction.
3. Latest backward rows are selected first.
4. Padding appears only when fewer historical rows exist than requested.
5. Source identity fields can be queried back in ClickHouse.

## Manifests

Every run writes:

```text
cache_root/manifest.json
cache_root/build_log.jsonl
cache_root/errors.jsonl
```

Every month writes:

```text
cache_root/month=YYYY-MM/manifest.json
```

Every ticker/month writes:

```text
cache_root/month=YYYY-MM/ticker_hash=XX/ticker=ABC/manifest.json
```

Ticker manifest fields:

```text
cache_id
cache_version
month
ticker
data_groups
event_context_rows
event_context_guard_rows
origin_first_ordinal
origin_last_ordinal
fetch_start_ordinal
fetch_end_ordinal
expected_origin_rows
written_origin_rows
written_context_rows
parts
source_daily_units_fingerprint
schema_fingerprints
audit_status
created_at_utc
completed_at_utc
```

## Graceful Shutdown

On Ctrl+C:

1. Stop accepting new jobs.
2. Signal fetch workers to stop before submitting new ClickHouse queries.
3. Let in-flight fetches finish or cancel known query ids if shutdown timeout is
   exceeded.
4. Let process workers finish payloads already fetched.
5. Let write workers flush completed processed payloads.
6. Write partial manifest state with `status=interrupted`.
7. Print a clear terminal message:

```text
Interrupt received. Stopping workers, flushing completed writes, and recording resumable state.
```

The next run should skip completed ticker/month parts only if manifests and
audits are complete.

## Resume and Retry

A completed part is valid only when:

1. parquet file exists
2. sidecar metadata exists
3. manifest references the part
4. audit status for that part is pass

On resume:

1. Read root/month/ticker manifests.
2. Build the daily index plan again.
3. Skip completed ticker/month modality outputs.
4. Retry missing or failed units.

Do not infer completion from file presence alone.

## First Implementation Order

Build this in small, verifiable steps:

1. Shared pipeline/runtime classes.
2. Rich terminal with fixed worker rows and simulated jobs.
3. Events-only pipeline:
   - daily index plan
   - event context ordinal calculation
   - fetch by ticker/ordinal
   - process order/time features
   - write ticker/month event parts
   - event audit
4. Add origins and context indices.
5. Add intraday labels.
6. Add macro/daily bars.
7. Add news/SEC embeddings.
8. Add XBRL.
9. Add corporate actions.
10. Add full cache audit script.
11. Update loader to consume the new cache manifest.

The first implementation should support:

```text
--data-groups events
--month 2019-09
--tickers AAPL,MSFT
```

before attempting a full six-month multi-modality build.

## Non-Goals

The permanent cache should not store:

- fully materialized trainer batches
- encoded event chunks by default
- duplicated sparse context per origin
- train/validation split decisions
- model-specific normalization outputs

Those belong in the loader, trainer, or experiment configuration.

## Implementation Checklist

Before considering the builder usable:

1. A one-ticker smoke build passes audit.
2. A multi-ticker one-day build passes audit.
3. A one-month events-only build passes audit.
4. Ctrl+C leaves resumable state.
5. Resume skips only audited-complete outputs.
6. Terminal panels do not blink and preserve fixed worker rows.
7. Peak RSS stays within configured memory budget.
8. Output manifests contain enough metadata to reproduce and audit the build.
9. Loader can materialize event windows from saved event parts without ordinal
   gaps.

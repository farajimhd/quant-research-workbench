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
    ticker=ABC/
      manifest.json
      daily_index.parquet
      events/
        part_000000.parquet
        part_000001.parquet
      origins/
        part_000000.parquet
      event_metadata/
        part_000000.json
      intraday_labels/
        intraday_base_bars_part_000000.parquet
        intraday_condition_events_part_000000.parquet
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

## Data Transfer Between Workers

Workers communicate with typed in-process queue payloads. The first
implementation should use bounded Python queues and thread workers because the
largest payloads are Arrow/Polars objects produced by ClickHouse clients and
written to local SSD. Process workers should use Polars/NumPy operations that
release the GIL where possible. If a future CPU-heavy stage is proven to be GIL
bound, that stage can move to a process pool with Arrow IPC spill files, but
that is not the default path.

The queue payloads should be small metadata objects plus one owned data handle:

```text
FetchJob
  modality
  job_id
  month
  ticker or global key
  source_date or source range
  ordinal/timestamp bounds
  expected_rows
  retry_count

FetchedPayload
  job
  arrow_table or polars_frame
  row_count
  estimated_bytes
  query_id
  fetch_seconds

ProcessedPayload
  job
  processed_frames
  origin/range metadata
  audit_summary
  estimated_bytes
  process_seconds

WriteJob
  job
  output_scope
  frames_or_ipc_paths
  manifest_update
  expected_rows
```

Payload ownership is single-pass:

```text
fetch worker owns FetchedPayload
  -> puts it on process_queue
  -> process worker becomes owner
  -> puts ProcessedPayload/WriteJob on write_queue
  -> write worker becomes owner
  -> write worker releases frames after atomic write and manifest update
```

Fetch workers must not keep references after enqueue. Process workers must not
keep references after enqueue. Write workers release memory immediately after a
successful write or after recording a failed write. This avoids accidental
long-lived references that keep large Arrow/Polars buffers resident.

Backpressure is byte based, not only item-count based. Before a worker enqueues
a payload, it reserves that payload's estimated bytes against the target queue.
When the next stage takes ownership, the reservation is released from the
previous queue and charged to the next queue. If the reservation would exceed
the queue budget, the worker blocks before submitting more work.

Large payloads may be spilled to a local temporary Arrow IPC/parquet file when
queue byte limits are tight or when a process-pool implementation is used. In
that case the queue passes an `ipc_path` plus metadata, and the receiving worker
maps/loads the file. Spill files are temporary build artifacts and must be
deleted after the write or failed job cleanup.

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

Event processing is intentionally light. It should be vectorized with Polars
and/or NumPy, but it is not the place for intraday bar or label computation.
The heavy event-derived calculations belong in the intraday-label pipeline.

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
The process worker must also not compute future intraday bars or labels. Those
are separate modality outputs with their own process workers.

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

Defaults should reflect workload severity. Events are fetch/write heavy and
process light. One event process worker is functionally enough for simple
validation and metadata, but the default uses two so validation can overlap
with writes without creating a large process queue. Intraday labels are process
heavy because they compute event-derived future bars and flags. Sparse contexts
are usually lighter.

Suggested first-run defaults on the workstation should sum to 96 worker slots
because all modality pipelines start together:

| Modality | Fetch | Process | Write | Total |
| --- | ---: | ---: | ---: | ---: |
| Events | 16 | 2 | 8 | 26 |
| Intraday labels | 6 | 20 | 6 | 32 |
| Macro bars | 3 | 6 | 3 | 12 |
| News embeddings | 3 | 2 | 2 | 7 |
| SEC embeddings | 3 | 2 | 2 | 7 |
| XBRL | 3 | 4 | 2 | 9 |
| Corporate actions | 1 | 1 | 1 | 3 |
| **Total** | **35** | **37** | **24** | **96** |

These are worker slots, not guaranteed simultaneous ClickHouse queries or disk
writes. Global caps such as `--max-active-clickhouse-queries` and
`--max-active-writers` still limit the truly concurrent I/O operations.

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

Each modality panel should be a stable table. The first row is the modality
overall status bar. That overall bar should be a wide full-panel bar, not a
small table-cell bar. A horizontal separator line sits under the overall bar so
it is visually detached from the worker/stage rows.

The default display is compact so all modality panels fit on a normal
workstation terminal: one stable row for `Fetch`, one for `Process`, and one
for `Write`. The compact view still uses visual progress bars: the summary has
a weighted wide overall bar, each modality has a wide overall bar, and each
modality/stage row has its own smaller worker/stage bar. Each stage row also
reports active workers, completed jobs, row progress and rate when available,
and the leading active job.

Detailed per-worker rows are still available with `--progress-worker-detail`.
The detailed view reserves stable rows by configured worker count. If the
terminal is short, the implementation may automatically fall back to compact
mode to keep the dashboard readable.

```text
Events
Overall [############################################--------]  61.4%  rows 8.2B/13.4B  eta 02:14:33
--------------------------------------------------------------------------------

Stage    Workers  Bar             Progress                                  Active job
Fetch    9/16     [###-------]    31/252 jobs rows 8.2M/11.0M 220k/s         2019-09 AAPL 2019-09-03 (+8)
Process  1/2      [###-------]    30/252 jobs rows 8.2M/9.7M 410k/s          2019-09 MSFT validate/time
Write    3/8      [###-------]    28/252 jobs rows 1.3GB/3.1GB 420MB/s       AAPL events part_0002 (+2)
```

For `--progress-worker-detail`, the Events panel has exactly:

```text
1 overall row
16 fetch rows
2 process rows
8 write rows
```

If a worker is idle, its row stays visible and reports `idle`. If a worker is
blocked by backpressure, its row reports the blocking queue, for example
`blocked process_queue_bytes` or `blocked writer_slots`. This makes it clear
whether a modality is slow because of ClickHouse, CPU processing, disk writing,
or global backpressure.

Summary panel should show:

```text
overall_status_bar = weighted sum of modality overall bars
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

The summary overall status bar is calculated from each active modality's
expected units:

```text
summary_done_units = sum(modality_done_units)
summary_total_units = sum(modality_total_units)
summary_progress = summary_done_units / summary_total_units
```

For event-like modalities, units are rows. For sparse/context modalities, units
are source rows or bytes, whichever is the modality's configured progress unit.
The implementation must not average percentages across panels because that
would over-weight tiny sparse modalities.

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
cache_root/month=YYYY-MM/ticker=ABC/manifest.json
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

The multi-modality implementation supports:

```text
--data-groups events,intraday_labels,macro_bars,news,sec,xbrl,corporate_actions
--month 2019-09
--tickers AAPL,MSFT
```

Use a small ticker/month smoke before attempting a full six-month build.

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
3. A one-month multi-modality build passes audit.
4. Ctrl+C leaves resumable state.
5. Resume skips only audited-complete outputs.
6. Terminal panels do not blink and preserve fixed worker rows.
7. Peak RSS stays within configured memory budget.
8. Output manifests contain enough metadata to reproduce and audit the build.
9. Loader can materialize event windows from saved event parts without ordinal
   gaps.

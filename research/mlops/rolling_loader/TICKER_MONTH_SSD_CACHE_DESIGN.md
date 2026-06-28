# Ticker/Month SSD Rolling Cache Design

This guide defines the next rolling-loader training cache design. It keeps the
stateful cache semantics from `research/mlops/data/STATEFUL_ROLLING_CONTEXT_DESIGN.md`,
but changes the storage and execution plan so the expensive historical work is
aligned with the current ClickHouse table layout.

This is the design to implement next. The older materialized-cache and indexed
daily-cache scripts should be treated as stale implementation experiments until
this design is implemented and verified.

## Core Decision

The permanent SSD cache should store source-aligned cache packages, not encoded
event chunks and not fully materialized training tensors.

The event table is partitioned and ordered as:

```sql
PARTITION BY toYYYYMM(event_date)
ORDER BY (ticker, ordinal)
```

That makes the natural high-throughput unit:

```text
one ticker, one month
```

For each `(ticker, month)` package, the builder reads the full ordinal sequence
for that ticker and month, adds only the required prior context from earlier
data, builds the reusable derived indexes once, and writes compact columnar
files to SSD. Training then loads these packages and materializes batches on CPU
while the GPU trains.

## Keys

Two keys are used because they represent different concepts:

- `origin_key = ticker_id + ordinal`
- `cache_state_key = timestamp_us`

`origin_key` identifies one market event origin exactly. It is the stable sample
identity.

`cache_state_key` identifies the cache state at an as-of time. Many ticker
origins can share the same timestamp, and a single timestamp can update many
as-of contexts. Timestamp is therefore not a unique sample identity.

The design should not write a giant universal-origin table. Origin and cache
state are derived from the package metadata and closed-form range formulas where
possible.

## Stored Package Layout

A cache root contains split and build metadata:

```text
cache_root/
  manifest.json
  split=train/
    month=YYYY-MM/
      global/
        manifest.json
        market_news_tokens.parquet
        global_daily_bars.parquet
        category_references.parquet
      ticker_hash=XX/
        ticker=ABC/
          manifest.json
          events.parquet
          intraday_bars.parquet
          daily_bars.parquet
          ticker_news_tokens.parquet
          sec_filing_tokens.parquet
          xbrl.parquet
          ranges.parquet
          audit_summary.json
```

Ticker directories can be hash-sharded to avoid very large filesystem
directories. The exact physical layout can change during implementation, but
the logical contents should remain the same.

## Event Payload

Events are stored as raw compact event rows. They are not encoded during cache
build.

Required event columns:

```text
ticker_id             uint32 or dictionary id
ticker                string or dictionary reference
ordinal               uint64
event_type            uint8
timestamp_us          int64
price_primary_int     int32
price_secondary_int   int32
size_primary          float32
size_secondary        float32
exchange_primary      uint8
exchange_secondary    uint8
event_flags           uint8
conditions_packed     uint32
```

Only useful market-event columns should be kept. Redundant derived price
columns such as `mid` should not be stored if bid and ask are present and the
model can derive the relation when needed.

## Event Time Features

Every event has `timestamp_us`. During cache construction the builder must
derive cache-time event time features once for every stored event row.

These features include absolute calendar/session features that do not depend on
which origin later references the event:

```text
utc_second_of_day_sin/cos
utc_day_of_week_sin/cos
utc_day_of_year_sin/cos
years_since_2000
session_second
session_progress
is_regular_hours
is_premarket
is_afterhours
```

All timestamps remain UTC in storage and metadata. Session features use the
market session definition but are computed from UTC timestamps with the correct
exchange timezone conversion.

Origin-relative features cannot be fully precomputed because the same event row
can appear in windows for many different origins. At training-time
materialization, the loader computes only the origin-relative deltas:

```text
origin_timestamp_us - event_timestamp_us
delta_seconds
delta_seconds_log1p_signed
age_seconds_log1p
```

This keeps timestamp handling consistent without repeating calendar/session
work for every sample.

## Event Windows

The event window is still controlled by the configured coverage parameters:

```text
event_window_size
context_chunks
context_chunk_stride_events
sample_stride_events
coverage = event_window_size + (context_chunks - 1) * context_chunk_stride_events
```

For the current default idea:

```text
event_window_size = 128
context_chunks = 32
context_chunk_stride_events = 64
sample_stride_events = 1
coverage = 2112 events
```

Because a package stores one ticker ordered by ordinal, most event windows are
closed-form:

```text
row_offset = origin_ordinal - package_first_ordinal
chunk_end = row_offset - chunk_id * context_chunk_stride_events
chunk_start = chunk_end - event_window_size + 1
```

A window is valid only when all referenced rows are present and ordinal order is
continuous. If source filtering removes invalid events, the stored ordinal map
must make the resulting discontinuity explicit so the loader never silently
builds a window across an ordinal gap.

## Origins

For a package month, eligible origins are the package events whose UTC timestamp
falls in the requested trading sessions for that period.

The active session rule is:

```text
04:00:00 America/New_York <= origin session time < 20:00:00 America/New_York
```

All stored origin timestamps remain UTC. The local session rule is only used for
session membership and session-relative feature construction.

The default origin stride is one event. With `sample_stride_events=1`, the best
case sample count should be close to the number of valid source events in the
requested period after removing events whose required context or labels are not
available.

## Text, SEC, And XBRL Context

The builder reads tokenized context tables, not raw text and not embeddings
computed in this loader.

Ticker package context:

- ticker news tokens
- SEC filing text tokens
- XBRL numeric rows

Global month context:

- market news tokens
- category references

Each token context package includes enough prior rows to fill the configured
as-of cache at the first origin in the month. If fewer historical rows exist,
the training loader zero-fills the missing slots and sets the corresponding
availability mask to false. Missing optional context is valid data; lookahead is
not.

As-of selection must obey:

```text
context_timestamp_us <= origin_timestamp_us
```

## Daily, Macro, And Global Bars

Only daily bars are loaded from the bar tables for this path. Do not load
independent `1w`, `1mo`, or `1y` rows.

Backward and forward daily features/labels are derived from daily bars:

```text
current day as-of
-1d, -2d, ...
+1d, +2d, ...
```

If the early history is shorter than the configured lookback, the loader uses
the earliest available daily bars and masks missing positions. The builder
stores only the daily bars needed to serve the package period plus configured
lookback and forward label horizons, not the full historical bar stream.

Global daily bars are cheap and can be stored once per month/global package for
the required symbol set and date span.

## Intraday Bars And Labels

Intraday bars are built once per ticker/month package from the stored event
stream. They are not rebuilt per origin.

For each trading session and each configured horizon:

```text
session_start = 04:00:00 America/New_York
session_end = 20:00:00 America/New_York
bar_index = floor((timestamp_us - session_start_us) / horizon_us)
```

Bars are session-bounded. A bar for a late after-hours origin must never use
events from the next trading day.

Origin-to-bar lookup is closed-form:

```text
origin_bar_index = floor((origin_timestamp_us - session_start_us) / horizon_us)
```

Intraday targets include current and next bars for the configured horizons:

```text
current_100ms, current_250ms, ...
next_100ms, next_250ms, ...
```

Current intraday bars are labels, not context features, because a current bar
can include events after the origin inside the same bar interval. They must not
be fed as no-lookahead features.

## Training-Time Materialization

Training loads package cache files and materializes batches on CPU. The GPU
should train on the current batch while CPU workers prepare later batches.

The permanent SSD cache does not store:

- encoded event chunks
- materialized event tensors
- final batch tensors

At materialization time, the loader:

1. Selects origins from `(ticker_id, ordinal)` identities.
2. Resolves event windows by ordinal range.
3. Reads raw compact event columns and cached event time features.
4. Computes origin-relative time deltas.
5. Resolves token, XBRL, daily, global, and intraday label indexes by as-of or
   forward rules.
6. Builds model-facing tensors for the requested batch.

An optional small rolling RAM or temporary SSD queue can hold ready batches
during training, but it is not the permanent cache artifact.

## Why Not Fully Query By Timestamp

ClickHouse can build many of these outputs directly, and we should profile that
path. The current event table, however, is not physically ordered for
all-ticker timestamp windows. Timestamp-window training slices can force broad
monthly scans and expensive resorting.

The ticker/month package shape matches the current physical order. It converts
one heavy sequential read into reusable SSD state and lets training sample or
materialize without repeatedly stressing ClickHouse.

If a future timestamp-ordered projection is added, the direct query/materialize
path should be revisited.

## Concurrency Design

The builder should be a DAG executor with separate resource lanes. Python
orchestrates tasks, progress, manifests, and audits. ClickHouse and Polars do
the large vectorized work.

### Task Units

Long-running tasks are split into:

- global/month package build
- ticker/month event fetch
- ticker/month text token fetch
- ticker/month SEC token fetch
- ticker/month XBRL fetch
- ticker/month daily bar fetch
- event time-feature computation
- intraday bar construction
- range metadata construction
- package write
- package audit

### Worker Lanes

The worker budget should be divided by resource type rather than forcing all
work through one sequential stage:

- `event_fetch_workers`: low-to-moderate concurrency for large ClickHouse
  ticker/month event reads.
- `context_fetch_workers`: higher concurrency for smaller token, XBRL, and bar
  reads.
- `cpu_polars_workers`: vectorized time-feature, intraday-bar, and range-index
  construction.
- `disk_write_workers`: independent Parquet/JSON writes after each payload is
  ready.
- `audit_workers`: low-concurrency source checks and invariant checks.

Each lane should have bounded queues and memory gates. Event fetches are the
largest memory users and should not be allowed to flood RAM while CPU workers
are still processing prior packages.

### Dependency Flow

For a ticker/month package:

```text
event fetch --------------> event time features ---> events write
       |                         |
       |                         +----> intraday bars ---> intraday write
       |                         |
       |                         +----> event ranges -----> ranges write
       |
daily bars fetch ----------------------------------------> daily write
text token fetch ----------------------------------------> text write
SEC token fetch -----------------------------------------> SEC write
XBRL fetch ----------------------------------------------> XBRL write
global/month package ------------------------------------> manifest reference
all writes ----------------------------------------------> audit
```

Independent fetches for one ticker/month can run concurrently. Independent
ticker/month packages can also run concurrently, bounded by ClickHouse, CPU, and
memory limits.

Global/month packages should be built once and reused by every ticker package in
that month.

### Progress Reporting

The terminal should report current month, active package, and worker-lane
progress. Worker rows should be based on the actual lanes above, not only on
days or a single stage label.

Useful counters:

- queued/running/done/failed packages by lane
- rows fetched and bytes written by lane
- current ticker/month per worker
- elapsed time and ETA from completed packages
- peak RSS and configured memory gate
- latest warnings and audit failures

Shutdown should be graceful: stop submitting new work, let in-flight writes
finish or mark them incomplete, flush manifests/logs, and write a final summary
that distinguishes complete, failed, skipped, and interrupted packages.

## Audit Requirements

The standalone audit and end-of-build audit should verify:

- manifest period matches requested period
- package min/max UTC timestamps and human-readable dates match the package
  period plus allowed context lookback/lookforward
- origin count is close to source event count after configured stride and
  documented validity filters
- every sampled `origin_key` resolves exactly one ClickHouse source event
- event rows match source values for sampled origins and sampled windows
- ordinal windows are continuous or explicitly invalidated
- event time features match timestamp-derived formulas
- all context as-of rows obey `context_timestamp_us <= origin_timestamp_us`
- intraday bars are session-bounded and do not cross into the next day
- daily future labels use only forward label targets, never context features
- optional missing text/SEC/XBRL inputs are zero-filled and masked
- no encoded chunks or final materialized tensors are present in the permanent
  cache

Any audit failure that can create lookahead or corrupt event-window continuity
must fail the build, not produce a warning-only cache.

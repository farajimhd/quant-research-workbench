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

For each `(ticker, month)` package, the builder reads the ordinal sequence for
that ticker and month, adds only the required prior context from earlier data,
builds the reusable derived indexes once, and writes compact columnar files to
SSD. Training then loads these packages and materializes batches on CPU while
the GPU trains.

Very liquid tickers can have too many monthly events for a single in-memory
fetch. The package therefore remains logical `(ticker, month)` state, but its
large files are physically split into ordinal-bounded parts. Part boundaries are
storage boundaries only; they are not model or cache-state boundaries.

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
        market_news_embeddings.parquet
        global_daily_bars.parquet
        category_references.parquet
      ticker_hash=XX/
        ticker=ABC/
          manifest.json
          events_part_00000.parquet
          origins_part_00000.parquet
          event_window_index_part_00000.parquet
          ranges_part_00000.parquet
          corporate_action_daily_labels_part_00000.parquet
          events_part_00001.parquet
          origins_part_00001.parquet
          event_window_index_part_00001.parquet
          ranges_part_00001.parquet
          daily_bars.parquet
          ticker_news_embeddings.parquet
          sec_filing_embeddings.parquet
          xbrl.parquet
          corporate_actions.parquet
          intraday_base_bars.parquet
          intraday_condition_events.parquet
          audit_summary.json
```

Ticker directories can be hash-sharded to avoid very large filesystem
directories. The exact physical layout can change during implementation, but
the logical contents should remain the same.

The package manifest owns the part list:

```json
{
  "parts": [
    {
      "part_id": 0,
      "origin_ordinal_start": 1000000,
      "origin_ordinal_end": 2999999,
      "fetch_ordinal_start": 997888,
      "fetch_ordinal_end": 2999999,
      "files": {
        "events": "events_part_00000.parquet",
        "origins": "origins_part_00000.parquet",
        "event_window_index": "event_window_index_part_00000.parquet",
        "ranges": "ranges_part_00000.parquet",
        "corporate_action_daily_labels": "corporate_action_daily_labels_part_00000.parquet"
      }
    }
  ]
}
```

`origin_ordinal_start/end` defines the samples saved in the part.
`fetch_ordinal_start/end` defines the event rows saved for that part. The fetch
start may be earlier than the origin start because event windows need prior
context rows. Only origins inside `origin_ordinal_start/end` are emitted.

The builder stores a configurable maximum raw-event lookback per part. This is
not the same as the default training event-window geometry. The package may
write a default `event_window_index` for the configured build coverage, but the
training loader can recompute raw flat or raw window event gathers from the
stored events as long as the requested coverage is no larger than
`max_cached_event_lookback_rows`. Requests beyond that value require rebuilding
or extending the cache.

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
condition_token_1..condition_token_5 uint8
```

Only useful market-event columns should be kept. Redundant derived price
columns such as `mid` should not be stored if bid and ask are present and the
model can derive the relation when needed.

## Event Time Features

Every event has `timestamp_us`. During cache construction the builder must
derive cache-time event time features once for every stored event row.

All absolute time features in the SSD cache use one timezone: UTC. Session
membership and session-progress features are the only exception; they are
derived from the same UTC timestamp after converting to the configured exchange
timezone for the market session rule.

Event features include absolute calendar/session features that do not depend on
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
time_delta_seconds              source_timestamp_us - origin_timestamp_us
time_delta_seconds_log1p_signed signed log1p(abs(delta_seconds))
time_age_seconds_log1p          log1p(max(0, origin_timestamp_us - source_timestamp_us))
```

This keeps timestamp handling consistent without repeating calendar/session
work for every sample.

## Context Time Features

Every context row with an availability timestamp should carry builder-computed
absolute UTC time features aligned row-by-row with the cached data:

```text
available_utc_second_of_day_sin/cos
available_utc_day_of_week_sin/cos
available_utc_day_of_year_sin/cos
available_years_since_2000
```

The source availability timestamp is:

```text
news embeddings:      published_at_utc -> timestamp_us
SEC text embeddings:  accepted_at_utc -> timestamp_us
XBRL rows:            source accepted/availability timestamp_us
```

The builder does not compute origin-relative age for these rows. The same news
item, SEC filing, or XBRL fact can be selected by many later origins, so age is
materialized by the loader per batch using the selected origin timestamp. The
loader should emit relative context time features alongside the cached absolute
features:

```text
time_delta_seconds
time_delta_seconds_log1p_signed
time_age_seconds_log1p
```

For text embeddings, the time tensor is item-level and aligned with
`[B, max_items]`, not chunk-level. Each chunk of the same article or filing
shares the item timestamp and item time features.

XBRL rows also carry accounting-period time fields. These are not availability
times and must not be used for no-lookahead selection:

```text
period_end_date       explicit accounting period end date
fiscal_year           numeric reporting year
fiscal_period         reporting period category
calendar_period_code  calendar period category
```

The loader should emit a separate period-end time tensor from
`period_end_date`, plus categorical ids for `fiscal_period` and
`calendar_period_code`. This gives the model both:

```text
when the fact was known       timestamp_us / availability time features
what period the fact reports  period_end_date / period time features
```

The categorical ids are persistent `training_category_reference` ids. The
builder joins that table while writing XBRL context and stores the resulting
`*_id` columns in `xbrl.parquet`. Id `0` is reserved for missing or unknown
values. Existing `(domain, field_name, category_value)` mappings must never be
renumbered; only new category values receive new ids.

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

For split packages, the same formula is applied within each physical part using
part-local row offsets. The part must include enough `fetch_ordinal_start`
lookback rows to satisfy the maximum configured context lag. If the lookback is
not available or the ordinal span contains a gap, the affected origins are
skipped and counted in the manifest. The next part starts a new physical row
offset range but continues the same logical ticker/month origin sequence.

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

If `sample_stride_events` is greater than one, stride is computed from the
month's first eligible origin ordinal, not from each physical part. This keeps
origin selection stable when a liquid ticker is split into several files.

## Text, SEC, And XBRL Context

The builder reads precomputed Qwen embedding context tables, not raw text and
not tokens that would require model inference during training.

Ticker package context:

- ticker news embeddings
- SEC filing text embeddings
- XBRL numeric rows

Global month context:

- market news embeddings
- category references

Each embedding context package includes enough prior rows to fill the configured
as-of cache at the first origin in the month. If fewer historical rows exist,
the training loader zero-fills the missing slots and sets the corresponding
availability mask to false. Missing optional context is valid data; lookahead is
not.

As-of selection must obey:

```text
context_timestamp_us <= origin_timestamp_us
```

The persisted embedding and XBRL parquet files should include the absolute UTC
availability-time features from the context-time policy. These columns are part
of the cached source row, so they remain aligned with the embedding vector or
XBRL value row after sorting, filtering, and as-of selection.

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

Daily bars keep `bar_start_ms` and compact bar-start time features. Do not add
both start and end timestamp features by default; for daily bars the interval
duration is fixed and the offset axis already tells the model whether the row
is `-1d`, `-2d`, `-7d`, `-200d`, and so on. The builder should add only compact
absolute UTC bar-start features:

```text
bar_start_utc_second_of_day_sin/cos
bar_start_utc_day_of_week_sin/cos
bar_start_utc_day_of_year_sin/cos
bar_start_years_since_2000
```

At materialization time, the loader should emit a separate bar time-feature
tensor aligned with the selected bar rows:

```text
bar_age_days
bar_age_days_log1p
```

The model should also receive or learn the completed-bar offset index; offset
semantics are more important than duplicating daily-bar start/end timestamps.

## Intraday Forward Labels

Dense intraday bar grids are not persisted as separate SSD cache files. The
builder maintains a shared ClickHouse intermediate table,
`intraday_base_bars_by_time_ticker` by default, with one row per
`(local_date, ticker, label_resolution_us, bucket_index, bar_family)`. Missing
local-session days are populated per ticker-month package, then every part for
that ticker/month reuses the same table. The builder also writes those compact
rows to `intraday_base_bars.parquet` inside the ticker package. It does not
persist backward intraday context per origin by default because that is highly
redundant for liquid tickers; the old `intraday_context_bars_part_*.parquet`
shape is available only with `--materialize-intraday-context-bars` for
diagnostics. The builder does not prebuild the full-market grid for a day/month
because the 100ms resolution can create hundreds of millions of intermediate
rows for one active market day. This keeps the cache format origin-relative
while avoiding repeated raw-event bar aggregation inside each part query.

Future condition flags use a parallel shared sparse ClickHouse artifact. For
each requested month, the builder ensures
`intraday_condition_events_by_time_ticker` and records completion in
`intraday_aux_build_status`. The sparse table contains only condition events
that match the forecastable groups used by the training labels. It is built
once for the month from raw `events` and `event_condition_token_reference`.
Ticker/month packages then read only bounded ticker/month slices into
`intraday_condition_events.parquet`, avoiding repeated raw-event condition scans
for every package while preserving the same source-token semantics.

The builder computes grid-aligned forward labels set-wise for each
ticker/month/part package. This is not a per-origin query. It is one bounded
query per ticker/month/part that:

1. Builds sparse family bars at only the base resolutions required by the
   configured horizons, or reuses already-built sparse family bars from the
   shared intermediate table.
2. Skips the origin's current partial bucket.
3. Aggregates full future buckets through the requested horizon or `eod`.

For each origin and horizon:

```text
label_resolution_us = selected by horizon
first_future_bucket = floor(origin_local_session_us / label_resolution_us) + 1
last_future_bucket = first_future_bucket + horizon_bucket_count - 1
label_grid_start_timestamp_us = session_midnight_us + first_future_bucket * label_resolution_us
label_grid_end_timestamp_us = session_midnight_us + (last_future_bucket + 1) * label_resolution_us
```

The label aggregates events that satisfy the equivalent grid window:

```text
event_ticker_id = origin_ticker_id
event_timestamp_us >= label_grid_start_timestamp_us
event_timestamp_us < label_grid_end_timestamp_us
event_session = origin_session
```

This deliberately bounds timing approximation error by the selected base
resolution and removes the expensive origin/horizon/raw-event range join. The
default base-resolution policy is:

| Horizon | Base resolution |
| --- | ---: |
| `<=60s` | `100ms` |
| `60s..900s` | `1s` |
| `900s..3600s` | `5s` |
| `3600s..10800s` | `30s` |
| `>10800s` and `eod` | `1m` |

There are no `current_*` intraday labels. Current fixed-grid bars are ambiguous
for event origins because they can include after-origin events inside the same
bar bucket. The label semantics contain only `next_*` targets.

The default cache does not persist `intraday_forward_labels_part_*.parquet`.
It stores compact `intraday_base_bars.parquet` and
`intraday_condition_events.parquet` once per ticker/month. The loader combines
those compact sources with origin rows to emit the same `intraday_labels` and
`future_bar_values` tensors for a requested batch. This avoids the fundamental
`origins x horizons` build-time expansion for liquid tickers.

`--materialize-intraday-forward-labels` is an opt-in debug/parity mode. In that
mode, persisted `intraday_forward_labels_part_*.parquet` files store one row
per origin. Horizon-dependent fields are list columns sorted by `horizon_us`;
this avoids transferring and writing one physical row per origin per horizon,
but it is still much larger and slower than compact-label mode.

Example materialized/per-origin columns:

```text
origin_key
ticker_id
origin_ordinal
origin_timestamp_us
horizon[]
horizon_us[]
label_resolution_us[]
label_grid_start_timestamp_us[]
label_grid_end_timestamp_us[]
price_primary_int[]
price_secondary_int[]
size_primary_sum[]
size_secondary_sum[]
event_count[]
last_event_timestamp_us[]
available[]
trade_open[]
trade_close[]
trade_high[]
trade_low[]
trade_size_sum[]
trade_event_count[]
quote_bid_open[]
quote_bid_close[]
quote_bid_high[]
quote_bid_low[]
quote_bid_size_open[]
quote_bid_size_close[]
quote_bid_size_high[]
quote_bid_size_low[]
quote_bid_event_count[]
quote_ask_open[]
quote_ask_close[]
quote_ask_high[]
quote_ask_low[]
quote_ask_size_open[]
quote_ask_size_close[]
quote_ask_size_high[]
quote_ask_size_low[]
quote_ask_event_count[]
trade_available[]
quote_bid_available[]
quote_ask_available[]
trade_last_event_timestamp_us[]
quote_bid_last_event_timestamp_us[]
quote_ask_last_event_timestamp_us[]
condition_halt_pause_flag[]
condition_resume_flag[]
condition_news_risk_flag[]
condition_luld_limit_state_flag[]
ticker_news_arrival_flag[]
sec_filing_arrival_flag[]
```

The canonical future bar labels are the family-specific arrays. Legacy
`price_primary_int`, `price_secondary_int`, `size_primary_sum`,
`size_secondary_sum`, `event_count`, `last_event_timestamp_us`, and `available`
remain compatibility projections and should not be the primary v3 target
contract. Bar families are:

| Family | Semantics |
| --- | --- |
| `trade_*` | Future trade events only, with trade OHLC, trade size sum, trade event count, and trade availability. |
| `quote_bid_*` | Future quote events only, using bid price/size fields. |
| `quote_ask_*` | Future quote events only, using ask price/size fields. |

The event-state and external-arrival targets are binary arrays per horizon. The
builder resolves condition dense token ids from `event_condition_token_reference`
using source family and Massive modifier code, so the saved targets are stable
across token-reference rebuilds. They intentionally cover only high-value
forecastable trading states: halts or pauses, resumptions, news-risk states, and
LULD or limit-state activity. News and SEC filing arrival flags are computed from
their embedding-table availability timestamps and are on when at least one item
arrives inside the future horizon.

Session boundaries are hard validity boundaries:

```text
label_grid_end_timestamp_us <= session_end_us
```

If the forward window crosses the 20:00 America/New_York session end, that
horizon is masked unavailable for the origin. The query must not pull events
from the next trading day.

Full dense intraday bars may be emitted only under an explicit debug/profiling
option. They are not part of the default permanent cache.

### Corporate Action Context And Labels

Corporate-action data is stored separately from events and intraday labels.
`corporate_actions.parquet` is a sparse ticker/month context file sourced from
q_live split and dividend reference tables. Each row has an availability
timestamp and an effective timestamp:

```text
available_timestamp_us <= origin_timestamp_us  controls X/no-lookahead context
effective_timestamp_us > origin_timestamp_us   controls Y/future labels
```

Split rows use the split execution date as both availability and effective
time, because the current split table does not expose a separate announcement
timestamp. Dividend rows use declaration date plus one New York session start as
availability when present, otherwise ex-dividend date; ex-dividend date is the
effective time.

The context file stores stable category ids from
`training_category_reference(domain='corporate_actions')` for:

```text
action_type
dividend_type
currency_code
frequency
```

It also stores split factors, log factors, cash amount, log cash amount,
indicator bits, and UTC time features for both availability and effective time.

Corporate-action labels are daily future targets and are not part of intraday
price bars. `corporate_action_daily_labels_part_*.parquet` stores one row per
origin with list columns ordered by `horizon_days`:

```text
future_split_flag[]
future_reverse_split_flag[]
future_forward_split_flag[]
future_dividend_ex_flag[]
future_special_dividend_ex_flag[]
future_any_corporate_action_flag[]
```

The default daily horizons are `1,2,3,7,28` days, matching the forward daily
price-label horizons through `plus_28d` and excluding `current_day_full`. The builder computes
these flags vectorized from sorted sparse effective timestamps, so every saved
origin remains row-aligned with its corporate-action labels.

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
4. Computes origin-relative time deltas for events, text, XBRL, corporate
   actions, and bars.
5. Resolves text embeddings, XBRL, corporate actions, daily, global, and
   precomputed forward label rows by as-of, forward, or `origin_key` rules.
6. Emits modality payload tensors plus aligned time-feature tensors and masks.
7. Builds model-facing tensors for the requested batch.

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

For liquid tickers, ClickHouse access should be:

1. Query the eligible monthly origin bounds for one ticker:
   `min(ordinal), max(ordinal)`.
2. Split that ordinal span into bounded physical parts.
3. Fetch event rows with `ticker = X AND ordinal BETWEEN fetch_start AND
   fetch_end`, where `fetch_start` includes required event-window lookback.
4. Compute intraday forward labels in ClickHouse with origins restricted to
   `origin_ordinal_start/end`. Future rows are limited to the part's origin
   timestamp range plus the current horizon and are joined on the same local
   session date, so intraday labels do not cross into the next trading day.

This query shape is aligned with `ORDER BY (ticker, ordinal)`. It avoids one
massive liquid-ticker fetch while still keeping sequential reads and set-based
ClickHouse computation.

If a future timestamp-ordered projection is added, the direct query/materialize
path should be revisited.

## Concurrency Design

The builder should be a DAG executor with separate resource lanes. Python
orchestrates tasks, progress, manifests, and audits. ClickHouse and Polars do
the large vectorized work.

### Task Units

Long-running tasks are split into:

- global/month package build
- ticker/month origin-bound discovery
- ticker/month part event fetch
- ticker/month text embedding fetch
- ticker/month SEC embedding fetch
- ticker/month XBRL fetch
- ticker/month daily bar fetch
- event time-feature computation
- part intraday forward-label query/fetch
- range metadata construction
- package write
- package audit

### Worker Lanes

The worker budget should be divided by resource type rather than forcing all
work through one sequential stage:

- `event_fetch_workers`: low-to-moderate concurrency for large ClickHouse
  ticker/month event reads.
- `context_fetch_workers`: higher concurrency for smaller embedding, XBRL, and bar
  reads.
- `cpu_polars_workers`: vectorized time-feature and range-index construction.
- `disk_write_workers`: independent Parquet/JSON writes after each payload is
  ready.
- `audit_workers`: low-concurrency source checks and invariant checks.

Each lane should have bounded queues and memory gates. Event fetches are the
largest memory users and should not be allowed to flood RAM while CPU workers
are still processing prior packages.

### Dependency Flow

For a ticker/month package:

```text
origin bounds ----> ordinal parts
                       |
                       +---- part event fetch ------> event time features ---> part events write
                       |          |                         |
                       |          |                         +----> part ranges/windows write
                       |          |
                       |          +---- part intraday label query -----------> part labels write
                       |
daily bars fetch -----------------------------------------------------------> daily write
text embedding fetch ------------------------------------------------------> text write
SEC embedding fetch -------------------------------------------------------> SEC write
XBRL fetch ----------------------------------------------------------------> XBRL write
global/month package ------------------------------------------------------> manifest reference
all part/context writes ---------------------------------------------------> audit
```

Independent fetches for one ticker/month can run concurrently. Independent
ticker/month packages can also run concurrently, bounded by ClickHouse, CPU, and
memory limits.

Within one liquid ticker/month, parts are processed sequentially by default to
bound peak RAM, but each part overlaps its event query, label query, CPU
window-index build, and writes through the existing resource lanes. If memory
gates later allow it, a small number of parts from the same liquid ticker can be
prefetched without changing the package format.

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
- intraday forward labels are grid-aligned, session-bounded, and do not cross
  into the next day
- daily future labels use only forward label targets, never context features
- optional missing text/SEC/XBRL inputs are zero-filled and masked
- no encoded chunks or final materialized tensors are present in the permanent
  cache

Any audit failure that can create lookahead or corrupt event-window continuity
must fail the build, not produce a warning-only cache.

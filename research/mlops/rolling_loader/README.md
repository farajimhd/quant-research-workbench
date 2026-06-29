# Rolling Loader Package

`research.mlops.rolling_loader` is the training and serving data path for
stateful market-context models. The current recommended path is:

```text
ClickHouse events/context tables
  -> ticker/month SSD cache builder
  -> ticker/month rolling data loader
  -> trainer batches
```

The cache builder writes source-aligned ticker/month packages to SSD. The data
loader reads those packages, builds a shuffled sample plan, materializes only
the data groups requested by the trainer, and feeds batches from CPU while the
GPU trains.

The older in-memory replay loader and materialized-cache scripts remain in this
package for comparison and profiling, but new training work should start from
the ticker/month cache and loader.

## Components

| Component | File | Purpose |
| --- | --- | --- |
| Ticker/month cache builder | `run_build_ticker_month_cache.py` | Builds reusable SSD packages from ClickHouse. |
| Ticker/month cache audit | `audit_ticker_month_cache.py` | Audits completed SSD cache packages. |
| Ticker/month data loader | `ticker_month_dataset.py` | Reads SSD packages and materializes trainer batches. |
| Loader profiler | `run_profile_ticker_month_loader.py` | Measures loader speed, memory, and output shapes. |
| Loader batch audit | `audit_ticker_month_loader_batches.py` | Verifies materialized batches against SSD package files. |
| Cache design guide | `TICKER_MONTH_SSD_CACHE_DESIGN.md` | Detailed design rationale and storage contract. |
| Legacy stateful replay | `loader.py`, `initialize.py`, `run_training_profile.py` | Older production-style replay/profiling path. |

## Ticker/Month SSD Cache

The event table is partitioned by month and ordered by `(ticker, ordinal)`.
The fastest natural unit is therefore:

```text
one ticker, one month
```

Each package stores raw compact events, origins, reusable index files, labels,
and token/context files. It does **not** store encoded event chunks and does
**not** store fully materialized training batches.

### Layout

```text
cache_root/
  manifest.json
  train/
    month=YYYY-MM/
      global/
        manifest.json
        market_news_tokens.parquet
        global_daily_bars.parquet
        category_references.parquet
      ticker_hash=XX/
        ticker=ABC/
          manifest.json
          events_part_00000.parquet
          origins_part_00000.parquet
          event_window_index_part_00000.parquet
          ranges_part_00000.parquet
          intraday_forward_labels_part_00000.parquet
          daily_bars.parquet
          ticker_news_tokens.parquet
          sec_filing_tokens.parquet
          xbrl.parquet
```

Very liquid tickers can be physically split into multiple ordinal-bounded
parts. These part boundaries are storage boundaries only. They are not model
or training boundaries.

### Keys

Two keys are used:

```text
origin_key = ticker_id + ordinal
cache_state_key = timestamp_us
```

`origin_key` uniquely identifies one training sample. `cache_state_key`
represents an as-of time; many tickers or origins can share it, so it is not a
sample identity.

### Origin Rule

Origins are only events inside the active trading session:

```text
04:00:00 America/New_York <= origin session time < 20:00:00 America/New_York
```

All timestamps in storage remain UTC microseconds. The New York session rule is
used only for session membership and session-relative features.

### Event Payload

Events are stored as raw compact rows plus cache-time event time features.

Core event columns:

```text
ticker_id
ticker
ordinal
event_type
timestamp_us
price_primary_int
price_secondary_int
size_primary
size_secondary
exchange_primary
exchange_secondary
event_flags
conditions_packed
```

Cache-time event features:

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

Origin-relative features are not stored because the same event can be reused by
many origins. The loader computes origin-relative deltas at materialization
time when a trainer actually asks for them.

### Event Lookback

The builder separates cached history from the default training window index.

`--max-cached-event-lookback-rows` controls how many prior raw event rows are
stored before each physical part's first origin. The default is:

```text
8192 rows
```

This is not per origin. For one normal ticker/month part, it stores up to 8192
rows before the first eligible origin of the month. For split liquid tickers,
the overlap is up to 8192 rows per physical part boundary.

The default `event_window_index` is still written for the configured build
coverage, but the loader can request any event coverage that fits inside
`max_cached_event_lookback_rows`. If a later experiment needs more history, the
cache must be rebuilt with a larger value.

### Labels

Intraday labels are stored as origin-relative `next_*` labels. The cache does
not store dense intraday bar grids and does not store `current_*` intraday
labels.

On disk, `intraday_forward_labels_part_*.parquet` stores one row per origin.
Each horizon-dependent field is a list column ordered by `horizon_us`, for
example `horizon`, `horizon_us`, `price_primary_int`, `price_secondary_int`,
`size_primary_sum`, `size_secondary_sum`, `event_count`,
`last_event_timestamp_us`, and `available`. This keeps the ClickHouse result
and parquet file proportional to origin count instead of
`origin_count * horizon_count`.

The current default horizons are:

```text
100ms,250ms,500ms,750ms,1s,5s,10s,30s,60s,120s,180s,300s,
600s,1200s,1800s,3600s,7200s,3h,4h,5h
```

Daily macro/future labels are based on daily bars and should be computed from
daily-bar sequences, not from independent weekly/monthly/yearly bars.

### Context Files

The builder reads tokenized context tables. It does not query raw text and does
not compute embeddings.

Per-ticker optional context:

```text
ticker_news_tokens
sec_filing_tokens
xbrl
daily_bars
```

Global context is stored once per month under `global/` where available.

Missing optional context is represented as empty/zero data plus explicit masks
at load time. Missing optional context is not an error by itself.

At load time, daily bar context is emitted only when requested in
`data_groups`. The loader uses completed daily bars only. It does not expose a
full current-day daily bar as context because that would include future
information for intraday origins.

## Build Cache

Workstation form:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\research\mlops\rolling_loader\run_build_ticker_month_cache.py `
  --month 2019-02 `
  --cache-id train_201902_ticker_month
```

Laptop/module form:

```powershell
python -m research.mlops.rolling_loader.run_build_ticker_month_cache `
  --month 2019-02 `
  --cache-id train_201902_ticker_month
```

Build every complete month inside a period:

```powershell
python -m research.mlops.rolling_loader.run_build_ticker_month_cache `
  --start-utc 2019-01-01T00:00:00Z `
  --end-utc 2019-04-15T00:00:00Z `
  --cache-id train_201901_201903_ticker_month
```

Partial months at period boundaries are ignored.

### Builder Defaults

Defaults are tuned for the 128-core / 512GB workstation and target practical
throughput without flooding ClickHouse:

```text
package workers                    64
max inflight packages              96
event fetch workers                 6
context fetch workers              16
label fetch workers                 6
CPU workers                        16
write workers                       8
audit workers                       2
ClickHouse max_threads              8 per query
ClickHouse memory cap             120G per query
max cached event lookback rows   8192
max origin events per part     500000
ClickHouse query retries            2 for transient HTTP read failures
```

Large liquid tickers are split by ordinal into physical parts. The default part
size is intentionally below the old 2,000,000-origin setting because intraday
label queries can return very large Arrow responses. Keeping parts smaller
reduces the chance of broken HTTP reads and makes retries cheaper.

If the workstation is quiet and ClickHouse has headroom, the first override to
test is usually:

```powershell
--label-fetch-workers 8
```

### Builder Logging

The builder writes these files under the cache root:

```text
terminal.log
builder_events.jsonl
builder_profile_events.jsonl
errors.jsonl
train_progress.json
manifest.json
```

The Rich terminal is heartbeat-driven, so progress continues updating while
the main thread waits on long ClickHouse futures. Ctrl+C requests a graceful
stop, cancels tracked active ClickHouse query ids, writes an interrupted
manifest, and keeps completed package directories intact.

### Rerun And Resume

Normal rerun:

```text
rebuild and atomically replace existing ticker packages
```

Use `--resume` only when you intentionally want to reuse completed package
directories from a compatible interrupted build.

### Builder Audits

Two audit layers are active by default.

Inline part audit:

```text
--inline-audit-samples-per-part 2
```

This checks two deterministic random origins from each part using only data
already loaded in memory. It catches origin/window mismatch, ordinal gaps,
missing labels, wrong label-origin alignment, and forward-label horizon
violations before files are written.

Final audit:

```text
audit_ticker_month_cache.py
```

This runs after all requested month packages finish unless
`--skip-final-audit` is passed.

## Rolling Data Loader

The loader reads the ticker/month SSD cache and emits trainer-ready batches.
It is implemented in:

```text
ticker_month_dataset.py
```

Main classes:

```text
TickerMonthLoaderConfig
TickerMonthCacheIndex
TickerMonthPartReader
TickerMonthBatchMaterializer
AsyncTickerMonthBatchLoader
TickerMonthTrainingBatch
```

### Loader Flow

1. Read the cache root manifest.
2. Discover complete `(month, ticker, part)` packages.
3. Read only the origin index for a small group of packages.
4. Build local sample refs over `(month, ticker, part_id, origin_row)`.
5. Filter origins by requested training period when `start_utc/end_utc` are
   provided.
6. Apply deterministic dataset sampling or hash buckets.
7. Load event, label, and context payload files only for packages that have
   selected origins.
8. Shuffle inside the loaded group for non-stream modes. In `raw_stream` mode,
   keep each package's origins in ordinal order so the trainer sees continuous
   sliding samples from the loaded part.
9. Materialize CPU batches with a bounded worker queue.
10. Yield batches to the trainer.

This gives useful training randomness while keeping SSD reads mostly
sequential and package-local.

The origin-first path matters for small repeatable benchmark sets. A sparse
`sample_fraction` should not force the loader to read large event parquet files
for packages that contribute no sampled origins.

### Loader Inputs

Important `TickerMonthLoaderConfig` fields:

```text
cache_root
split
start_utc
end_utc
months
tickers
batch_size
seed
data_groups
event_output_mode
events_per_window
event_stream_length
event_stream_chunk_size
context_chunks
context_stride_events
flat_coverage_events
loaded_parts_per_group
read_workers
materialize_workers
max_batches
event_columns
suppress_event_columns
dataset_id
randomize_seed
sample_fraction
sample_hash_modulus
sample_hash_buckets
max_origins_per_epoch
materialize_chunk_size
drop_last_batch
preserve_batch_order
```

For the stateful dataset-plan and checkpoint contract, see:

```text
STATEFUL_TICKER_MONTH_LOADER_GUIDE.md
```

### Data Groups

Use `data_groups` to avoid loading files the objective does not need.

Common values:

```text
events
intraday_labels
ticker_news_tokens
market_news_tokens
sec_filing_tokens
xbrl
daily_bars
global_daily_bars
```

Examples:

```text
events
events,intraday_labels
ticker_news_tokens,market_news_tokens,sec_filing_tokens
events,intraday_labels,daily_bars,global_daily_bars
```

For event-only pretraining, use:

```powershell
--data-groups events --event-output-mode raw_stream
```

For identity/context experiments with no event tensor:

```powershell
--event-output-mode none
```

### Event Output Modes

The default is raw continuous streams, not encoded chunks.

```text
raw_stream
  one dense float32 tensor shaped [B, event_stream_length, F]
  each row is the continuous event sequence ending at the origin event

raw_windows
  per-column arrays shaped [B, context_chunks, events_per_window]

raw_flat
  per-column arrays shaped [B, coverage_events]

encoded_uint8
  compatibility path:
  headers_uint8 [B, context_chunks, 14]
  events_uint8  [B, context_chunks, 128, 16]

none
  no event tensor materialization
```

Use `encoded_uint8` only for older trainer paths that still consume the old
market-encoder byte tensors.

`raw_stream` is the preferred training path. The builder saves ordered event
tables and an origin index. The loader reads origins from the origin index,
not from every event row, because some events are not training origins. For each
origin it uses `event_row_offset` to gather:

```text
events[event_row_offset - event_stream_length + 1 : event_row_offset + 1]
```

The loader validates that the last gathered event ordinal equals
`origin_ordinal` and that the stream is ordinal-contiguous. If either check
fails, the batch fails instead of training on misaligned data.

Raw event outputs are projected at loader time, not build time. The cache keeps
the richer reusable event table, while each trainer can choose the columns it
needs. By default the raw loader suppresses debug/identity fields that are
already exposed separately:

```text
ticker_id
ordinal
timestamp_us
```

Use `event_columns` for an exact allow-list, or `suppress_event_columns` to
remove a few cached columns from the default set. If `event_columns` is set, the
allow-list is emitted exactly and the suppression list is ignored. For example:

```powershell
--event-columns event_type,price_primary_int,price_secondary_int,size_primary,size_secondary,exchange_primary,exchange_secondary,event_flags,conditions_packed,session_second
```

### Coverage Compatibility

The loader validates requested coverage against the package manifest.

For raw/encoded event modes:

```text
requested_coverage <= max_cached_event_lookback_rows
```

If requested coverage is larger, the loader fails fast and tells you to rebuild
the cache with a larger `--max-cached-event-lookback-rows`.

### Loader Output

`TickerMonthTrainingBatch` contains:

```text
ticker
origin_ordinal
origin_timestamp_us
event_output_mode
raw_event_windows
raw_event_flat
raw_event_stream
raw_event_stream_feature_names
raw_event_mask
headers_uint8
events_uint8
intraday_labels
future_intraday_bars
future_intraday_bar_mask
input_availability
text_inputs
xbrl_inputs
bar_inputs
external_context
profile
```

Only fields requested by `data_groups` and `event_output_mode` are populated.

When token data groups are requested, `text_inputs` contains:

```text
text_inputs["ticker_news"]["input_ids"]      [B, ticker_news_max_items, ticker_news_token_chunks, text_max_tokens]
text_inputs["market_news"]["input_ids"]      [B, market_news_max_items, market_news_token_chunks, text_max_tokens]
text_inputs["sec_filings"]["input_ids"]      [B, sec_filing_max_items, sec_filing_token_chunks, text_max_tokens]
text_inputs[*]["attention_mask"]             same shape as input_ids
text_inputs[*]["chunk_mask"]                 [B, max_items, token_chunks]
text_inputs[*]["item_mask"]                  [B, max_items]
text_inputs[*]["item_timestamp_us"]          [B, max_items]
```

Selection is as-of each origin timestamp. The loader takes the latest tokenized
items with `timestamp_us <= origin_timestamp_us`, fills available chunks by
`token_chunk_index`, and leaves missing items/chunks as zero with false masks.
Default limits are:

```text
ticker_news_max_items: 8
market_news_max_items: 16
sec_filing_max_items: 4
xbrl_max_items: 512
text_max_tokens: 1024
```

When `xbrl` is requested, `xbrl_inputs` contains one array per XBRL attribute:

```text
xbrl_inputs["mask"]                         [B, xbrl_max_items]
xbrl_inputs["value"]                        [B, xbrl_max_items]
xbrl_inputs["fiscal_year"]                  [B, xbrl_max_items]
xbrl_inputs["period_end_days"]              [B, xbrl_max_items]
xbrl_inputs["taxonomy_id"]                  [B, xbrl_max_items]
xbrl_inputs["tag_id"]                       [B, xbrl_max_items]
xbrl_inputs["unit_id"]                      [B, xbrl_max_items]
xbrl_inputs["form_id"]                      [B, xbrl_max_items]
xbrl_inputs["row_kind_id"]                  [B, xbrl_max_items]
xbrl_inputs["location_id"]                  [B, xbrl_max_items]
xbrl_inputs["mapping_confidence"]           [B, xbrl_max_items]
xbrl_inputs["time_*"]                       [B, xbrl_max_items]
```

Selection is as-of each origin timestamp, using the latest XBRL rows with
`timestamp_us <= origin_timestamp_us`. Missing rows are zero-filled and masked
with `xbrl_inputs["mask"] == False`. Categorical ids are mapped from the
monthly `global/category_references.parquet` file. Id `0` means missing or
unknown.

When daily/global bar groups are requested, `bar_inputs` contains:

```text
bar_inputs["ticker_daily_bars"]["values"]    [B, ticker_daily_bar_offsets, 9]
bar_inputs["ticker_daily_bars"]["mask"]      [B, ticker_daily_bar_offsets]
bar_inputs["global_daily_bars"]["values"]    [B, global_symbols, global_daily_bar_offsets, 9]
bar_inputs["global_daily_bars"]["mask"]      [B, global_symbols, global_daily_bar_offsets]
bar_inputs[*]["offsets"]                     completed daily-bar row offsets
bar_inputs[*]["feature_names"]               open, high, low, close, volume, dollar_volume, trade_count, quote_count, vwap
```

The default ticker offsets are `1,2,3,7,14,28,40,200`. The default global
offsets are `1,2,7`. Bars are selected with a completion lag before they become
eligible, so a full current-day daily bar is not used for an intraday origin.

Pivoted intraday label files are expected to have one row per saved origin.
The loader first checks whether label rows are already origin-row aligned. If
they are not, it builds a strict origin-ordinal to label-row map and gathers
only the label rows requested by the current materialization chunk.

## Profile Loader

Workstation form:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\research\mlops\rolling_loader\run_profile_ticker_month_loader.py
```

The no-arg default is a repeatable sliding-stream benchmark over
`train_201902_201907_ticker_month`, month `2019-02`, with:

```text
dataset_id: bench_small_201902_v1
event_output_mode: raw_stream
event_stream_length: 1024
sample_fraction: 1.0
max_origins_per_epoch: 1,000,000
batch_size: 4096
batches: 16
loaded_parts_per_group: 8
read_workers: 4
materialize_workers: 16
materialize_chunk_size: 512
```

Module form:

```powershell
python -m research.mlops.rolling_loader.run_profile_ticker_month_loader
```

Profile encoded compatibility mode:

```powershell
python -m research.mlops.rolling_loader.run_profile_ticker_month_loader `
  --cache-id train_201902_ticker_month `
  --month 2019-02 `
  --event-output-mode encoded_uint8 `
  --batch-size 4096 `
  --batches 8
```

The profiler prints:

```text
profile_seconds.loader_init_seconds
profile_seconds.origin_load_seconds
profile_seconds.sample_refs_seconds
profile_seconds.payload_load_seconds
profile_seconds.identity_seconds
profile_seconds.event_seconds
profile_seconds.raw_stream_validate_seconds
profile_seconds.raw_stream_matrix_seconds
profile_seconds.raw_stream_gather_seconds
profile_seconds.label_seconds
profile_seconds.xbrl_seconds
profile_seconds.context_seconds
profile_seconds.materialize_wait_seconds
profile_seconds.ready_concat_seconds
```

These timings are intentionally block-level so bottlenecks can be assigned to
SSD reads, origin filtering, stream matrix conversion, sliding-window gather,
label materialization, worker wait, or ready-buffer concatenation.

```text
discovered parts
batches
samples
elapsed seconds
samples/sec
materialization seconds
profile_seconds
max RSS
first batch shape summary
loader state summary
```

`profile_seconds` breaks emitted materialization time into
`identity_seconds`, `event_seconds`, `label_seconds`, `xbrl_seconds`, and
`context_seconds`.
Use it to identify whether the trainer is waiting on event-window gather,
intraday label gather, XBRL as-of materialization, or optional context work.

For fraction-only benchmark sampling, the loader uses a deterministic
vectorized per-part mask instead of hashing every origin in Python. Hash-bucket
splits still use the exact origin-hash path so train/validation membership stays
non-overlapping.

By default the profiler appends JSONL summaries to:

```text
D:\market-data\prepared\data_provider_profiles\ticker_month_loader_profile.jsonl
```

Override the report path with:

```powershell
--report-path D:\market-data\prepared\data_provider_profiles\ticker_month_loader_profile.jsonl
```

Disable report writing only when intentionally profiling terminal output:

```powershell
--no-report
```

Save a replayable loader checkpoint with:

```powershell
--save-state-path D:\market-data\prepared\data_provider_profiles\loader_state.json
```

The no-arg default already saves state to:

```text
D:\market-data\prepared\data_provider_profiles\ticker_month_loader_state.json
```

Resume the same dataset plan and cursor with:

```powershell
--load-state-path D:\market-data\prepared\data_provider_profiles\loader_state.json
```

The loader updates state before yielding each batch. A checkpoint saved
immediately after receiving a batch resumes after that batch, not at the same
origin again. If a batch completes a package group, `origin_cursor` may equal
the number of selected origins in that group until the iterator is resumed; on
resume the loader slices to empty, advances `package_position`, and continues
without repeating samples.

## Audit Loader Batches

Run a focused audit of emitted loader batches against the SSD package files:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\research\mlops\rolling_loader\audit_ticker_month_loader_batches.py
```

The audit checks:

```text
batch shape consistency
duplicate sample identities
origin identity and timestamp against origins parquet
origin event_row_offset against events parquet
raw_stream values against source event rows
raw_stream ordinal continuity
intraday labels against label parquet
future_intraday_bars projection from labels
text token as-of selection
text token input_ids/attention_mask against token parquet
text token item/chunk masks and zero padding
deterministic first batch for same config/seed
resume-from-state next batch against uninterrupted loading
```

Use small settings for a quick smoke audit:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\research\mlops\rolling_loader\audit_ticker_month_loader_batches.py `
  --cache-id train_201902_201907_ticker_month `
  --month 2019-02 `
  --batch-size 1024 `
  --batches 2 `
  --samples-per-batch 4
```

By default the audit includes `ticker_news_tokens`, `market_news_tokens`, and
`sec_filing_tokens`, so the no-arg audit validates token tensor materialization
as well as event and label materialization.

## No-Lookahead Rules

The builder and loader must preserve these invariants:

- origins are inside the active session
- event streams/windows end at or before the origin
- event streams/windows never cross ordinal gaps
- text/SEC/XBRL context is selected as-of `origin_timestamp_us`
- labels are strictly future targets
- optional missing context is zero/masked, not silently treated as real data
- requested loader coverage must fit inside cached lookback
- invalid event windows are filtered or fail, not masked into training

## Programmatic Use

```python
from pathlib import Path

from research.mlops.rolling_loader import (
    AsyncTickerMonthBatchLoader,
    TickerMonthLoaderConfig,
)

config = TickerMonthLoaderConfig(
    cache_root=Path("D:/market-data/prepared/rolling_ticker_month_cache/train_201902_ticker_month"),
    split="train",
    months=("2019-02",),
    batch_size=4096,
    data_groups=("events", "intraday_labels", "ticker_news_tokens", "market_news_tokens", "sec_filing_tokens"),
    event_output_mode="raw_stream",
    event_stream_length=1024,
)

loader = AsyncTickerMonthBatchLoader(config)
for batch in loader.iter_batches():
    train_step(batch)
```

## Legacy Stateful Replay

The original `RollingContextLoader` path is still available. It warms bounded
in-memory caches, replays chronological ClickHouse events, creates
`RollingSamplePointer` ids, and materializes batches at the final step. It is
useful as a production-semantics reference and for profiling live-like replay,
but it is not the recommended high-throughput historical training path.

Legacy profile:

```powershell
python -m research.mlops.rolling_loader.run_training_profile `
  --database market_sip_compact `
  --events-table events `
  --index-table train_2019_to_2025 `
  --batch-size 4096 `
  --batches 4 `
  --replay-mode time-window `
  --replay-window-us 200000 `
  --context-chunks 32 `
  --context-chunk-stride-events 64 `
  --sample-stride-events 1 `
  --start-utc 2019-01-05T00:00:00Z `
  --max-threads 8 `
  --max-memory-usage 80G `
  --materialize-external-payloads
```

## Smoke Tests

Package smoke:

```powershell
python -m research.mlops.rolling_loader.test_smoke
```

Direct workstation form:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\research\mlops\rolling_loader\test_smoke.py
```

Use the ticker/month loader profiler for real cache throughput and shape
validation. Use the legacy smoke only for low-level in-memory cache mechanics.

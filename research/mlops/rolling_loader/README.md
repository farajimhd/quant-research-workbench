# Rolling Loader

This package contains one supported training-data path: the daily-index streaming cache.

Older materialized-cache, indexed-daily, replay, and ticker-month builder trials were removed intentionally. New training work should use the files below only.

## Supported Files

| File | Purpose |
| --- | --- |
| `run_build_daily_index_streaming_cache.py` | Builds the SSD cache from `events_ticker_day_index` jobs. |
| `daily_index_cache.py` | Shared daily-index cache constants, month windows, JSON helpers, and loader config parsing. |
| `daily_index_context.py` | Daily-index context queries and vectorized intraday bar/condition extraction used by the builder. |
| `daily_index_dataset.py` | Reads daily-index cache packages and materializes trainer batches. |
| `rust_chrono_loader.py` | Python `ctypes` API wrapper for the Rust chronological loader runtime. |
| `run_profile_rust_chrono_loader.py` | Standalone profiler for the Rust queue/cache hot path. |
| `run_profile_rust_real_cache_loader.py` | Reads real daily-index parquet event/origin parts, builds the real Rust rolling event cache, and profiles realistic cache throughput. |
| `run_profile_rust_full_batch_assembly.py` | Runs real daily-index batches and sends every numeric/bool batch tensor through the Rust full-batch assembly/gather ABI. |
| `run_profile_rust_native_cache_loader.py` | Opens real daily-index parquet cache artifacts directly from Rust and validates/touches events, origins, sparse context, bars, scanner, and label sources. |
| `run_build_offline_training_batch_cache.py` | Materializes real trainer batches from the daily-index cache and writes day/segment/shard parquet tensor files for compute-light training. |
| `offline_training_batch_cache.py` | Offline batch-cache manifest, parquet tensor writer, and tensor-row reader helpers. |
| `DAILY_INDEX_STREAMING_CACHE_DESIGN.md` | Design contract for the builder, cache layout, concurrency, and terminal reporting. |
| `CACHE_FIRST_CHRONOLOGICAL_LOADER_DESIGN.md` | Active cache-first chronological loader contract with ticker cache capacity, rolling context state, hybrid frontier origins, and detailed profiling requirements. |
| `RUST_CHRONOLOGICAL_LOADER_RUNTIME.md` | Rust implementation contract for the four-pool realtime/prefetch runtime and current profiling boundary. |

## Cache Layout

The builder writes a root manifest and month/ticker packages:

```text
cache_root/
  manifest.json
  build_log.jsonl
  errors.jsonl
  month=YYYY-MM/
    ticker=<utf8-hex-symbol>/
      daily_index.parquet
      manifest.json
      events/*.parquet
      origins/*.parquet
      event_metadata/*.json
      intraday_labels/*.parquet
      macro_bars/*.parquet
      news_embeddings/*.parquet
      sec_embeddings/*.parquet
      xbrl/*.parquet
      corporate_actions/*.parquet
    global/
      manifest.json
      global_macro_bars/*.parquet
      market_news_embeddings/*.parquet
      scanner/scanner_YYYY-MM-DD.parquet
```

Ticker package directory names encode the exact ticker bytes as lowercase UTF-8 hex, while package manifests keep the human-readable ticker. This avoids Windows case-insensitive path collisions such as `CPK` versus `CpK`. There is no split directory and no ticker-hash bucket. Train/validation/test periods are selected by the downstream loader/trainer.

## Builder Flow

1. Read the selected months from CLI args.
2. Query `events_ticker_day_index` to create one fetch job per ticker/day.
3. Fetch event rows by `ticker` and ordinal range, including the configured prior context rows for the first day/month boundary.
4. Process event payloads in memory with vectorized Polars operations:
   - build origin rows from non-context events;
   - build compact intraday base bars;
   - build compact intraday condition-event rows.
5. Fetch sparse context modalities independently:
   - daily macro bars;
   - ticker and market news embeddings;
   - SEC filing embeddings;
   - XBRL context rows;
   - corporate-action rows.
6. Write each modality to its package folder and finalize package/global manifests.

The builder uses separate fetch, process, and write worker pools for each modality. Ctrl+C requests a graceful stop and cancels active ClickHouse queries using daily-index query-id prefixes.

## Loader Flow

`AsyncDailyIndexBatchLoader` reads the root manifest, discovers `month=.../ticker=.../manifest.json` packages, and creates one `DailyIndexPartPlan` per event part.

The active chronological loader is specified in
`CACHE_FIRST_CHRONOLOGICAL_LOADER_DESIGN.md`. It keeps warmed production-like
ticker caches capped at 15,000 resident tickers, loads only small chronological
frontier periods, carries event and sparse context state across adjacent days,
and profiles cache, origin-frontier, and batch-assembly stages by time and
memory.

`RUST_CHRONOLOGICAL_LOADER_RUNTIME.md` contains the Rust implementation path for
the same idea. The Rust crate now has two validated boundaries:

- queue/cache/tensor hot-path profiling, including the four worker pools,
  realtime-priority queue stealing, shared-buffer handoff, per-ticker event
  cache update, ready batch accounting, and generic numeric/bool tensor
  assembly/gather;
- native Arrow/Parquet cache reading via `run_profile_rust_native_cache_loader.py`,
  which opens the real daily-index manifests and parquet artifacts from Rust,
  prepends saved event context rows before origin event parts, validates raw
  stream continuity, and touches sparse context/bar/scanner artifacts.

The remaining boundary is trainer integration: v3 still receives
`DailyIndexTrainingBatch` from `AsyncDailyIndexBatchLoader`. The native Rust
reader does not yet expose Rust-owned nested `x`/`y` buffers back to the trainer.

Use `run_profile_rust_real_cache_loader.py` for realistic throughput. The older
`run_profile_rust_chrono_loader.py` synthetic profiler is only a concurrency
smoke/stress test and intentionally does not read real cache files.
Use `run_profile_rust_full_batch_assembly.py` to measure the Rust final tensor
assembly boundary against real emitted batches.
Use `run_profile_rust_native_cache_loader.py` to validate direct Rust parquet
reads and manifest-driven cache integrity before moving more trainer work into
Rust-owned buffers.

## Offline Training Batch Cache

`run_build_offline_training_batch_cache.py` is the supported escape hatch when
real-time materialization is too slow for GPU training. It uses the same
daily-index chronological loader and `DailyIndexTrainingBatch` contract as v3
training, then writes fully materialized batches to SSD. Training can then use a
light reader that loads ready tensors instead of rebuilding rolling context,
labels, scanner, sparse context, and event streams at training time.

Default workstation output is on the SSD:

```text
D:\market-data\prepared\offline_training_batch_cache\<cache_id>
```

Each shard holds `10` complete batches by default:

```text
<cache_id>/
  manifest.json
  logs/
    build_log.jsonl
    shards.jsonl
  month=YYYY-MM/
    day=YYYY-MM-DD/
      segment=000000/
        shard=000000/
          shard_manifest.json
          tensors/
            ticker.parquet
            origin_ordinal.parquet
            origin_timestamp_us.parquet
            raw_event_stream.parquet
            text_inputs/ticker_news/embeddings.parquet
            xbrl_inputs/value.parquet
            future_bar_values/trade.parquet
            ...
```

Every tensor is saved as its own parquet file. Each parquet row is one batch
payload with `batch_index`, `encoding`, `dtype`, `shape`, `payload_nbytes`,
`sha256`, and `data`. Numeric and bool arrays are raw contiguous bytes; string
or object arrays are JSON-encoded. `shard_manifest.json` records tensor paths,
origin boundaries, sample counts, byte counts, source dates present, and per
batch identity summaries.

Shards are committed atomically: the script writes a temporary shard directory
and renames it only after tensor parquet files and the shard manifest are
complete. On Ctrl+C, the loader is cancelled, any fully materialized pending
shard is committed as `partial_after_interrupt`, and the root manifest is marked
`interrupted`.

Run a full February 2019 cache build on the workstation with:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\research\mlops\rolling_loader\run_build_offline_training_batch_cache.py `
  --cache-root D:\market-data\prepared\daily_index_streaming_cache\events_daily_index_2019-02 `
  --cache-id temporal_v3_offline_batches_2019-02_bs1024 `
  --months 2019-02 `
  --batch-size 1024 `
  --batches-per-shard 10 `
  --max-output-gib 2800
```

For a small smoke run:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\research\mlops\rolling_loader\run_build_offline_training_batch_cache.py `
  --cache-root D:\market-data\prepared\daily_index_streaming_cache\events_daily_index_2019-02 `
  --cache-id smoke_offline_batches_2019-02 `
  --months 2019-02 `
  --training-days 2019-02-01 `
  --max-shards 1 `
  --overwrite
```

The Rich terminal reports created batches, committed batches, saved shards,
sample throughput, parquet size, current day/segment/shard, loader telemetry,
recent shard summaries, and ETA. If `--max-batches` or `--max-samples` is not
provided, ETA is based on the loader-reported available origins for the selected
cache period.

The script does not assume a full month can fit on disk. By default it stops
when committed shard parquet bytes reach `2800 GiB`, which is intended for a
workstation SSD with roughly `3 TiB` free. Use `--max-output-gib 0` only when an
external disk quota or a much larger volume is controlling the run.

`run_profile_rust_native_cache_loader.py` now defaults to the practical
trainer-facing experiment: warm the cache once, then emit 20 complete
1024-sample batches through the same materialized batch path the v3 trainer
uses. `read_workers=0` and `materialize_workers=0` resolve to aggressive
workstation-sized defaults. Use `--mode native-artifact-smoke` only when you
want the lower-level Rust parquet reader integrity smoke.

The active v3 chronological loader now exposes these cache-first controls:

| Argument | Default | Meaning |
| --- | ---: | --- |
| `--ticker-cache-capacity` | `15000` | Maximum resident rolling event/context ticker states. |
| `--origin-cursor-chunk-rows` | `1024` | Per-ticker origin rows loaded per cursor chunk. |
| `--frontier-max-origins-per-window` | `0` | Maximum origins selected in one frontier period. `0` uses the automatic memory-bounded cap. |
| `--warm-all-ticker-caches` | on | Warm each day's ticker event caches before replaying frontier periods. Use `--no-warm-all-ticker-caches` only for targeted smoke tests. |

In chronological replay it:

1. Builds per-ticker origin cursors and selects the current hybrid frontier
   period with a k-way merge sorted by `(origin_timestamp_us, ticker,
   origin_ordinal)`.
2. Warms rolling event cache state from saved prior context rows.
3. Warms rolling context state for requested text, XBRL, corporate-action,
   bar, and scanner modalities.
4. Advances event and context caches in timestamp order for each frontier
   period.
5. Emits raw event streams from the rolling event cache.
6. Emits text, XBRL, and corporate-action tensors from rolling final-tensor
   caches. Sparse ticker/global contexts carry forward when no new row is
   available and refresh relative time features for the current origin.
7. Emits daily/global bars, intraday context bars, and scanner tensors from
   rolling final-tensor caches keyed by selected bar/scanner state signatures.
   Private loader-only `__cache_*` timestamps are used to refresh relative time
   features and are stripped before model batches are emitted.
8. Builds intraday and corporate-action labels from label/context payloads.
9. Reads optional offline scanner artifacts and emits scanner context tensors.
   Existing caches that do not yet contain scanner artifacts emit padded, fully
   masked scanner tensors unless `scanner_required=True`.
10. Emits `DailyIndexTrainingBatch`.

The loader preserves state through `state_dict()` / `load_state_dict()`. The
state includes manifest fingerprint, epoch, RNG state, and seen-sample
accounting so benchmark and validation subsets can be repeated. Training jobs
can additionally pass `days=(...)` to restrict a loader to an explicit
non-contiguous schedule discovered from the cache.

## Scanner Context Contract

Scanner context is a global day-level market-leader modality. It is keyed by
scanner snapshot time and must obey:

```text
scanner_snapshot_timestamp_us <= origin_timestamp_us
```

Scanner is intentionally built after the main daily-index cache because it
needs all tickers for a day. `run_build_daily_index_streaming_cache.py` runs
this step automatically after a successful cache build by default. Use
`--no-build-scanner` only for a targeted cache/debug run where scanner artifacts
are intentionally deferred.

1. Discover intraday bar parquet files for a day and skip empty files from
   Parquet metadata.
2. Lazily scan the valid intraday bar files. The builder does not concatenate
   the full market day into one eager in-memory table.
3. Rank every closed scanner bucket across the market.
4. Select leaders for:
   - `top_gainers`
   - `top_volume_large_cap`
   - `top_volume_mid_cap`
   - `top_volume_small_cap`
   - `top_volume_penny`
5. Save one row per ticker/scanner bucket with rank columns and compact bar
   columns for `1s`, `5s`, `30s`, and `1m`.
6. Save scanner snapshots under
   `month=YYYY-MM/global/scanner/scanner_YYYY-MM-DD.parquet`.

Scanner days are market-wide operations. `--workers` controls visible worker
slots and scheduling, but `--max-active-day-builds` limits how many full-day
scanner builds can run at the same time. The default is `0`, which resolves to
`min(workers, 4)`. This keeps concurrency after the lazy/streaming scanner
rewrite while avoiding the old failure mode where many full days were
concatenated eagerly in memory.

Rebuild scanner artifacts from an existing cache, or run a scanner-only smoke
test, with:

```powershell
python research\mlops\rolling_loader\run_build_daily_scanner_cache.py `
  --cache-root D:\market-data\prepared\daily_index_streaming_cache\<cache_id> `
  --month 2019-09 `
  --workers 8 `
  --overwrite
```

For a workstation run that has enough RAM and fast SSD bandwidth, explicitly
raise the active-day cap:

```powershell
python research\mlops\rolling_loader\run_build_daily_scanner_cache.py `
  --cache-root D:\market-data\prepared\daily_index_streaming_cache\<cache_id> `
  --month 2019-09 `
  --workers 8 `
  --max-active-day-builds 8 `
  --overwrite
```

The loader output shape is:

| Field | Shape | Meaning |
| --- | --- | --- |
| `scanner_inputs["leader_values"]` | `[B,G,K,H,3,F]` | Top-K leader bars by scanner group, horizon, and bar family. |
| `scanner_inputs["leader_mask"]` | `[B,G,K]` | True when a leader slot exists. |
| `scanner_inputs["leader_horizon_mask"]` | `[B,G,K,H]` | True when that leader has at least one real bar family for the horizon. False means the zero values are padding. |
| `scanner_inputs["leader_start_time_features"]` | `[B,G,K,H,9]` | Bar-start UTC features plus age from origin for leader bars. |
| `scanner_inputs["leader_end_time_features"]` | `[B,G,K,H,9]` | Bar-end UTC features plus age from origin for leader bars. |
| `scanner_inputs["origin_values"]` | `[B,G,H,3,F]` | Origin ticker bars for comparison with each scanner group. |
| `scanner_inputs["origin_mask"]` | `[B,G]` | True when origin ticker scanner comparison is available. |
| `scanner_inputs["origin_horizon_mask"]` | `[B,G,H]` | True when the origin ticker has at least one real bar family for the horizon. False means the zero values are padding. |
| `scanner_inputs["origin_start_time_features"]` | `[B,G,H,9]` | Bar-start UTC features plus age from origin for origin-comparison bars. |
| `scanner_inputs["origin_end_time_features"]` | `[B,G,H,9]` | Bar-end UTC features plus age from origin for origin-comparison bars. |
| `scanner_inputs["origin_rank"]` | `[B,G]` | Origin ticker rank in each scanner group. |
| `scanner_inputs["origin_in_topk"]` | `[B,G]` | Whether the origin ticker is one of the leaders. |

`G=5`, `K=5`, `H=4`, bar families are `trade`, `quote_bid`, `quote_ask`, and
`F` is padded to the max bar-family feature width. This gives the model both
market-leader context and explicit origin-ticker identity/comparison features.

## Bar Family Contract

Raw event streams keep the compact event fields `price_primary_int` and
`price_secondary_int` plus the scale bits in `event_meta`. Intraday, scanner,
daily, and macro bars do not expose those packed event names. They use decoded
float price levels in exactly three families:

```text
trade
quote_bid
quote_ask
```

The canonical future target tensors are `future_bar_values["trade"]`,
`future_bar_values["quote_bid"]`, and `future_bar_values["quote_ask"]` with
their matching masks. The older single-family `future_intraday_bars` projection
is not populated by the daily-index v3 loader path.

The loader emits the same family structure for backward intraday context,
ticker daily context, and global daily context:

| Group | Family value tensors | Family mask tensors | Family time tensors |
| --- | --- | --- | --- |
| `bar_inputs["ticker_intraday_bars"]` | `trade_values [B,H,6]`, `quote_bid_values [B,H,9]`, `quote_ask_values [B,H,9]` | each `[B,H]` | each has `*_start_time_features [B,H,9]` and `*_end_time_features [B,H,9]` |
| `bar_inputs["ticker_daily_bars"]` | `trade_values [B,O,6]`, `quote_bid_values [B,O,9]`, `quote_ask_values [B,O,9]` | each `[B,O]` | each has `*_start_time_features [B,O,9]` and `*_end_time_features [B,O,9]` |
| `bar_inputs["global_daily_bars"]` | `trade_values [B,S,O,6]`, `quote_bid_values [B,S,O,9]`, `quote_ask_values [B,S,O,9]` | each `[B,S,O]` | each has `*_start_time_features [B,S,O,9]` and `*_end_time_features [B,S,O,9]` |

Trade feature order is `open, close, high, low, size_sum, event_count`. Quote
bid/ask feature order is `open, close, high, low, size_open, size_close,
size_high, size_low, event_count`. A zero value with mask false is missing or
padded, never a real zero-price bar.

## Time Feature Contract

All absolute time features emitted by this package use UTC. New York session
fields are only added where the event/session interpretation requires them.

The loader materialization path validates time-bearing payloads before emitting a
`DailyIndexTrainingBatch`:

| Payload | Required time tensor | Width | Meaning |
| --- | --- | ---: | --- |
| `raw_event_stream` | event columns named in `EVENT_TIME_FEATURE_COLUMNS` | 12 | UTC cyclic features, `years_since_2000`, and NY-session fields per event row. |
| text embeddings | `item_time_features` | 10 | availability/published/accepted UTC features plus delta/age from origin. |
| XBRL | `time_features` | 10 | XBRL availability UTC features plus delta/age from origin. |
| XBRL | `period_end_time_features` | 7 | period-end date features plus age from origin. |
| corporate actions | `time_features` | 10 | availability UTC features plus delta/age from origin. |
| corporate actions | `effective_time_features` | 10 | effective-date UTC features plus delta/age from origin. |
| bars | `*_start_time_features` | 9 | bar-start UTC features plus bar-start age from origin. |
| bars | `*_end_time_features` | 9 | bar-end UTC features plus bar-end age from origin. |
| scanner context | `leader_start_time_features`, `origin_start_time_features` | 9 | scanner leader/origin bar-start UTC features plus age from origin. |
| scanner context | `leader_end_time_features`, `origin_end_time_features` | 9 | scanner leader/origin bar-end UTC features plus age from origin. |

`DailyIndexLoaderConfig.validate_time_feature_contract` is enabled by default.
When enabled, mismatched widths, missing required time tensors, or mismatched
time-feature-name metadata raise immediately. This keeps the v3 model path from
silently treating time as an ordinary numeric feature or sending a modality to
the wrong time role.

## Common Builder Command

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\research\mlops\rolling_loader\run_build_daily_index_streaming_cache.py `
  --cache-id train_201909_daily_index `
  --month 2019-09
```

For a full month, omit `--ticker`. For a smoke test, pass `--ticker SYMBOL`.

## Common Loader Use

```python
from pathlib import Path
from research.mlops.rolling_loader.daily_index_dataset import AsyncDailyIndexBatchLoader, DailyIndexLoaderConfig

loader = AsyncDailyIndexBatchLoader(
    DailyIndexLoaderConfig(
        cache_root=Path("D:/market-data/prepared/daily_index_streaming_cache/train_201909_daily_index"),
        months=("2019-09",),
        batch_size=4096,
        data_groups=("events", "intraday_labels", "macro_bars", "news", "sec", "xbrl", "corporate_actions"),
    )
)

batch = next(loader.iter_batches())
```

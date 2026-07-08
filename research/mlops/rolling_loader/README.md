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
| `DAILY_INDEX_STREAMING_CACHE_DESIGN.md` | Design contract for the builder, cache layout, concurrency, and terminal reporting. |

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
      scanner/scanner_YYYY-MM-DD.parquet
    global/
      manifest.json
      global_macro_bars/*.parquet
      market_news_embeddings/*.parquet
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

For each loaded part group it:

1. Loads origins first.
2. Loads requested payload groups only.
3. Materializes raw event streams from saved sequential event rows.
4. Builds intraday labels from compact base bars and condition-event rows.
5. Performs as-of lookup for text embeddings, XBRL, daily bars, and corporate actions.
6. Reads optional offline scanner artifacts and emits scanner context tensors.
   Existing caches that do not yet contain scanner artifacts emit padded, fully
   masked scanner tensors unless `scanner_required=True`.
7. Emits `DailyIndexTrainingBatch`.

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

Scanner is intentionally built as a second offline step after the main
daily-index cache:

1. Load all intraday bars for a day.
2. Rank every closed scanner bucket across the market.
3. Select leaders for:
   - `top_gainers`
   - `top_volume_large_cap`
   - `top_volume_mid_cap`
   - `top_volume_small_cap`
   - `top_volume_penny`
4. Save one row per ticker/scanner bucket with rank columns and compact bar
   columns for `1s`, `5s`, `30s`, and `1m`.
5. Save scanner snapshots under
   `month=YYYY-MM/global/scanner/scanner_YYYY-MM-DD.parquet`.

Build scanner artifacts from an existing cache with:

```powershell
python research\mlops\rolling_loader\run_build_daily_scanner_cache.py --cache-root D:\market-data\prepared\daily_index_streaming_cache\<cache_id> --month 2019-09 --overwrite
```

The loader output shape is:

| Field | Shape | Meaning |
| --- | --- | --- |
| `scanner_inputs["leader_values"]` | `[B,G,K,H,3,F]` | Top-K leader bars by scanner group, horizon, and bar family. |
| `scanner_inputs["leader_mask"]` | `[B,G,K]` | True when a leader slot exists. |
| `scanner_inputs["leader_time_features"]` | `[B,G,K,H,9]` | Bar-end/start time features for leader bars. |
| `scanner_inputs["origin_values"]` | `[B,G,H,3,F]` | Origin ticker bars for comparison with each scanner group. |
| `scanner_inputs["origin_mask"]` | `[B,G]` | True when origin ticker scanner comparison is available. |
| `scanner_inputs["origin_rank"]` | `[B,G]` | Origin ticker rank in each scanner group. |
| `scanner_inputs["origin_in_topk"]` | `[B,G]` | Whether the origin ticker is one of the leaders. |

`G=5`, `K=5`, `H=4`, bar families are `trade`, `quote_bid`, `quote_ask`, and
`F` is padded to the max bar-family feature width. This gives the model both
market-leader context and explicit origin-ticker identity/comparison features.

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
| bars | `*_time_features` | 9 | bar-start UTC features plus bar age from origin. |
| scanner context | `leader_time_features`, `origin_time_features` | 9 | scanner bar time features for leader and origin-comparison rows. |

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

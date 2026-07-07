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
6. Emits `DailyIndexTrainingBatch`.

The loader preserves state through `state_dict()` / `load_state_dict()`. The state includes manifest fingerprint, epoch, RNG state, and seen-sample accounting so benchmark and validation subsets can be repeated.

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

# Stateful Rolling Loader

This package implements the production-aligned rolling-loader design from
scratch. It replaces the old dense-per-sample materialization path with bounded
stateful caches and stable sample pointers.

## Core Flow

1. A source replays chronological market events and low-frequency context
   updates.
2. `RollingContextLoader` appends each item to the correct bounded cache.
3. Every ready event origin creates or reuses 128-event chunk ids.
4. A `RollingSamplePointer` is emitted once all configured short and long event
   context lags are available.
5. Training materializes raw chunks/tokens from stable ids at the collator step.
   Production can resolve the same ids to cached embeddings.

The key rule is that low-frequency context is pushed once and referenced by id
many times. This prevents news, SEC, XBRL, macro bars, and global bars from
being rebuilt for every event-origin sample.

## Caches

Per ticker:

- event rows and encoded 128-event chunks
- latest 32 ticker-news items
- latest 16 SEC filing text items
- latest 512 XBRL rows
- ticker macro bars

Global:

- latest 64 market-news items
- global market bars

The event cache warm-loads enough prior rows to satisfy the largest configured
context lag. With the default lags this is `1850 + 128` prior events per ticker,
plus headroom.

## Sample Pointers

`RollingSamplePointer` stores stable ids:

- event chunk ids for dense recent and sparse long market context
- global news ids
- ticker news ids
- SEC filing ids
- XBRL ids
- ticker macro bar ids
- global market bar ids

The pointer is intentionally payload-free. This is what keeps both training and
production consistent while letting training fine-tune encoders.

## Materialized Batch

`materialize_training_batch()` resolves pointers into:

- `headers_uint8`: `[B, context_chunks, 14]`
- `events_uint8`: `[B, context_chunks, 128, 16]`
- context id matrices, padded with zero ids
- optional raw external payload arrays for profiling or encoder training

The profiler records materialized batch bytes so we can compare candidate batch
sizes and context sizes directly.

## Profiler

Run a local synthetic profile:

```powershell
python -m research.mlops.rolling_loader.run_profile --tickers 64 --rows-per-ticker 8000 --batch-size 4096 --batches 4
```

The default profile is ID-only for low-frequency context. This matches the
intended training/production flow where sample pointers carry stable cache ids
and the final collator decides which payloads to resolve. To diagnose raw text,
SEC, XBRL, or bar payload collation cost, add:

```powershell
--materialize-external-payloads
```

The profiler can also be run directly from a synced workstation copy:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\research\mlops\rolling_loader\run_profile.py --tickers 64 --rows-per-ticker 8000 --batch-size 4096 --batches 4
```

The profiler uses the real `RollingContextLoader` class and reports:

- warm-load time
- event cache push time
- event chunk creation time
- external cache push/pop time
- sample-index creation time
- external payload materialization time
- batch materialization time
- final batch memory footprint

Reports are appended as JSONL under:

`D:/market-data/prepared/data_provider_profiles/rolling_loader_profile.jsonl`

## Smoke Test

```powershell
python -m research.mlops.rolling_loader.test_smoke
```

Direct workstation form:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\research\mlops\rolling_loader\test_smoke.py
```

The smoke test checks that synthetic replay creates ready sample pointers and
that the materialized batch has the expected event tensor shapes.

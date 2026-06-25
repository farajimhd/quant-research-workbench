# Stateful Rolling Loader

This package implements the production-aligned rolling-loader design from
scratch. It replaces the old dense-per-sample materialization path with bounded
stateful caches and stable sample pointers.

## Core Flow

1. A source replays chronological market events and low-frequency context
   updates.
2. `RollingContextLoader` appends each item to the correct bounded cache.
3. Every eligible event origin creates or reuses 128-event chunk ids.
4. A `RollingSamplePointer` is emitted once the 32 configured context chunks
   spaced by the context stride are available.
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

The event cache warm-loads enough prior raw rows to satisfy the configured
chunk coverage. Warmup does not encode chunks. Context chunks from the warm
range are encoded lazily only when a sample references their origins. The
default market context is:

```text
chunk_size = 128 events
context_chunks = 32
context_chunk_stride_events = 64
coverage = 128 + (32 - 1) * 64 = 2112 events
adjacent_chunk_overlap = 64 events
```

Sample origins are independent from context spacing. The default
`sample_stride_events=1` means every event can become a training/serving origin,
while each sample uses context chunks ending at `origin`, `origin-64`,
`origin-128`, and so on.

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

Run a ClickHouse-backed profile:

```powershell
python -m research.mlops.rolling_loader.run_profile --database market_sip_compact --events-table events --index-table train_2019_to_2025 --tickers 64 --batch-size 4096 --batches 4 --events-per-ticker-block 64 --max-threads 8 --max-memory-usage 80G
```

The default profile source is `--source clickhouse`. It uses the configured
ClickHouse URL/user/password from the standard `.env` discovery path unless
`--clickhouse-url`, `--user`, or `--password` are passed explicitly.

The default profile uses `--context-chunks 32 --context-chunk-stride-events 64`.

The default profile is ID-only for low-frequency context. This matches the
intended training/production flow where sample pointers carry stable cache ids
and the final collator decides which payloads to resolve. To diagnose raw text,
SEC, XBRL, or bar payload collation cost, add:

```powershell
--materialize-external-payloads
```

The profiler can also be run directly from a synced workstation copy:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\research\mlops\rolling_loader\run_profile.py --database market_sip_compact --events-table events --index-table train_2019_to_2025 --tickers 64 --batch-size 4096 --batches 4 --events-per-ticker-block 64 --max-threads 8 --max-memory-usage 80G
```

The profiler uses the real `RollingContextLoader` class and reports:

- vectorized next-K per-ticker event-block fetch time
- timestamp-ordered block replay time
- low-frequency update fetch/apply time for each block
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

For local no-ClickHouse smoke profiling, pass `--source synthetic`.

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

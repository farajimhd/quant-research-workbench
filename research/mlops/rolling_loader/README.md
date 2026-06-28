# Stateful Rolling Loader

This package implements the production-aligned rolling-loader design from
scratch. It replaces the old dense-per-sample materialization path with bounded
stateful caches and stable sample pointers.

## Current SSD Cache Direction

The next implementation target is the query-driven ticker/month SSD cache
described in `TICKER_MONTH_SSD_CACHE_DESIGN.md`. That design keeps the same
stateful no-lookahead semantics, but stores raw compact event packages and
derived cache indexes instead of encoded event chunks or fully materialized
training tensors.

New cache-build rule: every stored event row must derive timestamp-based event
time features during cache construction. Absolute calendar/session features are
computed once from `timestamp_us`; origin-relative deltas are computed later
when a training batch resolves an event window.

Intraday labels are stored as origin-relative `next_*` forward labels computed
by set-based ClickHouse queries. The default SSD cache does not store dense
intraday bar grids and does not store `current_*` intraday labels.

Liquid tickers are still one logical ticker/month package, but the event,
origin, window-index, and intraday-label files are physically split into
ordinal-bounded parts. Each part uses ClickHouse's native access pattern,
`ticker = ... AND ordinal BETWEEN ...`, and includes the required prior event
lookback rows so event-window continuity is checked inside the part without
creating a training boundary at the part edge. The default maximum origin span
is controlled by `--max-origin-events-per-part`.

Build the new ticker/month SSD cache with:

```powershell
python -m research.mlops.rolling_loader.run_build_ticker_month_cache --month 2019-02 --cache-id train_201902_ticker_month
```

Or build every complete month inside a period; partial months at the boundaries
are ignored:

```powershell
python -m research.mlops.rolling_loader.run_build_ticker_month_cache --start-utc 2019-01-01T00:00:00Z --end-utc 2019-04-15T00:00:00Z --cache-id train_201901_201903_ticker_month
```

The builder writes `terminal.log`, `builder_events.jsonl`,
`builder_profile_events.jsonl`, `errors.jsonl`, and split progress JSON under
the cache root so failed workstation runs can be reviewed without copying the
interactive terminal output.

Default concurrency is tuned for the 128-core / 512GB workstation and targets
roughly 65% practical utilization without flooding ClickHouse with too many
heavy scans at once:

```text
package workers          64
max inflight packages    96
event fetch workers       6
context fetch workers    16
label fetch workers       6
CPU workers              16
write workers             8
audit workers             2
ClickHouse max_threads    8 per query
ClickHouse memory cap   120G per query
```

These defaults are intentionally not the full 128-core capacity. The event and
label lanes issue ClickHouse queries, and each query can use up to
`max_threads`; increasing those lanes too aggressively can reduce total
throughput by making the database compete with itself. For a dedicated quiet
workstation, the first override to test is usually `--label-fetch-workers 8`.

Progress reporting is heartbeat-driven, so the Rich panels and progress JSON
continue updating while the main thread is waiting on long ClickHouse futures.
The dashboard also reports active ClickHouse query count and longest active
query duration.

Ctrl+C requests a graceful stop, cancels active ClickHouse queries by their
tracked query ids, writes an interrupted manifest, and leaves completed package
directories intact.

The final audit is still part of the normal build loop. After all requested
month packages complete, the builder runs `audit_ticker_month_cache` unless
`--skip-final-audit` is passed. During package creation the audit panel remains
idle; it starts only after the build phase changes to `auditing`.

## Core Flow

1. Resolve the ticker universe and create every per-ticker cache before replay.
2. Warm-load enough high-frequency rows per ticker to satisfy context coverage.
3. Load bounded low-frequency and global context as-of the replay start.
4. Replay chronological market events and later low-frequency context updates.
5. `RollingContextLoader` appends each item to the correct bounded cache.
6. Every eligible event origin creates or reuses 128-event chunk ids.
7. A `RollingSamplePointer` is emitted once the 32 configured context chunks
   spaced by the context stride are available.
8. Training materializes raw chunks/tokens from stable ids at the collator step.
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
chunk coverage as-of the replay start timestamp. Warmup does not encode chunks.
Context chunks from the warm range are encoded lazily only when a sample
references their origins. The default market context is:

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

Pending sample pointers protect their referenced arena payload ids. Event chunk
payloads are materialized strictly: if a ready pointer references a chunk that
is no longer available, batch materialization fails instead of silently filling
zeros.

## Initialization

Use `initialize_clickhouse_replay()` to build a guide-aligned replay state:

- every ticker from the resolved universe has event, news, SEC, XBRL, and macro
  caches before replay starts
- high-frequency warm rows end at `--start-timestamp-us` when provided
- ticker/global low-frequency context is loaded once as-of the replay start
- later replay blocks fetch only incremental context updates
- cursors are positioned at the warm high-frequency boundary

A zero replay start is a compatibility mode that warms from the start of the
configured index table. The training-accurate profiler passes a replay start by
default.

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

The profiler is ClickHouse-backed. It uses the configured
ClickHouse URL/user/password from the standard `.env` discovery path unless
`--clickhouse-url`, `--user`, or `--password` are passed explicitly.

The default profile uses `--context-chunks 32 --context-chunk-stride-events 64`.
Pass `--start-timestamp-us <utc_microseconds>` to initialize all caches as-of a
specific replay timestamp. A zero value keeps the older compatibility warmup.

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

The profiler uses the real `RollingContextLoader` class and real ClickHouse
sources for both high-frequency events and low-frequency/global context. During
each event block it fetches real ticker news, global news, SEC filing tokens,
XBRL rows, ticker macro bars, and global market bars, then replays context
updates and events in timestamp order.

The profiler reports:

- vectorized next-K per-ticker event-block fetch time
- timestamp-ordered block replay time
- low-frequency/global context fetch/apply time for each block
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

## Training-Accurate Profiler

`run_training_profile.py` is the workstation-oriented profiler for measuring
the loader as a training data path. It is separate from `run_profile.py` and
does not use the loader's internal cumulative profiler for timing. Instead, it
records phase start/end events, per-batch metrics, per-block throughput, RSS
memory samples, cache sizes, and a final summary under one run directory.

Laptop form:

```powershell
python -m research.mlops.rolling_loader.run_training_profile --database market_sip_compact --events-table events --index-table train_2019_to_2025 --batch-size 4096 --batches 4 --replay-mode time-window --replay-window-us 200000 --context-chunks 32 --context-chunk-stride-events 64 --sample-stride-events 1 --start-utc 2019-01-05T00:00:00Z --max-threads 8 --max-memory-usage 80G --materialize-external-payloads --run-name train_all_b4096_tw200ms_20190105
```

Direct workstation form:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\research\mlops\rolling_loader\run_training_profile.py --database market_sip_compact --events-table events --index-table train_2019_to_2025 --batch-size 4096 --batches 4 --replay-mode time-window --replay-window-us 200000 --context-chunks 32 --context-chunk-stride-events 64 --sample-stride-events 1 --start-utc 2019-01-05T00:00:00Z --max-threads 8 --max-memory-usage 80G --materialize-external-payloads --run-name train_all_b4096_tw200ms_20190105
```

Outputs are written under:

`D:/market-data/prepared/data_provider_profiles/rolling_loader_training/<run_name>/`

The key files are:

- `profile_events.jsonl`: phase timing, block, batch, and final events
- `memory_samples.jsonl`: periodic RSS samples
- `summary.json`: final elapsed time, throughput, peak RSS, cache state, and
  initialization/replay summaries

Use `--no-materialize-external-payloads` to measure the ID-only path and
`--materialize-external-payloads` to measure the raw external payload gather
path that trainable encoders need.

The default replay mode is `time-window`: each block starts at the next eligible
market event after the current replay cursor, then loads the following
`RollingLoaderConfig.replay_time_window_us` (`200000` microseconds by default).
The first query is event-driven and does not send the full ticker universe to
ClickHouse. Tickers are discovered from each event window. A newly discovered
ticker is initialized, high-frequency-warmed through the event immediately
before its first row in that window, and loaded with bounded low-frequency
context before any of its events are replayed. Existing tickers keep their cache
state; tickers without market events in the window do not participate in that
batch window.

The default replay start is `2019-01-05T00:00:00Z`.

By default `--tickers 0` means no ticker cap. The source still uses the index
table as the eligibility filter, but ticker caches are created only when a
ticker first appears in an event window. Pass `--tickers N` only for a smaller
debug profile.

## Smoke Test

```powershell
python -m research.mlops.rolling_loader.test_smoke
```

Direct workstation form:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\research\mlops\rolling_loader\test_smoke.py
```

The smoke test is intentionally isolated from the profiler and uses generated
events only to verify cache mechanics, ready sample pointers, and materialized
event tensor shapes. Profiling should use the ClickHouse-backed
`run_training_profile.py` path when measuring real training speed and memory.

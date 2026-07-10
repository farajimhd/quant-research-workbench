# Cache-First Chronological Rolling Loader Design

This guide defines the next rolling-loader algorithm for training from the
daily-index streaming cache. It is intentionally separate from the cache-builder
design because this document is the loader contract: how cached files become
chronological, production-like training batches.

The goal is to keep the same logical state the production system would have at
an origin timestamp, but replay it from SSD instead of live gateways. The loader
must warm caches once, advance them chronologically, emit batches from the
current cache state, and profile every expensive stage.

## Design Name

Use this name in code, logs, configs, manifests, and terminal panels:

```text
cache_first_chronological_loader
```

Long form:

```text
Cache-First, Hybrid-Frontier Chronological Rolling Loader
```

## Core Principles

1. Warm modality caches before replaying origins.
2. Do not materialize the full day origin table.
3. Keep origins in bounded frontier periods, normally `1s` to `60s` depending
   on throughput and memory.
4. Merge per-ticker origin cursors by `(origin_timestamp_us, ticker,
   origin_ordinal)` instead of loading a full-day origin table.
5. Advance caches in timestamp order, exactly like production.
6. Emit samples from current cache state, not by rebuilding context for each
   sample.
7. Cap ticker cache residency so memory cannot grow without bound.
8. Carry cache state across adjacent days.
9. Rebuild cache state when days are non-adjacent or the run resumes from a
   checkpoint.
10. Record detailed time and memory profiles for every stage.

## Inputs

The loader reads one or more daily-index cache roots built by
`run_build_daily_index_streaming_cache.py`.

Required cache groups:

| Group | Purpose |
| --- | --- |
| `events` | Sequential compact event rows, including prior context rows at month/day boundaries. |
| `origins` | Origin identities for rows that may become training samples. |
| `intraday_labels` | Future intraday bar labels and event/condition labels. |
| `macro_bars` | Ticker daily bars and global daily bars. |
| `news_embeddings` | Ticker news embedding cache rows. |
| `market_news_embeddings` | Market-wide news embedding cache rows. |
| `sec_embeddings` | SEC filing embedding cache rows. |
| `xbrl` | XBRL context rows. |
| `corporate_actions` | Corporate-action input context rows and future daily labels. |
| `scanner_context` | Offline daily scanner artifact rows. |

The loader config controls which groups are requested. Missing optional groups
emit masked zeros. Required groups must fail fast if their cache artifacts are
missing or malformed.

## Ticker Cache Capacity

The loader owns one in-memory `TickerState` per resident ticker. The default
capacity is:

```text
ticker_cache_capacity = 15_000
```

CLI/config controls:

| Setting | Default | Meaning |
| --- | ---: | --- |
| `ticker_cache_capacity` / `--ticker-cache-capacity` | `15_000` | Maximum resident rolling event ticker states. |
| `frontier_max_origins_per_window` / `--frontier-max-origins-per-window` | `0` | Maximum eligible origins selected from one frontier period. `0` means automatic cap from batch size, materializer workers, and payload settings. |
| `origin_cursor_chunk_rows` / `--origin-cursor-chunk-rows` | `1_024` | Per-ticker origin rows loaded into the moving cursor at a time. |
| `warm_all_ticker_caches` / `--warm-all-ticker-caches` | `true` | Warm each day ticker's rolling event cache before replaying frontier periods. |

Each `TickerState` must track:

| Field | Meaning |
| --- | --- |
| `ticker` | Human-readable ticker. |
| `ticker_id` | Stable numeric ticker id from cache metadata. |
| `last_used_source_date` | Last source date where this ticker produced or updated a sample. |
| `last_used_timestamp_us` | Last origin/event timestamp that touched this ticker. |
| `last_loaded_source_date` | Last source date whose event/context payload was loaded for the ticker. |
| `event_cache_rows` | Number of valid rows in the rolling event cache. |
| `sparse_cache_rows` | Per-modality sparse cache row counts. |
| `ready_flags` | Per-modality readiness flags. |
| `memory_bytes_estimate` | Estimated resident bytes for this ticker state. |

When a new ticker must be added and the cap is exceeded, evict the least
recently used unprotected ticker:

```text
ORDER BY last_used_source_date ASC, last_used_timestamp_us ASC, ticker ASC
```

Protected tickers cannot be evicted:

| Protected set | Reason |
| --- | --- |
| current frontier tickers | They are about to produce samples or cache updates. |
| pending materialization tickers | Their samples are already queued for batch assembly. |
| validation fixed-sample tickers | Validation must be deterministic during the run. |
| explicitly pinned tickers | Optional debugging/benchmarking override. |

If all resident tickers are protected and a new ticker would exceed the cap, the
loader must not silently grow without limit. It should first drain pending
batches. If the cap is still impossible, fail with a clear error that reports
the cap, protected count, resident count, and requested ticker.

## Day Lifecycle

### 1. Discover Day Plan

For the next training or validation source date:

1. Find all ticker packages that have origin rows for the date.
2. Build a compact day plan containing ticker, package path, first/last origin
   timestamp, first/last ordinal, row counts, and available modalities.
3. Compute the day start/end timestamps in UTC.
4. Record day profile metadata before loading any payloads.

The day plan is metadata only. It must not load all origins for the day.

### 2. Warm Required Ticker Caches

For all tickers selected for the day, warm the resident cache state.

Event cache warm-up:

```text
event_context_length = context_chunks * events_per_window
```

For the v3 default context:

```text
context_chunks = 8
events_per_window = 128
event_context_length = 1024
```

Before the first origin of a ticker/day is replayed, the warmed event cache must
contain:

```text
previous 1023 events
```

During replay, the current origin event is appended to the rolling cache before
the sample is emitted. Therefore the emitted sample sees:

```text
previous 1023 events + current origin event
```

The builder already saved prior context rows at the month/day boundary, so the
loader should load them from the ticker package. It should not query
ClickHouse.

Sparse context warm-up:

| Modality | Warm rule |
| --- | --- |
| ticker news embeddings | Fill latest available ticker news rows with `availability_timestamp_us <= first_origin_timestamp_us`. |
| market news embeddings | Fill latest market news rows with `availability_timestamp_us <= first_origin_timestamp_us`. |
| SEC embeddings | Fill latest accepted SEC rows with `accepted_timestamp_us <= first_origin_timestamp_us`. |
| XBRL | Fill latest XBRL rows with `availability_timestamp_us <= first_origin_timestamp_us`. |
| corporate actions | Fill latest known action context rows with `availability/effective timestamp <= first_origin_timestamp_us`. |
| daily/global bars | Fill daily bars available as of the origin date. No future daily bar can enter X. |
| scanner context | Load scanner artifact index for the day; gather rows as-of each origin later. |

If a sparse modality has fewer rows than its configured context length, the top
of the cache contains available rows and the remaining older slots are masked
zeros. Zero value plus false mask means missing/padded, not a real zero.

### 3. Replay Hybrid Frontier Origins

Origins are replayed through a k-way frontier merge over per-ticker origin
cursors. The period is time bounded, and the selected origin count is capped so
memory cannot grow with a dense market burst:

```text
frontier_period_seconds = time_window_seconds
frontier_max_origins = frontier_max_origins_per_window or auto_cap
```

The automatic cap should be large enough to form many batches but small enough
to bound active payload memory. The current implementation derives it from
batch size, materialize chunk size, materializer workers, and payload-cache
settings, then caps it at 65,536 origins.

For each frontier period:

1. Ensure each ticker cursor points at its next eligible origin at or after
   `frontier_start_us`.
2. Push one next origin per ticker into a min-heap keyed by
   `(origin_timestamp_us, ticker, origin_ordinal)`.
3. Pop the earliest origin, append that origin to the selected frontier slice,
   advance only that ticker cursor, then push that ticker's next eligible
   origin if it is still inside the period.
4. Stop the slice when the period ends, the origin cap is reached, or no ticker
   has another origin in the period.
5. Materialize the selected refs in the already sorted heap-pop order. No
   additional sort is required.
6. For each origin in sorted order:
   - load the compact event row for that origin from the ticker event cursor;
   - append/update the ticker event rolling cache before emitting the sample;
   - update sparse caches if new context rows became available by this origin;
   - gather scanner rows as-of the origin timestamp;
   - create a sample view from the current cache state;
   - append the sample view to the ready-sample buffer.
7. Form training batches from ready samples.

The frontier period is the moving stream. The full day origin table should
never be resident as one large DataFrame. If a dense period hits the origin cap,
the loader keeps the same `frontier_start_us` and resumes after the last emitted
origin key. The checkpoint state stores:

```text
chronological_window_start_us
chronological_frontier_after_timestamp_us
chronological_frontier_after_ticker
chronological_frontier_after_ordinal
```

This prevents duplicate or skipped origins at identical timestamps after a
restart.

### 4. Carry State Across Adjacent Days

If the next source date is adjacent in the configured day schedule, keep the
existing cache state:

1. Do not clear ticker states.
2. Update `last_loaded_source_date`.
3. Load next-day event/context payloads only as needed.
4. Refresh sparse context caches as new rows become available.
5. Evict least-used tickers only when the ticker cap is exceeded.

If days are not adjacent, rebuild from the target day first-origin state using
saved prior context rows.

## Data Integrity Rules

The loader must enforce these invariants:

| Invariant | Required behavior |
| --- | --- |
| Event order | Per ticker, event cache ordinals are strictly increasing with no gaps inside the retained rolling window unless the cache artifact explicitly marks dropped invalid source rows. |
| Origin order | Emitted samples follow sorted `(origin_timestamp_us, ticker, origin_ordinal)` within each frontier period. |
| No lookahead in X | Sparse context rows must have availability/accepted/effective timestamps `<= origin_timestamp_us` unless they are labels. |
| Future labels | Intraday and corporate-action labels are read only from label payloads and never fed into X tensors. |
| Mask semantics | Padded/missing rows must carry false masks. The model must not interpret zero values as real data when mask is false. |
| Determinism | Given the same cache, config, seed, day list, and checkpoint, replay produces the same sample sequence and validation set. |
| Resume | Checkpoints restore epoch, day position, frontier period start, after-key, RNG state, sample counters, and enough cache metadata to rebuild or continue safely. |

## Concurrency

The loader should use bounded queues. It should never let prefetch grow until
RAM is exhausted.

Recommended lanes:

| Lane | Work | Notes |
| --- | --- | --- |
| day-plan | Reads manifests and daily indexes. | Metadata only; low concurrency. |
| cache-warm | Loads event prior context and sparse context payloads into `TickerState`. | Parallel by ticker; bounded by memory budget and file handles. |
| origin-prefetch | Maintains per-ticker origin cursors and selects the next hybrid frontier slice. | Produces compact arrays, not a full-day DataFrame. |
| cache-update | Applies sorted origin rows to ticker states. | CPU vectorization where possible; ordered commit to preserve chronology. |
| batch-assembly | Converts ready sample views into model tensors. | Runs ahead of GPU up to `prefetch_batches`. |
| gpu-consumer | Trainer consumes batches. | Loader should keep ready batches > 0 after warm-up. |

Data passed between lanes should be compact typed arrays or immutable dataclass
views. Avoid passing large Polars DataFrames through multiple queues.

## Profiling Contract

Every run must write a profile JSONL. Each record should include:

```text
run_id
split
epoch
source_date
frontier_start_us
frontier_end_us
stage
seconds
rss_before_mib
rss_after_mib
rss_delta_mib
rss_peak_mib
rows
tickers
files
bytes_read
bytes_written
queue_depth
ready_batches
ready_samples
cache_tickers
evicted_tickers
event_cache_rows
sparse_cache_rows_by_modality
message
```

Stages to profile:

| Stage | Metrics |
| --- | --- |
| `discover_day_plan` | tickers, packages, origin rows, seconds, RSS delta. |
| `load_day_indexes` | files, rows, bytes, seconds. |
| `warm_event_cache` | tickers, rows loaded, valid cache rows, missing context rows, seconds, RSS delta. |
| `warm_sparse_cache` | modality, tickers, rows loaded, padded rows, seconds, RSS delta. |
| `load_scanner_index` | scanner rows, index rows, seconds, RSS delta. |
| `load_origin_frontier` | period seconds, selected origins, cap hit, active tickers, bytes, seconds. |
| `apply_origin_frontier` | origins applied, cache updates, sparse updates, seconds. |
| `assemble_batch` | samples, tensor bytes, seconds, RSS delta. |
| `prefetch_wait` | time trainer waited for loader. |
| `evict_ticker_cache` | evicted tickers, freed estimated bytes, seconds. |
| `checkpoint_loader_state` | state bytes, seconds. |

Profiling should also produce rolling summaries for terminal panels:

| Panel | Required values |
| --- | --- |
| Training loader | day, window, origins loaded/applied, samples emitted, batches ready, loader wait. |
| Training cache state | resident tickers, ticker cap, protected tickers, event rows, sparse rows by modality, estimated RAM, evictions. |
| Validation loader | same as training loader, separate counters. |
| Validation cache state | same as training cache state, separate counters. |

All metrics and logs are keyed by samples seen, not by optimizer step. Expensive
validation/audit metrics must run at a lower frequency than training loss.

Current implementation note:

- The event stream path is cache-first and capacity-bound. It warms day ticker
  event caches from saved prior context, then reuses or single-ticker rebuilds
  when a new/evicted ticker appears in a frontier period.
- Origin rows are loaded through per-ticker cursors in small chunks and merged
  through a hybrid frontier. Each ticker contributes only its next eligible
  origin to the heap; when that origin is consumed, only that ticker advances.
  The selected frontier slice is time-bounded and origin-capped, already sorted
  by `(origin_timestamp_us, ticker, origin_ordinal)`, and checkpoint-resumable
  by its after-key. The loader no longer rescans every active origin parquet for
  every window.
- Non-event context now flows through the chronological context cache boundary
  before worker submission. This prevents materializer workers from
  independently rebuilding the same context path.
- Sparse ticker/global contexts are true rolling tensor caches in final batch
  format. Ticker news, market news, SEC filings, XBRL, and corporate actions
  are warmed at the first origin, keyed by ticker/global modality, and restored
  directly into later batches when no newly available source row exists.
  If ticker news, market news, SEC filings, XBRL, or corporate-action context
  has no newly available row for the current origin, the loader restores the
  last cached tensor state and refreshes relative time/age features against the
  current origin timestamp. Masked zeros are emitted only when no valid prior
  context exists.
- Daily/global bars, intraday bars, and scanner context are also rolling tensor
  caches. Their cache freshness is not based on sparse source-row availability;
  it is based on a deterministic state signature:
  - daily/global bars: selected completed bar starts by symbol, family, and
    offset;
  - intraday context bars: selected backward bar start/end grid timestamps by
    horizon;
  - scanner: source date, scanner bucket, and origin ticker.
  If the signature is unchanged, the cached final tensor row is restored and
  its relative start/end time features are refreshed against the current origin.
  If the signature changes, the existing vectorized/audited selector creates a
  new tensor row and updates the cache.
- Operational cache metadata such as selected start/end timestamps is held only
  inside the loader cache under private `__cache_*` keys and is stripped before
  `DailyIndexTrainingBatch` is emitted. The model receives only the documented
  X tensors and masks.
- Implementation nuance: the cache still reuses the existing vectorized/audited
  as-of selectors to create cache misses and stale rows. The state boundary and
  carried tensors now match the production-style cache contract, while selector
  math remains centralized for correctness and auditability.
- Training telemetry reports `loader/cache/text_hits`,
  `loader/cache/text_misses`, `loader/cache/text_stale`,
  `loader/cache/xbrl_*`, `loader/cache/corporate_action_*`,
  `loader/cache/bar_*`, `loader/cache/intraday_bar_*`, and
  `loader/cache/scanner_*` so runs can verify whether the cache-first path is
  being used.

## Back-of-Envelope Memory Target

With a 15,000 ticker cap and the v3 default event context:

```text
event cache per ticker ~= 1024 * 24 * 4 B + 1024 * 8 B ~= 106 KB
15,000 tickers ~= 1.6 GB plus object overhead
```

Expected practical loader CPU RAM:

| Component | Estimate |
| --- | ---: |
| event rolling cache | 1.5-2.5 GB |
| sparse context caches | 4-12 GB |
| scanner/day indexes | 0.5-2 GB |
| origin frontier slices | <0.5 GB normally |
| ready batch queue | depends on batch size; usually 1-4 GB |
| Python/Polars/Arrow overhead | 5-10 GB |

Target budget:

```text
normal: 20-35 GB
safe cap: 40-50 GB
```

If memory exceeds this range for one active day and one prefetched day, the
implementation is probably loading full-day origins or full-day decoded event
payloads instead of using rolling caches and hybrid frontier origins.

## Back-of-Envelope Timing Target

For a February 2019 scale day:

| Operation | Target |
| --- | ---: |
| cold day plan + cache warm | 1-3 minutes |
| adjacent day transition | 20-90 seconds, mostly hidden by prefetch |
| origin frontier load/apply | sub-second to a few seconds |
| first batch after cold day warm | immediately after enough samples exist in the first frontier slice |
| steady-state loader wait | near zero when GPU batch time is longer than loader window work |

If the first batch waits several minutes after cache warm-up, the loader is
still doing repeated origin scanning, rebuilding context per sample, selecting
too small a frontier slice, or filling too much materializer backlog before
yielding.

## Implementation Acceptance Checks

Before replacing the current chronological loader path, the implementation must
pass:

1. Smoke run on one day and one or two tickers with strict audit enabled.
2. Full-day warm-up profile that reports resident ticker count and RSS after
   each warm stage.
3. Origin-window audit confirming the loader never holds all origins for the
   day.
4. Event-cache audit for random samples: origin event is present, preceding
   context rows are strictly ordered, and no future event appears in X.
5. Sparse-context audit for random samples: all text/SEC/XBRL/corporate rows
   are as-of the origin timestamp or masked.
6. Scanner audit: scanner rows are as-of the origin timestamp and masked zeros
   are not treated as real rows.
7. Resume audit: checkpoint mid-day, restart, and verify the next emitted sample
   sequence matches an uninterrupted run.
8. Capacity audit: force a small ticker cap and verify LRU eviction removes
   least-used unprotected tickers and never evicts active-window tickers.
9. Trainer profile: first-batch wait, loader wait, GPU time, and cache RSS are
   all logged and visible in terminal panels.

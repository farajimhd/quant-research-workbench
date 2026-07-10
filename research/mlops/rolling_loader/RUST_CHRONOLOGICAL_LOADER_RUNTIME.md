# Rust Chronological Loader Runtime

This is the implementation target for the production-style chronological
training loader.  The goal is to replace the Python hot path with a shared-memory
runtime that keeps cache state resident and only performs small chronological
updates per origin.

## Core Runtime Shape

The Rust runtime uses four worker pools:

| Pool | Queue | Job type | Priority behavior |
| --- | --- | --- | --- |
| realtime read | `rt_read_q` | read current ticker/origin/cache data | consumes realtime first, can help prefetch if realtime is empty |
| prefetch read | `pf_read_q` | read future ticker/origin/cache data | checks realtime queue first, then prefetch |
| realtime process | `rt_process_q` | update current cache and assemble samples | consumes realtime first, can help prefetch if realtime is empty |
| prefetch process | `pf_process_q` | prepare future cache/sample state | checks realtime queue first, then prefetch |

Realtime priority never interrupts a running prefetch job.  It only controls
which queued job an idle worker takes next.

## Atomic Job Flow

1. A read job owns a ticker/modality/time-slice request.
2. The read worker loads or creates shared buffers for that request.
3. The read worker enqueues the exact process job that consumes those buffers.
4. The process worker updates resident cache state and records ready samples.
5. Batch accounting fills ready batches from sample fragments.

The read-to-process handoff moves an owned buffer handle.  The current Rust
profile implementation uses `Arc<Vec<T>>` to model shared memory without copying
between queues.  The Python wrapper calls the Rust `cdylib` through `ctypes`.

## Implemented Now

The first Rust crate is dependency-free and implements the queue/concurrency
runtime plus the event-stream cache hot path:

- four worker pools;
- realtime and prefetch queues;
- prefetch-worker priority stealing for realtime jobs;
- read jobs that hand off owned shared buffers to process jobs;
- one resident event stream per ticker;
- ordinal append/update logic;
- 1024-row event-stream snapshot copying to emulate final batch output;
- ready batch accounting and profiling counters.

This validates the most important implementation risk: whether the proposed
concurrency shape can keep shared cache state hot without Python object copying.

## Not Yet Replaced

The current Rust profile does not yet read parquet directly and does not yet
emit full trainer batches.  Full integration still needs:

- Rust-side parquet/Arrow readers or Python-fed zero-copy Arrow buffers;
- sparse context cache tensors for news, SEC, XBRL, corporate actions, bars, and
  scanner;
- label index/cache state;
- Python batch wrapper returning NumPy/DLPack-compatible tensors;
- v3 trainer switch from `AsyncDailyIndexBatchLoader` to the Rust loader.

The current crate is intentionally dependency-free so it can build in the
workstation environment without fetching PyO3/Arrow crates.

## Profiling

Build and profile from Python:

```powershell
python research\mlops\rolling_loader\run_profile_rust_chrono_loader.py
```

The profiler writes:

```text
D:/TradingML/runtimes/rolling_loader/rust_chrono_loader_profiles/rust_chrono_loader_YYYYMMDD_HHMMSS/
  rust_chrono_loader_profile.json
```

Important counters:

| Counter | Meaning |
| --- | --- |
| `samples_per_second` | Event-cache sample snapshots prepared per second. |
| `batches_per_second` | Ready batch accounting rate. |
| `read_priority_steals` | Prefetch read workers that consumed realtime read work. |
| `process_priority_steals` | Prefetch process workers that consumed realtime process work. |
| `read_worker_seconds` | Cumulative worker time spent building/read-handoff buffers. |
| `process_worker_seconds` | Cumulative worker time spent updating ticker caches and assembling sample snapshots. |
| `event_cache_rebuilds` | Ticker states initialized from read buffers. |
| `event_cache_appends` | Event rows appended into resident ticker streams. |
| `event_cache_reused` | Origins that reused already-current ticker state. |
| `allocated_gib` | Synthetic read buffer allocation volume. |

# Session-scoped autoregressive shards, sampled v2

The revised contract is **4,096 consecutive observed 1s origins per block,
all from one New York trading session**. This replaces the unlaunched monthly-v1
configuration. Retired phase-two roots remain untouched.

## Sampling and context

Freeze a seeded random order of ticker/session-month units from the retained
21,556-ticker universe. A sampled month chooses its first session using a seeded
UTC clock. Each eligible session then chooses a seeded feasible starting origin
among its observed 1s bars. The remaining origins are consecutive. One CPU owner
keeps its history in RAM across the selected consecutive sessions of that month.

Default warmup is **64 real completed bars in each of five views**: 1s, 10s, 1m,
1h and regular-session daily. A larger cache is configurable. Earlier sessions
provide context only; none of their origins can enter the current block.
A session with fewer than 4,096 observed 1s bars produces no training block, but
all its observed bars still advance the worker's history for later sessions.
Partial tails never combine with the next session.

Each view is saved once per session as a warmup prefix followed by unique new
bars. All blocks in that session share those pools. For example, given 64 warmup
10s bars and a new 10s close at 09:00:10 UTC:

| Origin UTC close | Last completed 10s row |
| --- | --- |
| 09:00:01 through 09:00:09 | 63, the last warmup bar |
| 09:00:10 through 09:00:19 | 64, newly completed at 09:00:10 |
| 09:00:20 | 65 |

A dense 4,096-origin block therefore needs roughly 64 + 4,096 1s rows and
64 + 409 new/completed 10s rows, rather than 4,096 copies of the 10s context.
Actual counts depend on observed bars and clock alignment. No synthetic empty
clock bars are created. There is **no saved tokenizer stride or nonoverlapping
64-bar window layout**. Token sampling remains a training-reader decision.

## Split and reverse-split handling

All origins in a session share one executed-action basis, using phase one's
04:00 New York execution-date rule. The worker retains an unadjusted rolling
cache, creates a session-basis copy with the shared phase-one adjustment math,
and freezes an independent snapshot before advancing to the next session.
This avoids accumulated Float32 rounding and mutation of an earlier snapshot.

Prices, including bid/ask OHLC, multiply by the split factor; volume and quote
sizes multiply by its inverse. Counts, masks and clocks remain unchanged. New
current-session bars are already on that session's source share basis.

The receipt binds the session date, basis UTC clock and frozen ticker action
hash. `ShardReader` recognizes this basis and **does not apply the split again**.
Its optional `CachedBlock.windows` helper rejects a different action catalog.
A session-basis snapshot cannot be restored as an unadjusted generation cache.

The raw canonical SIP tables remain unchanged. Saved session history is in the
current session's original market units, ready for phase one's numerical input
transform. No embeddings, decoder outputs, future targets or derived UTC
features are stored.

## Files and GPU reader

A session directory contains shared per-frame NPY pools, `[blocks,4096]` origin
records with five causal indices, pending-tail evidence and a sealed receipt.
Each pool row contains 18 Float32 market values, 18 validity masks and its actual
UTC close. Every origin references the latest completed bar with close `<=` its
own close. Publication and reads reject blocks mixing sessions.

`ShardReader(directory).block(i)` caches the required pool slices and rebases the
indices. A training reader can transfer contiguous values/masks/clocks and the
index matrix to GPU, then gather its contexts there. Structured NPY field views
should be made contiguous before conversion to Torch tensors. `MonthReader`
remains a name alias for this same strictly validated reader.

## Years, selection and missing history

Training uses **2019–2025 New York session dates**; validation uses **2026
session dates**. Timestamps are always UTC. All of the December 31 after-hours
session stays in training even when its UTC timestamps reach January 1.
Validation context may contain completed prior-year bars.

Selection admits three liquid full blocks per one less-liquid full block in
each split, preserving 75/25 selected-origin exposure. The validation shard-data
budget is **250,000,000 bytes**, including context, NPY headers and publication
receipts. It is an upper bound: whole sessions/full blocks are admitted, so the
final size can be smaller. The train cap defaults to 4 decimal TB. Shared plan
and catalog metadata is outside these shard-data budgets.

A genuine 64-bar daily context needs 64 observed daily bars, not 64 calendar days.
The workstation currently has no canonical 2018 events or ordinal inventory, so
early-2019 origins are deferred until enough real history exists, often about
three calendar months and longer for sparse tickers. This is measured per sampled
session. No missing history is padded into a valid block. Short/history-deficient
sessions receive sealed SQLite deferrals with counts and reasons.

## Workers, source pages and safety

Defaults are 32 CPU processes plus a **separate eight-thread I/O pool**. CPU
owners fetch and compile session snapshots; I/O workers hash, fsync and atomically
publish them. Queues and staged monthly results are bounded. Frozen snapshots
let CPU work advance without changing arrays owned by an I/O worker.

V3's actual extractor used calendar-month source pages despite its unused
`query_days=7` setting, 65,536-row Arrow batches and up to four prefetched pages.
This extractor uses the same month alignment and Arrow batch size, with global
bounded query concurrency rather than four extra lanes per CPU worker.
`--chunk-events 0` selects that default; a positive value enables an optional
session-bound event budget for measured dense cases. Query size is independent
of the 4,096-origin storage block size.

Only canonical `market_sip_compact.events_YYYY` is read. The unchanged current
phase-one aggregation supplies all five frames, including quote-only updates,
condition rules, quote carry/invalidation and final paired quote masking.
There is no rollup from only saved trade-bearing 1s bars or old v3 feature code.

Queries have one ClickHouse thread, 60 seconds and at most 8 GiB. RAM/disk reserves
remain 64/150 GiB; query admission is reduced during trainer validation or stale
status. The 2026 extension verifies policy, actual part placement, schema,
ordinal counts and corporate-action coverage. Coverage gaps fail closed.
No acquisition, flatfile fallback or global server changes are introduced.

## Running and restart

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
& C:/Users/Mehdi/miniconda3/envs/ml4t/python.exe -B research/bar_gpt/v5/run_phase2_ar_shards.py --source-revision <committed-revision>
```

Preview is the default. Execution requires `--execute`, a released corrected
phase-one parent and a certified 2026 authority extension. Default output is
`D:/TradingML/runtimes/bar_gpt/phase2-ar-sampled-v2`. Retired roots and the old
monthly-v1 root cannot be reused. `--only-ticker` / `--max-months` bound a pilot
through the same path; a pilot cannot declare a full release.

Completed monthly batches are sealed after all session publications/deferrals
are acknowledged. Resume skips them without source queries. An interrupted
partial month is recomputed from the frozen start; existing session receipts
are preserved and reused. A `STOP` gracefully drains work, a lease prevents
duplicate ownership, and the coordinator holds its own keep-awake request.

`progress.json` exposes CPU/I/O/query activity, staged sessions, counts, bytes
and failures. Receipts reconcile selected candidate origins into full blocks,
pending tails and history deferrals. Shards remain `training_ready=false` until
the phase-two future-target/purge contract is finalized. This extractor never
signals the trainer or repair, and does not resume scheduled monitoring.

# Monthly autoregressive context shards

This replaces the cancelled phase-two stream generator. It adopts v3's
ticker-owned chronological extraction and shared monthly arrays, using the
current phase-one market/quote semantics. The old roots remain retired.

## What one block means

Every published logical block contains **exactly 4,096 observed 1s origins**.
An origin is the close of an actual trade-bearing 1s bar, not a fabricated
one-second clock tick. Each origin stores its UTC close and five as-of indices.
The 1s reference equals that close; every other reference is the latest completed
observed bar whose close is at or before it. A block is a loader/storage unit,
not an attention sequence length or one tokenizer sample.

Default cached history per origin:

| Frame | Real historical bars | Nonoverlapping 64-bar windows |
| --- | ---: | ---: |
|1s|1,024|16|
|10s|512|8|
|1m|512|8|
|1h|256|4|
|Daily, regular session|128|2|

These are cache budgets supporting 38 historical tokenizer windows with stride 64.
Token width, token overlap, forecast advance and decision sampling are separate
concepts. The reader can choose another overlap within the cached budget. For
example, 1,024 bars support 16 windows with stride 64 or 31 with stride 32. Changing
origin by one observed 1s bar advances that frame's endpoint by one; coarser
endpoints advance only after another real coarse bar completes. This extractor
does not finalize latent targets or train the model.

For 4,096 consecutive origins, 1s context is typically 4,096+1,023 shared rows,
rather than 4,096 copies of 1,024 rows. Coarse history is also shared; repeated
as-of references point at the same bars. `MonthReader.block(i)` loads the
necessary slice of each view once. `CachedBlock.context(origin,frame)` then
does no disk or source queries. Reuse a `MonthReader` across its blocks: it
hash-checks each touched mmap part once.

## Extraction and files

One owner processes a ticker's months across years. Canonical ClickHouse reads
use the frozen phase-one ordinal inventory and explicit UTC-month partition
predicates covering complete 04:00–20:00 New York source sessions. Query pages
target 2 million events; an indivisible dense session may exceed that soft size
and will fail closed if bounded query resources are insufficient. Fine and
coarse bars use the shared phase-one aggregation, including quote-only source
updates, quote invalidation/carry, eligibility and final paired quote masking.
There is no rollup from only saved trade-bearing 1s bars.

Each ticker-month directory has:

- Shared per-frame raw BAR arrays in NPY parts of at most 128 MiB of array data.
  A row contains 18 Float32 market values, 18 masks and its actual UTC close.
- `origins.npy`: `[blocks,4096]` records with close and five cached indices.
- `pending.npy` and cache-slice metadata: exact rolling context/restart state.
- A sealed `receipt.json` binding authority, geometry and file sizes/SHA256.

Monthly publication flushes/fsyncs payloads and atomically renames its owned
partial directory. Restart discovers sealed months, restores the last cache and
pending origins, and queries only missing months. Complete blocks may span
months. The current month contains their shared prefix, so no neighboring file
lookup is needed to read their input context.

Bars stay on their original share basis. `CachedBlock.windows(...)` gathers
64-bar phase-one records in a vectorized batch, applies corporate actions using
each decision's common origin (including quote prices/inverse sizes), and leaves
normalization and nine UTC features to the existing phase-one transform.
The caller supplies the frozen ticker actions named by the authority. No
embeddings, decoder outputs, future targets or transformed UTC features are
stored. The full query inventory is retained independently of selected blocks.

## Coverage, splits and selection

Keep the current 21,556-ticker inventory and its frozen liquidity classes.
Selection admits three liquid blocks per one less-liquid block independently
for training and validation: exact 75/25 selected-origin exposure because all
blocks have 4,096 origins. Unselected complete blocks remain stored and counted.

The chronological cutoff is 2024-04-16 04:00UTC, matching the actual phase-one
train-05 split. Input context crossing it receives split 2 and is not selected.
Validation context must lie wholly after it. A future-target reader must still
purge targets crossing the boundary; these raw-context shards are explicitly
marked `training_ready=false` until that learning contract is finalized.

No padding is promoted into a full block. Origins with insufficient history are
counted as warmup-deferred; their source bars remain available in shared arrays.
Partial blocks carry across months. A liquidity/split boundary defers and counts
the prior partial tail. A ticker's final tail remains saved and counted in its
checkpoint. Sparse tickers may therefore have no eligible full block; this is
reported, not silently hidden.

## Running

From the immutable synchronized code snapshot, preview without creating output
or querying ClickHouse:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
& C:/Users/Mehdi/miniconda3/envs/ml4t/python.exe -B research/bar_gpt/v5/run_phase2_ar_shards.py --source-revision <committed-revision>
```

Execution adds `--execute`. Default output is the fresh
`D:/TradingML/runtimes/bar_gpt/phase2-ar-monthly-v1`; it never reuses retired roots.
Both preview and execution fail explicitly while the corrected parent's final
ordinal manifest is unavailable. Execution additionally requires its sealed
complete repair release, unchanged phase-one producer code, canonical authority
and corporate-action catalog. There is no compatibility bypass.

Defaults: 32 bounded process workers, 8 simultaneous background publications,
4 query lanes during validation/startup/stale trainer status, one ClickHouse
thread/query, 60 seconds/query, 3 GiB/query, 64 GiB RAM and 150 GiB disk reserves.
`--history-bars` freezes alternative cache budgets; it never changes 64-bar token
width. `--only-ticker TICKER --max-months N` runs a bounded pilot through the same
path and cannot report a full build. `--target-bytes` defaults to 4 decimal TB and
stops new admissions at that soft, month-aligned cap; already active months
drain and can exceed it. Writes separately check disk reserve before publication.

`progress.json` and 15-second console reports expose active queries/months, queued
tickers, source origins, candidate/selected blocks, deferred tails and failures.
`timing.jsonl` separates query, compile and publication time. SQLite WAL is owned
by one coordinator; monthly acknowledgments update cached progress counters.
Create `STOP` for graceful drain. Existing STOPs are never cleared, a controller
lease prevents duplicate ownership, and keep-awake is held only while running.
This extractor never signals the phase-one trainer or repair.

# V18 causal feature expansion

Copied from V17. Its global/local costs, 50/50 reproduction, genetic quotas,
execution accounting, random-only initialization and display-only validation
remain unchanged. Existing feature IDs 0..901 are preserved; 55 causal channels
are appended (957 total). V17 code, inputs and running campaign are untouched.

## Added inputs

* Own clock-based log returns and volatility: 5/15/60/300 seconds.
* Volume/trade acceleration and 15-second volatility versus round-trip fees.
* Completed 5/15/60-second candle body, range, close location, return,
  log volume/trades and active-second fraction.
* Full pinned preopen universe (unique ticker, not venue-alias-weighted or
  top-ranked-only) 15/60/300-second median/mean return, advancing fraction,
  dispersion, coverage, own relative return and average-tie return percentile.
* Full-universe trailing-60-second dollar-volume percentile and 15-second change.
  This is an activity rank; it does not relabel the original attention ranking.

Every source endpoint is at or before the completed feature clock. The existing
consumer selects causal features before its decision time. Returns never cross
sessions; both endpoints must have valid prices and be at most one second old.
Volatility needs all one-second price increments. Candle extrema need complete
valid source seconds. Receipts preserve the full preopen census and missing canonical-source units.
Unavailable tickers remain masked; coverage uses the full preopen denominator,
never a universe inferred from later event presence.
Context aggregates currently observed valid tickers and
exposes coverage; fewer than two peers masks statistics/ranks. Missing evidence
is NaN in storage and false validity in the evaluator, never a known zero.
Early session history remains masked rather than borrowing overnight prices.

## Genetic operations

`STD` (opcode 23) is population standard deviation over an observed-candle
window. Its output retains the operand unit. All samples in the window must be
known. It is available in initialization, subtree crossover, operand/window and
operator mutation, pruning, complexity and semantic identity. Existing temporal
operators still count observed candles; prepared elapsed-time returns use real
seconds. CUDA STD uses a bounded Welford scan. CPU/Torch is the tested oracle;
STD agrees within floating-point tolerance, existing operations remain exact.

CPU evaluation residency is an LRU of at most four sessions per worker; active
evaluation and the two bounded plans retain their own references. This prevents
year-long datasets from accumulating every session in RAM.

New features participate in typed family sampling and compatible substitutions.
Market ranks/aggregates are prepared once, never recomputed per candidate.
Enriched gather is fused into the existing one-pass CUDA feature projection.

## CPU shard completion

Use immutable `v8-split-manifest-v1` inputs with indicator banks already bound.
The script supports both the current and January-onward datasets without hardcoded
dates. It reads certified ARTE bars whose units and pinned snapshots are verified,
checks storage policy and actual parts, and never opens raw SIP files or writes
ClickHouse tables. Two to four workstation CPU processes prepare independent
sessions; each query uses one server thread. Dense full-market scratch is
runtime-only, disk-backed; native reductions/sorts use 1024-second tiles.
RAM/disk admission, bounded outstanding jobs, day/ticker progress, failures,
cooperative STOP and sealed-day resume are explicit. Unsealed failed days are
recomputed; sealed outputs are verified and never overwritten.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -u -m research.vectorized_backtest.v18.prepare_enriched --training <training.json> --validation <validation.json> --output D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v18/enriched/<source-id> --workers 4
```

Output `training.json` and `validation.json` bind per-session enriched receipts.
They can be supplied to the queue; future manifests must be explicitly bound.
A pending expanded dataset never silently falls back to the old 30/6 split.

## Deferred launch and strict speed gate

`run_after_v17 --job <runtime-job.json>` waits for V17's successful exit, all
requested generations, both final seals, released owner lock and exited exact
controller PID/creation time. Interrupted or failed V17 cannot trigger V18.
A runtime STOP cancels the queue before launch. Pending datasets stay pending.

The queue first runs `qualify_v17`: workstation tests, identical 1024-member
legacy accounting on two full training sessions (no result reuse), and separate
native 1024-member V17/V18 searches over the chosen complete training/validation
sets with batch512/two workers. One cold and three warm generations include
selection, validation and publication. Both warm median ratios must be <=1.0.
V18 is not declared as fast until this benchmark passes. Failed qualification
blocks launch and retains evidence; no wider tolerance or silent feature removal.

`run_train` independently verifies successful V17 completion, qualification,
source hash, exact bound manifests, predecessor checkpoint and optimizer ownership.
It launches a fresh 50-generation V18 population using seed182237. Tests and
benchmarks do not import selected strategies into the final population.

Job fields: `predecessor` (pinned active JSON), `source_sha256`, `commit`,
`training`, `validation`, `dataset_pending`, `qualification` (fresh runtime
folder), `campaign` (own runtime folder), and `generations` (default50).
Logs/status/receipts and temporary arrays belong exclusively under runtimes.
The inherited older qualification utilities are archival; `qualify_v17` is the
only launch authority for V18.

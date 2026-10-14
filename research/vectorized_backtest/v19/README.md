# V19 profitability and activity objectives

V19 copies V18 features, GA operations, causal data bindings, vectorized FP64 protected replay, and execution limits. It reuses the certified V18 enriched shard format; no feature extraction is repeated.

Global score adds 0.10 times pooled trade-trimmed profit to V18's session-trimmed profit/downside score. Global parents require positive net and session-trimmed profit, >=20 complete positions and >=8 active training sessions. Local score retains V18 arithmetic; local parents require >=3 complete positions and positive trade-trimmed profit in their source session. Upper10% positive contributions are trimmed; all losses remain. Risk limits remain <=20% actual stop distance and <=$300 planned per-stock risk.

Global and local eligibility are independent. The selection/reproduction budgets, ancestry/mating, mutation and fresh exploration remain unchanged. Empty eligible parent pools produce fresh candidates, never relaxed gates. Profit-ranked diagnostic rows remain visible. If neither path qualifies, validation observes a frozen feasible diagnostic shortlist without authorizing it as parents. Validation never affects reproduction.

Run a fresh random1024 population, batch512, two GPU session workers,50 generations on the same chronological24/12 data. Six validation sessions have recorded prior V17 training exposure. Do not describe this split as pristine held-out data. Stop V18 only at a sealed training+validation boundary with hashed checkpoint/process proof. `run_after_v17` is retained as a compatibility filename but is V19's V18-handoff controller. `qualify_v17` now freshly benchmarks V18 vs V19, including full selection/validation/publication. Both ratios must be <=1.0 before launch; first real generation also receives risk and complete-time verification.

The inherited technical documentation follows; V18 feature-producer identifiers intentionally remain unchanged for certified shard compatibility.

# V18 causal feature expansion

Copied from V17. Its 50/50 reproduction, genetic quotas,
execution accounting, random-only initialization and display-only validation
remain unchanged. Existing feature IDs 0..901 are preserved; 55 causal channels
are appended (957 total). V17 code, inputs and running campaign are untouched.

## Risk protection and costs

Normal V18 optimization requires protected execution. Fixed stop genes remain
evolvable from 0.01% to 20%; actual fixed and swing stop distances are checked
at entry. Entries exceeding 20% are rejected, never silently clamped. Additions
must keep average-cost-to-stop distance within 20% and total planned stock-position
risk within $300. Planned risk includes surviving shares' average-cost loss to
stop, paid costs/net realized losses, and estimated exit costs; realized gains
do not subsidize more risk. Gaps/adverse fills can exceed this planned budget.
This is a per-stock budget, not a portfolio-wide capital constraint.

Global scoring retains 10% positive session trimming and now charges 5% of
position count times worst-10% mean position loss, 20% of session count times
worst-20% mean session loss, 10% of summed session drawdowns, 25% of the worst
session drawdown, 1% of stop-risk dollar-hours and 50% of peak position risk.
The capital-time penalty remains unchanged. Local scoring retains 10% positive
trade trimming and other-session loss penalty, and applies the same trade-tail,
session drawdown, stop-risk-hour and peak-position-risk charges. Global and local
eligibility reject receipts exceeding the fixed risk envelope. Rejected entries
and additions, peak risk and maximum stop distance are reported in rankings.

Legacy accounting parity is benchmarked with guards disabled only inside the
accounting oracle. Native full-generation speed qualification uses the protected
V18 execution and updated costs. Optimization cannot disable protection.

Protected CUDA replay uses a financial kernel over 128-listing tiles plus a
bounded candidate-statistics reduction. Arithmetic remains double precision with
multiply/add fusion disabled; Torch is the independent financial oracle. Entire
tiles skip prospective entry/add risk calculations when no action is requested;
held-position risk and exposure remain accounted. CUDA trajectory, boundary and
complete-ledger tests verify the path. `protected_kernel=False` in `replay_cohort`
selects the protected Torch reference for comparison, without disabling limits.

## Added inputs

* Own clock-based log returns and volatility: 5/15/60/300 seconds.
* Volume/trade acceleration and 15-second volatility versus nominal $1,000
  round-trip entry fees ($0.005/share, minimum $1/order, integer shares).
  This is a causal cost reference, not a prediction of actual exit prices or adds.
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

Per-stock outputs cover only listings that enter the pinned top 10, including
their causal compact history. All other compact rows remain masked. Full candle
fields are read only for those listings; peer reads project only completed close,
price validity and notional for broad-market aggregates/ranks. Receipts record
the selected listings and required, prepared and unrequested row counts.

Use immutable `v8-split-manifest-v1` inputs with indicator banks already bound.
The script supports both the current and January-onward datasets without hardcoded
dates. It reads certified ARTE bars whose units and pinned snapshots are verified,
checks storage policy and actual parts, and never opens raw SIP files or writes
ClickHouse tables. Two to eight workstation CPU processes prepare independent
sessions; each query uses one server thread. Dense full-market scratch is
runtime-only, disk-backed; native reductions/sorts use 256-second tiles.
Admission requires 2 GiB per worker plus 8 GiB host headroom (16 GiB for four
workers). New session assignments pause below 8 GiB available memory; active
sessions finish without abandoning their work. These are bounded admission
budgets, not measured peak-memory guarantees.
The feature certificate binds calculation AST and math bytes independently of
scheduling policy. `repartition` creates immutable 24/12 bindings, preserves
original receipts and values, and explicitly records six validation sessions'
prior V17 training exposure. Reuse of existing enriched values requires exact
calculation identity and verification against the original producer certificate.
Unchanged source sessions are read-only directory bindings; moved sessions have
new input/history/cache/indicator receipts over immutable linked arrays.
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

`run_after_v17 --job <runtime-job.json>` normally waits for V17's successful
completion. The user-requested `stop_when_features_ready=true` handoff instead
verifies every bound feature certificate before requesting V17's generation-boundary
STOP. A hashed handoff receipt binds the completed checkpoint, both generation
seals, released owner lock, and exited controller/worker PID identities. Only
this explicit handoff permits an interrupted predecessor; arbitrary failures do not.
A runtime STOP cancels the queue before launch. Pending datasets stay pending.

The queue first runs `qualify_v17`: workstation tests, identical 1024-member
legacy accounting on two full training sessions (no result reuse), and separate
native 1024-member V17/V18 searches over the chosen complete training/validation
sets with batch512/two workers. One cold and three warm generations include
selection, validation and publication. Both warm median ratios must be <=1.0.
V18 is not declared as fast until this benchmark passes. Failed qualification
blocks launch and retains evidence; no wider tolerance or silent feature removal.
After a V18-only change, `--reuse-baseline` may reuse the completed V17 side of
a prior attempt only after verifying its source, manifests, predecessor checkpoint,
population, production execution contract, all four seals and recorded timings.
V18 accounting and native-generation measurements are always rerun on new code.

`run_train` independently verifies successful V17 completion or the authorized
sealed handoff receipt, qualification,
source hash, exact bound manifests, predecessor checkpoint and optimizer ownership.
It launches a fresh 50-generation V18 population using seed182237. Tests and
benchmarks do not import selected strategies into the final population.
The queue monitors the first real generation's seals, financial/risk invariants
and full wall time against the matched V17 cold generation. Ratio >1.0 or failed
invariants request a safe V18 STOP and retain `first-generation.json` for diagnosis
and a fresh, requalified rerun. Fixes remain an agent task, not hidden auto-tuning.

Job fields: `predecessor` (pinned active JSON), `source_sha256`, `commit`,
`training`, `validation`, `dataset_pending`, `qualification` (fresh runtime
folder), `campaign` (own runtime folder), and `generations` (default50).
Logs/status/receipts and temporary arrays belong exclusively under runtimes.
The inherited older qualification utilities are archival; `qualify_v17` is the
only launch authority for V18.

Latest user instruction: do not run long qualifications. Use run_train --first-generation-check for direct launch after focused tests, certified dataset validation and sealed predecessor-stop proof. This mode records timing_verification=first-real-generation and no prelaunch speed pass; monitor the first actual generation's seals, risk and wall time. The standalone qualification mode remains available only when requested.

Performance repair: complete-position profits already have contiguous candidate/session groups. Native stable sorts within each group replace the redundant global two-key lexsort; reduction order, trimming, cost coefficients and eligibility remain unchanged. Validate against sealed rankings before migrating a checkpoint; preserve old source/campaign and exact GA state.

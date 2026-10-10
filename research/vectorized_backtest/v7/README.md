# V7 lightweight rule optimization

V7 searches causal feature programs using an assumed-fill position evaluator.
V6 remains the detailed financial broker version. No V6 campaign, checkpoint,
population, or immutable deployment is migrated or resumed by V7.

Each entry buys $1,000 of fractional shares; each add buys $200. There is no
cash, buying-power, liquidity-capacity, spread, retry, or pending-order simulation.
Adds are bounded by an optimized count of 0–5 and a management cooldown.
Cost basis stays unchanged through elapsed clocks and partial sales. Entries
set basis to the fill price; adds use an incremental weighted basis update.
This avoids repeated arithmetic drift changing exact zero-profit eligibility.
Entry/exit/add/reduce/trail programs, percentage stops and profit-taking,
partial reductions, and certified swing stops remain position-aware.
Portfolio replacement is absent because this evaluator has no capital ceiling.

## Causality and feature access

Programs decide at a 1-second close. Orders execute at the next **observed**
trade close, with fractional assumed fills. Stops and percentage targets also
use previously completed prices; they do not assume favorable intrabar fills.
Terminal liquidation uses the last known certified mark at session end,
even if that ticker has no contemporaneous trade. Missing terminal prices
make that strategy/session infeasible. These assumptions need execution-aware
validation before interpreting profit as tradable performance.

Rules can use all original 149 feature channels, the 332 persisted market-history
channels, relative-price versions, and eleven quote/price/activity channels. The latest
causal bid/ask/spread snapshot is sampled at each second close, with quote age;
quotes older than one second are invalid. These are **snapshots**, not fabricated
bid/ask OHLC bars. Quote/activity inputs are strategy features only. None enter
the position engine as execution constraints.

Rule windows count elapsed seconds. Indicators/levels carry their most recent
causal observation; histories include every session clock before and after rank
entry. Activity is zero on clocks without a current execution bar. New entries
require previous-clock top10 membership; existing positions remain manageable
after leaving the top10. There is no ticker-specific hand tuning.

## Execution and memory

The original compact and history receipts are verified. Full history stays
memory-mapped on CPU; feature blocks stream to the GPU. Only prices, observation
masks, rank membership, history row IDs, population gates, and requested swing
columns are resident. Quotes never become a broker liquidity-bar tensor.
Each candidate batch's program tensors are packed once and shared across all
sessions. Host inputs are retained across generations; changed source file
metadata fails closed. GPU inputs are loaded in bounded cohorts.

Feature programs evaluate ticker/time blocks in parallel. Position updates
vectorize over **session × strategy × ticker** in one cohort engine. The time
axis is chronological because entry prices, adds, stops and partial exits depend
on earlier decisions. `compile` fuses the small transition; `eager` supports CPU
and CUDA. This does not claim simultaneous execution of all 30 sessions or a
measured speedup. Cohort/batch envelopes fail closed instead of truncating work.

Enriched features can be prepared once with `python -B -m
research.vectorized_backtest.v7.feature_cache --inputs INPUTS --history HISTORY
--output CACHE`. This training-only producer reuses certified compact inputs and
histories. It writes losslessly compressed 2048-clock/four-listing tiles with
packed validity, exact source/schema/implementation binding, per-tile hashes and
restart checkpoints. `--first-session-only` measures preparation before all30.
The default storage limit is 1200 GiB; insufficient disk headroom fails closed.
Readers verify tiles before first use and keep at most 1 GiB of decoded tiles
per session. Loading/decompression and device transfer still occur; feature
gathers, quote transforms, relative histories and validity construction do not
repeat for candidate batches. This is an implementation, not a speed claim.
`prepare_features` exposes the same inputs/history/output arguments and a bounded
`--workers 1..8` pool for all30 preparation, with drained failures and durable
per-tile resume. Its default is four workers. `STOP` is honored after a durable
tile; restarting requires removing that operator stop marker deliberately.

`qualify_full --inputs INPUTS --history HISTORY --feature-cache CACHE --output
QUALIFICATION --days TRAINING_DATES` compares complete clocks and identities to
an independent NumPy ledger at zero and 10 basis points. Forced lifecycle gates
exercise management alongside sampled feature programs.
Full-session feature gates also compare CPU/CUDA on four identities with exact
equality before lifecycle gates are injected. Financial comparison covers all
session identities and requires terminal closure plus add/reduction evidence.
`profile` accepts the same roots plus `--qualification QUALIFICATION/qualification.json`; it requires
same-code full-session qualification, measures fixed population/cohort options,
then runs the fastest measured option across all30. It performs no evolutionary
selection, mutation, validation read or campaign launch. Timings include verified
loading, feature read/decode/assembly/transfer, signals, replay including compiler
work, publication and complete wall time. Compiler diagnostics are saved separately.

Every generation evaluates all 30 training sessions before selection. The fixed
lower-tail dollar objective rewards total profit and the worst 20% session mean,
and penalizes drawdown, stop-risk time, capital time, complexity and inactivity.
Here inactivity is the fraction of clocks with no held position. Ranking shows
50 strategies including profit excluding the best session, lower-tail profit,
winning-session fraction and worst complete-position P&L. Costs default to zero
under the assumed-fill contract; `--cost-bps` enables a symmetric notional cost.

## Launcher

Set `PYTHONDONTWRITEBYTECODE=1`. Run from a committed, verified source deployment:

```powershell
python -B -m research.vectorized_backtest.v7.run_search `
  --inputs D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v6/compact/20261009-56905d599-all30 `
  --history D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v6/history/20261010-07ecd389f-all30-ladder60 `
  --feature-cache D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v7/features/CHOOSE_VERIFIED_CACHE `
  --output D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v7/campaigns/CHOOSE_NEW_RUN `
  --population 128 --generations 1 --batch-size 128 --session-workers 8 `
  --device cuda --backend compile
```

These numbers are an explicit example, not a measured full-run recommendation.
The command exposes cohort/strategy-batch/replay-clock progress and ETA. Output
contains timings, all-session receipts, rankings, population and exact RNG state.
The same immutable command resumes completed generations; changed contracts are
rejected. `STOP` is honored after a durable generation checkpoint. There is no
sealed-validation read or evaluation path. Full V7 optimization is not started
as part of implementation.

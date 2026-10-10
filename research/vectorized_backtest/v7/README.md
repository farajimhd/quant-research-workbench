# V7 lightweight rule optimization

V7 searches causal feature programs using an assumed-fill position evaluator.
V6 remains the detailed financial broker version. No V6 campaign, checkpoint,
population, or immutable deployment is migrated or resumed by V7.

Each entry buys $1,000 of fractional shares; each add buys $200. There is no
cash, buying-power, liquidity-capacity, spread, retry, or pending-order simulation.
Adds are bounded by an optimized count of 0â€“5 and a management cooldown.
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

The original compact and history receipts are verified and reused. CUDA keeps
compact source-feature and float32 history banks, elapsed-second row maps and
market snapshots resident within each bounded cohort. It gathers only channels
used by reachable rules. Expanded feature tiles remain the CPU reference/cache;
the CUDA path no longer repeatedly decodes or transfers those expanded tiles.
Host preparation runs in the background loader and pinned banks transfer on a
separate CUDA stream. Cohort guards include compact banks, both signal copies,
swing storage and rule captures.

Entry uses the flat rule; exit/add/reduce/trail use the open rule. Unreachable
branch genes do not consume execution or complexity scoring, and mutation selects
only reachable genes. Rules with no temporal dependencies evaluate only eligible
execution observations. Temporal rules retain the original elapsed-second context;
trail rules also evaluate unobserved seconds because they can ratchet held stops.
No rule work is needed before an identity's first top-ten admission.

CUDA rule graphs are reused by candidate group and padded input shape, with a
bounded capture cache. Signals occupy one byte per candidate/second/identity.
Position updates vectorize over **session × strategy × ticker**. The compiled
CUDA path captures blocks of 32 chronological steps, advancing its clock on the
GPU; the eager path remains an independent execution comparison. There is no
new holding limit. This implementation still has a dense identity ledger and
does not claim a measured speedup until real-session checks pass.

Enriched features can be prepared once with `python -B -m
research.vectorized_backtest.v7.feature_cache --inputs INPUTS --history HISTORY
--output CACHE`. This training-only producer reuses certified compact inputs and
histories. It writes losslessly compressed 2048-clock/four-listing tiles with
packed validity, exact source/schema/implementation binding, per-tile hashes and
restart checkpoints. `--first-session-only` measures preparation before all30.
The default storage limit is 1200 GiB; insufficient disk headroom fails closed.
Readers verify tiles before first use and keep at most 1 GiB of decoded tiles
per session on the CPU reference path. Existing caches remain reusable without
rebuilding causal inputs or histories.
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

### State-conditional strategy execution

Each candidate has flat `rules` and separate `open_rules`, both restricted to
causal market catalog inputs. Only flat entry and open management rules can
change a transition, so they are evaluated independently and packed into uint8
signals. Replay's actual held state selects the applicable actions and applies
optional per-stage `minimum_age` conditions at the decision close. Age counts
elapsed seconds since entry execution; additions and partial reductions do not
reset it. A newly opened position cannot run its open branch in the same step.
The legacy int16 two-branch signal contract remains supported for comparisons.

Fill price, basis, quantity magnitude, realized/unrealized profit and account
metrics are not program inputs. Flat/open and age are the only permitted
position conditions. Fees remain charged on every purchase and sale and the
objective uses net P&L. Existing stop/target and management safety policies
remain execution mechanics. Legacy individuals without open_rules use their
market rules in both branches; uint8 diagnostic gates retain legacy semantics.
Search contract v3 rejects old campaign resume. Existing market feature tiles
remain reusable. Age predicates are opt-in rather than inventing a new search
range; configured age predicates are preserved by mutation.

Profiling sizes must come from the intended optimization configuration. The
previous arbitrary pilot results do not qualify optimization wall time.

### Overlapped I/O

A bounded background loader prepares the next session cohort while the current
one computes. On the CPU reference path, feature tiles are read/decoded by one worker per active cache,
with two-tile lookahead; mutable cache integrity/LRU state has a single owner.
Pinned host tensors transfer on a separate CUDA stream, using nonblocking
copies and event dependencies. Status, timing and session receipts use a bounded
background publisher. Missing-data dependencies and full queues still cause
explicit waits; durable receipts are flushed before scoring/checkpoint sealing.
No claim of literally zero blocking is made.

The profiling options are explicit. Candidate batch comparisons approved by
the user are 128,256,512,1024; cohort bounds derive from available GPU memory.
Memory-budget failures are recorded, never silently downgraded. The chosen
throughput option receives one complete all30 evaluation through run_search,
including net scoring and publication, without generating another population.

GPU rule workspace is bounded by 80% of currently free device memory unless an
explicit workspace limit is supplied; gate residency has its separate bound.

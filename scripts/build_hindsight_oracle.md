# Exact Phase 3 episode oracle

`build_hindsight_oracle.py` finds the maximum terminal equity achievable by
selecting and allocating capital to the retained Phase 1 hindsight episodes.
It is an exact dynamic program for this defined action space, up to floating-point
precision. It does not enumerate an exponential tree or discretize position size.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B scripts/build_hindsight_oracle.py --phase1 <completed-arte-v3-phase1-directory>
```

Defaults are $10,000 starting cash, $1,000 per episode in the all-opportunities
benchmark, zero per-share cost, and a 250,000-input-episode safety bound. Override
with `--initial-cash`, `--allocation`, `--cost-per-share`, or `--max-episodes`.
The input is a session's `days/<date>/phase1` directory, not the campaign root.
The script processes every listing in the pinned input; it never truncates a
population to meet the bound. It generates long, short, and combined modes,
both per ticker and for the pooled market population.

## What is optimized

- Maximize terminal equity; cash earns zero interest. The Phase 2 half-life is
  not part of this objective.
- Each action enters at a retained episode's entry and commits until its fixed
  exit. No early exit, partial mid-episode reallocation, new arbitrary-bar trade,
  or additional terminal fallback trade is invented.
- The source is certified arte price-action Phase 1 V3. Entry/exit prices are
  completed-100ms-bar hindsight extrema, not observable executable fills at
  those completion times. No quotes or raw events are read.
- Sizes are continuous fractional shares. No leverage, capacity limits, borrow
  restrictions, fixed fees, slippage, intermediate margin calls, or drawdown
  constraints are modeled. Costs are proportional per share on entry and exit.
- Long capital per share is entry price plus cost. Short capital is the same
  synthetic reserve used in Phase 2; sale proceeds stay locked. Covering releases
  reserve plus realized profit/loss. Losing episodes remain in candidate data;
  waiting is preferable to selecting them under this objective.
- Proceeds from exits may fund entries at the same timestamp. Equal-bar extrema
  do not establish an intrabar execution ordering; this is a benchmark convention.
- All accepted episodes exit before the 19:58 ET cutoff. The selected schedule
  is therefore already flat at forced liquidation and stays in cash afterward.

This output is optimal **among the retained episode trades under these rules**.
It is not an optimum over all market actions. It is intentionally an optimistic
hindsight benchmark, not a profitability forecast. Unrestricted full-capital
compounding of extrema trades can produce enormous theoretical returns.

## Why the algorithm is efficient and exact

Represent each episode as a time-directed edge whose weight is its cash return
multiplier. Cash waiting is a multiplier-one edge. Sort episodes by entry time,
then work backward. For each episode compare skipping it with its multiplier
times the best continuation at or after its exit. Binary search locates that
continuation. Log multipliers avoid overflow during optimization; cash replay
fails explicitly if the selected result cannot be represented as finite Float64.

Time is O(N log N), memory is O(N). There is no search over cash balances or
fractional allocation grids. Every initial dollar follows some nonoverlapping
episode path. Since returns and costs are linear and there are no capacity caps,
splitting capital yields a weighted average of path multipliers. That cannot
beat the best path; assigning all capital to that path attains the bound.
Thus the single active position in the returned schedule follows from the
objective and assumptions, rather than an extra restriction on diversification.
Changing those assumptions can invalidate this reduction.

## Outputs and training boundary

Artifacts are written beneath the required machine runtime root:

```text
hindsight-oracle/<date>/<plan-hash>/
  plan.json, progress.json, summary.json, complete.json
  <listing-hash-or-market>/<long-or-short-or-long_short>/
    metrics.json
    trajectory.json.gz
    action-labels.json.gz
    ready.json
```

`trajectory.json.gz` records selected episode IDs, timestamps, prices, shares,
committed capital, cash before/after, costs and realized P&L. It is an independently
replayed sequence of variable-duration committed trades; gaps mean cash waiting.
It is not a mark-to-market equity curve or a complete one-second RL transition
dataset. No unrealized drawdown metric is claimed.

`action-labels.json.gz` records the best achievable log terminal wealth multiplier
after choosing each episode, the optimum from a flat state at that entry time,
and their difference (`log_regret`). These labels include the best future
continuation, unlike Phase 2's local values. Nonpositive settlement factors have
null action values and an explicit solvent-at-exit flag. Ties are resolved
deterministically; one selected sequence does not imply a unique optimum.

All choices and continuation values are future-derived. The plan's
`oracle_label_available_us` is the session cutoff; the individual episode's
availability time is separate and does not make continuation labels available
earlier. Join causal market/account features separately before training.
These artifacts can supervise an episode-selection model or provide demonstrations
for RL; they do not supply the causal features, arbitrary invested-state actions,
or exploratory transition coverage needed for a general trading RL environment.

## All-opportunities comparison

The benchmark takes each retained episode exactly once with an equal entry
allocation, including net losses caused by configured costs. Independent lots
can overlap, including opposing directions for one ticker. It reports total
entry capital, profit, and their ratio. Chronological funding replay calculates
the minimum initial cash needed, including cash reuse and losses, and reports
profit/required cash and profit after scaling that schedule to $10,000.
The pooled benchmark merges ticker cash flows before calculating funding; it
does not sum their separate funding requirements. This benchmark is not the
optimal allocation. Phase 2 per-second opportunity rows must not be counted as
separate episodes.

## Integrity, restart and validation

Source plan/completion certificates, listing identity, target counts, ordering,
session bounds and file hashes are checked. Source hashes are rechecked before
completion. No ClickHouse writer or upstream rebuild is started. Plans include
source pins, code hashes and all optimization assumptions. Modes are immutable
checkpoint units, with atomic files and completion-last publication. Resume
verifies completed files before reusing them. Different code or settings produce
a new directory. Source corruption, non-finite values and limits fail closed.

Ctrl+C or a `STOP` file in the output root interrupts at safe boundaries. Remove
`STOP` and rerun the identical command to resume. An interrupted solve restarts
that mode; completed modes are reused. Exit 2 means failure or interruption.
Progress reports active/queued/completed/reused/failed units; there are no
automatic retries or background processes.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -m pytest tests/test_hindsight_oracle.py -q -p no:cacheprovider --basetemp=D:/TradingML/runtimes/hindsight-oracle-tests
```

Tests compare independent exhaustive enumeration with the dynamic program,
including overlapping trades, waiting for a better opportunity, ticker switching,
same-time funding, short reserves, costs, losses, ties, empty inputs, corruption,
interruption and resume. Full-market runtime and trading realism require separate
evaluation; the AAPL/SUGP canary only establishes bounded integration coverage.

Validation on the retained August 21 AAPL/SUGP source processed 3,172 episodes
and published nine units in about 0.5 seconds, excluding Python startup. A
100,000-episode synthetic case took 0.35 seconds for solving and 0.15 seconds for
benchmark funding, with approximately 135 MB process RSS afterward. Synthetic
timings exclude source I/O and output serialization and are not a full-market
throughput claim. The focused Phase 1/2/3 suite passed 96 tests, including a
continuous multi-position linear-program reference in addition to enumeration.

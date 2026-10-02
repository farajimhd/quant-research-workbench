# Audited GPU strategy search

The unified replay uses one **1 s** clock by default, or **500 ms** for both
policy and broker. Version `unified-strategy-one-causal-v2` freezes the working
order state for each completed interval. End-quote repricing, cancellations,
protective amendments and new proposals apply to the following interval.
The legacy app-parity experiment keeps its original sequencing separately.

## What was audited

- Completed-bar boundaries, quote age and source timestamp order.
- Amendment eligibility, next-interval orders, partial fills, protective child
  activation, stop delays, OCA ownership and cumulative commissions.
- Account-level cash, purchase-group exposure and realized/unrealized equity
  reconciliation. Invalid financial states fail the objective boundary.
- Independent candidate accounts, account reset and ledger envelopes.
- Eager CPU versus compiled/captured GPU results, including actual fill ledgers.

These checks qualify this explicit coarse broker model. They do not establish
live-broker equivalence or guarantee that no undiscovered defect exists.
The 100 ms MACD addition gate remains intentionally removed in BOTH coarse
variants; completed 1 s MACD remains. A close aggregate app P&L is not an oracle.

## Search representation

The optimizer genome is **`[B,10]`**, where B is the population size. Three entry
thresholds, capital fraction and six protection parameters are decoded into
the existing atomic parameter tensors. Integer values retain their declared
constraints; target breakpoints are ordered. Fixed constants and operation/
input labels remain unchanged, so a generation needs no recompilation.

Each candidate receives its own $10,000 cash, positions, reservations and
commission registers. All candidates share certified market tensors. Liquidity
is shared among orders within one candidate account, not among independent
counterfactual candidates. Every evaluation resets accounts and runs the whole
04:00–09:30 America/New_York session; there is no rolling capital between days.

The market universe remains the frozen released funnel. This search cannot
loosen its static filters, invent unobserved candidates, or optimize arbitrary
instruction topologies without new preparation and compilation.

## Fitness and split

Fitness is maximized:

```text
mean(net_PnL / initial_cash)
  - drawdown_weight × mean(max_drawdown / initial_cash)
  - dispersion_weight × std(session_net_returns)
  - position_weight × mean(entered_position_episodes / 100)
  - exposure_weight × mean(position_seconds / 3600)
```

Default drawdown and dispersion weights are 0.5 and 0.25; activity weights are
zero unless selected explicitly. Net P&L includes commissions and terminal
marked exposure. No implicit terminal liquidation is invented. Drawdown is
sampled on the main clock; exposure counts positions held at interval start.

This objective permits inactivity. Its activity terms penalize counts/exposure;
they do not require a minimum number of trades. A nearly inactive candidate can
beat an active but risky candidate. If useful trading activity is required,
declare a minimum-entry constraint or inactivity cost before a new training
campaign, and reserve new untouched validation sessions for that campaign.
Changing this requirement after seeing validation creates a different experiment.

1. Optimize Aug 18 with default policy as the initial guessed solution.
2. Seed a fresh search with that winner and optimize Aug 18 + Aug 19.
3. Freeze both winners, then compare them and the default on Aug 20 validation.

Validation is a later disjoint session and is never an objective input,
generation selector or plateau signal. One validation day is a workflow test,
not sufficient evidence of out-of-sample profitability. Add more certified
later sessions using repeated `--validation-run-id` arguments for stronger
evaluation. Saved source-run cash or P&L never supplies search account outcomes.

## Run and monitor

From the repository root, using the configured research Python environment:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -m research.vectorized_backtest.v1.torch_backtest.run_optimization `
  --population 8 --generations 8 --clock-ms 1000
```

Defaults select two training source run IDs and a separately certified Aug 20
source run. All selected market, candidate, seed, identity, entry and liquidity
products are certified before optimization. Only SELECT queries are allowed.
Certified, hashed parquet may be reused with `--cache run_id=absolute_path`;
source tokens are recertified, and parquet counts/hashes are validated.
All results and compiler caches belong under `D:/TradingML/runtimes`.

The solver uses elitism, tournament crossover, bounded mutation and random
immigrants. Three training generations without improvement increase diversity.
The generation cap remains fixed; a genetic solver does not certify a global
optimum. The default budget evaluates 8 candidates × 8 generations in each
phase, plus frozen finalists. Batched candidates reuse compiled GPU runners.

`checkpoint.json` stores the next population, RNG state, best candidate and
training history after each completed generation. Interrupted partial
generations are reevaluated; completed generation receipts remain immutable.
Restart with `--resume absolute_runtime_run_path` and identical arguments/cache
mappings. Source bytes, split, clock and objective must match the original run.
A source fix requires a new run, rather than mixing results across engines.

Inspect `status.json`, phase-generation receipts, `winner_1.json`, `winner_2.json`
and the final `report.json`. Failures preserve checkpoints and explicit errors.
GPU memory is bounded with transfer headroom checks; there is no silent CPU
fallback or ticker truncation. The monitor may fix a defect and launch a new
bounded training run, but must never tune against validation outcomes.

## Measured audit and current campaign

The corrected default replay covers 19,800 one-second intervals on Aug 18,
04:00–09:30 ET, in 28.35 s; the 39,600-interval 500 ms version takes 56.71 s.
Complete ledgers match the preceding causal-v2 implementation after the masked
admission optimization. Three-candidate CPU/GPU ledger and reset comparisons
pass at both clocks. The package suite passes 158 tests, including a CUDA
compiler-reset regression. These timings exclude certification and setup.

An initial eight-candidate/eight-generation search completed both training
phases but failed before validation scoring: listing-specialized compiler
guards accumulated across session shapes and hit Dynamo's cache limit. The
fix resets Python compiler guards at sequential session setup boundaries;
existing captured CUDA graphs remain usable. Search requires captured one-step
graphs, so there is no eager fallback after this reset. The failed receipt is
preserved; a fresh campaign uses the corrected source identity.

The failed campaign's training-only observations are diagnostic, not final
validation results: its one-session winner earned $3,506.93 versus the default
$3,160.51 on Aug 18. Its two-session winner reduced activity to zero entries on
Aug 18 and one on Aug 19. This demonstrates the inactivity incentive discussed
above; it does not establish out-of-sample profitability. Batched replay took
about 30.7 s on Aug 18 and 39.7 s on Aug 19 for eight independent candidates.
Cold setup for those session shapes took 81.85 s and 591.99 s respectively.

The corrected campaign is monitored under runtime run
`strategy_search/bffc924a099e46a898102665f9eaea3c`. Its checkpoints, status and
eventual `report.json` are the authority for completion; validation was pending
when this progress note was written. No validation score is used to change the
objective, budget, mutation schedule or winner selection.

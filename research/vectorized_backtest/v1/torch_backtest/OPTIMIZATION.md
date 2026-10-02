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

## Expanded numeric search (second experiment)

The current optimizer discovers every nonfixed numeric slot across entry,
addition and all three action graphs, then appends protection thresholds.
The genome is now `[B,14]`: three entry values, two addition ordinal bounds,
capital fraction, stop distance multiplier, target distance multiplier and
six protection values. The ten-parameter results below describe the previous
experiment and must not be attributed to this expanded search.

| New parameter | Range | Default | Meaning |
|---|---|---:|---|
| Minimum addition purchase ordinal | 2–3, integer | 2 | Earliest allowed additional purchase |
| Maximum addition purchase ordinal | 2–3, integer | 3 | Latest allowed purchase; at least the minimum |
| Initial stop distance multiplier | 0.25–2 | 1 | Distance below causal bid, relative to certified stop |
| Initial target distance multiplier | 0.25–3 | 1 | Distance above causal ask, relative to certified target |

Multiplier one adds exactly zero to the original bracket. The engine still
checks bracket validity, cash and liquidity. The three-group broker envelope
does not allow a fourth purchase; ordinal bounds cannot expand that capacity.
Addition theta is updated in place before each independent replay, like entry
and action theta. Topology and structural literals remain fixed. Numeric
slots are discovered from declared graph contracts, not hardcoded slices.

The expanded experiment starts with a fresh population and RNG; no previous
winner or checkpoint seeds it. It retains the objective and 8×8 budget per
phase. A SELECT-only inventory found no later source session with the same
configuration. Aug 20 can therefore be reported as previously observed
evaluation data, excluded from optimization, but not as a fresh untouched
holdout. A new certified later session is needed for stronger acceptance.

## Previous ten-parameter search representation

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

## Measured audit and completed campaign

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

The corrected campaign completed both eight-generation training phases and
independent Aug 20 validation at a **1 s main clock**. Every session has 19,800
intervals and independent $10,000 starting cash. The compiler lifecycle fix
reproduced both previous training winners exactly. All nine reported candidate
accounts ended with zero open positions, and final risk-adjusted scores were
independently recomputed from the saved results.

| Session / split | Candidate | Net P&L | Max drawdown | Entry episodes | Fills |
|---|---|---:|---:|---:|---:|
| Aug 18 / training | Default | $3,160.51 | $1,351.13 | 26 | 182 |
| Aug 18 / training | One-session winner | $3,506.93 | $1,410.95 | 28 | 209 |
| Aug 18 / training | Two-session winner | $0.00 | $0.00 | 0 | 0 |
| Aug 19 / training | Default | -$2,368.05 | $3,117.15 | 32 | 217 |
| Aug 19 / training | One-session winner | -$2,150.61 | $3,044.75 | 32 | 216 |
| Aug 19 / training | Two-session winner | $2.17 | $29.49 | 1 | 7 |
| Aug 20 / validation | Default | $859.82 | $1,602.66 | 25 | 194 |
| Aug 20 / validation | One-session winner | $221.98 | $2,185.94 | 25 | 209 |
| Aug 20 / validation | Two-session winner | -$2.38 | $18.40 | 2 | 8 |

The one-session winner improved training P&L but underperformed the default on
validation, with greater validation drawdown. The two-session winner maximized
the selected risk-adjusted training objective largely by avoiding trading.
Neither optimized policy improved validation fitness over the default. This
is a completed search experiment, not an accepted profitable strategy.

Phase 1 improved fitness from 0.248494 to 0.280146 at generation four. Phase 2
improved its best population fitness from -0.010857 to -0.000656 by generation
four. Both then plateaued for four generations; bounded diversity increases
did not improve the winners within the budget. A plateau is not proof of a
global optimum. No extra search or objective change was triggered by validation.

The final batched replay timings were **30.69 s** on Aug 18, **39.11 s** on
Aug 19 and **30.67 s** on Aug 20. These are eight-lane full-session timings;
finalist evaluation uses three unique policies plus five duplicate padding
lanes to retain the compiled batch shape. Training setup took 38.79 s and
549.35 s for Aug 18 and Aug 19, additional to replay. New session shapes still
have substantial cold compilation cost; warm replay is not end-to-end latency.
The 500 ms audit above is separate; this search did not optimize at 500 ms.

Immutable authority: [completed report](</D:/TradingML/runtimes/vectorized_backtest/strategy_search/bffc924a099e46a898102665f9eaea3c/report.json>)
and [completion verification](</D:/TradingML/runtimes/vectorized_backtest/strategy_search/bffc924a099e46a898102665f9eaea3c/completion_verification.json>).
The run retains 16 generation receipts, frozen winners, RNG checkpoints,
certified input identities and source hashes. Completion verification matched
all 32 pinned package source files. Validation was never used to change the
objective, budget, mutation schedule or winner selection. Monitoring can stop.

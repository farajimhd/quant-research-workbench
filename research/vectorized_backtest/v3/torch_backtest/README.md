# Semantic GPU strategy optimization

V3 builds on v2's causal Squeeze portfolio engine. Strategy choices, numeric
policy values, atomic input IDs, temporal operation IDs and Boolean operation
IDs are searchable. The optimizer starts from a random executable tensor,
optimizes one training session, then both training sessions, and freezes both
winners before constructing the evaluation tapes.

The authoritative [time and search contract](TIME_AND_SEARCH_CONTRACT.md)
defines candle indexing, execution boundaries, assumptions and every numeric
search range. Its version and SHA-256 seal are included in tape provenance
and the optimizer manifest; untagged or open-labelled tapes are rejected.

## Representation and contracts

`StrategySpace` defines a versioned **[B, 71]** float64 tensor. B is the number
of independent candidate accounts. Float storage is convenient for mutation
and device buffers; all categorical/count coordinates require integer values.

| Segment | Coordinates | Meaning |
| --- | ---: | --- |
| Portfolio policy | 10 | Entry ID, MACD mask/ANY-or-ALL, hold duration, number of positions, allocation ID, target ID, trailing ID, stop ID, replacement ID |
| Numeric policy | 34 | Liquidity/spread gates, stop/target/trail distances, history lengths, deadlines, rotation parameters, score weights/scales |
| Atomic entry clauses | 24 | Four instructions of six coordinates each |
| Boolean connectors | 3 | AND/OR between enabled instructions, evaluated left to right |

Each entry instruction is:

```text
[enabled, comparison_id, input_id, temporal_id, lookback, threshold]
```

The decoded clause means, for example, `mean(interval_notional, last 3 completed
seconds) >= $20,000`. The compiler is the fixed tensor interpreter in `rules.py`;
no generated Python, `eval`, or ticker/candidate loop is used at a decision.
The clause result is an additional entry gate over the selected v2 entry mode.
It does not override the broker, remove financial constraints, or invent fills.

The catalog includes close, return above VWAP, spread fraction, interval
notional/trades/volume, elapsed squeeze age, 1s MACD gap, and five distances to
currently available V7 resistance levels. Every atomic input supports completed
history operations: current, lag, minimum, maximum and mean, with 1..12 seconds
of declared history. A lag excludes the current bar; reductions include the
current completed bar. Missing evidence in any enabled clause fails closed,
including when another clause is connected by OR. Disabled clauses are neutral.

`space.manifest()` is the machine-readable authority for coordinate order,
class labels, input kinds, legal numeric ranges and fixed settings.
`space.decode(rows)` exposes the readable policy and rules before replay.
Increasing grammar capacity or admitting new input types requires a new
version and corresponding data dependencies; this is a bounded grammar,
not every possible mathematical program.

### Semantic safeguards

Absolute timestamps and identity registers are not threshold inputs.
A duration comparison requires a bounded elapsed-duration literal. A price
and a price delta have different kinds; fractional returns differ from
absolute prices. `semantics.py` declares these distinctions and legal
arithmetic types. The present executable entry grammar supports comparison
and temporal reduction; its arithmetic type declarations do not imply that
arbitrary arithmetic expression trees are already searchable.

Unknown or fractional class IDs are rejected before repair. Deterministic
repair clips numeric bounds, enforces trail-stop <= trail-up, canonicalizes
inactive hold/MACD coordinates, and supplies a duration/mask of one when
crossover activates a policy with an empty dependent coordinate. Repair calls,
rows and changed-coordinate counts are saved with the population and restored
from checkpoints. Semantic rejection is never silently converted into fitness.

Fees, liquidity participation, initial cash, price tick, source validity and
the common **1s strategy/broker clock** are fixed experiment contracts, listed
in the manifest. Objective weights are fixed across candidates too. This v2
port does not implement a 500ms clock. Configured MACD lanes are 1s, 5s, 10s and
30s; it does not allow a strategy timeframe below its main clock.

## Preserved v2 lifecycle and causal V7 evidence

The engine keeps multiple positions per ticker, equal/inverse-log/log
allocation, signal/hold/retest/MACD entry modes, percentage/structural targets,
percentage/confirmed-swing initial stops, adaptive/step trailing, rotation,
partial fills, reservations, commissions and deterministic shared liquidity.
It acquires **one batch per ticker per session**, with 1..15 positions. It does
not resubmit another acquisition batch later; multiple positions should not
be confused with later Strategy 1 additions.

Historical preparation still reads certified ARTE products with SELECT-only
credentials. ClickHouse produces the released 100ms Early Squeeze admission
stream and price envelope; the admitted watchlist's bars, indicators, quotes
and liquidity are then fetched by bounded chunks. Only downstream parameters
are searched in this version. The released funnel and certified source
population remain fixed; a narrower search predicate must never be pushed
into a source query if it would remove evidence another candidate needs.

V7 geometry starts from the prior-session seed and advances the canonical
level book on completed current-session 1s bars. A current-session end-of-day
book cannot initialize the same session. Structural prices and atomic V7
inputs are gated by the causal structural clock; unavailable levels remain
missing. The source/algorithm fingerprint, seed token and structural token
are carried into tape provenance and sealed on first training preparation.
Resume requires matching training fingerprints, not just matching dates.

Decisions at boundary t may submit orders. Those orders first compete for
capacity in the next completed interval. Stop/target ambiguity uses the
existing conservative stop-first contract. Final positions are not liquidated
at an invented price: residual exposure makes a candidate invalid with a
recorded reason and null fitness.

## GPU execution and timing

`SearchRunner` keeps class IDs, numeric values, instructions and connectors in
persistent device buffers. Changing genes copies into those buffers in place.
For a fixed B and resident tape, classes, thresholds **and history lengths**
can reuse the same compiled function/CUDA graph.

Shared source histories are padded to grammar caps: atomic/retest/momentum/
attention histories use 12 seconds, adaptive movement 32, and swing evidence
11. Candidate masks select their own windows; confirmed swing state remains
[B,N]. Source history is [H,N,F], not a duplicate per candidate. This removes
the per-window compilation groups that would otherwise make random search
expensive. A time step remains causal and sequential; candidates, tickers,
positions and rule evaluation are tensor operations within each step.

`SessionObjective` reports **compile/setup seconds separately from prepared
replay seconds**. The generation receipt also records end-to-end generation
time. Source preparation and certification metrics are retained in
`training_sources.json`. These times must not be conflated with warmed replay.
A copied v2 default is evaluated after selection; it is not injected into the
first random population.

## Training, evaluation and activity

Each session starts an independent account with $10,000. The two-session phase
evaluates each candidate on both training dates; it does not carry cash or open
positions across dates. It seeds one lane with the one-session winner and
starts the remaining lanes randomly.

The default fitness is:

```text
mean(session net P&L / initial cash)
- 0.50 * mean(session maximum drawdown / initial cash)
- 0.25 * population_std(session net P&L / initial cash)
```

The objective function also supports explicit entry-count and exposure costs.
Those weights are fixed for an experiment, not optimized to improve its score.
`--minimum-training-entries K` requires K actually filled position orders **on every training session**;
its default is zero. A constraint changes the experiment identity. Inactivity
is reported even when permitted; it is not evidence of a replay defect.
Evaluation is always diagnostic and never feeds selection, mutation, weights
or constraints. Mark previously inspected dates with `--validation-preobserved`;
such dates are not a fresh holdout. Synthetic evaluation uses dummy fixtures
and demonstrates the workflow, not out-of-sample profitability.

The default budget is 8 candidates x 8 generations in each phase. The solver
uses elitism, tournament selection, categorical crossover, bounded mutation
and random immigrants after three stagnant generations. Every generation
saves its evaluated population, metrics, rejection reasons and elapsed time.
Checkpoints contain the next population, RNG state, best genome, repair counts
and last completed metrics. An interrupted generation is rerun from its prior
completed boundary; no partial evaluation becomes a selection result.

## Run it

Run from the laptop repository, with outputs beneath `D:/TradingML/runtimes`.
No command below writes market data.

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$python = 'C:/Users/g835l/miniconda3/envs/ml4t/python.exe'

# Safe default: write the grammar, split and budget plan; no data fetch/search.
& $python -B -m research.vectorized_backtest.v3.torch_backtest.optimize

# Small executable qualification using dummy data and independent CPU accounts.
& $python -B -m research.vectorized_backtest.v3.torch_backtest.optimize `
  --synthetic --device cpu --backend eager --population 4 --generations 2

# GPU compilation and replay using dummy data.
& $python -B -m research.vectorized_backtest.v3.torch_backtest.optimize `
  --synthetic --device cuda --backend compiled_graph --population 8 --generations 2

# Historical run after providing certified producer paths and explicit dates.
& $python -B -m research.vectorized_backtest.v3.torch_backtest.optimize `
  --execute --sessions D:/TradingML/runtimes/vectorized_backtest/v3_sessions.json `
  --minimum-training-entries 1

# Resume with the identical split, code, seed, budget and objective arguments.
# Add --resume <printed experiment directory> to the original command.
```

The sessions JSON has exactly two ordered training dates and at least one
later, disjoint evaluation date. Each item supplies `manifest`, `ledger`,
`start`, and `end`. Start/end must have timezone offsets, for example
`2026-08-18T04:00:00-04:00` to `2026-08-18T09:30:00-04:00`. Producer paths must
refer to the matching certified source; no fabricated paths are supplied as
executable defaults. Date checks use New York market dates.

Status lives in the printed experiment directory. `entered` counts submitted ticker acquisition batches; `positions_opened` counts position orders that actually received fills, and `open_positions` counts remaining live positions. The activity constraint uses `positions_opened`, so an unfilled submission cannot satisfy it. `status.json` preserves the
last complete generation's P&L, drawdown, entries, fills, open quantity and
exposure during the next generation. Completed status retains finalist metrics too. `winner_1.json` and `winner_2.json` contain
frozen decodes. `report.json` orders finalists as default, one-session winner,
two-session winner and includes per-session training/evaluation metrics.
Failure leaves receipts and checkpoints intact; identical `--resume` is the
supported recovery path. Source/grammar changes require a new experiment.

## Qualification

Tests cover copied financial/causal contracts, semantic rejection, categorical
mutation, random initialization, full-ledger default parity, mixed numeric/
history/deadline lanes versus independently configured accounts, atomic
history, missing V7 evidence, future-data prefix invariance, state recovery,
genetic checkpoint determinism, and CUDA/compiled-graph buffer reuse.
The copied qualification also checks 1,024 independent GPU accounts.

```powershell
& $python -B -m pytest research/vectorized_backtest/v3/torch_backtest/tests `
  -q -p no:cacheprovider `
  --basetemp D:/TradingML/runtimes/vectorized_backtest/v3_qualification/tests
```

This delivery qualifies the implementation on dummy data. It does not claim
new historical v3 P&L, convergence or a full-session runtime. V2 source is
preserved byte-for-byte; the prior v1 optimization remains a separate campaign.

### Initial delivery measurements (before the adaptive activation fix)

On the RTX 5090 Laptop GPU with PyTorch 2.12, the initial delivered suite passed
**96 tests**. The end-to-end optimizer smoke run used eight random candidates,
two generations per phase, two dummy training tapes and one dummy evaluation
tape. Each tape had **75 one-second slots and two tickers**.

| Measurement | Seconds |
| --- | ---: |
| First compile/capture setup | 34.487 |
| Next one-session generation, including objective/status work | 0.025 |
| Prepared eight-candidate replay in that generation | 0.0115 |
| Next two-session generation, including objective/status work | 0.054 |
| Prepared replay summed over both training sessions | 0.0225 |

These are small resident-tape qualification measurements; they do not estimate
an entire historical premarket session. Both finalists were frozen before
synthetic evaluation. All four generation receipts, objective values, source
code hash, winner hashes and random-initialization evidence were checked.
Runtime evidence is under
`D:/TradingML/runtimes/vectorized_backtest/v3_qualification/gpu-delivery/experiments/6a97e28fac7e400ab6dfa94b6cba14fb`.

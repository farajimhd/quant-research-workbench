# Semantic GPU strategy optimization

The current [risk objective and population study](POPULATION_STUDY.md) compares
64, 128, 192, 256 and 512 candidates with separate replay and search-quality
measurements. The earlier campaign remains stopped.

V3 builds on v2's causal Squeeze portfolio engine. Strategy choices, numeric
policy values, atomic input IDs, temporal operation IDs and Boolean operation
IDs are searchable. The optimizer starts from a random executable tensor,
runs ONE optimization phase over all training sessions, and freezes the winner
before constructing the evaluation tapes. Each session resets cash; cash evolves
causally within that session.

The authoritative [time and search contract](TIME_AND_SEARCH_CONTRACT.md)
defines candle indexing, execution boundaries, assumptions and every numeric
search range. Its version and SHA-256 seal are included in tape provenance
and the optimizer manifest; untagged or open-labelled tapes are rejected.

## Representation and contracts

`StrategySpace` defines a versioned **[B, 77]** float64 tensor. B is the number
of independent candidate accounts. Float storage is convenient for mutation
and device buffers; all categorical/count coordinates require integer values.

| Segment | Coordinates | Meaning |
| --- | ---: | --- |
| Portfolio policy | 10 | Entry ID, MACD mask/ANY-or-ALL, hold duration, number of positions, allocation ID, target ID, trailing ID, stop ID, replacement ID |
| Bounded policy values and remainder class IDs | 40 | Liquidity/spread gates, stop/target/trail distances, history lengths, deadlines, rotation parameters, score weights/scales |
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

## Multi-session workstation optimization

Use [WORKSTATION_OPTIMIZATION.md](WORKSTATION_OPTIMIZATION.md) for the operational
contract, visible SSH launcher and dashboard. All code is local to v3: no v1/v2
package, deployment or runtime is required.

The optimizer has **one phase**, from a repaired random `[B,77]` tensor. Every
candidate is scored across the complete training set before selection. Each
session resets to $10,000; fills, fees, reservations and cash evolve causally
inside that session. The last six available dates are reserved for later frozen
evaluation by the workstation launcher. Previously inspected evaluation dates
are explicitly labelled preobserved, never used in training selection.

Fixed defaults: three-second discretionary hold floor (protective stops and
terminal exits exempt), at least one actually filled acquisition batch on
**every training session**, a soft maximum of 20 batches/session, and an exposure
penalty after 300 seconds. An acquisition split into fifteen child positions
still counts as ONE batch. All numeric gene ranges remain searchable; these
experiment constraints cannot be mutated away.

The objective is mean net return minus 0.5 times normalized drawdown, 0.25 times
return standard deviation, 0.05 times normalized excess batch count and 0.01
times overdue capital-hours. Residual exposure or a missing required session
activity makes fitness null. Feasibility ranks guide infeasible candidates toward
satisfying all sessions; they never replace actual fitness or qualify a winner.

`run_optimization_workstation.py` defaults to 64 candidates × 50 generations;
the lower-level optimizer defaults to 32 × 50. Resource guards admit larger
populations only when they fit. The search is heuristic, not a global-optimum
certificate. Wide positive price/activity thresholds use a declared log1p random
density over the unchanged bounds; default is never injected into the population.

Completed session receipts allow mid-generation restart without replaying those
sessions. Selection happens only after the entire generation is complete.
Checkpoints seal code, source fingerprints, split, population, RNG, objective
and budget. A frozen `winner.json` precedes evaluation; `report.json` orders
finalists as `[default, optimized]`.

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$python = 'C:/Users/Mehdi/miniconda3/envs/ml4t/python.exe'
# On the verified workstation deployment: discover and pin sources first.
& $python -B -m research.vectorized_backtest.v3.torch_backtest.run_optimization_workstation dates
& $python -B -m research.vectorized_backtest.v3.torch_backtest.run_optimization_workstation plan
# Profile a TRAINING session before any full optimization.
& $python -B -m research.vectorized_backtest.v3.torch_backtest.run_optimization_workstation profile
# Run with the actual qualification path printed/saved by that profile.
# Add: run --qualification <profile-job>/qualification.json
# Reopen a live or completed dashboard without changing worker state:
# Add: monitor --resume <optimization-job>/experiment
```

Laptop dummy qualification remains available through `optimize --synthetic
--device cpu --backend eager --population 4 --generations 2
--minimum-training-entries 0`; the zero override is allowed only for dummy data.
Historical training cannot disable its minimum activity constraint.

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

### Seeded mutation and source-version continuation

Offspring choose a mutation strength using the checkpointed NumPy RNG:
60% light (4% per-coordinate mutation, 1% numeric range scale), 30% medium
(12%, 4%), and 10% broad (30%, 15%). Light offspring refine one selected
parent; medium/broad offspring cross parents while keeping semantic clauses
intact. Every searchable class ID, count, policy value, clause input/operator/
temporal/window/threshold and connector is eligible. Class IDs are replaced
with legal labels; bounded integers receive integral steps. Wide positive
ranges use log1p steps to refine small price/activity thresholds without
jumping by a large fraction of their absolute maximum. Input changes reset
threshold units to the new atomic input. Repairs remain explicit.

Two elites, three-way tournaments and 20% random immigrants are retained.
After three stagnant generations random immigration becomes 50%. These are
probabilities, not guaranteed counts or guarantees of improved fitness.

`--continue-training OLD_EXPERIMENT` starts a NEW immutable experiment when
mutation implementation changes. It checks protected grammar, objective,
sessions, cash/financial contracts, seed and total budget; verifies every
completed session fingerprint and score/rejection calculation; pins identity,
checkpoint and receipt hashes; and preserves the best result. It regenerates
the next population with new mutation using the saved RNG state. Incomplete
session receipts are preserved in the old run and never reused. Ordinary
`--resume` still requires exactly matching source and identity. Inherited
results retain their old-source attribution in `continuation_receipt.json`.
No continuation is allowed after a frozen winner or validation input exists.

The October 5 continuation keeps generations 1�4 and restarts generation 5,
with B128 and the original total 32-generation ceiling. The objective, causal
broker, 30 training dates and six sealed validation dates remain unchanged.

The workstation profile launcher accepts `--run-after-profile NEW_JOB`: it
launches the new campaign in the same visible console only after same-source
qualification passes full financial and fill-ledger parity. A failed profile
stops before training. Use `--continue-training OLD_EXPERIMENT` and
`--reuse-prepared OLD_EXPERIMENT` to retain certified training bytes and
completed generations while changing mutation implementation.

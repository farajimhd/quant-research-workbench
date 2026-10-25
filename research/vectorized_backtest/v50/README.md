# V50 direct phase2 pilot

Single authorized pilot: Aug13, 50 generations, laptop GPU, seed2020.
Start fresh with 3072 templates and 3072 newly sampled/calibrated random candidates.
Generation1 evaluates all6144; V49 phase2 reproduction selects the next1024.
No phase0, local phase1 search, historical experts, old pool environment route or validation data.
Score = NetProfit - 0.1 * GrossLosingPositionDollars - 1.0 * MaximumEquityDrawdown.
Equity drawdown includes open positions. For future multiple sessions, the inherited aggregate
sums identical session costs; launching that campaign requires separate authorization.
Keep bid/ask fills, fees, execution/risk guards, elite/fresh allocations, ordinary and whole-network
crossover, mutation, ancestry/species and memory/cache guards from V49.
Global checkpoint and generation seals bind population, RNG, scores, receipts and source.
Use full_launch to prepare, pilot to execute through Task Scheduler, progress for the color terminal.
Validation stays unopened. V49 remains stopped and its checkpoints are preserved.

## Authorized full run

The full run uses full_launch --full, then full_run --job full-job.json on workstation only.
All24 training sessions and12 later validation sessions;100 generations, same1024 population
and fresh6144 random/template founders. Training-only global calibration uses8 representative
certified windows. Validation is opened after successor/RNG freeze and never changes selection.
Memory guards remain128GiB reserve +32GiB admission; one GPU process, two CPU data entries,
one compiler graph plan and one bounded CPU prefetch. Progress T toggles sealed training/validation.
The original Aug13 pilot and its source/checkpoints remain preserved separately.

## Reproduction repair and generation-2 continuation

Reproduction v2 ranks financially eligible candidates with closed training positions,
including negative scores. Inactive candidates cannot supply elites or parents.
The 10% elite / 20% fresh / 70% offspring allocation is unchanged. Missing mating
pairs and invalid crossover children use unique mutation-only descendants of ranked
parents; ancestry remains explicit. With no valid active parent, calibrated fresh
immigrants fill the missing slots. Every fresh immigrant uses the sealed training
activation probe and founder policy priors. Ordinary and whole-network crossover,
execution guards, objective weights and validation isolation remain unchanged.

`reproduction_resume.py export-context` runs under the stopped original deployment
and reconstructs generation-2 ancestry/RNG by reproducing generation 1 on CPU from
its verified training receipts. Its output must exactly match the saved successor.
`prepare` runs under the repaired deployment, verifies unchanged replay code and
generation-2 training receipts, and builds the corrected generation-3 successor.
Neither operation executes market replay. A separate linked continuation preserves
the original generation seals and binds the replacement successor in an explicit
transition receipt. Old partial generation-3 replay results are not reused.

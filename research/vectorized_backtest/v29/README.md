# V29: recurrent action-network evolution

V29 is a separate copy of V28. V28 source and results remain intact. Each
strategy owns entry, exit, add, reduce and trail tanh networks. There is no
V27 symbolic crossover path and no backpropagation.

## Representation and search

- Neurons compute tanh(bias + weighted sum). Current-clock connections are
  acyclic; delayed connections read only the preceding clock's neuron values.
  All five actions see their old memory before any new memory is committed.
- Graphs grow internally, shrink and change connectivity. The explicit compute
  envelope is 512 reachable neurons total across the five actions, fan-in eight,
  with depth bounded by that node budget. This replaces V28's 64-node/action,
  depth-16 limits; it is not unlimited network growth.
- Initial action sizes cover 1..16 and 20/24/32/48/64, with extra simple atoms,
  varied depth, family-balanced inputs and sampled delayed self-connections.
  Fresh initial population is 6 x 1024; no evolved V28 genomes are imported.
- Persistent neuron IDs survive inheritance and module exchange. Connections
  align by source identity and delay type; ambiguous duplicate identities are
  excluded from weight alignment. Metadata IDs do not alter phenotype dedup.
- Mating changes one or two actions: 40% inheritance, 30% connected-module
  exchange, 30% nonlinear composition. Composition selects whole/internal
  modules equally, combining parent margins through a new tanh neuron.
  Homology alignment is attempted with probability 50%; each aligned weight
  has probability 50% of being averaged. Other action/policy cooperation stays.
- Mutation chooses parameter/structural edits at 70/30. Parameter edits include
  weights, biases, thresholds, sizing heads and policy/age genes. 80% use local
  reflected Gaussian perturbation, 20% reset; weights/biases remain bounded by
  four and thresholds strictly inside (-1,1). Weight edits sometimes perturb a
  whole neuron's parameters. Structural edits include weak internal insertion,
  connection edits and delayed links. Invalid children are rejected, never cut
  down or silently truncated. The 20..85% mutation mask and 1..5 edit budget stay.
- Structural niche champions receive at most half the elite budget, capped at
  16 champions. A quarter of score-tournament draws first select a structural
  niche uniformly. This changes reproductive allocation explicitly; it is not
  NEAT compatibility speciation. Financial objectives remain unchanged.

## Decisions, sizing and feedback

Entry/add/reduce have separate trigger and continuous sizing readouts. A hidden
node feeds (tanh(weight * node + bias) + 1) / 2. Execution converts fractions to
up to $1000 entry, $200 per addition, or a fraction of the held position for a
signal-driven reduction. Target-hit reductions retain the policy fraction.
Orders below $1 are suppressed; invalid sizing fractions fail closed to zero.
Fees use actual size; existing stop, risk, membership and causality gates remain.

Networks read causal certified shard values, same-clock cross-sectional values,
validity, position state, previous requested fractions, actual filled fractions,
unfilled requests, action bits and elapsed time. Entry may read feedback but not
held-position-only state. Missing values are neutral and an action needs an
observed current or delayed market-value ancestor. A recurrent history of valid
observations can therefore remain usable when a current feature is missing.
Memory resets at each independent session. Replay state can be exported/restored;
optimizer resume is from sealed generations, re-evaluating each session from reset.

Local objective: P - .25W - .05D. Global objective:
sum(P) - .25 max(sum(W), W_session) - .05 sum(D).
Risk limits remain $300 planned risk and 20% maximum stop distance. Validation
never affects reproduction, memory, normalization, selection or RNG.

## GPU implementation and qualification

Float32 neural inference uses ragged contiguous node storage, not padded maximum
size per strategy. Two one-clock buffers hold current and delayed values and
validity; the explicit neural workspace admission is 4 GiB. Financial replay is
float64 and chronological, with a protected fused transition and CUDA capture.
Features are shared across candidates. No full-session hidden-state tape is
allocated. Cache paths are short and runtime-only. Laptop host admission retains
12 GiB reserve plus 8 GiB load allowance, one worker and no prefetch.

`profile.py` separates float32 inference from financial correctness: a recorded
GPU decision/sizing tape drives the CPU financial oracle with strict tolerances;
GPU eager/captured replay is compared separately, followed by cold/warm metric
hashes. Prefixes are diagnostic only. Full-session qualification also checks
normal Worker publication against direct replay. Certified producer raw bytes
and original input certificates remain exact.

## Run and monitoring

Set PYTHONDONTWRITEBYTECODE=1 and use Python -B. Use a qualified immutable code
snapshot. Example arguments for `research.vectorized_backtest.v29.run_train`:
`--mode local --authorize-run --day 2026-08-04 --generations 30 --population 1024
--initial-multiplier 6 --batch-size 512 --seed 2020 --commit <qualified-commit>`.
Supply the pinned training manifest and a new output under
D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v29/campaigns.

Local mode opens no validation. Full mode retains Phase 1 locals followed by
Phase 2 global optimization, requiring chronological certified 24/12 manifests.
Both phases preserve source/input-bound checkpoint resume and ancestry/archive
budgets. `experiments.py` writes a source-bound multi-seed study plan only; its
ablations require separate implementations and it launches no extra runs.
An Aug4 run alone does not demonstrate generalization.

`local_details.py` uses the V27 detailed terminal canvas in a normal independent
window, with recurrent equations and sizing heads shown. Closing that observer
does not stop the hidden training process. Ctrl+C on the training controller
requests a sealed-generation stop. Never overwrite a live campaign or remove
STOP without an intentional exact-checkpoint resume.

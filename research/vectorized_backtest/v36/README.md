# V36: generic multi-child neural crossover

V36 uses V34's execution limits, initial sampling, V21 objective, speciation, ancestry,
mutation, archives, fees and execution. The first laptop experiment remains
Aug 4, population 1024, seed 2020, initial 6144 (3072 templates and 3072 calibrated
random), 50 local generations. Both local/global engineering paths are retained;
the deferred laptop job launches only Phase 1. No evolved V35 candidates or
financial caches initialize V36.

## Crossover

- Legal pairing uses existing 80% within / 20% between-species requests,
  training-only allocation, ancestry exclusion and tournament selection.
- Draw 1/2/3 siblings with probabilities 75%/20%/5%. Clip to remaining species
  slots. Reciprocal siblings alternate primary parent. Each independently draws
  crossover actions and participates in the unchanged mutation mask. This
  changes offspring correlations, not population or evaluation budgets.
- Change 1..5 action graphs. Each uses 40% inheritance, 30% exchange, 30%
  composition. Existing 50% homologous-weight exchange remains. Duplicate/invalid
  graphs use existing bounded retries/fresh fallback.
- Exchange chooses a compatible donor 70% of the time, random 30%. Compatibility
  ranks feature/temporal role, shape and output statistics, sampling one of the
  closest four. It is a heuristic, not proof of equivalent function.
- Composition joins: tanh 40%, min 10%, max 10%, product 10%, gate 20%, attention
  10%. Whole-action versus connected-module composition remains 50%/50%.
  Parent action margins are centered for new joins; base sizing heads remain.
  All existing neuron/connection/weight bounds apply.
- 25% of exchanges request an identity scale/offset interface, matching donor to
  recipient mean/std with at least eight valid, nonconstant observations. Scale
  is bounded [0.25,4]; otherwise it skips explicitly. This is an adapter proposal,
  not a fitness fit or a behavior-preservation claim.
- Whole-action inheritance carries related policy genes with probability 50%.
  Swing parameters stay together. Remaining policy exchange/mutation stay active.

Statistics use the first window, first eight clocks and up to eight listings of
an identity-bound training activation probe. This fixed input/state probe is not
a financial replay or validation feedback. CPU float32 recurrent arithmetic uses
Numba without fastmath. A bounded 256-network generation-local LRU caches all-node
descriptors/statistics; caching consumes no RNG. These statistics do not estimate
full-session trading behavior.

Reproduction telemetry records sibling requests/realization, distinct pairs,
composition operators/interfaces, linked policies, cache/profile timing, sibling
behavior distance and elite survival from already evaluated training results.

## Execution and launch

V34's $300 planned-risk cap, 20% stop-distance ceiling, $1000 entry
and $200 addition caps, terminal validity, compact T/R ancestry percentages and
M/X counters remain. Press `h` in the detailed observer for family aggregation.

`deferred_launch` waits without importing Torch or opening CUDA until V35 seals
all 50 generations, exits successfully and releases exact process/lock ownership.
It then performs short GPU boundary/parity checks before one fresh campaign.
Identity changes, failures or existing V36 campaigns stop launch. The laptop keeps
12 GiB host reserve plus 8 GiB load admission. A finite 64-clock diagnostic is not
a full-session timing qualification. Compare saved generation 1/50 with V34 after
completion; this single-session experiment cannot establish generalization.

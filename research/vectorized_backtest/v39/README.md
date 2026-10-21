# V39: behavior-preserving crossover and mutation priors from V36

All six Entry composition operators (tanh, min, max, product, gate, attention)
retain the full primary network and independent size head. The child output is
`tanh(primary_output - primary_threshold + epsilon * bounded_interaction)`,
with epsilon uniform in [-0.1,0.1] and threshold initially zero. The value-based
boundary can initially move by at most 0.1 before ordinary homologous exchange
and mutation. Other action composition paths retain V36 arithmetic and RNG.
Feature availability still uses union provenance, so this is not a promise of
identical financial behavior where parent feature availability differs.

Changed action counts 1..5 have probabilities 55%,30%,10%,4%,1% (previously
uniform). Mutation edit counts 1..5 have probabilities 55%,25%,12%,6%,2%.
The existing 20..85% mutation mask and 1..5 support remain. Entry threshold
mutation uses reflected normal sigma 0.02 with 95% probability and a full-range
uniform reset with 5% probability. Other parameter perturbations remain V36.
Entry input/delay rewiring inserts a bounded identity blend preserving the
original edge plus a new contribution weighted uniformly in [-0.05,0.05].
Every recurrent reference and size-head index is remapped on insertion.
These weights can subsequently evolve across the existing full parameter bounds.
No operators, growth, recurrence or legal action counts are removed.

Multiple-child probabilities remain 1/2/3 = 75%/20%/5%. Matched exchange,
interfaces, linked policies, speciation, objectives, $300 risk cap, 20% stop,
fees, sizing and certified raw producers remain unchanged. These are birth
priors informed by observational single-day logs, not proven causal improvements.

Matched Aug 4 local trial: population 1024, seed 2020, the same 6144 unevolved
initial genotypes/RNG as V36 (3072 templates/3072 random), 50 generations,
batch 1024. No financial evaluations or evolved candidates are imported.
The Phase 1 detailed panel and profiling remain active. V39 waits for V38.
Short qualification uses eight new composition/rewiring graphs with eager vs
captured financial checks plus 512/1024 initial fixtures over only 64 clocks.
Comparison against V36 at generations 1,20,50 explicitly records all intentional
crossover/mutation differences. This is not held-out generalization evidence.

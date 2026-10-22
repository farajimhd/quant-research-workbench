# V40: deterministic reproduction allocation from V34

Aug 4 local comparison: population 1,024, 50 generations, seed 2020, batch
1,024. The launch reuses exactly V34's 6,144 original initial genomes and
post-initialization RNG state. Only the version label changes. No financial
results are imported; every initial candidate is evaluated normally.

Reproduction budgets, species, ancestry constraints, selection objective,
financial execution, risk $300 and stop distance 20% remain V34's.

Deterministic allocations replace randomized parent tournaments, mating routes,
action subsets/modes, mutation selection/count/targets, parameter field/reset
selection, homologous-edge mixing, new neuron identity, and archive sampling.
Mutation fraction is fixed at 52.5%; edit counts 1..5 have equal quotas.
Requested within/between-species routes are 80/20. Changed action counts 1..5
have equal quotas; inherit/exchange/compose requests are 40/30/30. Parameter
versus structural requests are 70/30. Invalid proposals still use the original
bounded retries and may fall back to immigrants. Requested quotas and accepted
outcomes are recorded separately. Rank coverage follows the rank bias of a
size-three tournament using deterministic quantiles.

Novel feature/source/structure proposals and numerical perturbations retain
seeded exploration. Independent streams are addressed by saved root RNG state,
generation, offspring slot, operation and attempt. Retry draws cannot shift
another offspring's stream. Inherited neuron identities remain unchanged.
This is a deliberately different search policy, not a results-preserving
performance optimization or a guarantee of better profit/generalization.

The existing detailed Phase 1 panel and profiling remain active. Comparison
reports verify sealed receipts and compare generations 1, 10, 20 and 50 against
the original V34 run. Single-day in-sample results do not establish generalization.

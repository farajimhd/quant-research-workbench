# V39: Entry product/gate residual initialization ablation of V36

V39 changes only crossover composition initialization for Entry product and gate.
The complete primary Entry network and its sizing head remain. Its original
output margin is combined with a bounded interaction of selected parental
modules: child = tanh(primary_output - primary_threshold + epsilon * interaction).
Epsilon starts uniformly in [-0.1,0.1]; the final threshold starts at zero.
Consequently interaction can alter the value-based decision only within 0.1 of
the primary boundary before the usual homologous-weight exchange and mutation.
This is an initialization prior: subsequent mutation and
crossover can change every weight, bias and threshold under V36's normal bounds.
Known-feature provenance remains the existing union rule; this does not guarantee
identical decisions when parental feature availability differs.

Whole-action/module selection remains 50/50. Module composition uses the selected
primary internal node for the interaction while retaining the full primary
trigger and size head. The production path adds four wrapper neurons plus the
donor module, with no activation-probe replay or fitness fitting for this fix.
The original 512-neuron strategy budget and bounded retry rules remain.

All other action/operator composition paths and their RNG draws match V36.
Operator probabilities, siblings, matched exchange, interfaces, linked policies,
mutation, speciation, objective, $300 risk cap, 20% stop limit, fees and sizing
remain unchanged. Source-certified feature bytes are preserved exactly.

Matched Aug 4 local trial: population 1024, seed 2020, 6144 identical unevolved
initial candidates (3072 templates/3072 random), 50 generations, batch 1024.
No financial results or evolved candidates are imported. The detailed Phase 1
panel and profiling remain active. V39 queues after the existing V37/V38 jobs;
GPU qualification is finite (64 replay clocks), not a long optimization test.
Comparison against V36 includes generations 1,20,50 and checks other contracts.
This single-session trial is not held-out generalization evidence.

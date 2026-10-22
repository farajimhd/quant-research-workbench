# V42: aligned sparse mask evolution

V42 retains V40's phase-one/phase-two execution, objective, speciation, mutation
budgets, and financial/risk semantics. The requested experiment is Aug 4 local,
50 generations, population 1024, seed 2020, with V34's exact 6144 initial programs
and initialization RNG. No historical financial results are imported.

Crossover replaces graph composition and module exchange with connection-mask
recombination at one aligned node per selected action network. Surrounding
computations remain inherited. Uniform inheritance, union, intersection and XOR have equal
request shares. Rates describe requests: invalid proposals are counted/retried by
the existing bounded reproduction controller, so accepted shares can differ.
Empty nodes, invalid arities, cycles and existing capacity violations are rejected;
there is no silent repair or truncation.

Each initial action graph receives persistent node slots by its original node
order. This is a common scaffold, not a claim of biological homology. New growth
nodes receive distinct persistent slots from their identities. Features align by
certified input identity. Hidden and recurrent edges align by persistent slots,
never shifted execution indices. Live-node activation is derived from reachability
of the action and sizing heads; unused merged nodes are not executed. The primary
parent supplies those head identities. Nodes absent in the other parent retain
that parent's computation when referenced.

Weights, biases, thresholds and float policy genes use 50% parental inheritance
and 50% convex blending, elementwise. Integer/categorical genes inherit either
parent. Coupled swing settings and ordered attention/conv/gate argument blocks
inherit together. Mixed operation types inherit a complete node block. This
preserves argument roles, rather than treating categorical values as bit fields.
Existing structural mutations toggle connections, alter operators, grow/remove
modules and change recurrence. No backpropagation is included.

Masks are packed for Boolean operations; bounded sparse numerical arrays are
processed with NumPy. Graph dependency validation and sparse assembly still need
bounded Python traversal. Replay uses the existing sparse fused CUDA kernels;
there is no dense computation over disabled features. Existing 512-node strategy
and 16-input node budgets are retained, not tightened. This version does not
claim unbounded network capacity or a whole-run speedup without measurement.

Run through `python -B -m research.vectorized_backtest.v42.run_experiment --job ...`
using the source-bound runtime job. The launcher verifies initial identities,
qualification and no live optimizer, preserves a unique campaign, and opens the
existing detailed panel without taking focus. Diagnostics and outputs stay under
`D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v42`.

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

V41 management update: 1/2/3 sibling requests use 75/20/5% pair-group quotas,
clipped only at the existing species/population allocation boundary. Siblings
share a parent pair, alternate primary orientation, and have independent addressed
proposal streams. Existing ancestry constraints, species budgets, fresh/elite
budgets, and mask operators remain. Operator/mutation outcome tables, best-parent
comparisons, sibling survival and behavioral distance are derived from already
computed training results; they never influence reproduction or consume RNG.
Tags can overlap and are observational, not causal operator-effect estimates.
The original completed V42 run and its Aug5 replay remain on their immutable
pre-update source. This source update does not restart or extend that campaign.

## Full two-machine campaign

`python -B -m research.vectorized_backtest.v42.distributed_train --job <job.json> --authorize-run`

The authorized full campaign uses 24 independent local sessions, 100 generations
per local, population1024, initial6x (3072 templates +3072 calibrated random), then
200 global generations over the same 24 training sessions and display-only
validation on 12 chronologically later sessions. Global seeds retain the original
balanced local-archive/fresh mixture. The Aug4 imported pool can be pinned as an
optional session-specific initial pool; other sessions sample once normally.

One laptop coordinator owns global population/RNG/pedigree/species/checkpoints.
Two persistent node agents claim immutable whole-local or frozen-session replay
units through a shared runtime queue. Atomic exclusive claims never expire by
elapsed time; recovery checks exact PID/creation and stays on the original host
with its checkpoint. Sealed results are hash-verified and merged in chronological
session order before one reproduction. Validation cannot affect reproduction.
CPU reproduction overlaps display-only validation. No historical result caches
are imported into the fresh campaign.

Each node keeps certified data on local disk, with explicit bounded cache-day
eligibility. The workstation can evaluate every session. Laptop eligibility may
be bounded to preserve disk capacity for campaign outputs; no session is omitted.
Only certificate-declared runtime inputs are staged, excluding producer scratch
and per-ticker construction copies. Whole dataset certificates stay unchanged.

Population decode, identity and packing run once per node/generation, overlapped
with data I/O. Validation subsets reuse exact training preparation when available.
One CPU-only next-session prefetch is allowed under the unchanged node-specific
reserve/admission guards. GPU upload remains demand-only. The workstation keeps
128 GiB reserve +32 GiB admission; the laptop uses12+8. Node artifacts, queue
claims, transfer times, stage/replay/capture timings and ownership are durable.

Use a new source-bound job and campaign; the old Aug4 experiment launcher and
completed results remain preserved. Stop requests use the campaign/transport
STOP files; incomplete work resumes under the exact recorded source and contract.

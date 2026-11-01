# V62: minimal founders with bottom-up growth

Self-contained copy of V61 at `da6a21929`, with the replay, reproduction and publication optimizations qualified through `7dc3f8c2d`. V62 starts a separate fresh global campaign; it never resumes V61 genomes or fitness.

## Fresh construction

All 6144 founders start with 1-2 output-reachable nodes per action. There are five action networks, so a fresh candidate has 5-10 nodes total. Node counts exclude market inputs and policy parameters. Initial sampling balances one- and two-node proposals per action; calibration can replace invalid proposals, so final counts need not be exactly equal.

Larger combination and template founders are removed. Training-only policy calibration and joint feasibility checks remain. Fresh immigrants, calibration replacements and construction fallbacks use the same 1-2-node sampling bounds. A fresh-construction boundary validates final founder and immigrant sizes. Failed proposals are replaced; exhausting bounded retries fails explicitly instead of submitting an incomplete batch. No prior genomes or financial replay results seed V62.

## Evolution and unchanged behavior

The size restriction applies only to fresh construction. Mutation retains identity insertion, confirming conditions and alternatives; crossover retains dependency-preserving subtree exchange and the existing operators. Output reachability remains required. The total and per-action maximum remain 1024 nodes, depth 512 and fan-in 16. Growth requests remain 20% of the crossover/mutation allocation. Parent selection, size diversity, smaller-on-equal-score tie breaking, objective, execution and market inputs remain unchanged from V61.

The existing progress panel and generation telemetry are retained. A smaller starting network is not a guarantee of profitable evolution or generalization; assess growth acceptance, parent/child improvements, inactivity, costs and validation alongside node counts.

## Validation and launch status

CPU checks cover full 6144-founder construction with a synthetic training-only probe, uniqueness, replacement and prefetched-immigrant bounds, growth beyond two nodes, identity insertion parity, dispatch and replay scheduling. These checks do not measure market replay speed or candidate profitability. The V61 optimization path passed an isolated complete 24-training/12-validation generation before being ported. Profile fresh V62 genomes on the workstation before launching; retain exact financial parity evidence and distinguish cold compilation, replay, liquidation and publication time. Runtime reports remain outside source. Use the local `run_train` entry point with a separately prepared, source-bound V62 job when authorized; never resume a V61 checkpoint as V62.

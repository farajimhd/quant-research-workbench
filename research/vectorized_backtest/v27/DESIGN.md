# V27 implementation boundary

The design authority is [README.md](README.md). Baseline: V21 after its approved 128-node continuation. Experimental changes: extended composition and training-only behavioral second-parent selection. Original crossover remains explicit and receives half of operator requests by default. A V21-only control preserves original reproductive RNG and payload behavior for plain candidates.

Copied source remains version-isolated under `v27/`. V21 objective, initial sampling, archive selection, ancestry budgets, leaf mutation, financial transition arithmetic, data authority and validation isolation are retained. Routing requires additional replay/gate support and bounded two-leaf policy ownership. Compatible compute and host-memory support comes from V26; the pure-profit objective, 15% mutation-only mating substitution and reduced crossover-child mutation count do not.

Acceptance evidence must distinguish CPU/control parity from CUDA financial parity and measured full-session throughput. No full campaign, automatic launch, V26 interruption or sealed validation run is authorized by implementation alone. Generated evidence belongs only below the V27 runtime root. A future immutable launch must retain certified raw producer bytes and qualify driver-cache setup before kernels.

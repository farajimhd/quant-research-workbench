# V25: V21 two-phase search with net-profit scores

Isolated V21 copy. Local score = session net profit P. Global score = sum of training-session net profits P. P includes the existing commissions and execution costs. No profit trimming, concentration, drawdown or complexity deductions. Financial diagnostics and all feasibility checks remain.

Retains 24 local sessions (30 generations, initial 6144 then 1024), 50 global generations at 1024, 128 nodes per rule, 0..20 additions, V21 archives and global-only breeding, 20% maximum stop distance and $300 planned position risk. Validation is display-only and never selects parents.

Optional initial reuse: prepare a manifest with `python -B -m research.vectorized_backtest.v25.initial_reuse --local-root <V21 local root> --output D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v25/initial-reuse.json`. Pass it as `--reuse-initial-manifest` to `v25.run_train` with the existing required dataset, predecessor, output and commit arguments. Compute additionally requires `--authorize-run --mode full`. Run `--help` for complete arguments. No automatic launch.

Reconstructs the original fresh random pool from seed 2020 and session date. Exact ordered payload hashes must match all 6144 old results. The source contract, sealed receipt hashes, current certified inputs, execution and training-only identity must match. Re-scores raw metrics as P; old fitness, archive and descendants are not imported. RNG advances through fresh reconstruction and V25 reproduction, never restores the old evolved checkpoint RNG.

The imported evaluation counts as local generation 1; selection, crossover and mutations run immediately. The first new GPU evaluation is generation 2 (1024 candidates). Thus 30 local generations includes one reused evaluation and 29 new evaluations. No global evaluation is reused: every local pool belongs to a different session. Global experts come from the newly evolved V25 local archives.

Reuse accepts the original V21 source identities before/after the exact numeric-mean wrapper optimization. It does not enable arbitrary cross-version result-cache sharing. Any mismatch fails closed; omit reuse explicitly to evaluate a new sixfold pool.

Generated files belong only under D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v25. This implementation does not stop V24 or start V25.

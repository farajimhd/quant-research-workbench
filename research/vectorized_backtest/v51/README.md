# V51: reduced loss and drawdown costs

Duplicate of repaired V50, with score:

`net profit - 0.05 * gross losing-position dollars - 0.05 * equity drawdown`.

Global fitness still sums session costs, including each session's maximum equity
drawdown. Execution, risk safeguards, eligible-active parent ranking, calibrated
fresh immigrants, ordinary/whole-network crossover and mutation fallback are unchanged.
Population remains 1,024; the full budget is 100 generations over 24 training and
12 display-only validation sessions on the workstation.

The authorized start reuses the exact original V50 generation-1 population of
6,144 candidates, its initial RNG/pedigree, and sealed financial receipts. It
changes only scores and rankings, reproduces generation 2, then rescores existing
validation results for display after freezing the successor. No founder market
replay or new random founder initialization occurs. All V50 artifacts are preserved.
An explicit initial-reuse manifest and new generation-1 seal bind source, execution,
population, receipt hashes and the changed objective. Checkpoints require this binding.

Prepare using `python -B -m research.vectorized_backtest.v51.full_launch` with
`--source <original-v50-campaign> --stopped-v50 <latest-stopped-v50-campaign>
--output <new-v51-runtime> --code <immutable-v51-deployment>` and
`PYTHONDONTWRITEBYTECODE=1`, then run `full_run --job <runtime>/full-job.json`
through the pinned Task Scheduler launcher. The first new market replay is
generation 2. Keep the runtime directory short to avoid Windows compiler-cache
path-length failures. Progress T switches training/validation, Q closes only the observer.

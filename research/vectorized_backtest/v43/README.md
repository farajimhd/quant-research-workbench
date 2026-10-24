# V43: profit surviving concentrated winners

V43 keeps V42's execution, fees, risk limits, calibrated initial programs,
genetics, speciation and reproduction budgets. Only objective scores change.
Its own runner, source identity, outputs, checkpoint contracts and compiler cache
are separate. It imports unchanged V42 numerical infrastructure from a pinned
runtime snapshot; it never patches the active V42 deployment.

Remove the largest `ceil(0.10 * positive_count)` positive contributions, retaining
all losses. Maximize net profit minus the maximum of the trade and session
removals, minus 0.05 times the sum of daily drawdowns. Trades are complete net
position episodes, including adds, reductions and fees. Tickers aggregate across
the evaluated sessions. With no positive contributions, removal is zero.

For a single-session local search, session concentration is unavailable and is
explicitly omitted; removing that only session would erase every positive result.
The local objective uses the trade removal only. Diagnostics are
sealed in each generation's scores.json. Ticker concentration is diagnostic only and never affects fitness. No trade-count reward or complexity
penalty is added; all original financial feasibility checks remain in force.

The authorized laptop test is 2026-08-13, 50 generations, population 1024, seed
2020, initial multiplier 6. Its exact 6144 original V42 candidates are deterministically
reconstructed and must match the original sealed population hash before admission.
The original full-session financial receipt is hash-verified and rescored; old
objective scores and offspring are never imported. The post-initialization RNG is
preserved so the comparison begins at the same initialization boundary.

Run from the dedicated immutable runtime snapshot, using Task Scheduler:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -m research.vectorized_backtest.v43.run_test --job D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v42/distributed-399e034e1/full-job.json --output D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v43/aug13-test --day 2026-08-13 --authorize-run
```

The output contains launch-status.json, run.log, fairness-proof.json, the original
cohort, and the local session's generation seals and resumable checkpoints.
This command neither opens validation data nor launches V42 workers/controllers.

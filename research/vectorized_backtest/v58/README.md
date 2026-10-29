# V58: V57 replay engine with concentration scoring

V58 preserves the V57 feed-forward genomes, execution, 1024-node limits,
24 training / 12 display-only validation sessions, 25% ticker-specialist parents,
2 concurrent workstation replay lanes, and 200 generations.

For each ticker-date, score is closed net profit minus gross losses minus
`0.5 * largest positive closed trade * (1 - 1 / max(closed trade count, 1))`.
The session component is total net profit minus total gross losses minus all
session equity drawdowns minus
`0.5 * largest positive session net * (1 - 1 / max(profitable session count, 1))`.
Final fitness is the sum of ticker and session components. Only profit is
penalized; no losses or drawdowns are removed. The same ticker formula drives
specialist selection; local and global ranking share one scoring authority.
One winning trade retains credit. Inactive candidates are still excluded from
parent pools by the unchanged active-trade filter.

Initialization verifies V57 generation-1 seals, raw training receipts, exact
founder genomes and replay-engine hashes. It recomputes fitness and specialist
scores for all 6144 founders, reproduces 1024 candidates with the unchanged
reproduction policy, then starts V58 generation 1. No founder GPU replay and no
validation-based initial selection are permitted. A separate runtime and
hash-bound bootstrap checkpoint preserve V57 history.

`python -B -m research.vectorized_backtest.v58.founder_restart --predecessor <V57-job.json> --root <new-runtime> --code <pinned-deployment>`

Then use `run_train --job <new-runtime>/global-job.json` and
`campaign_progress --job <new-runtime>/global-job.json`.
Every launcher must set PYTHONDONTWRITEBYTECODE=1. Artifacts belong under
D:/TradingML/runtimes. Existing genome, quote, and receipt format tags deliberately
remain V57: these representation and execution contracts have not changed.

Coefficient study: `profile_concentration --campaign <V57-runtime> --generation 8`.
The retrospective training-only comparison of coefficients 0, .1, .25, .5, .75,
and 1 did not distinguish a winner. The selected .5 is a provisional default,
not a demonstrated optimum or an independent out-of-sample result.

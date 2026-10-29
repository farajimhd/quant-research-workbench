# V57: fresh feed-forward evolution with trimmed fitness

V57 starts from fresh version-local templates and random candidates. No V56
population or financial results are reused. Prepared certified market data and
training-only calibration probes retain their original authority.

Fitness is the sum of two components:
1. For each ticker-date, remove its largest strictly positive closed-position
   profit. Sum remaining profits minus remaining gross losses. Position-level
   marked-to-market paths are unavailable, so this component has no drawdown.
2. Exclude the session with highest strictly positive net profit, including its
   loss and drawdown components. Sum net profit minus gross losses minus session
   drawdowns for the remaining sessions. Ties use the first manifest day.

All-losing groups never discard losses. Empty groups contribute zero unless
an observed equity drawdown remains. On the one-day pilot, a positive session
has zero session component; the ticker component still drives evolution.
Ticker-specialist parent ranks sum the trimmed ticker-date costs. Parent draw
allocation remains 25% ticker specialists / 75% overall score. No risk admission
caps are reintroduced. Financial net/loss/drawdown reports remain untrimmed.

Networks reject negative (delayed) edge indices. Mutation cannot add recurrence.
Previous-action requests/fills/masks are removed; current position state and
elapsed replay gap remain. Fresh crossing templates become current-side
comparisons, with all 3072 template programs unique. Scratch storage uses one
buffer rather than recurrent double buffering. Financial replay stays sequential.

The V56 terminal/navigation is preserved. A -> training/validation candidate
rankings include opposite-split score, profit, drawdown, and observed session
coverage. Missing opposite-split results are shown as unavailable, never zero.
The global fitness is recomputed from components, not summed single-day fitness.

Prepare with `python -B -m research.vectorized_backtest.v57.full_launch
--source-job <certified-source-job> --output <runtime-run> --code <immutable-code>`.
Run with `python -B -m research.vectorized_backtest.v57.pilot --job <pilot-job>`.
Open `python -B -m research.vectorized_backtest.v57.campaign_progress --job
<pilot-job> --view global_sessions`. Launchers set PYTHONDONTWRITEBYTECODE.

The authorized pilot is laptop CUDA, August 13, 2026, 50 generations, population
1024 with 6144 fresh initial candidates, seed 2021, 512-candidate batches, and no
validation. This pilot does not establish out-of-session generalization.
All profiles, deployment snapshots, logs and results belong beneath
D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v57.

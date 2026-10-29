# V57: fresh feed-forward evolution with trimmed fitness

V57 starts from fresh version-local templates and random candidates. No V56
population or financial results are reused. Prepared certified market data and
training-only calibration probes retain their original authority.

Fitness is the sum of two components:
1. For each ticker-date, remove its largest strictly positive closed-position
   profit. Sum remaining profits minus remaining gross losses. Position-level
   marked-to-market paths are unavailable, so this component has no drawdown.
2. Subtract the highest strictly positive session net profit from total net
   profit, then subtract ALL session gross losses and equity drawdowns. The
   trimmed session's losses and drawdown remain penalized.

All-losing groups never discard losses. Empty groups contribute zero unless
an observed equity drawdown remains. On the one-day pilot, a positive session
has session component `-gross_losses - drawdown`, plus its trimmed ticker score.
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

Laptop replay uses 32-lane GPU tiles. Byte-identical position-independent entry
graphs share a replay result, including their size head; genomes remain intact.
An entry tile may skip outside current membership; management may skip if every
listing is flat. Original arithmetic is preserved inside active tiles.
No features, financial gates, candidates or clocks are removed. Source upgrades
resume only from an explicitly hash-bound checkpoint and result-cache receipt.

Fresh proposal calibration runs on one bounded CPU lane during main replay,
using a cloned RNG and the exact same slot-addressed proposals. Diagnostic
mutants share GPU batch capacity with training but publish separate receipts;
their reports overlap reproduction, and successor packing overlaps seal
publication. Diagnostics never feed selection or the retained training cache.
Full generation timings include
checkpoint and observer publication; older seals predate that final timing field.

Candidate detail -> select session -> Enter opens its saved position ledger.
S cycles sorting and R reverses it. Receipt verification/loading runs only in
the observer, with one active read and one coalesced pending selection. Replay
clocks and listing IDs are shown as recorded; absent prices/fills are not inferred.
The global session table fixes its column widths and shows replay elapsed time,
average session replay and average generation duration over the last five seals.

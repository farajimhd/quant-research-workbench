# V48 full distributed optimization

Current authorized full run: 24 training sessions and 12 later chronological
validation sessions. Population 1,024, seed 2020, initial sixfold 6,144 per local
session. Phase0 = 50 generations per ticker/session-isolated population;
phase1 = 50 generations mixing tickers within that session; phase2 = 100 global
generations across all 24 training sessions. Each session's phase0 and phase1
stay on the same GPU host. Assignments balance measured GPU work across laptop
and workstation, then remain immutable for checkpoint/cache authority.

    Score = NetProfit - 0.05 * RealizedDrawdown - 0.75 * GrossLosingPositionDollars

Phase0 uses the owned-case score for every parent/elite route. Phase1 has one
session-score pool. Phase2 has one global-score pool, with chronological realized
closed-position equity across ordered training sessions. Fees and bid/ask spread
are already included in net episode profit; the loss term is an extra charge.
There is no separate raw-profit selection route. Keep baseline 10% elite and 20%
fresh slots, remaining crossover, 25% whole-network OR/AND/XOR (50/25/25), ordinary
mask crossover, sibling schedules, mutation, ancestry/species and execution guards.

Phase0 ownership is immutable across trading, parent selection, mutation, caches,
archives, receipts and checkpoints. Dynamic case quotas reserve exploration and
never transfer a candidate between tickers. Promote the bounded case-balanced
positive specialist archive accumulated across ALL phase0 generations, with
verified source seals and pedigree; remove ownership only at the phase1 barrier
and fully re-evaluate promoted programs. Phase2 similarly seeds 75% session
experts in balanced session-rank rounds from historical phase1 archives, plus
25% calibrated fresh programs (or explicit extra fresh slots for shortages).
Namespace source serials and ancestry by session. No old fitness becomes new
fitness. The existing August4 initial sample pool remains pinned in the job.

The 12 validation sessions evaluate the frozen current population after training
has already selected/frozen its successor and RNG. Full before/after hashes prove
validation independence. Validation receipts have distinct split/cache identities,
are display-only, and never affect reproduction, weights, behavior or training RNG.
Validation includes episode tracing so its displayed cost has the same definition.

One persistent scheduled GPU node per host processes paired session units and
then immutable global replay requests through a hash-bound shared runtime bus.
A CPU coordinator on workstation owns the global barrier/checkpoint. Transport
copies only immutable receipts/expert exports, not mutable checkpoint mirrors.
Claims retain host affinity, exact PID/creation time and durable completion/failure
records. No automatic ownership timeout/reassignment or optimizer restart.

Preserve laptop 12-GiB reserve + 8-GiB admission; workstation 128 + 32 GiB. CPU
prefetch is bounded to one next staged session, with demand admission authoritative.
At most two CPU session entries and one graph plan are retained. GPU prefetch is
not speculative; existing physical GPU/workspace guards remain enforced. Reuse
verified unchanged-candidate results, packed populations and compiler caches.

First run focused local/remote CPU/GPU tests and a short representative replay
profile per host, then prepare via full_launch using measured rates and deploy
full_node/full_controller via hidden Task Scheduler entries. Do not run long
all-session qualifications. Stop previous campaigns only at preserved boundaries.
The full console uses Rich color bars, paired phase counters, both node heartbeats,
per-ticker score drill-down and global training/validation tables; Q only closes UI.

## Historical pilot

The earlier immutable August13 pilot used lambda 0.5, phase0 30 + phase1 20, and
its own pinned source/checkpoints. It was stopped at phase0 generation14 before
this newly authorized full run. Do not change its contracts or resume it as .75.

## Read-only phase-aware observer

The observer has an independent deployed overlay; updating it does not replace
the immutable optimizer, change its source/checkpoint identity or restart training.
Default view ranks all phase0 candidates by owned-case score and labels their
ticker. G toggles ticker summaries; Enter opens the selected ticker; brackets
cycle owned tickers and 0 clears the ticker filter. Summary Next counts are the
sealed reproduction allocation for the next generation, not current row counts.

Sort keys: 1 score, 2 raw net profit, 3 gross losses (low first), 4 realized
drawdown (low first), 5 win rate, 6 closed positions, 7 maximum stop risk (low
first). V reverses the order. Z toggles financial eligibility; C toggles active
strategies. J/K selects rows; N/B pages. P shows policy; E/X/A/D/T shows the
corresponding network; U/I pages network lines; Q closes only the observer.

Ticker summaries use eligible candidates for best and median scores; invalid
high-score candidates cannot become the group leader. Combined best-per-ticker
score sums are display diagnostics from separate candidates, not portfolio
profit or the score of a single strategy. Phase1 automatically clears owned-case
filters and ranks full-session strategies using the unified session score.

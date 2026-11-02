# V65: ARTE multiscale shards with V64 diversity selection

V65 retains V64's execution engine, explicit-price programs, objective, and
behavior-grouped elite/parent selection. It starts 1,024 fresh candidates with
fresh training-only calibration and fitness; no prior financial results transfer.

Fresh construction mixes ordinary mutable templates with random minimal graphs.
It proposes 2,048 candidates on the training activation probe, extending to at
most 6,144 only if needed to fill 1,024 distinct slots. Templates occupy at most
384 slots and 48 per family. Vectorized action-pattern distance selection permits
at most two identical probe request patterns. This is coverage screening, not
profit selection: only the selected 1,024 candidates undergo full-session replay.
Later fresh immigrants use the same template/random proposal mixture, with
source families recorded in the pedigree. No template receives a survival quota.

Six persistent replay lanes each prepare up to six upcoming CPU sessions with
three loader threads, subject to host admission. Prepared sessions remain in
pageable RAM across generations. Only replay owners allocate/upload CUDA data.
Repeated swing-window reductions share exact intermediate results. Use
`profile_global --repeat-warm --days <2 to 24 training dates>` to verify retained
RAM residency and exact cold/warm financial parity without result-cache reuse.
Generation-boundary disk admission reserves room for two overlapping publications
plus 64 GiB. It stops before beginning another generation when capacity is low,
preserving sealed results; it never deletes prior evidence to make room.

Inputs are the sealed market_shards/v1 dataset: 150 training and 38 chronological
validation sessions. The scanner selects the causal top five by trailing
60-second trade counts after the prior-close 0.80–50 filter. Validation is
reporting only; there is no separate holdout.

V7 and all four float/share fundamentals remain forbidden. The legacy catalog
IDs are retained, followed by 1,812 completed-bar channels: 60 one-second,
30 ten-second, 30 one-minute, 16 hourly, and 20 regular daily trade bars; plus
60 one-second bid and ask bars. Prices are relative to the current execution
mark; nonnegative activity uses log1p. Gaps and unavailable fields are masked.
No coarse bars seed one-second indicators. These indicators explicitly warm up
from session-open observations; missing prior-session RVOL stays unavailable.
Cross-sectional context uses only the current top five, never the future union.

Only selected ticker/clock points plus their previous-clock observations receive
feature banks. The execution grid covers the full session ticker union, and the
execution-only tail continues through 20:00. The 09:00–10:00 page is split at the
09:30 cutoff; post-cutoff rows never enter feature preparation.

Source is cloned from V64; the shared immutable shard reader is an explicit
hashed dependency. Outputs and profiles belong under
D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v65.

Prepare using `python -B -m research.vectorized_backtest.v65.prepare_shard_campaign
--source <V64-job> --root <new-runtime> --code <committed-deployment>`.
Then profile with `python -B -m research.vectorized_backtest.v65.profile_global
--job <new-job> --output <profile-runtime> --count 1024 --reproduce`, and launch
with `python -B -m research.vectorized_backtest.v65.run_train --job <new-job>`.
Set PYTHONDONTWRITEBYTECODE=1. Deployment must be committed, pushed, and hash-verified.

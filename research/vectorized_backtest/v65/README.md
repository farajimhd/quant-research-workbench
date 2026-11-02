# V65: ARTE multiscale shards with V64 diversity selection

An opt-in `initial_multiplier=6` job pins `founder_pool.SETTINGS`: 3,072 random
graphs, 1,536 parameterized templates and 1,536 trigger/filter combinations.
All 6,144 distinct genomes receive full training replay at generation zero.
The constructor uses the eight-date, four-window training probe, rejects invalid
or unselective entries and duplicate genomes, and fails explicitly at 98,304
proposals rather than padding the population. Probe pattern duplicates are
reported rather than hard-excluded. Combinations allow three entry nodes.
Training-only selection keeps 768 quality/diversity representatives plus 256
coverage choices. Quality combines existing fitness (50%), lower-quartile profit
across five chronological blocks (20%), active sessions (15%), and dispersion of
positive session profits (15%). At least five trades across two sessions are
required. Entry-time, ticker-duration and session-return descriptors group near
duplicates at cosine similarity 0.98; a reported capacity limit prevents one
group from dominating when sufficient distinct groups exist. No family survives
by quota. Generation zero selects without reproduction or validation replay;
the retained 1,024 start ordinary evolution and validation at generation one.
Six network batches remain cached per worker during the initial replay.

Event-based replay uses a bounded ring of eight pinned host buffer sets. A slot
is reused only after its CPU execution completes; execution stays chronological.
Admission includes all eight outstanding GPU packets, and timings report ring
capacity, peak occupancy and allocated pinned bytes. This absorbs temporary CPU
delays; sustained CPU throughput still bounds a finite queue.
Candidate plans stay on each worker's GPU across sessions within a generation,
keyed by the exact candidate batch, with a bounded two-plan cache. Generation
changes invalidate it; more than two distinct batches can cause eviction.

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
Random entry priors span 0.1–10% activation on feasible training-probe observations,
weighted toward the lower end. Each random management head starts inactive with
60% probability; active add/reduce/trail heads target 0.1–5%. These are construction
priors only: ordinary mutation and crossover can change every graph and threshold.

Six persistent replay lanes each prepare up to six upcoming CPU sessions with
three loader threads, subject to host admission. Prepared sessions remain in
pageable RAM across generations. Only replay owners allocate/upload CUDA data.
ARTE resident tensors share their immutable NumPy storage instead of retaining
duplicate history, indicator and context banks for every cached session.
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
# Resident RAM and concurrency qualification

For 16 replay lanes, `host_loading={"resident":true,"workers":2,"depth":32}` prepares each replay
lane's upcoming sessions in a bounded CPU queue and retains them for subsequent
generations. Ready lanes replay immediately without waiting for other lanes'
preparation. The existing RAM reserve remains enforced; deferred
sessions are reported and use demand loading. CUDA upload remains owned by the
replay lane. One shared semaphore caps training plus validation CPU preparation
at 32 active loads, including demand loads. Each lane chooses a ready session
without waiting for a slower earlier load; returned fitness retains date order.
Worker counts 6, 8, 12 and 16 are supported for measured comparisons.
`profile_global --days ... --repeat-warm` accepts up to all 150 training sessions,
reports preparation separately, and checks exact cold/warm financial evidence.
`--prefetch-all-sessions` additionally verifies all 188 sessions can remain in RAM
with the 128 GiB reserve; only the selected training prefix is replayed.
Changing concurrency on resume requires a pinned `scheduling_change` describing
both configurations; candidates, RNG, completed receipts and financial semantics
remain unchanged. A higher worker count is not assumed faster.

`publication_workers=4` uses four independent session-receipt writers with at
most twelve queued payloads. A bounded CPU handoff queue lets replay workers
advance while results are dispatched. Each session uses a stable FIFO writer.
Generation sealing waits for receipt futures in a background coordinator and
propagates writer errors; checkpoint publication remains single-process and
ordered while the next generation can replay. Publication concurrency is also
bound in resume scheduling authority.

# V6 causal compact-market optimization (implementation in progress)

V6 has certified sparse training inputs, six lifecycle programs, semantic
sampling, management orders and a signed lower-tail dollar objective. The
inherited staged search is not the V6 full-training controller: do not launch
optimization from it. V5 immutable deployments are untouched.

First runnable producer:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -m research.vectorized_backtest.v6.torch_backtest.materialize --sessions D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v4/campaign_inputs/20261006-source-aligned/sessions.json --output D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v6/materialized/volume30-top10 --workers 2
```

The producer reads only the 30 training sessions and their previous regular
session context. An explicit certified context manifest/ledger is required when
the prior date is outside the main source build. Price eligibility is previous
regular-session final traded canonical bar close, adjusted for opening-known
splits, inclusive $0.80--$50. It precedes all current-session volume reads.
Missing context fails the session closed rather than substituting a close.

Rolling share volume includes the current completed second and the preceding
29 elapsed seconds. Zero-volume/unavailable listings do not fill empty slots.
Stable listing identity breaks ties. Bounded session workers query certified
ARTE bars, validate source counts/keys/content and storage placement, and write
top-N blocks, rank arrays and sparse backing rows beneath the runtime root.
Completed output resumes only with identical arguments/source/output hashes.
Validation is sealed; no validation market data is opened.

These first blocks are deliberately marked `ready_for_replay=false`: feature
history, execution quotes and held/pending-order binding remain required.
V5 must not stop merely because these preliminary blocks are complete.

`compact_prepare` binds all causal feature history and certified quote/fill
evidence. `sparse_runner` is a profiling baseline with stable daily-union broker
state and sparse market/gate lookups; it avoids full-time dense market and gate
tensors. `compact_runner` implements candidate-specific top-N-plus-held/pending
financial slots with stable ledger identities and shared causal source history.
Capacity exhaustion rejects the run; retained identities are never discarded.
Dense-reference fixtures verify exact fills/financial metrics including a
departed holding and raw structural targets. `sparse_structure` prepares raw
V7 structural targets outside replay with opening-known references; absent
certified prior seeds are explicitly masked. Sidecars bind exact market keys
and receipt hashes. Structural modes fail closed without this sidecar.

`training_pass` supplies a bounded concurrent all-30-session selection barrier
and the lower-tail objective. `sparse_evaluator` owns independent accounts and
audits durable fill receipts; `full_search` preserves exact RNG/population
checkpoints and reuses completed generations on resume. `resident_evaluator`
loads immutable training inputs once and retains compatible broker captures
across candidate batches and generations. It evaluates shared rule programs
behind a serial preparation barrier, then replays independent accounts on
separate CUDA streams. All accounts finish and publish audited receipts before
the next batch or selection. A changed input/structural identity fails closed.
Rule histories use batched causal gathers. Swing confirmations are shared by
exact left/right window pair; prefix maxima and momentum lags are selected from
shared listing history without repeating it across candidate account slots.
Rule evaluation retains instruction results in one buffer instead of repeatedly
copying all earlier instruction results. Exact history allocation remains part
of the broker capture identity: rounding 31-row histories to 32 changed compiled
financial arithmetic despite identical fills, and was removed. Compatible
captures are reused; different exact allocations require a new capture.
Declared concurrent memory envelopes are checked before launching evaluation.
Terminal 50-row pages and
session/complete-position diagnostics are implemented.

`profile_sparse` is workstation-CUDA profiling only, with explicit percentage
target stratification unless a structural sidecar is supplied. Short profiles
are timing evidence, not profitability or full-generation throughput evidence.
Pointwise lifecycle instructions execute only their participating candidate
lanes. Temporal instructions retain the existing per-window lane grouping and
causal scans; an unpruned pointwise path remains available for exact comparison.
`profile_compact_sessions` measures complete candidate-session throughput,
preparation, replay/audit time and peak allocated/reserved GPU memory.
Resident session timings separate broker compilation/first tick, capture
warm-up, captured blocks and final reset enqueue; reused captures report no
new compilation phases. These host timings use existing synchronization points.
External reference comparison checks candidate metrics and exact fills independently of
batch partitioning. `--resident-repeats 2` distinguishes initial preparation
from a second full pass retaining input and broker buffers.
`--population-reference` can select a prefix of a completed training-only
profile's frozen `population.json`, preserving its exact policies, management
parameters and programs instead of sampling different candidates at a smaller
population count. The source hashes and selected indices are recorded.
`--graph-steps` controls captured broker block length (1..64, default 16). The block
length is recorded in the profile identity and resident contract; changing it
requires new capture rather than reuse of an incompatible graph. Performance
choices require measured full-session timing and exact financial/fill parity.
Local fixture and partial-cohort checks do not certify full-training throughput: the current
optimized deployment still requires completed 30-session financial audits,
exact reference/repeat comparisons and the measured population/generation report.
Full optimization requires the user's final parameter decision.
Full-search session concurrency is bounded to 1..8 and validated before any
generation preparation; the all30 selection barrier and RNG resume are retained.

# V31: behavior-calibrated initial recurrent networks

V31 changes initialization only. V30 campaigns, source and checkpoints are never
modified or imported. The replay, execution, objective, genetic operators and
permitted search bounds are retained from V30. No optimizer is auto-launched.

Initial per-action neuron counts remain stratified over 1, 2, 4, 8, 12, 16, 24,
32, 48, 64, 80 and 96. Independent size permutations are retained; structural
profiles additionally cover shallow/mixed/chain depth, sparse/full-range fan-in
and zero/20%/50% delayed-edge proposal rates. These are starting priors only.
Later fresh immigrants retain unrestricted V30-style sampling; mutation and
reproduction retain every original permitted value.

For each initial action, threshold routes are 30% rare (log-uniform target
0.1–5%), 30% moderate (uniform 5–30%), 20% frequent (uniform 30–95%), and 20%
unchanged random threshold. Quantiles use normalized causal training inputs.
Entry calibration uses actual top-10 membership and flat-state feedback; other
actions use three explicitly hypothetical held-state scenarios. Recurrent memory
resets independently at each short consecutive window. The probe does not claim
to reproduce real position trajectories. No profits, fees, validation observations
or future labels are used. Negligible output span (<1e-4) or missing valid output
permits at most two same-neuron-count resampling attempts; unresolved constants
are retained and counted. Quantiles can miss target rates because of ties or
bounded thresholds; actual achieved rates are measured rather than guaranteed.

Half the initial policy genomes retain independent sampling. Half use overlapping
short/medium/long priors that correlate stop and target scales and vary management
cooldowns, profit requirements and age gates. All policies remain evolvable across
the full original bounds. The probe NPZ is bound to the exact training manifest,
input receipts and slice hashes. `run_train --initialization-probe <probe.npz>`
requires this binding and pins its hash in the campaign/resume contract.

Bounded CPU diagnostic (population 1024, seed 2020, no optimization launch):

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -u -m research.vectorized_backtest.v31.compare_sampling --training <training.json> --output D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v31/diagnostics/<new-run> --population 1024 --seed 2020
```

The comparison uses disjoint calibration and later diagnostic windows from the
same training day. It reports activation/switching/saturation/structural coverage,
policy distributions, memory and wall overhead, not backtest profitability or
generalization. Diagnostic output must be reviewed before any full launch.

The bounded activation scanner uses CPU Numba 0.68.0 (`requirements-initialization.txt`),
with no fastmath or disk JIT cache. It does not enter a CUDA context or alter the
GPU replay kernels. Scalar LLVM tanh can differ from NumPy vector tanh by float32
rounding; tests use the existing neural 2e-6 tolerance and separately require
matching validity, fixture decisions and saturation counts. There is no financial
tolerance change. A NumPy reference remains available as `observe(reference=True)`.

V31 evaluates a fresh population on all 24 certified training sessions in one
global phase. There is no local search or evolved seed import. Fitness is the
sum of net profit after the unchanged fees. Drawdown, concentration and exposure
remain diagnostics, never fitness deductions. Twelve later validation sessions
are display-only: the successor, species state and RNG are frozen before their
evaluation. Validation cannot influence any reproduction or stopping decision.

The five independent sparse tanh action networks retain delayed recurrence,
request/fill feedback and separate entry/add/reduce sizing heads. Execution caps
remain $1000 entry, $200 addition, $300 planned position risk and 20% maximum stop
distance. Certified causal input bytes and float32 neural/float64 financial
arithmetic are unchanged. Missingness masks and observed-input ancestry remain.

The CUDA evaluator retains the full fan-in envelope but loops over each neuron's
actual connection count, in its original order. Padded connection slots perform
no work. Throughput qualification must use the checkpoint's full population and
complete certified sessions, including a one-versus-two-worker comparison;
small-population or kernel-only timings do not establish campaign throughput.
Position and request/fill feedback channels are computed once per candidate,
listing and clock, then shared across all action neurons. The original CUDA
arithmetic and activation order are retained; the additional workspace remains
inside the unchanged 4 GiB admission limit.
Exact-state resumes pin listing-width groups separately from worker concurrency,
preserving the predecessor's financial reduction layout when worker count changes.

Optional `--host-prefetch` admits one next-session CPU load on the existing
single-thread loader, overlapping the current replay. The main thread owns the
two-session LRU; the background reader never mutates active data, pins buffers
or activates CUDA. Speculation checks the unchanged 128 GiB reserve plus 32 GiB
admission allowance before and after loading, defers without waiting when low,
and releases completed speculative data under pressure. This is the same soft
process-cooperative admission policy, not an operating-system memory guarantee.
Demand loading retains its normal memory wait. Certificate failures propagate,
and shutdown cancels speculation without entering a memory-admission wait.

Search is broader than V29: initial action sizes span 1 through 96 nodes,
fan-in increases from 8 to 16, parameter bounds from 4 to 8, and crossover can
modify any 1 through 5 actions. The measured finite workspace envelope remains
512 total reachable neurons per strategy; it is a compute bound, not a size
target or fitness penalty. Initial sampling is stratified and deterministic.

## Species and ancestry

A training-only distance combines normalized structure (40%), homologous node
parameters (20%) and behavior (40%). Structure includes per-action size, depth,
connectivity, recurrence and selected feature identities. Parameter alignment
uses unique persistent node identities and source identities. Behavior includes
entry-clock, holding-time and listing sketches plus per-session capital use,
turnover and position counts. Inactive behavior supplies no similarity evidence;
structure and parameter weights are renormalized in that case.

Deterministic representative assignment targets 16-32 species with an adaptive
threshold and an explicit bound of 64 representatives. At that bound, new
candidates join their nearest representative; the genotype is never rejected
because a new species cannot be created. Stable species state and the threshold
are checkpointed. Ten generations without better training profit marks a species
stagnant. Such species lose protected offspring allocation, except the species
containing the overall best training candidate. Stagnation does not redefine
species membership and validation cannot revive or retire a species.

Reproduction keeps 10% elites, 20% fresh immigrants and 70% offspring. Half the
elite budget first protects non-stagnant species champions. Species receive an
offspring floor and the remaining slots by champion profit rank, which remains
well-defined with negative profits. Parent requests are 80% within species and
20% between species. Parents sharing ancestry within three parent-child links
cannot mate. If bounded attempts find no legal pair, a recorded fresh immigrant
fills the slot; ancestry is never relaxed. The existing 20-85% mutation mask and
1-5 distinct edits remain. Species policy is a separate diversity allocation,
not an extra term hidden in the net-profit fitness.

## Engineering and launch

Use `python -B -m research.vectorized_backtest.v31.run_train` with
`PYTHONDONTWRITEBYTECODE=1`, `--authorize-run`, pinned `--training`, `--validation`,
`--initialization-probe`,
`--output` under D:/TradingML/runtimes, and a qualified `--commit`. Defaults are
population1024, seed2020 and 100 generations. Batch size, worker count and capture
memory must be measured on the target GPU. Workstation defaults preserve
128 GiB host reserve plus32 GiB load admission and no prefetch. Source changes
must be committed/pushed before immutable workstation deployment.

This reuses Phase2 session lanes, chronological replay, result caching, receipts,
training/validation seals, global checkpoint/resume and the existing terminal
canvas. The single visible observer is `v31.observe --output <campaign>/global`.
It never maximizes or focuses windows. Compiler children use CREATE_NO_WINDOW;
other child processes and interactive observers retain their normal behavior.

Species/size/depth/recurrence and structural-offspring survival summaries are
computed once per generation from CPU genomes and existing training receipts.
Their timings are recorded. No extra GPU evaluation, device transfer or hidden
state tape is added for telemetry. Renderers read the published snapshot.

A single seed and repeatedly viewed validation sessions are research diagnostics,
not evidence of deployment readiness or a profitability guarantee.

Cross-sectional normalization shifts each current-clock feature by its first
observed value before computing float32 moments. This avoids CPU/CUDA cancellation
on constant and nearly constant columns without future data or widened parity
tolerances. A measured CUDA tile is set by V31_NEURAL_BLOCK (32/64/128/256),
recorded in the exact resume contract; it changes parallel layout only.

Worker replay uses native candidate-tail widths instead of repeating padded
candidates. This avoids duplicate neural/financial computation for validation
shortlists and cached-elite generations. The no-padding mode is resume-bound.
If fewer valid candidates exist than elite slots, only valid elites survive and
the unused elite slots become explicit offspring/fresh-fallback slots.

Projection uses the fixed complete certified feature catalog, so another
candidate joining/leaving a batch cannot alter the reduction layout of a
retained candidate. Individual graphs still connect sparsely to their own
selected inputs. Normal global profiles already cover the full catalog.

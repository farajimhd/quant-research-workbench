# V32: calibrated local evolution followed by global mixing

V32 uses two phases: independent training-session evolution, then global mixing
of the sealed local experts. V30 campaigns, source and checkpoints are never
modified or imported. The replay, execution, genetic operators,
speciation and permitted search bounds are retained. No optimizer is auto-launched.

Defaults: 24 local sessions, 50 generations each, population 1024 and seed 2020.
Each local first generation evaluates a 6x initial pool (6144 calibrated fresh
candidates) in bounded GPU batches; normal species-aware reproduction reduces
the successor to 1024. Subsequent generations never repeat the 6x sampling.
An initial completed=0 checkpoint preserves the sampled population and RNG before
the first replay. Local species, pedigree, RNG, archives and seals survive resumes.

Each local probe uses only its own training session. Phase 2 balances unique
positive-profit experts across sessions for up to 75% of its initial population;
at least 25% are fresh calibrated candidates (more if archives are insufficient).
Global fresh calibration combines one window from each of up to eight evenly
spaced training sessions. It imports no V30/evolved external candidates. Local
origins and ancestry are carried into Phase 2 with distinct per-session IDs.
All local source/dataset/checkpoint/archive certificates are verified first.
Phase 2 evolves 1024 candidates for 50 generations by default. Fitness in both phases uses the exact V21 objective; validation remains display-only during Phase 2.

Local score: P - 0.25 W_ticker - 0.05 D. Global score: sum(P) -
0.25 max(sum(W_ticker), W_session) - 0.05 sum(D). W uses the positive
contribution of the highest-profit 10% of traded tickers or sessions, with
fractional boundary weighting. Losses remain fully included. There is no
complexity penalty. Selection, archive ranking and species stagnation use this
score; raw net profit remains a separate diagnostic and archive eligibility
condition. Initialization, replay, sizing and speciation rules are unchanged
from V31; the local generation budget increases from 30 to 50. V31 runs and checkpoints are not modified or imported.
The objective module is copied verbatim from V21 and regression-tested against it.

Use the existing detailed Phase-1 terminal via `v32.local_details --active
<active-campaign.json>`. It retains session/ranking/rule navigation and switches
automatically to the existing `v32.observe` Phase-2 panel when global status is
published. Windows remain normal and do not maximize or steal focus. The
controller publishes completed/active/queued/failed local-session counts; only
sealed session completion advances the completed count.

Initial per-action neuron counts remain stratified over 1, 2, 4, 8, 12, 16, 24,
32, 48, 64, 80 and 96. Independent size permutations are retained; structural
profiles additionally cover shallow/mixed/chain depth, sparse/full-range fan-in
and zero/20%/50% delayed-edge proposal rates. These are starting priors only.
Later fresh immigrants retain unrestricted V30-style sampling; mutation and
reproduction retain every original permitted value.

For each initial action, threshold routes are 30% rare (log-uniform target
0.1â€“5%), 30% moderate (uniform 5â€“30%), 20% frequent (uniform 30â€“95%), and 20%
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
input receipts and slice hashes. The two-phase controller prepares these probes
under the campaign's `initialization-probes` directory and pins their identities
in the local checkpoints and global seed pool.

Bounded CPU diagnostic (population 1024, seed 2020, no optimization launch):

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -u -m research.vectorized_backtest.v32.compare_sampling --training <training.json> --output D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v32/diagnostics/<new-run> --population 1024 --seed 2020
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

V32 evaluates local experts plus fresh candidates on all 24 certified training
sessions in Phase 2. Fitness is the sum of net profit after the unchanged fees.
Drawdown, concentration and exposure
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

Use `python -B -m research.vectorized_backtest.v32.run_train` with
`PYTHONDONTWRITEBYTECODE=1`, `--mode full`, `--authorize-run`, pinned `--training`, `--validation`,
`--output` under D:/TradingML/runtimes, and a qualified `--commit`. Defaults are
population1024, seed2020, `--generations 30`, `--global-generations 50`, and
`--initial-multiplier 6`. `--mode local --day 2026-08-04` runs only that local stage.
Batch size, worker count and capture
memory must be measured on the target GPU. Workstation defaults preserve
128 GiB host reserve plus32 GiB load admission and no prefetch. Source changes
must be committed/pushed before immutable workstation deployment.

This reuses Phase2 session lanes, chronological replay, result caching, receipts,
training/validation seals, global checkpoint/resume and the existing terminal
canvas. The single visible observer is `v32.observe --output <campaign>/global`.
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
tolerances. A measured CUDA tile is set by V32_NEURAL_BLOCK (32/64/128/256),
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


## Typed neural operators

V32 adds evolvable bounded operators to each independent action graph. `tanh`
and `conv` compute tanh(bias + ordered weighted sum). `max`, `min`, `mean`,
and `product` apply their named reduction to weighted inputs, then add bias
and tanh. `gate` computes sigmoid(first weighted input) times tanh(bias plus
remaining weighted inputs). `attention` consumes a scalar query followed by
key/value pairs: tanh(bias + softmax(query * keys) weighted values), using a
stable softmax. Weights, biases, connectivity, thresholds and operator type
evolve. Attention uses at most seven key/value pairs under the existing fan-in
budget; it can attend to upstream learned representations or causal memory.

`identity` clips its weighted sum to [-1,1]. With unit weight and zero bias it
is an exact bounded shift register. Initial temporal modules connect two to
four preceding replay-clock taps to a convolution or pooling node. They use
existing delayed edges and zero-reset per-session memory, with no future input
or shared cross-session state. Evolution can change and extend these modules
within the existing graph budgets. Taps are replay-clock steps, not adjacent
feature IDs; this is temporal convolution, not convolution over the feature
catalog. Mean pooling and convolution retain signed weights. Missing channels
remain neutral zero, and a nonzero-weight observed market ancestor is required
before an action can fire. Delay warm-up uses zero history, as does recurrence.

Half of sampled action graphs select the original tanh-only family. In the
mixed family each node has a 35% chance of a typed operator, uniformly among
arity-compatible non-delay operators. Graphs with at least three nodes have a
50% chance of an initial temporal module. These are sampling priors, not search
restrictions. Mutation can select every operator; malformed attention arity
is rejected by the normal bounded retry logic. Node identity, payload hashes,
species descriptors, module crossover and detailed rule displays include the
operator. All outputs remain within [-1,1], preserving threshold and sizing
contracts. No backpropagation or financial-objective change is introduced.

V32 local default is 50 generations (global remains 50). Compare generation-30 seals for matched historical budgets.

Reuse already sealed V31 sixfold evaluations without importing old fitness or offspring:

```powershell
$env:PYTHONDONTWRITEBYTECODE="1"
python -B -m research.vectorized_backtest.v32.initial_reuse --local-root <v31-campaign>/local --output D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v32/diagnostics/initial-reuse.json
```

Pass `--reuse-initial-manifest <that-file>` to `v32.run_train`. The manifest
is pinned into the run and local checkpoints. Only the qualified V31 source is
accepted. V32 regenerates its own initial payloads and RNG, verifies their exact
ordered hash, data/execution contracts, input identities and sealed financial
evidence, then scores with V21. Missing or incompatible sessions evaluate normally;
corrupt evidence fails closed. Prepare a new manifest for a new campaign if more
V31 initial sessions become available; do not mutate a running campaign manifest.
The retained species telemetry field `best_profit` stores selection fitness,
which in V32 is the penalized objective rather than raw net profit.

### Isolated architecture benchmarks

`python -B -m research.vectorized_backtest.v32.benchmark_architectures --job <json>`
runs finite frozen-candidate replays, never evolution or campaign resume. The job
requires `training`, `checkpoint`, `day`, `offset`, `population`, `clocks`, `mode`
(`gpu` or experimental `hybrid`), a new runtime `output`, and runtime `cache`.
Optional `repeats` defaults to two warm passes; `cpu_threads` defaults to one.
Use a source snapshot with the exact certified producer bytes, just as for the
normal deployment. Do not normalize producer bytes or bypass certificate checks.

GPU mode uses the normal captured chronological replay. Hybrid mode keeps neural
weights and recurrent memory on GPU and uses Inductor-compiled CPU financial
execution; position/action/feedback transfers occur every clock. It requires a
configured Windows C++ build environment. Short runtime compiler-cache paths
avoid Windows compiler path-length failures. This hybrid is experimental:
cross-device reductions can differ by floating-point rounding, so a timing win
alone is never sufficient for promotion.

Each job publishes cold and warm timings and complete financial outputs. Compare
the financial outputs, episode identities and ordered candidate hashes across
designs. For concurrent lanes, supply the same `start_file`, wait for each lane's
`ready.json`, then create that file. Use a bounded external controller with exact
process-identity cleanup and host/GPU memory admission. Report preparation and
compiler startup separately from steady replay, and distinguish replicated-day
lane experiments from a benchmark over distinct sessions. None of these options
change the active training launcher or authorize importing benchmark results.

`benchmark_session_batches --job <json>` additionally prepares independent
session/batch graphs in one CUDA context. It compares sequential enqueue,
round-robin enqueue, independent streams, and directly captured multi-session
graphs with and without parallel streams. Every mode must reproduce the same
complete financial and episode hashes. The normal evaluator remains the default;
`capture_only=True` exposes retained replay state only to explicit callers.

Jobs require `training`, `checkpoint`, `days` (one to four), `batch`, `output`
and `cache`. Optional `clocks` bounds the chronological prefix; omission runs the
full session. `block` defaults to 32 clocks, `repeats` to two passes, and host
reserve/admission to 128/32 GiB. `additional_checkpoints` concatenates frozen
diagnostic populations to test larger initial-evaluation batches, never training
initialization. Preparation, graph scheduling time, GPU events, memory and full
outputs are recorded separately. Reusing a graph for timing does not establish
reuse across generations with different candidates. Run a bounded supervising
process and compare each batch design against the ordinary evaluator before
considering deployment. No training parameters change through this benchmark.

The optional `shared_market` scheduler computes the market-only projection once
per session/clock, then fans out to that session's candidate batches on separate
streams. It joins those batches before advancing that session's projection;
positions, recurrent memory, feedback and ledgers remain independent. Captured
projections are retained explicitly for safe cross-stream lifetimes. This trades
additional retained projection buffers and per-clock synchronization against
removing duplicate market preparation; it must be measured, not assumed faster.

Training can opt into the qualified independent-stream design with
`--shared-replay --batch-size 512 --session-workers 2`. Phase 1 runs two independent
local evolutions; Phase 2 evaluates two sessions at a time. One CUDA service owns
all preparation/capture/replay operations and admits at most two candidate
cohorts per session (four streams total). Separate local RNG, species, ancestry,
archives and receipt order are preserved. Training/validation services never
evaluate concurrently; native listing widths stay independent of concurrency.
The shared-market experiment is not used. Existing checkpoints require an
audited compute-only source/contract migration; the launcher still rejects an
unmatched manifest or checkpoint. Host reserve/admission and GPU guards remain
enforced. Benchmark speedups apply to replay, not total generation time.

Shared replay also uses a bounded spawned CPU preparation pool (default four,
`V32_PREPARATION_WORKERS=1..8`). It packs the current two cohorts plus at most
one pair ahead per session while GPU replay proceeds. Workers validate the same
networks and return the same packed arrays; no RNG or financial arithmetic runs
in packing jobs. Each new local initialization is a whole CPU-process job that
returns the exact final RNG state, avoiding contention with the CUDA owner's
Python thread. Candidate sampling and calibration order remain unchanged.
Host reserve plus admission is checked before every submission. CUDA allocation,
compilation and graph capture remain on the single GPU owner; CPU workers never
create GPU contexts. Timings distinguish CPU packing, queue wait, upload, capture
and replay. Four workers improved warm packing of four evolved 512-candidate
cohorts from 2.20s serial to 1.64s on the laptop; eight gave 1.57s and were not
selected. Cold process startup and end-to-end generation time are separate costs.

Shared local reproduction now executes as one ordered transaction in the same
bounded CPU pool. The job receives and returns the exact RNG, pedigree and species
state; random draws and acceptance order are not parallelized. Descriptor work
inside that job is serial to avoid nested process pools. The caller adopts the
successor only after the job completes. Resident local metadata reads no longer
queue behind GPU replay; only a single day's certified identity/listing metadata
is retained by each proxy, while tensor ownership remains on the CUDA thread.
Restoring a candidate validates each network once, retaining every original
validation rule. New V32 machine artifacts use compact standard JSON with the
same atomic replacement and strict finite-number checks; existing sealed files
are never reformatted. These are compute/publication changes, not changes to
search, objectives, execution arithmetic or checkpoint state semantics.

Shared replay parks sessions on bounded CPU result-processing/publication futures. Device export remains on the CUDA owner; CPU snapshots are independently owned. Receipt writes must finish successfully before local scoring/evolution advances; failures propagate and shutdown drains pending publication. Other sessions may replay while these CPU jobs run.

Independent session scheduling uses completion events and bounded 16-block replay quanta (512 causal clocks). Ready session streams advance independently; capture/export and new device preparation require all currently queued quanta to finish. Packing futures yield to the scheduler. CPU completion and subsequent evolution no longer wait for another session's entire replay. Candidate counts, per-candidate clock order, arithmetic, RNG and memory guards remain unchanged.

Reproduction uses a bounded per-job cache of successful validation for immutable Network/Neuron tuples. New or changed networks retain the same full validation; mutable containers are never cached. The cache is discarded at the end of reproduction and does not consume RNG.

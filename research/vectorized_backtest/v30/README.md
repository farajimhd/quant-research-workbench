# V30: fresh global recurrent-network evolution

V30 evaluates a fresh population on all 24 certified training sessions in one
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

Use `python -B -m research.vectorized_backtest.v30.run_train` with
`PYTHONDONTWRITEBYTECODE=1`, `--authorize-run`, pinned `--training`, `--validation`,
`--output` under D:/TradingML/runtimes, and a qualified `--commit`. Defaults are
population1024, seed2020 and 100 generations. Batch size, worker count and capture
memory must be measured on the target GPU. Workstation defaults preserve
128 GiB host reserve plus32 GiB load admission and no prefetch. Source changes
must be committed/pushed before immutable workstation deployment.

This reuses Phase2 session lanes, chronological replay, result caching, receipts,
training/validation seals, global checkpoint/resume and the existing terminal
canvas. The single visible observer is `v30.observe --output <campaign>/global`.
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
tolerances. A measured CUDA tile is set by V30_NEURAL_BLOCK (32/64/128/256),
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

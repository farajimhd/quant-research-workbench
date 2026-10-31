# V60: market-driven feed-forward evolution

## Market-only revision (v4)

The latest user instruction supersedes the position-input design below. Every
action head now consumes causal market data only. All position/age inputs and
age gates are forbidden by construction. Current-market routing replaces
sticky position ownership. Identical market-only action graphs can share
inference, including management heads; genealogy does not affect computation.

Inference produces bounded time-batched signal packets on a separate CUDA
stream. Execution consumes packets chronologically, still using quote sizes,
candidate limits, partial fills, costs and position accounting. Execution never
feeds results back into inference; replay results reach evolutionary selection
through the unchanged objective. Two signal buffers bound memory/backpressure.
Workstation profiling selected GPU execution: the existing vectorized CPU path
was substantially slower in the bounded execution-only probe. The producer uses
up to 1 GiB (also bounded by one eighth of currently free GPU memory), enabling
four-clock batches in the measured workload. Candidate/node limits are unchanged.

### Compact workload design, 2026-10-31

- The construction authority exposes 834 allowed base features. Runtime projection
  now physically removes the 123 forbidden catalog columns: 2,502 market channels
  instead of 2,871. Saved genomes retain canonical IDs; packing and calibration
  remap them. Crossing nodes receive only their referenced lag features (36 in
  the measured cohort), reusing prior rows within each time batch.
- A temporary reduction scratch preserves the previous floating-point summation
  order. Forbidden source values are never fetched into it, and it is not a model
  input. Removing this scratch requires exact threshold/decision parity, because
  a mathematically equivalent reduction changed some float32 results.
- Entry inference uses the 10 eligible listing slots directly. Management retains
  contiguous, full-listing evaluation. Two causal management-mask experiments
  preserved results but regressed whole-session performance and were removed.
  No holdings or fills are fed back into inference. Normalization retains the
  full market cross-section.
- Replay packing balances estimated edges plus nodes, preserving all identities,
  fixed population sizes and memory bounds. On 6,144 founders, six old batch costs
  ranged from 45,179 to 1,144,879; new costs are 592,864–592,866 each. Planning took
  0.064–0.067 seconds. These are workload proxies, not measured replay speed ratios.
- Whole-network composition shares numerically identical parent branches rather
  than duplicating their nodes. Threshold/sizing bridges remain distinct. A
  64-node identical-branch fixture produces 70 rather than 134 child nodes, with
  exact Boolean/sizing outputs for OR/AND/XOR/XNOR; 100 constructions took about
  0.045 instead of 0.085 seconds. This changes the future genome representation,
  not the current candidate's behavior. Unreachable nodes remain prohibited.
- Node limits, financial cost, execution rules, mutation allocation and population
  sizes are unchanged. Graph scheduling uses edge cost rather than node count.

The compact projection and shared-branch changes passed exact complete-session
financial parity. A packed management-index experiment also preserved financial
results but regressed replay to about 75 seconds; a contiguous mask took
62.18/60.87 seconds. Both were removed. Retained runtime sources are byte-for-byte
identical to the measured `f928ddd71` deployment.

Retained whole-session result, same pinned 1,024 candidates and two concurrent
workstation lanes:

| Measurement | Prior `bc037a06e` | Retained compact design `f928ddd71` |
|---|---:|---:|
| Aug 13 replay | 62.32 s | 59.59 s |
| Aug 18 replay | 61.03 s | 58.35 s |
| Both sessions returned, including loading | 80.19 s | 77.30 s |
| Reproduction, 1,024 successors | 7.37 s | 7.44 s |
| Final publication blocking wait | 0.015 s | 0.016 s |

Mean replay improved 4.4%; total two-session return improved 3.6%. This is a
single bounded comparison, not a confidence interval. Full financial evidence
(including order-content hashes) matches exactly on both sessions; there were
zero terminal-invalid candidates or re-entry violations. Forty-three retained
focused tests passed. Construction tests demonstrate exact shared-branch outputs;
future evolutionary trajectories are not claimed identical after graph compaction.
The <45-second target remains unmet, and the full campaign remains stopped.

A separate instrumented 512-clock sample measured graph traversal at 34.7–36.4%
and fills at 26.3–27.3% of summed GPU kernel time. Those overlapping kernel times
are not wall-time percentages. The remaining cost is GPU traversal/fills and
projection/buffer operations; publication is not the blocking bottleneck.

Workstation evidence under
`D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v60/compact-f928ddd71/`:
`profile/batch-1024-0-1024-mixed/profile.json`, `construction-profile.json`, and
`diagnostic/batch-1024-0-1024-mixed/` (instrumented; not timing acceptance).
Rejected experiments remain under `compact-2d3f2fda8` and `compact-96ef712dc`.
No full 6,144-founder replay, 24/12 campaign, or maximum-size evolved-network
qualification was performed in this bounded comparison.

### Earlier market-only qualification, 2026-10-31

Same 1,024 graphs (512 templates + 512 random founders), Aug 13 and Aug 18,
two concurrent workstation sessions; no outcome-based cohort selection:

| Path | Aug 13 replay | Aug 18 replay | Both sessions returned, including loading |
|---|---:|---:|---:|
| Previous sequential engine, same market-only graphs | 59.60 s | 58.32 s | 76.58 s |
| First market-only pipeline | 70.62 s | 68.59 s | reproduction failed on stale age-mutation weight |
| Final four-clock pipeline (`bc037a06e`) | 62.32 s | 61.03 s | 80.19 s |

The final pipeline is 11.4% faster than the first pipeline by mean replay time,
but remains 4.6% slower than the previous sequential engine. **The <45-second
target is not met. No full V60 campaign was started.** These are bounded
two-session measurements, not qualification of all 24/12 sessions or evolved
maximum-size networks. The initial six-times cohort was constructed; only the
specified 1,024-member subset was replayed for this comparison.

Exact financial arrays and order-content hashes match on both complete sessions.
Reproduction now produces 1,024 successors in 7.37 s; final publication wait is
0.015 s. Removing the interprocess GPU gate worsened replay to 76.24–76.73 s,
so it remains enabled. A bounded trace attributed about 34% of sampled GPU
kernel time to neural traversal and 23% to fills; projection/buffer work remains
a material optimization area. Kernel-time sums are not additive wall-time
percentages when streams overlap.

Validation: 31 construction/execution/GPU tests plus four observer tests passed,
including reserved-input rejection, history-independent routing, exact scalar/
batched projection, forced journal retry and sell fills bounded by holdings and
quote size. The removed age field's stale mutation weight was fixed, and the
real reproduction path passed. Numerical graph duplicates no longer launch
redundant CUDA programs; obsolete position-projection kernels were removed.

Evidence on workstation:
`D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v60/market-only-bc037a06e/profile/batch-1024-0-1024-mixed/profile.json`.
Earlier timings below describe the preceding v3 qualification.

Old V60 genomes have a different version and cannot be silently resumed as v4.
The sequential market-only diagnostic path is selectable with
`V60_INFERENCE_PIPELINE=0` for controlled parity/performance comparisons.

Implementation in progress. This package is **not yet qualified for a campaign**.
The governing specification is `docs/trading/v59-strategy-execution-audit.md`.
V59 was gracefully stopped before implementation; existing receipts are retained.

## Preserved experiment

Global-only, 24 training / 12 display-only validation sessions, 6,144 fresh
founders, 1,024 successors, 200 generations, two workstation replay lanes.
Total/per-action node budgets remain 1,024. Parenting retains 25% ticker
specialists and 75% global ranks. Validation does not affect evolution.

The objective remains half the ticker-date logarithmic reward plus half the
session logarithmic reward, minus account drawdown. Targets remain $2,000 and
$5,000 respectively, with logarithmic coefficient 0.1.

## Construction authority

`feature_registry.json` pins all 957 source IDs, their permitted status,
construction bounds, basis, history dependencies and readiness conditions.
`authority.py` exposes only the 834 permitted inputs to constructors. Raw market
values are not clipped to construction bounds. Engineering caps still require
the training-only coverage qualification before a campaign can start.

The registry restores price-based indicators, EMAs, ATRs and bid/ask/spread.
V7 levels and the agreed explicit market-time/date-event features are excluded.
Actual position state remains available without recurrent edges. Order feedback
and general elapsed-clock inputs are excluded. Reserved storage slots are zeroed
and cannot be referenced by a valid genome.

## Minimal execution decisions

The deterministic controller owns a standing remainder until filled or superseded
by an exit, protective stop, or terminal liquidation. Repeated buy signals do not
increase an outstanding order. Exit takes precedence over a pending buy. An
unfilled initial buy whose stop is no longer below its executable ask is cancelled
by strategy admission with an explicit reason. No arbitrary replacement stop is
inserted. Re-entry waits three seconds after the final exit fill.

Candidate-defined buy/sell limits use the current causal closing quote as their
reference. Stop and target prices use candidate parameters and causal history.
Protective exits use the candidate sell-limit rule; terminal liquidation uses a
deterministic marketable closing-quote limit. Targets retain the candidate's
reduction fraction. Partial fills are reported through actual position state.
Only subsequent candle closes may fill a remainder; liquidity from one quote
snapshot is consumed once. Missing quotes do not fabricate fills or remove a
candidate from fitness. An incomplete terminal ledger is an explicit data failure.

## Acceptance gates still required

- Source is self-contained and isolated workstation replay has been exercised.
  No prior-version runtime imports are allowed. Certified market artifacts retain
  their immutable data-producer identities.
- CPU/GPU execution, current membership, feasible routing, construction,
  calibration boundaries, score, observer input and full-result retention tests
  are implemented. Captured replay and forced journal overflow match eager replay.
- The audit's V60 implementation register distinguishes fixes from preserved
  policy decisions. Two full training replays, one validation replay, an actual
  successor/checkpoint/resume boundary and rendered panel drill-downs passed.
  Full 24/12-session replay coverage remains unqualified.
- Profile real 1,024-candidate batches, two concurrent sessions, reproduction,
  publication, loading and full generations on workstation. Target <45 seconds
  per batch without changed network budgets or omitted work.

Latest workstation comparison: concurrent Aug13/Aug18 replay 67.17/66.82 ->
63.91/64.42 seconds, two-session return 88.24 -> 82.53 seconds, with exact
financial arrays and order-content hashes. Reproduction is about 7 seconds and
overlaps validation; final publication wait is 0.016 seconds. **The 45-second
target is not met.** The two-training/one-validation qualification generation
took 139.07 seconds including publication and checkpoint, not a full campaign
generation. See the audit implementation register for artifact identities and
remaining gates. No full V60 optimization has been launched.

No V60 campaign or timing claim is authorized by this README alone.

Flat exit veto is removed: management heads apply only to held positions. Entry
calibration uses a V60-owned training probe of current stop/book feasibility.
Admission counters are part of the receipt and must reconcile before publication.
The panel retains the V59 paths and adds these counters to candidate details.

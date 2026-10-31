# V59 strategy, execution and optimization audit

Audit date: 2026-10-31. Source baseline: `99fb181db501dbd3ed8c871be7f85ccb2f465b3a`.

Design revision: 2026-10-31, following the user's clarification after the audit. The minimal close-time execution design below supersedes the earlier proposal to make candidates manage pending orders. Existing-code findings remain pinned to the source baseline; proposed interfaces are not deployed.

## V60 implementation register — 2026-10-31

**Subsequent market-only revision implemented; performance acceptance open:** the user explicitly removed all
position feedback. This supersedes retained-position-input, held-head gating,
age-gate and sticky-routing decisions below. V4 constructors forbid all position
inputs and age gates; routing uses current market signals only. Quote-size fills,
partial fills and execution accounting remain deterministic and affect only
fitness at reproduction, never neural inference. The cost function is unchanged.
Time-batched inference and chronological execution use bounded separate CUDA
streams. The recorded v3 benchmarks below remain the comparison baseline;
they are not evidence of v4 performance acceptance.

Latest compact-workload revision (`f928ddd71`, retained after rejecting two slower
management-mask experiments): allowed model inputs shrink from 957 to 834 base
features; lag projection is limited to referenced features; entry inference uses
compact eligible slots; graph/batch scheduling uses estimated edge work; identical
whole-crossover branches share nodes. Caps and the cost/execution contracts are
unchanged. The six founder batches have nearly equal estimated work, planned in
0.066 seconds; this is not a measured six-batch throughput claim.

Two complete concurrent 1,024-candidate replays improved to 59.59/58.35 seconds
(4.4% mean improvement); total return including loading improved 80.19 to 77.30
seconds. Exact full financial evidence and order hashes match. Reproduction took
7.44 seconds; final publication wait was 0.016 seconds. Forty-three retained
focused tests passed. The 45-second target remains unmet. No full campaign was
started; see the README's compact-workload section for detailed measurements,
rejected experiments, artifact paths and remaining qualification boundaries.

Earlier V4 bounded workstation result (`bc037a06e`): 62.32/61.03 seconds for two
concurrent 1,024-candidate sessions; 80.19 seconds until both sessions return,
including loading. The identical cohort on the previous sequential engine took
59.60/58.32 seconds. Both full financial arrays and order-content hashes match.
Reproduction completed in 7.37 seconds with 1,024 successors. Thirty-five focused
tests passed, including forced journal rollback and sell fills limited by actual
holdings and displayed size. The first pipeline's 70.62/68.59 seconds improved,
but the 45-second target and comparison against the old engine remain unmet.
No full campaign was launched; qualification workers exited. See the V60 README
for benchmark scope, source revision and artifact paths.

The original V59 audit below is retained as historical evidence. Implementation
is now in `research/vectorized_backtest/v60`; V59 has been stopped with receipts
preserved. V60 is self-contained and is undergoing workstation qualification;
**it is not yet accepted for a full campaign or the 45-second target**.

| Finding | V60 disposition and remaining qualification |
|---|---|
| F01 | Entry swings use current membership. Boundary fixture passed. |
| F02 | V4 routes on current market signals and book-feasible entry branches. Sticky position ownership is removed; routing never reads fills or holdings. Routed CPU/GPU and batched/scalar fixtures passed. |
| F03 | Candidate buy/sell limit parameters, initial stop and target; same-close matching against the current displayed book. CPU/GPU limit and fill fixtures passed. |
| F04 | Construction authority owns permitted inputs, bounds, history and dependencies. Strategy-side admission precedes a separate liquidity fill primitive; no age/risk admission caps. Admission reasons are published. |
| F05 | Explicitly retain certified consecutive-second history semantics and readiness masks; do not fill gaps or reinterpret them as observed-bar counts. Unsupported history parameters cannot be constructed. Missing current history abstains, rather than inventing a stop. |
| F06 | Deterministic controller retains standing size, ignores repeated buy requests, and lets held-position exit/protective events supersede buys. No candidate keep/amend/cancel protocol or previous-action inputs. |
| F07 | Model bid/ask/spread use the same causal book as execution. V60 builds its own training probe; old age-gated activation probes cannot initialize a campaign. |
| F08 | Terminal liquidation remains deterministic and size-limited. Incomplete coverage is a replay failure, never a candidate-level fitness exclusion. |
| F09 | Fused device counters reconcile entry signals = accepted requests + disjoint abstentions, with separate limit/liquidity waits and first fills. Counters survive capture/overflow rollback and reach candidate details. Full-session reconciliation is an acceptance check. |
| F10 | Stop fraction 1 is invalid. Entry calibration masks use the sampled stop/limit policy and current book. A valid strategy with no opportunities in the bounded probe is reported, not discarded based on profitability. Sizing activity remains observable through replay counters; calibration is not a full financial replay. |
| F11 | V4 evaluates every management head from market inputs regardless of holdings. Execution alone ignores flat-position management signals; it cannot veto inference. Position inputs, age gates and prior-action feedback are forbidden by construction. Ordinary input validity and crossing/composition masks are preserved. |
| F12 | Forbidden delay edits removed; legal six-way structural schedule is explicit. Exhausted parent/post-construction mutations receive fresh replacements. Full unique-population authority checks every output. Post-construction mutation remains on fresh proposals; parent-mutation fallbacks are separate. Intended and realized routes remain reported. |
| F13 | Preserve zero fitness for no trades, but require a closed position somewhere in training for parenting. Expose parent eligibility separately from score rank; no positive-score gate. |
| F14 | Preserve the 25% ticker-specialist / 75% global pathway and its explicit specialist reward-only objective. Account DD remains in global score; no silent substitution of summed ticker DD. |
| F15 | Pin DD weight 1, extra-loss weight 0, ticker target 2000, session target 5000, alpha .1. Score/ledger fixtures cover losing and zero-trade days. Validation remains display-only. |
| F16 | Profile real two-session replay, reproduction and publication separately. Retain GPU turn lock after ungated comparison failed to improve throughput materially. Paired journal append is fused. Target qualification still open. |
| F17 | Capture/overflow checkpoints include ratios, clock, feedback, branch state and admission counters. Eager/captured/padded/forced-overflow GPU replay fixture matches exact financial and event outputs. |
| F18 | CPU/GPU fill/accounting oracle, limits, partial fills, terminal orders and re-entry fixtures passed. Representative full-session qualification is required after the latest changes. This is displayed-liquidity simulation, not queue/impact modelling. |
| F19 | Pinned 834 allowed / 123 forbidden features; prices, EMA/ATR and quotes restored. Construction thresholds are clamped; raw observations are never clipped. Training-only probe publishes per-feature bounds coverage. Engineering caps are not claims about physical maxima. |

Initial workstation baseline (1,024 fixed diagnostic genomes, Aug13/Aug18):
68.45/69.73 seconds per concurrent replay; two-session return 89.44 seconds.
An ungated comparison produced 67.23/68.77 seconds with exact financial and
order-content parity, but had less compiler warmup, so this is **not** an accepted
speedup. Instrumented publication final wait was 0.015 seconds. The earlier
22-second post-replay interval included reproduction and must not be described
as publication blocking. These measurements predate the held-only action change.

An isolated replay exposed an optional-buffer compiler error at terminal drain.
It failed closed; an empty diagnostic tensor replaces the optional buffer, with
compiled regression coverage. Subsequent full-session replays succeeded.

Latest bounded workstation evidence (not a full campaign):

- `qualification-2f920f844/profile/batch-1024-0-1024-mixed/profile.json`:
  Aug13/Aug18, 1,024 unchanged diagnostic V60 genomes, **63.91/64.42 seconds**
  per concurrent session; both returned in **82.53 seconds**. Against the same
  held-only/admission baseline `qualification-67722fdc7`, 67.17/66.82 seconds
  and 88.24 seconds total: 6.5% lower two-session return time. All financial
  arrays and candidate order-content hashes match exactly. This is one bounded
  pair, not a statistical performance guarantee. The <45-second target failed.
- Reproduction 7.02 seconds; final publication wait 0.016 seconds. The original
  16-clock/21-listing calibration work budget is retained. An accidental larger
  qualification probe was corrected, not presented as an algorithmic speedup.
- `qualification-d1171ce4f/profile/pipeline-profile.json`: real controller,
  two training sessions plus one validation session, 1,024 main candidates and
  32 isolated paired mutation diagnostics. Reproduction 6.95 seconds completed
  inside validation; validation load was prefetched (0.14 seconds). Full bounded
  generation including checkpoint/publication: 139.07 seconds. Actual saved-state
  resume passed, stopping before generation 2 dispatch. This is not a 36-session
  generation timing.
- Full replays reported zero incomplete terminal positions and zero re-entry
  violations. Admission counts reconciled. CPU/GPU, capture/padding, journal
  overflow/retry and financial/event parity checks passed.
- Fresh initialization under the current V60 training probe produced all 6,144
  unique founders in 104.38 seconds, with validation unopened and no financial
  replay/result reuse (`qualification-c31af8d73/sampling-profile.json`). This is
  startup construction time, not a replay timing. The final focused suite has
  25 passing tests.
- The copied panel loads both rankings, full three-session candidate details,
  crossover groups and sortable position records from the qualification. The
  position reader's old mode whitelist rejected V60 terminal clocks; this is
  fixed with a regression test. Ten panel paths were rendered; session and
  position screens were visually inspected. Observer I/O remains isolated.

Artifacts above are under the workstation runtime root
`D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v60/`.
GPU traces identify the execution/accounting and ragged network kernels as the
remaining large costs. Two replay lanes also wait on their GPU turn lock;
removing it did not improve total throughput materially. This is distinct from
publication or reproduction blocking. Unreachable-node analysis found no dead
nodes in the sampled 1,024 graphs, so no pruning change was made. Thread-layout
tuning that changed floating-point totals was rejected. No budgets, candidate
counts or financial work were dropped to achieve these timings.

Full 24/12 replay coverage, broader construction-bound coverage and the
<45-second performance acceptance remain open. No full V60 optimization was
launched; V59 remains stopped.

## Outcome and scope

The current implementation does not yet satisfy the agreed strategy/execution contract. The principal problems are incomplete strategy visibility of admission conditions, a stale membership clock in swing projection, branch routing before feasibility, and the absence of limit orders. These are different from legitimate insufficient-liquidity waits. Moving existing checks without reviewing their semantics would preserve the problem.

This is an audit and proposed revision specification, **not an implemented fix**. No optimizer code, inputs, settings, checkpoints or running processes were changed. All six workstreams below were reviewed. Verification comprised source tracing, the current workstation job contract, existing focused tests and bounded CPU probes. No GPU replay, full-generation benchmark, new validation evaluation or exhaustive candidate audit was run during this audit. Remaining acceptance work is explicit below; passing existing tests is not proof that the new contract is implemented.

Runtime authority inspected: workstation `D:\TradingML\runtimes\vectorized_backtest\torch_backtest_v59\global-dust-99fb181db-g200`; source copy `D:\TradingML\codes\quant-research-workbench-v59-99fb181db`. Job source digest: `afa7c3022f7bdab6ee24476872cfc5dd02ff372d65fa9d1ba16a3a3e3bc2f56b`. This is a global-only 24-training/12-display-validation, 200-generation campaign: 6,144 initial founders, 1,024 successors, two replay lanes. Base batch size is 512; adaptive packing can use 1,024. A batch is not a whole founder session.

Twelve core local/deployed source files were compared: network, network_cuda, orders, order_transition, order_liquidity, routing_plan, swing_projection, lineage, robust_score, calibration, order_replay and config. All matched after CRLF/LF normalization. Raw byte hashes differ for eleven due to line endings; no byte-identical deployment claim is made.

The TASK-0223/V50 handoff is historical and is not authority for the active V59 contract. The latest user instructions and the pinned V59 job take precedence.

## Agreed contract

1. Sampling and reproduction construct supported, structurally coherent strategies. They may not silently shrink networks, change the objective, discard valid founders, or screen on validation results.
2. A feed-forward strategy evaluates completed-candle inputs and current position state at the exact candle-close boundary. It defines entry, exit, add and reduce requests, their sizing and limit prices, and stop-loss and profit-target prices. It emits an admissible request or an explicit abstention. No recurrent network connections are needed for position inputs maintained by the simulator.
3. Execution receives accepted order intents. It does not independently apply strategy profitability, history, stop-distance, risk-budget or network conditions.
4. Execution is minimal and runs at that same candle-close boundary, using the valid quote and remaining displayed size available by that boundary. Buy limits execute only at ask <= limit, at ask; sell limits only at bid >= limit, at bid. No future quote, arbitrary price substitution, same-snapshot liquidity reuse or extra execution-risk screening. A candle's last-trade close and its closing bid/ask are distinct: the agreed exact close **time** does not guarantee a fill at the last-trade close **price**.
5. A deterministic controller owns outstanding orders, quantity conversion, partial fills, duplicate suppression, fees, cancellation/replacement and terminal handling. Candidates do not learn keep/cancel/amend protocols or partial-fill handling. Filled quantity and weighted entry cost update their position inputs at the next candle evaluation. The proposed standing remainder is checked at subsequent candle closes, never retroactively; its lifetime and deterministic supersession rules must be fixed and tested before implementation, rather than evolved or silently inferred.
6. Initial stops must be positive and below the intended entry. Trailing stops may subsequently exceed entry. Re-entry waits three seconds after final liquidation. The $300 risk and 20% distance selection/execution caps remain disabled.
7. Malformed/nonfinite orders are explicit contract errors. Missing valid quotes and exhausted liquidity are pending states, not fabricated fills or fitness exclusions. Terminal incompleteness is a data/execution-completion issue, not silent candidate removal.
8. Causal data and order-event ordering must be documented. Same-candle execution must not use information from after the decision boundary or reuse earlier consumed liquidity.
9. V60 must have one construction authority used by every creation path. Constructors request permitted feature/period/output handles from that authority, never propose arbitrary catalog IDs and rely on repeated downstream rejection. Repair numerical proposals within their declared semantics before validation; expose every repair. Validation remains mandatory for external, restored or corrupt inputs.
10. Rejected or duplicate construction proposals never reduce the requested population: replace each before replay. A population is either complete and validated or is not submitted. Full means6,144 accepted unique founders and1,024 accepted unique successors under the current campaign dimensions, not6,144 proposals with some removed. Never fill slots with duplicate real candidates, silently smaller networks, copied financial results or counted GPU padding.

## V60 final implementation plan — self-contained refactor

Status: agreed implementation plan only. This documentation update does not authorize or perform a run restart or source refactor. The V59 findings above/below remain evidence about the pinned baseline. V60 will implement the agreed revisions without making its runtime depend on old strategy versions.

### 1. Establish a self-contained package and versioned contracts

Create `research/vectorized_backtest/v60/` with local feature schema/authority, causal feature/history preparation, genome and typed construction, reproduction/selection, network projection/CPU/GPU evaluators, strategy intent, minimal execution/position accounting, objective, replay scheduling/cache/checkpoint, launcher and progress UI modules. Copy only necessary reviewed code from previous versions; record source commit/path, changed semantics and accompanying tests. Copying is a starting point, not evidence of correctness. Remove dead compatibility branches and stale historical launch assumptions.

No runtime imports, dynamic imports, subprocess module calls or path-based source loading from `research.vectorized_backtest.v1..v59`, or experimental `research.rl_trading.v*`. Move required mathematical/catalog/feature functionality into V60 with its own version and tests. Standard dependencies and stable shared infrastructure (for example `research.mlops` environment/runtime utilities and canonical data services) may remain explicit dependencies; list them. Do not duplicate or bypass the repository's canonical historical-data authority. Input manifests/data may be reused only when exact semantics/provenance match; a V18-named artifact path alone does not make the V60 executable depend on V18 code.

Require a clean deployment smoke test in which past research-version directories are unavailable. Static import checks alone do not prove independence; exercise launcher, preparation, reproduction, CPU replay and progress reads in that environment. Generated artifacts remain under the applicable TradingML runtime root.

### 2. Implement the single construction authority

Make one immutable, versioned registry authoritative for allowed feature IDs and channels, raw units, construction caps and their basis, transforms, valid periods/windows, history prerequisites, missing-data rules, state visibility, output-price constraints and structural budgets. Integrate rather than duplicate rules in templates/sampling/mutation/crossover/restoration/packing. Use precomputed permitted pools and compatible substitutions; avoid retry-until-allowed feature sampling.

Enforce the revised834 allowed /123 forbidden pinned-feature proposal, removing V7 and explicit market-time channels but not excluding an input merely because its unit is price/log_price. Keep current position state without recurrence. Replace V7 support-bounce templates with causal swings. Preserve causal pivot confirmation; do not silently change the unresolved swing missing-bar definition. Freeze a training-only coverage review of engineering caps/history availability before using them to generate the population. Any added swing-high family gets new IDs and verified preparation, not a relabeled old field.

Compile each candidate's dependency certificate and reject unsupported period/history requests before replay allocation. Prefer direct in-range generation for founders and fresh immigrants; clamp/repair permitted numerical mutation/crossover proposals. An invalid structural operation selects another compatible operation. Forbidden feature references are not valid proposal handles. Do not change a candidate during financial evaluation when history is absent; abstain with a recorded reason.

### 3. Guarantee complete accepted populations

Use a shared slot-filling builder for founders, fresh immigrants, crossover children, mutations and any compatibility migration explicitly approved later:

`allocate slot -> construct from permitted choices -> bounded semantic repair -> validate dependency/graph/output contracts -> canonical deduplication -> accept`, repeating replacement until the slot has an accepted candidate.

Rejection reasons include invalid external input, unrepaired constraints, duplicate genotype and proven dead construction. A valid rare or losing strategy is not automatically invalid. Proposal filtering is not retrospective replacement based on bad replay profit. Data failures or incomplete financial replays trigger recovery/failure diagnostics, not replacement of inconvenient candidates.

Replacement hierarchy: repair the proposal; try a new legal proposal through the intended operator; if the bounded operator attempt budget is exhausted, fill the slot from a valid fresh generator and explicitly report the operator-to-fresh substitution. Preserve total population and node limits. Record requested versus realized operator quotas so fallback is not hidden. Each slot/attempt receives its own deterministic RNG key; concurrency cannot change candidates or IDs. Allocate accepted serial IDs only after acceptance, while retaining separate proposal IDs for telemetry.

Before scheduling replay, assert exact accepted count, unique canonical identities, valid schemas and complete slot ownership. Batch planning must cover every accepted candidate exactly once (or reuse a specifically valid identical receipt), with no missing or duplicate indices. Physical GPU padding is masked and never a candidate or selection result. An unrecoverable inability to construct a legal unique population must fail explicitly with its saved build state; it must not launch a partial population, hang forever or weaken the bounds to claim success. Resume continues unfilled slots deterministically.

### 4. Implement candidate decisions and minimal close-time execution

Use feed-forward graphs with market/current-position inputs and candidate-defined action, size, limit, stop and target outputs. Express prices causally relative to a specified current reference and convert them to finite positive currency prices. Define compatible initial-stop/entry/target relationships before accepting a request. Keep the1024-node total/per-action envelope; no recurrent network edges.

Separate strategy feasibility/arbitration from execution. Repair the current-membership swing indexing and route among admissible branch requests, with deterministic ownership for pending/held positions. The executor runs at candle-close boundaries: buys at valid ask<=limit and sells at valid bid>=limit, capped by remaining displayed size/inventory, with exact fees and conservation. No hidden old risk caps, intrabar neural decisions or invented fills.

Keep partial fills and outstanding-order policy deterministic. Specify remainder lifetime, replacement precedence, target reduction/full-exit semantics and which candidate-defined sell limit governs protective exits before implementation. Retain the three-second re-entry wait and observable actual position quantity/weighted entry/P&L/age/stop/target. These contracts must be implemented during optimization wherever they affect realized results, rather than added only after evolution.

### 5. Rebuild reproducible evolution and objective wiring

Keep the agreed logarithmic objective (ticker2,000/session5,000,alpha.1;0.5 ticker+0.5 session−summed account DD), training/validation split and25% ticker-specialist /75% global parent-selection policy. Do not silently add a breadth penalty, positive-only filter or restored risk cap. Make active-parent eligibility and the specialist objective visible. Use the one authority on every reproduction route and preserve full populations under fallback.

Implement coherent routing/veto/block and existing approved crossovers with budget-aware parent/operator choices, canonical deduplication and reproducible lineage. Resolve/report scheduled versus realized mutation and sibling/fallback proportions. Keep validation strictly display-only, with no input to construction-cap tuning, reproduction or RNG decisions. Price/input/feature changes require new identities and fresh financial evaluation; no V59 score reuse under changed semantics. Do not assume old genomes can be restored under the new schema.

### 6. Add complete diagnostics and preserve the progress interface

Expose proposal, repaired, rejected, duplicate, replacement, accepted and outstanding-slot counts by operator, generation and reason. For each candidate/session distinguish raw proposals, strategy abstention, accepted intents, liquidity/limit waits, fills and positions. Reconcile totals; unknown/missing metrics are not zero. Group by technique, family and origin as before, with candidate/session/position drill-down and sorting.

Retain separate training/validation rankings, counterpart metrics, current-versus-complete coverage, per-session replay and generation bars, stable column widths, elapsed/average times and ETAs. The observer must use background reads and immutable published snapshots; no training replay or expensive synchronous work on a keypress. Copy/adapt the required progress code into V60 rather than importing its older implementation.

### 7. Verify the actual runnable path and optimize it

Test forbidden inputs across every channel/path; caps/endpoints; supported history; first admission; swing/branch readiness; filled population under forced rejection/duplicate/operator exhaustion; deterministic parallel/resumed construction; canonical identities; output-price constraints; no recurrence; quote/limit/size matching; partials/fees; terminal coverage; score reconciliation; validation isolation; eager/captured/overflow-retry state parity and UI snapshots. Include frozen representative real candidates/session data, not just mocks. Report deliberate financial differences separately from unchanged-case parity.

Use bounded concurrency, host prefetch, resident/precomputed market-only features, asynchronous publication and reproduction during validation. Profile full generation and total two-session throughput, including loading, capture, replay, terminal completion, reproduction, metrics and publication. Target roughly45 seconds per1,024-candidate session replay on workstation; measure initial and representative later/larger networks. No smaller cohort/network/history or altered financial behavior to meet timing, and no long qualification campaign. Report unmet targets honestly.

### 8. Deliver and deploy only after acceptance

Review task-owned diff, validate the isolated V60 package, commit/push on laptop, then deploy a hash-verified immutable workstation copy. Preserve existing run checkpoints/receipts and do not modify the active V59 deployment. A V60 launch must use a new runtime identity and the agreed full dimensions; transition/start is a separate authorized action after implementation and bounded verification. This plan update itself starts no run.

### Minimal design to implement (not current V59)

| Layer | Responsibility | Excluded responsibility |
|---|---|---|
| Candidate, feed-forward | Action, requested size, candidate-defined limit, stop and target prices; may update permitted position levels | No order queue, partial-fill management, cancellation/amendment policy or recurrent hidden state |
| Strategy boundary/controller | Check observable history/price feasibility, arbitrate conflicting actions/branches, enforce explicit cooldown and lifecycle rules, translate outputs into coherent intents | No invisible profitability/fitness filter or replacement stop invented after a fill |
| Minimal executor | At candle close: side/limit compatibility, positive finite valid quote, available size, inventory conservation and fees; report fills | No intrabar neural replay, hidden $300/20% admission caps, network/history reevaluation or market-impact/queue model |
| Deterministic position/order ledger | Maintain actual quantity, weighted entry, realized/unrealized P&L, age, stop, target and unfilled remainder | No optimization over order-management protocols |

Price outputs should be represented as causal relative offsets/ratios and converted to positive finite currency prices by the controller. This output encoding does not forbid absolute market-price or price-valued indicator inputs: the revised V60 feature policy permits them. For the current long-only strategy, initial stop must be below the intended entry; target is a profit level above its entry basis. Define how the stop is checked against allowed fills when a buy receives price improvement; do not silently move the candidate's stop. Level updates and request/fill ordering require explicit tests. Target distances may legitimately exceed100%; old policy fraction bounds are not automatically the new output bounds.

The controller can deterministically turn a crossed stop/target into an exit/reduction request at a close; the next implementation must specify which candidate-defined sell limit applies. Stops must not be promised guaranteed fills when the available bid is below that limit. Decisions about remainder expiry, target reduction versus full exit and limit choice for protective exits are controller-contract work, not learned order-management actions.

Current position inputs are kept without recurrence. They are simulator observations, just like a current candle, not recurrent graph edges. A candle-only model cannot reconstruct its actual historical fills from the same market sequence alone. Preserve the market/position distinction and expose normalized position quantities clearly.

## Six-workstream coverage

| Workstream | Reviewed authority | Result |
|---|---|---|
| 1. Map signal to score | feature policy/projection, network kernels, routing, swing projection, evaluator, persistent order/financial kernels, ledgers, objective | Current path and boundary owners mapped below; confirmed mismatches F01–F08 |
| 2. Rejection accounting | aggregate/order/episode exports, replay receipts, sampling and reproduction telemetry, session progress | Fill/cancellation and crossover telemetry exist; no reconciled per-candidate intent/abstention reason ledger (F09) |
| 3. Explicit feasibility and partial fills | current state vector, request controller, fill primitive, tail | Partial-order management exists, but state/interface and limit support are incomplete (F03–F08) |
| 4. Sampling and reproduction | founder templates/random calibration, policy sampling/mutation, routing/mask/whole crossover, parent selection, deduplication | Structural bounds enforced; calibration does not measure admissible requests; routing and mutation/parent scheduling deserve correction/clarification (F02, F10–F12) |
| 5. Selection and score | global/local objective, active/terminal masks, ticker specialists, training-only reproduction, receipt identity | Main objective matches current job; extra parent restrictions and specialist objective differ and must be visible (F13–F15) |
| 6. Bounded validation/performance | focused tests, synthetic CPU probes, capture/rollback, publication/prefetch, cache identity | 40 passed/3 skipped initially; final test totals below. CPU probes reproduce membership/routing/history issues. GPU parity and throughput acceptance remain open (F16–F18) |

Code paths below are relative to `research/vectorized_backtest/` unless qualified. Proposed behavior is not current behavior.

## Actual runnable path

`global_runner/full_controller -> generation_flow -> session_pipeline/session_lanes -> evaluator.evaluate -> NetworkPlan or RoutingPlan -> state_signals -> order_replay.financial_tick -> orders.transition (CPU) / order_transition.order_fill_kernel (CUDA) -> order_liquidity -> accounting/episodes/order records -> financial/ticker ledgers -> full_common.aggregate -> robust_score.combine -> lineage.next_population`.

The active job uses `strategy-managed-quote-orders-v3`, not `evaluator.step`'s older immediate-fill path. Both implementations remain in the tree; tests/benchmarks must name which path they exercised. `risk_protection=true` remains in job metadata, but persistent execution requires `risk_limits_enabled=false` and does not enforce the old $300/20% admission caps. Raw numeric configuration values alone are not evidence those caps are active.

## Strategy input contract

### Market feature channels

The pinned catalog contains **957 base feature IDs**, of which **641 are allowed** and **316 blocked**. There are three model channels per base ID (2,871 market slots), followed by 11 state slots. Blocked IDs are rejected in all three channels; they can still exist in execution data. The appendix enumerates every ID, name, unit, declared range and searchable status.

For catalog index `i`, with `C=957`:

| Slot | Meaning | Transformation and boundary |
|---|---|---|
| `i` | Absolute feature | Ratio/bool: `tanh(x)`; other units: `tanh(sign(x)*log1p(abs(x))/4)`. Missing/nonfinite raw input becomes value 0 with validity false. Normally bounded [-1,1], allowing numerical saturation at endpoints. |
| `C+i` | Current cross-sectional relative feature | `tanh((x-current_valid_mean)/(2*max(current_valid_std,1e-6)))`; normalization uses available listings in the resident universe, not just the ten active members or the replay candidate batch. No future-session normalization. |
| `2*C+i` | Validity | 0/1. It is a separate explicit input, but does **not** independently certify an action as market-value-dependent. |

Catalog `lower/upper` values are legacy sampling-prior metadata, **not runtime clipping or guaranteed empirical/mathematical bounds**. The appendix labels have been corrected accordingly. Missing is not the same as zero. Generic nodes OR valid nonzero-weight market ancestors; they do not require every referenced input to exist. Thus a partially missing graph can still fire. Templates use explicit validity predicates; arbitrary random/mutated graphs need not do so.

In current V59, raw/log price units and split-age/event features are blocked. The blanket price-unit exclusion is explicitly removed in the revised V60 proposal; current V59 counts above are not the V60 allowlist. `log_float_shares` and `log_shares_outstanding` are transformed to decade buckets before absolute/relative projection. Other allowed channels can still be proxies for liquidity, regime, time or identity; blocking names is not proof against overfitting.

Source families:

| Family | Definition/availability authority | Relevant boundary |
|---|---|---|
| Certified base bars, relative indicators, time-of-day, V7 levels, reference context | `v6/torch_backtest/feature_bank.py`; prepared `features.npy` and `feature_valid.npy` | Source validity mask, finite values, causal feature-row mapping. No guarantee of all inputs at every ticker-clock. |
| History recent high, lagged close, attention/notional, average move, swing pivots | `v42/certified_history_bank.py`; `v7/features.py`; `v27/resident_features.py` | Elapsed-clock windows; missing observed bars invalidate windows. Swing confirmation uses left+right+1 finite observations on consecutive seconds and only prior-clock confirmed state. History carries previous confirmed pivots. |
| History relative-to-close | Same history plus current mapped mark | Positive valid mark; ratio generally `history/mark-1`; average-move fraction corrected to `history/mark`. |
| Continuous EMA/RSI/ATR and MACD variants | `v42/features.py` SPECS and certified indicator bank | Periods and units listed in appendix; finite indicator value and valid source/feature row. |
| Enriched returns, candles, activity, liquidity and cross-sectional context | `v42/enriched_math.py` and sealed enriched bank | Bank-defined causal lookback and finite value. Catalog range does not replace that formula's validity requirements. |
| Market bid/ask/spread-relative features | `v7/resident_features.py` original feature quote data | Model-side quote mask still requires age <=1s for price/spread quote channels. Active execution uses a different certified quote-size authority without this age expiry (F07). |
| Quote age / log execution activity | Same source | Quote age needs positive causal timestamp; volume/notional/count are zero on noncurrent mapped trade rows. Do not confuse zero activity with missing quotes. |

No absolute session timestamp or ticker identifier is added as a direct state slot. Time-of-day and available market/reference proxies remain. This is a catalog/interface statement, not proof that every candidate generalizes.

### Current model state inputs

All state values below are converted to float32 and passed through `tanh`. They reflect state **before** the current tick's execution; resulting partial fills are visible at the next strategy evaluation. Entry sampling uses market channels only; validation forbids its first seven position-state slots but permits the remaining four. Non-entry sampling can choose slots 0–6 and 8–10. Structural connection mutation can reach permitted slots including elapsed time.

| Offset after 2,871 | Name | Actual pre-tanh definition / boundary |
|---:|---|---|
| 0 | held | 1 when quantity >1e-12; otherwise 0 |
| 1 | profit_fraction | mark/average_cost−1 while held and mark valid; otherwise 0 |
| 2 | age_hours | (current relative replay clock−first-fill clock)/3600 while held |
| 3 | capital_entries | quantity*current mark/1000 while held; despite the name this is marked exposure, not original entry capital |
| 4 | stop_distance | (average_cost−current_stop)/average_cost while held; may be negative after profitable trailing |
| 5 | adds_fraction | recorded add/amend count /20 |
| 6 | management_age | (clock−last-management clock)/300 while held; flat post-exit cooldown is not directly exposed |
| 7 | elapsed_seconds | preceding evaluation interval /60; name is scaled, not raw seconds |
| 8 | pending_order_kind | enum/4: none=0, entry=1, add=2, reduce=3, exit=4 before division |
| 9 | pending_remaining_capital | remaining quantity*current mark/1000 when an order is pending |
| 10 | pending_fill_fraction | filled/requested quantity, denominator >=1e-12, while pending |

The state vector does not explicitly expose current bid/ask size, selected initial-stop availability/value, membership eligibility, flat cooldown remaining, exact filled/remaining quantity, order limit, order-specific average fill, order age, terminal countdown or abstention reason. Some quantities can be approximately inferred from transformed position variables, but that is not a complete execution interface.

Revised required inputs: keep causal market and current position observations, including held quantity, weighted entry basis, unrealized profit, age, current stop and target, plus observable strategy-boundary availability such as selected history/stop validity, action eligibility and cooldown. Separate normalized model observations from exact controller fields. Exact pending side/quantity/limit, per-order partial fills, expiry, cancellation and amendment state belong to the deterministic controller; **they are no longer required learned candidate inputs**. Current V59 pending-state slots in the table above describe existing code, not the requested future interface. Removing or repacking those slots requires a new genome/input version and fresh evaluation.

### Strategy genes and restrictions

| Gene/output | Current bound | Restriction owner / audit note |
|---|---|---|
| Five heads | entry, exit, add, reduce, trail | No separate sixth stop head: initial stop is policy; trailing uses head+policy. Document actual V59 interface rather than historical V54 architecture. |
| Graphs | total <=1024 nodes including routed branches; per action <=1024; depth <=512; fan-in <=16 | Structural validation and packing; never truncate to satisfy budget. |
| Weights/bias | finite, abs <=8 | Graph validation; operator output need not remain [-1,1] for identity/linear bridges. |
| Head threshold | strictly between −1 and 1 | Head fires when valid and output > threshold. Validity/threshold are separate. |
| Sizing | `(tanh(size_output*weight+bias)+1)/2` | Entry <=$1,000; add <=$200; reduction fraction <=1. No automatic positive minimum dollars in current job. |
| stop/target/trail/reduce fractions | strictly >0 and <=1 | Stop fraction=1 is structurally valid but produces zero initial stop and cannot enter in percent-stop mode (F10). |
| add/reduce minimum profit | [0,1] | Strategy policy thresholds, not cost-function restrictions. |
| maximum_adds | integer 0..20 | Applies to add/amend controller behavior. |
| cooldown | integer 1..300 seconds | Management/amendment; separate hard-coded three-second post-exit re-entry rule. |
| swing pair | both zero, or each in {1,2,3,4,5,10,15,20,30,45,60} | Sampling/validation verifies supported numbers, not observed-history availability. |
| minimum action ages | integer 0..3600 for recognized stages | Applied while held; bypassed while flat, including the exit head used to veto entry. |
| state recurrence | delayed edges forbidden | Crossing operator uses current/prior causal market values, not recurrent network memory. |

## Finding register and required revisions

Severity indicates correctness/contract priority, not a claim that each issue explains every inactive candidate. **Confirmed** means source and/or a bounded probe establish current behavior. **Review** means an intended-policy decision or additional measurement is needed.

### F01 — Confirmed: swing projection uses the wrong membership clock (high)

`v59/swing_projection.py:prepare` populates stop rows from `membership[:-1]`; the persistent `order_replay.financial_tick` uses current membership. CPU probe with membership `[false,true,true,false]` and a valid underlying stop returned projected IDs `[0,0,1,0]`: the first admitted candle loses its stop. Existing test covers persistent membership, not a membership transition.

Revision: bind stop preparation to the current decision contract, preserve prior-confirmation causality inside history, and test first admission, removal/re-admission, clock zero and terminal. This is not authorization to use future pivot confirmations.

### F02 — Confirmed: routed first branch can suppress a feasible later branch (high)

`v59/routing_plan.py:signals` chooses the first raw entry bit before exit conflict, stop validity and sizing admission. CPU probe: branch A emitted entry+exit (bits 3), B emitted entry only (1); router selected A and output 3, which request admission rejects. The same structural issue applies when A lacks a stop but B has a valid one.

Revision: produce branch-local admissible intents and arbitrate among them. Keep ownership sticky for accepted pending/held orders; define the ownership transfer point on cancellation and final liquidation. Preserve A-first only among feasible branches. Test both branches, partial fills, cancellation and re-entry.

### F03 — Confirmed contract gap: no limit price or explicit limit-order type (high)

`orders.ORDER_WIDTH=17`, `order_liquidity.fill` and CUDA counterpart have no limit field/comparison. Buys fill at ask and sells at bid when structural quote/size checks pass. They cannot implement the agreed buy-limit/sell-limit condition.

Revised resolution: candidate defines the limit together with action, size, stop and target. Version the minimal intent schema accordingly. The deterministic executor checks side/limit and quote size at candle close; controller-owned lifetime, replacement and partial-fill bookkeeping are not candidate decisions. Unmarketable limits do not fill. Missing or malformed limits are schema errors. Test price improvement, crossing/noncrossing limits, partial fills across closes and deterministic retirement with accrued fees. This is proposed for the next implementation; current V59 has no limit-price head or field.

### F04 — Confirmed: admission conditions are not fully visible to the model (high)

`orders.transition` silently gates raw entry on quote/mark validity, stop below ask, stop below bid and mark, membership, action conflict, pending state, three-second cooldown and sizing. The model state has no unified admissibility/reason inputs. Stop below bid and mark are stricter than just stop below intended entry; they encode avoidance of an already-triggered stop and must be explicit strategy rules, not hidden execution policy.

Revision: one vectorized strategy-intent boundary authority shared by sampling probes, CPU oracle and CUDA controller. Distinguish raw neural proposals from accepted requests and record abstention reasons. Review each boundary for necessity; do not simply relocate every old restriction.

### F05 — Confirmed restrictive history definition, policy review required (high)

`v42/certified_history_bank.py:calculate` requires all values in a swing window to be finite; unobserved seconds become NaN. For left60/right3 that is 64 consecutive observed one-second bars. A synthetic 140-second sequence with one absent bar every20 seconds produced **zero** valid 60/3 pivots but **134** valid 2/2 pivots. The earlier real-candidate check found 904 eligible entry-signal moments on July30 with no 60/3 stop.

Revision: decide and name the intended swing definition (elapsed-time observations, observed-bar count, or another explicitly causal rule). These are different strategies; do not silently forward-fill lows or shorten the window. Expose availability and effective lookback. Sampling must use supported semantics and training-only availability diagnostics; it must not remove a candidate merely because it is unprofitable or infrequent. A history change requires a new prepared-data identity and affected replays.

### F06 — Confirmed: cancellation and pending-order policy are implicit action mappings (medium/high)

`orders.transition` maps exit/reduce signals to pending-buy cancellation and add signals to pending-sell cancellation. Add signals can amend a pending buy under cooldown/max-add limits. Partial positions can be managed, and accrued fills/fees are preserved by existing tests. However the strategy has no explicit keep/cancel/amend intent or exact order-specific fill-price state. Minimum ages apply only while held, so a zero-fill pending entry can be cancelled by an exit head that would be age-blocked after its first partial fill.

Revised resolution: candidates should not learn order-management intent. Replace these implicit action-to-order mappings with a documented deterministic controller policy and test before-first-fill/after-partial-fill transitions. Keep position inputs, preserve actual fills/fees, and define when action requests supersede existing remainders. Do not label all current cancellations execution rejections.

### F07 — Confirmed: model and execution quote visibility differ (medium/high)

Model quote features in `v7/resident_features.py` use original feature-market quotes and age<=1s for bid/ask/spread channels. Persistent execution uses certified execution pages, valid book structure and no quote-age expiry. Consequently the execution book may be usable while model quote features are invalid or differ. A cleared freshness gate at execution did not update the model contract.

Revision: expose a coherent causal execution-state view with book validity, quote timestamp and available sizes; retain legacy feature identity only where intentional and clearly named. Audit equality/ordering of timestamps at candle boundaries. Do not change historical feature values in place or claim this mismatch alone proves bad fills.

### F08 — Confirmed limitation: terminal drain is autonomous; incomplete tail aborts (medium/high)

`order_replay` sets terminal, zeroes network signals in tail mode, cancels pending buys and drains liquidation using certified later liquidity. `require_complete` raises if any held quantity or pending order remains. This avoids fabricated fills, but the strategy cannot manage the execution-only tail. A missing tail prevents receipt publication for the batch.

Revision: expose decision cutoff and terminal policy before it occurs; specify expiry/liquidation semantics and preserve unresolved state and counts if coverage ends. Do not silently mark missing completion as zero or exclude only inconvenient candidates. The current complete-flat objective requires complete evidence; an alternative valuation rule needs explicit approval.

### F09 — Confirmed observability gap: no reconciled intent/reason ledger (high)

Current output contains fills, cancellations, terminal validity, inactive fraction, risk diagnostics and crossover rejection reasons. It does not reconcile raw entry signals to strategy abstentions, accepted requests, quote/limit waits and actual first fills by candidate/day. Old `risk_rejected_entries` counters do not represent these current conditions. A zero-trade result therefore hides multiple very different causes.

Revision: bounded device-side counters with a single primary outcome and overlapping reason bit counts. Reconcile proposal opportunities = abstained + accepted; separately track standing-order waits and fill events. Aggregate by candidate/session/method. Detailed traces only for requested cases; asynchronous snapshots/publication must never block successor preparation unnecessarily.

### F10 — Confirmed: calibration tests head activation, not executable strategy activity (high)

`calibration.calibrate` calibrates each head independently. Entry uses probe eligibility, but not the sampled stop policy, current quote/size constraints, exit conflict, age/cooldown or routed branch feasibility. Management probes use synthetic held/order states. `Policy.validate` accepts stop_fraction=1 although percent-stop admission requires stop>0. This exact endpoint may be rare under sampling; the validator nevertheless admits an impossible policy.

Revision: enforce mathematical impossibilities at construction and add training-only, contract-matched joint strategy probes with named coverage/reasons. Distinguish calibration from performance selection. Avoid replaying the same obviously invalid construction; retain valid but low-activity strategies under the explicit selection policy.

### F11 — Confirmed semantics requiring review: OR input validity and flat exit calibration (medium)

Generic neural node validity is OR over available nonzero market ancestors, not ALL referenced required inputs. Missing values become zero. Exit heads are calibrated using held-state probes but also evaluated flat to veto entries. These are legitimate possible definitions, but neither guarantees coherent complete strategies. They can generate accidental validity-dependent triggers and frequent entry vetoes.

Revision: formalize optional versus required inputs for each operator/strategy boundary; calibrate flat exit-veto behavior if retained. Do not globally change OR to AND without evaluating the intended semantics and replay impact.

### F12 — Confirmed reproduction behavior needing policy visibility (medium)

`lineage.next_population` filters mating by ancestry/species and, for some routes, node budget; failures fall back to parent mutation. It reports several rejection counts. The scheduled post-construction mutation pool contains **fresh candidates only**, and telemetry explicitly says `post_crossover_mutation=False`. The reported mutation rate therefore does not mean that fraction of crossover children are mutated. Some mutation retries catch ValueError/RuntimeError and rotate without preserving each detailed reason. Inherited schedules still contain a forbidden delay edit which rotates to another legal edit.

Revision: document intended mutation scope, expose attempts/fallbacks by scheduled versus realized operator, remove impossible proposals from the schedule without silently reallocating probabilities, and test exact population/RNG budgets. Review ticker/global parent proportions after sibling reuse and fallback: the 25/75 planned slots do not guarantee exactly 25/75 realized successful parents. Do not change proportions or enable post-crossover mutation as an unannounced repair.

### F13 — Confirmed: inactivity is neutral in fitness but excluded from parenting (medium)

`robust_score.combine` makes a no-trade day contribute0; below the profit targets score reduces to total net minus summed session DD. `profitable_sessions` is descriptive only. `lineage.next_population` requires at least one closed position across training for elites and parent pools; it does not require positive score. Inactive candidates can rank above losing active candidates in the panel yet are not selectable parents.

Revision: expose score rank, replay validity and parent eligibility separately, including the no-closed-position reason. Treat any breadth objective or removal of the active-parent rule as an explicit policy change, not an execution fix. No positive-only selection gate was found on this path.

### F14 — Confirmed: ticker specialist ranking omits drawdown (medium)

`ticker_selection.ticker_scores` sums logarithmic ticker rewards. Specialist pools aggregate those rewards by ticker across sessions, without subtracting ticker drawdown; the global objective subtracts summed account DD. This can prefer a profitable but volatile/concentrated specialist. The job's 25% specialist pathway is active; the main global score remains unchanged.

Revision: document separate specialist objective and decide whether to align its drawdown treatment. Do not substitute summed ticker DD for account DD and call them equivalent. Add attribution of realized parent selections to each objective.

### F15 — Confirmed safeguards, with contract hardening needed (medium)

Main global and local scoring call `full_common.aggregate -> robust_score.combine`: 0.5*ticker reward +0.5*session reward −sum(account DD), C_ticker=2000, C_session=5000, alpha=.1. Losses are linear below zero; no extra gross-loss penalty. Episode/ticker cashflows are reconciled. Global reproduction receives only training panels; validation runs separately/concurrently. Initial admission requires all6,144 valid founders, not execution-filtered survivors. Terminal validity still feeds eligibility, but the persistent path requires completion before publishing rather than accepting partial terminal results.

The generic `Objective` accepts arbitrary nonnegative weights while `combine` applies fixed DD1/loss0; current global job validation pins the intended values, so this is not a demonstrated current-score error. Revision: reject unsupported generic settings or make the authority consistent, and cover local/global equivalence, missing sessions, nonfinite metrics and incomplete ledgers explicitly. Never impute missing session results as zero.

### F16 — Confirmed performance controls; no new speed claim (medium)

Existing code already has two bounded session lanes, next-session loading, execution-tail prefetch, reproduction/validation overlap, process-owned publication, cached identities and adaptive 512/1024 batches. Execution still synchronizes at chunks for journals/episodes; journal backpressure and terminal drain can extend wall time. A GPU utilization chart alone does not show progress through these stages.

Revision: measure total two-session wall time and full generation, with load/transfer/capture/decision/tail/drain/metrics/reproduction/publication/queue-wait components. Fuse admissibility/reason counters with existing strategy kernels where possible, precompute market-only availability once, and publish asynchronously with bounded memory. Preserve exact arithmetic, event ordering and restart state. The 45-second/1,024-session target has **not** been requalified here.

### F17 — Review: rollback must restore every state affecting future signals (medium)

`order_replay.run_page` snapshots financial/order/owner buffers for capture and journal-overflow retry, but does not list `plan.previous_clock` alongside every rollback checkpoint. It is mutable and contributes elapsed-time feedback; elapsed-time sources are permitted by structural validation even though ordinary random_input excludes them. Scratch is feed-forward, but that alone does not prove state-independent retry.

Revision: add a complete mutable-state inventory and an elapsed-sensitive candidate parity probe for eager, capture, padding and forced-overflow rollback, including routed base state. This is a concrete uncovered dependency, **not a measured production P&L divergence**.

### F18 — Review: execution realism and persistent-input costs need bounded acceptance (medium)

Same-candle feature evaluation and quote matching are implemented. Timestamp ties, within-second quote changes, quote-size renewal and tail boundaries still need event-order fixtures. The book tracks snapshot timestamp and remaining sides per candidate/listing; independent candidates are alternative simulated accounts, not a shared portfolio. Verify that timestamp identity reliably distinguishes liquidity refreshes and never allows repeated same-snapshot fills. The latest dust fix avoids charging fees on <=1e-12 numerical residuals; real small fractional fills remain supported.

Revision: verify fill conservation at each snapshot and across chunks/pages, monetary ledgers, partial-fill fee increments, price-limit compliance, and immutable results under different batching/concurrency. Do not claim queue-position or market-impact realism from top-of-book size alone.

## Evidence and bounded verification

- Existing focused suite: `test_vector_v59.py`, `test_vector_v59_orders.py`, `test_vector_v59_swing_projection.py`, `test_vector_v59_crossing.py`, `test_vector_v59_initial_restart.py`: **40 passed, 3 skipped, 5.30s** on laptop. Skipped checks are not counted as verified GPU behavior.
- Additional capture lifecycle, legacy-founder and observer-I/O checks: **4 passed, 3.40s**. Combined executed suite: **44 passed, 3 skipped**. Commands used the laptop ml4t Python with `-B`, `PYTHONDONTWRITEBYTECODE=1`, and pytest `-p no:cacheprovider`. These are regression checks of the existing implementation, not acceptance tests of proposed fixes.
- CPU membership probe: valid underlying stop9, membership `[false,true,true,false]` -> stop IDs `[0,0,1,0]` (F01).
- CPU router probe: A raw bits3 (entry+exit), B bits1 -> selected A/3 despite usable B (F02).
- CPU history probe:140 seconds, mark/high/low10, absent every20th observation ->60/3 finite count0,2/2 finite count134 (F05).
- Prior live leader analysis in this chat (not rerun here): candidate5540 across eight completed training receipts had5 trades on3 days, net374.47, score107.00; three previously examined inactive days were explained by stop availability/invalid stops/exit veto. These counts do not describe all24 sessions, all candidates or a completed generation.
- No source repair, GPU qualification, fresh replay, run stop/resume, new input acquisition or terminal modification was performed for this document.

## Revision order and acceptance gates

1. **Freeze the minimal close-time contract and schemas:** candidate action/size/limit/stop/target outputs; causal market and current position inputs without recurrence; deterministic remainder/expiry/replacement/protective-exit rules; same-boundary quote and size authority. Record bug fixes versus deliberate behavior changes. Keep objective, sessions and budgets fixed.
2. **Fix proven boundary defects:** current membership swing projection, feasibility-aware branch routing, impossible policy endpoint, and named abstention reasons. Preserve history definition until explicitly revised.
3. **Implement price outputs and deterministic execution:** candidate-defined limit/stop/target prices; fixed order/partial-fill bookkeeping; position feedback without learned pending-order management; minimal close-time matching. Share CPU/CUDA semantics and retain actual fills/fees. Version genomes, state packing, execution and replay identities as affected.
4. **Align initialization and reproduction:** jointly sample/mutate/cross action and price outputs with admissibility probes, without profitability filtering; coherent branch selection, retry accounting, exact scheduled/realized counts and deterministic RNG. Enforce the new per-feature construction caps and history contracts through a versioned feature-aware constructor, mutations and crossover checks. Remove all V7 and explicit market-time inputs, replace the V7 support-bounce template with causal swings, and reject unsupported history requests before replay. Use formula/domain checks and bounded training-only coverage to prevent impossible, duplicate or uninformative proposals. Do not use legacy priors as value limits or silently clip market observations. Separate policy choices F12–F14 from necessary correctness repairs.
5. **Validate real runnable paths:** causal boundary fixtures; missing-history, invalid-stop, first-admission, routed fallback, exit conflict, cooldown, partial fills, limit waits, cancellation/amendment, cutoff/tail exhaustion, ledger conservation, all-founder inclusion, validation isolation, identity invalidation and deterministic recovery. Include a forced-overflow retry and shared-entry state-dependence check.
6. **Short performance qualification then explicit recovery:** use frozen representative candidates and two contrasting sessions; compare baseline/fixed wall time and all unchanged-case decisions/ledgers. For intentional behavior changes report changed requests/fills/P&L and reasons instead of claiming financial parity. A history/input/execution change invalidates affected financial receipts; retain genomes/RNG where schema-compatible, but replay affected founders before selection. Do not reuse old scores merely because genomes match.

No blanket removal of stop checks, no artificial fills, no forced trading on every day, no smaller networks/populations, no validation-driven repair, and no hidden fee/risk/score changes are acceptable acceleration shortcuts.

## Diagnostic schema to implement

Per candidate/session: raw proposal counts by action; decision opportunities; required-input-missing; stop-unavailable; stop-invalid; entry/exit-conflict; not-member; cooldown/age-block; pending-order-conflict; invalid/zero sizing; accepted intents; pending-unmarketable-limit; pending-missing-book; pending-no-size; partial/complete fills; cancelled remainder and reason; terminal unresolved quantity/orders. Keep unique primary outcomes separate from overlapping condition counts and repeated standing-order observations.

Every record must carry candidate/program identity, session/split, decision/execution contract versions, clock range and complete/partial coverage. Missing data is unavailable, never zero. Aggregate counters should be bounded tensors; sampled details can be persisted by the existing publication lane. UI should distinguish raw proposals, actionable requests, orders and positions.

## Full pinned market-input catalog

### Feature exclusions and history admission revision

User revision: remove V7 level features and market time-related inputs; use causal swings instead. This changes the **next-version** search space only. Existing V59 catalog identity and its current Allowed column remain historical facts. A new **Next allowed** column records the revised authority. Remove all110 `v7.*` feature IDs across absolute, cross-sectional and validity channels, not the shared Python modules whose names happen to contain v7. Also remove `time_of_day_sin`, `time_of_day_cos`, `premarket`, `regular`, `after_hours`, `log_inter_candle_gap` and `market.quote_age_seconds` from model inputs. Previously forbidden split/date-age features stay forbidden.

Interpretation: “time-related” means explicit clock/calendar, session-phase and elapsed-market-time signals. Return horizons, candle resolutions and indicator periods remain necessary feature definitions. Position age and cooldown state remain available as agreed position/controller state; the general `elapsed_seconds` model slot should be removed. Exact timestamps remain internal to the deterministic executor and data-validity checks. This does not assert that all remaining market features are free of time-of-day or identity correlations.

After removing the inherited blanket price-unit restriction, the pinned-catalog V60 proposal has **834 allowed /123 forbidden** feature IDs. The123 forbidden IDs are110 V7 features, seven explicit market-time features and six split/date-event features. Forbidden rows have no construction caps or history request. No exclusion applies solely because a field has unit `price` or `log_price`.

The full review restores310 previously blocked inputs: four log-OHLC fields;272 price-valued history features (recent highs, lagged closes, average moves and confirmed swing lows); four market fields (bid, ask, spread and close); and30 indicators (15 absolute EMAs and15 absolute ATRs). Relative/normalized variants remain available. All restored rows now have construction bounds and history/readiness definitions, rather than retaining N/A. MACD relative variants and RSI were already allowed and remain so. Enabling inputs does not require every candidate to use them.

New numerical construction envelopes for these restored rows are engineering proposals, not market-data validity rules: positive absolute price/EMA/swing/high/lag thresholds0.0001..10,000 dollars; nonnegative ATR/average-move/spread thresholds0..1,000 dollars; log-OHLC thresholds ln(0.0001)..ln(10,000). Apply the existing training-only coverage gate before freezing these ranges. Values outside these construction envelopes remain observed values, not rejected data. Do not use a declared currency-price cap to prove a live input can never exceed it.

Candidate versus execution availability are distinct: `Next allowed=yes` permits the model input; the exact quote/size remains available to the deterministic controller regardless of model selection or normalization. Quote age/timestamps remain internal validity/accounting data even though the quote-age model feature is excluded. Bid/ask size is not a row in the pinned957-input catalog: adding explicit size inputs requires versioned new IDs and valid source mapping, not a claim that removing the price filter supplied them. The quote feature/executor source mismatch in F07 still requires resolution before V60 activation.

Use normalized confirmed swing-low features already present in `history.swing_low.L.R.relative_to_close`, plus their availability masks. Left/right choices are limited to {1,2,3,4,5,10,15,20,30,45,60}; maximum confirmation window is121 observed one-second slots for60/60. Current history uses prior-clock confirmation and invalidates windows containing unobserved seconds. F05 remains unresolved: do not silently change to observed-bar indexing or fill gaps. A missing pivot makes that strategy branch unavailable; it must not submit an unusable stop request.

Replace V7-based `support_bounce` templates with swing-based entry/management templates before constructing founders. The current catalog has swing lows, **not confirmed swing highs**. If symmetric resistance/target features are required, add a separately versioned confirmed swing-high family with the same causal confirmation/availability contract and dedicated tests; do not claim those highs already exist or relabel V7 outputs as swings. Candidate-defined target prices need not depend on a swing-high input.

#### Per-row history contract

The table now separates **Supported history/dependency** from **Readiness requirement**. A static table cannot truthfully promise that every session/ticker has that much valid history at every close. The next manifest/runtime must provide actual available counts, boundary clocks and validity masks. Construction may select only registered feature IDs and their fixed supported periods; it cannot invent a longer period or ask a missing bank to synthesize it.

- Fixed-window rows state their window. A window ending at the previous clock still preserves that lag. Return endpoints are valid only when the producer's freshness/missing-data rules hold.
- Candle aggregations use the most recent completed bucket; its end may be up to one bucket old. Reading a return at that bucket's end can reach nearly2H seconds behind the decision. The table records that dependency rather than claiming onlyH seconds.
- Recursive indicators use certified accumulated state. Their warm-up observation count is not a maximum memory length, nor authorization for arbitrary historical bar queries. EMA, RSI and ATR periods up to60 and MACD registered combinations are supported; state may include a longer certified prefix.
- Carried confirmed pivots can be older than their confirmation window. “L+R+1” is the formation requirement, not a guaranteed maximum pivot age. Expose availability; any stale-pivot limit would be a separately declared strategy condition.
- The base loader retains up to119 preceding actual feature rows as context, not119 guaranteed seconds. This is not a universal lookback cap on already prepared300-second features or running indicator state. Do not impose it on all rows.
- V59 graphs have no general arbitrary lag/window operator. The next design retains only an explicit one-previous-clock input for the existing crossing operator, requiring both observations valid. Do not add unbounded history access. Nested history requests and unsupported periods must fail construction before replay.

For each candidate, compile a dependency certificate from all referenced features/branches: fixed windows, indicator warm-up requirements, pivot pairs, optional crossing lag and required masks. Check it against the sealed input/history manifest before allocating replay work. At each candle, the strategy boundary checks actual readiness and reports abstention. Checking only a maximum declared lookback cannot guarantee activity; market-dependent signals and gaps still exist. Do not discard an entire otherwise valid candidate merely because one early candle lacks history.

Required implementation additions: new feature-policy/version; enforce exclusions in restoration/validation as well as all construction paths; replace affected templates; compile dependency certificates; prepare only registered histories; expose readiness; audit masked/rejected proposal counts; verify no removed input survives crossover or aliasing. Preserve population and compute budgets. New input/strategy semantics require new replay identities and affected financial replays before selection.

### Next-version hard construction caps (proposed; not deployed)

The user requests hard limits on candidate construction to prevent wasting replay compute on inappropriate thresholds and incompatible conditions. The table now has **Next construction min/max** for every searchable feature, separate from the unchanged legacy priors. Blocked features have no construction range: they remain forbidden. These are bounds for raw-feature comparison thresholds and feature-specific numeric proposals, not automatic clipping of observed market data and not financial order limits.

Two bases are distinguished: **M** = formula-derived domain bounds; **E** = explicit finite engineering search envelope (possibly with a mathematical bound on one side). E values below are proposed defaults, not empirically certified limits for this universe. Before launch, a bounded training-only scan must report valid counts, missingness, extrema, quantiles and out-of-envelope counts by feature/session and freeze the approved cap manifest. Do not consult validation to tune them. If the scan shows an unsuitable envelope, revise it visibly before creating the next population; never silently clamp data or tighten it during evolution. No existing run is modified.

Concrete envelope choices:

- Positive-price ratios minus1: −0.99 to9 (ratio0.01 to10); known support/resistance center side narrows this to [−0.99,0] or [0,9]. Band edges can straddle the current price and are not incorrectly constrained by the center's side.
- Log returns: ±ln(10), including a tenfold rise or fall over the stated horizon; context-relative log returns: ±2ln(10). Thus a tripling is within the proposed envelope. Positive volatility/dispersion default to ln(10); these are engineering limits, not mathematical maximum volatility.
- Signed MACD ratios: ±4; nonnegative ATR/average-move fractions:0..4; candle range/spread fractions:0..9; candle body:−9..0.99. Finite envelopes intentionally exclude extreme threshold proposals without deleting extreme observations.
- Boolean/RSI/fractions/percentiles:0..1; time sine/cosine and percentile change:−1..1; nested5s/60s activity acceleration:−1..11 (upper endpoint is a supremum, not an attainable value).
- Log1p activity caps distinguish units: shares/volume1e9, trades/counts1e6, notional1e11; share-reference quantities1e12; relative-volume ratio1e4. Intraday gaps/quote age use57,600 seconds; historical level age uses10*365.25 days. These are engineering upper limits in the stated units, converted to log1p where appropriate. History notional means use1e11 dollars; volatility/fee ratio uses1,000.

Implementation requirements:

1. Introduce a versioned feature-contract authority with construction caps, mathematical domain, endpoint openness, units, missing policy and model transform. Validate finite ordered caps. Keep forbidden IDs forbidden across all channels.
2. Apply it to initial templates/random feature predicates, fresh immigrants, threshold mutations, input substitutions and crossover repair **before GPU replay**. Sample only inside the permitted interval; Boolean predicates use explicit true/false tests rather than continuous random thresholds. Handle strict/non-strict comparator endpoints so known bounded features do not generate tautologies or impossible predicates.
3. The present V59 graph threshold is on a weighted/transformed graph output, not directly on a raw feature. Merely changing catalog numbers will not enforce this design. Use feature-aware predicate construction and transform raw thresholds through the same feature transform; validate graph-output thresholds using operator output ranges and bounded training probes. Do not compare a raw return threshold directly to tanh(return).
4. Keep channel-specific contracts: these table caps describe raw/absolute-feature predicates. Cross-sectional z-score/tanh features are dimensionless relative channels and need their own construction interval and training coverage; validity channels use Boolean predicates. Share-decade inputs likewise require mapping through their special transform.
5. Use sound domain/operator reasoning to reject provably dead/constant constructions, and deduplicate canonical genomes before replay. An engineering search envelope is **not** a guaranteed bound on live input values: do not use it as a false proof that a graph is constant. Separate mathematically impossible, duplicate, and training-probe-uninformative proposals in counters. Low profitability or rare trading alone is not a proof of redundancy.
6. Add tests for every family, extreme observations beyond the construction envelope, missing values, endpoint comparisons, crossover/mutation compliance, deterministic proposal counts and unchanged population/network budgets. Report prevented proposals and actual saved replay work; caps alone cannot guarantee unique or useful neural behavior.

The current design therefore keeps position inputs without recurrence, implements candidate-defined trading prices, and adds construction caps as an additional pre-replay authority. All are next-implementation tasks, not changes activated by this document.

### F19 — Catalog-range review and corrected interpretation

**Confirmed:** the catalog's numerical lower/upper metadata is not the mathematical domain of each feature and is not enforced as a clip in the active V59 neural replay. It would be incorrect to interpret upper=2 as "values above2 are impossible or discarded." The misleading table labels are corrected in this revision; the immutable source catalog numbers remain recorded exactly.

Code traced in this review:

- `v6/torch_backtest/feature_bank.py:catalog,validity`: defines legacy ranges; validity checks finiteness and feature masks, not lower<=value<=upper.
- `research/rl_trading/v6/features.py:encode`: source of base ratios, log features and V7 normalized distances; the reviewed ratios are calculated, not clipped to catalog bounds.
- `v42/enriched_math.py:returns,own,market_summary`: enriched returns are `log(current_close/previous_close)`; returns, candle body/range and context are not clamped to the catalog prior.
- `v42/indicator_math.py:calculate`: current indicator formulas and normalized RSI; no generic catalog-range clipping.
- `v27/resident_features.py` and `v7/resident_features.py`: gather raw banks and apply validity/relative-price formulas; no generic lower/upper comparison.
- `v59/network.py:_project` and the shared feature projection path: squash valid values after projection; no prior-range clip. Share-count bucketing is an explicit, separate transform, not return clipping.
- `v27/search_space.py`: legacy lower/upper values actually govern symbolic numeric threshold sampling and compatible mutation classes. V59 uses its family feature-index sampler, but its neural thresholds/weights and calibration use their own bounds/observed outputs; they do not call that symbolic numeric threshold sampler. Changing shared prior numbers would alter older symbolic searches and is not necessary to fix a nonexistent V59 replay cap.

This is a review of formula families and consumers plus an enumeration of all957 catalog records. It is not an empirical scan of every stored feature value across every session. Do not claim that all upstream source data or all economic interpretations have been independently certified by this review.

| Feature family | Actual value/domain interpretation | Why the legacy prior is not a cap |
|---|---|---|
| Simple return `P_new/P_old-1` (example notation, not the enriched formula) | For positive prices, greater than−1, with no finite mathematical upper bound | Tripling price gives2 (+200%); quadrupling gives3 (+300%). A future return is not an input before that candle has completed. |
| `enriched.return.*`, candle returns, context mean/median returns | Log return `log(P_new/P_old)`; signed, no finite mathematical bound for positive prices | Tripling gives1.098612; a rise by exp(3) gives3. Current enriched prior [−.5,1] excludes neither. |
| Context relative return | Own log return relative to context baseline | Signed, no universal [−.5,1] domain; verify source definition when changing it. |
| EMA/VWAP/history positive-price ratios minus1; V7 normalized positive level distance | Greater than−1, potentially far above2 (some formulas admit tighter contextual bounds) | Positive history/reference prices can be much higher than current price. No generic ±2 or ±10 clip. |
| MACD relative line/signal/histogram | Signed normalized price differences | Not generally restricted to the chosen sampling interval. |
| ATR relative, average-move fraction, volatility, dispersion, candle range | Nonnegative under valid source geometry; no universal small upper bound | A large move can exceed .5 or1; declaring a sampling interval does not truncate it. |
| Candle body `(close-open)/close` | Less than1 for positive open/close; unbounded below mathematically | This is not `(close-open)/open`; do not interpret its prior as a conventional simple-return range. |
| RSI, advancing fraction, coverage, percentile, active fraction, valid close location | [0,1] under valid inputs and source definitions | These have genuine formula-derived bounds. They are not bounded merely because the catalog says so. |
| Time-of-day sine/cosine | [−1,1] | Legacy generic ratio prior [−2,2] is wider than the real domain. |
| Boolean validity/role/session flags | {0,1} | Boolean definition, not a numerical clipping choice. |
| Nonnegative count/activity/share/duration log1p values | >=0; no universal upper30 | Prior30 is a sampling choice. Share features have a separate V59 decade-bucketing transform. |
| Activity acceleration | `(short_rate)/(long_rate+1)-1`; formula-specific, not generic return | For the implemented nested5s/60s nonnegative sums, lies in [−1,11); legacy prior is not its mathematical domain. |
| Rank percentile change | Difference of valid percentiles, [−1,1] | Separate from price/log returns. |
| Execution volatility/fee ratio | Nonnegative when valid; no universal upper100 | Depends on modeled volatility, price, sizing and fee assumptions; missing denominator validity remains separate. |

**Numerical compression already present:** ratio/bool absolute channels use `tanh(raw)`. Thus raw2 ->0.964028 and raw3 ->0.995055, rather than both becoming2. At sufficiently large magnitudes float32 tanh can saturate to1, so distinct extremes can lose resolution. Cross-sectional channels also use tanh. This is a real representation limitation, not a catalog upper-bound rejection. A different tail-preserving scale (for example signed log before squashing) is a proposed versioned feature change requiring calibration/replays, not a silent fix in this documentation task.

**Bounded CPU verification on the current functions:** `enriched_math.returns` produced log(3)=1.0986122886681098 and3.0 for price multiples3 and exp(3). A synthetic raw3 passed through `NetworkPlan._project` in both `enriched.return.5s` (legacy upper1) and `ema_7_rel` (legacy upper2): both remained valid and projected to0.9950547814. The probe checked projection semantics, not that EMA3 occurred in the live dataset. No runtime clipping defect at upper1/2 was found, so no shared feature-generation code or pinned data was changed.

**Current versus next implementation:** the957 catalog, masks,641 allowed features,316 blocked features, normalization and11 current state inputs are already implemented in V59. The corrected documentation interpretation is delivered now. The minimal deterministic controller contract and candidate-defined limit/stop/target output schema, revised candidate input slots and audit fixes remain proposed work for the next implementation. They are not activated by this report. New schema metadata should separate `sampling_prior`, `mathematical_domain`, `missing_policy` and `model_transform` instead of overloading lower/upper; preserve finite prior ranges for any legacy sampler that requires them.

The table enumerates the pinned source catalog and adds proposed next-version construction caps and per-row history/readiness. `V59 allowed` describes current code; `Next allowed` describes the new exclusions across all three channels. **Legacy prior lower/upper are not permitted-value boundaries.** New caps constrain candidate construction only as specified above. M/E label the basis; next-version `forbidden` rows cannot be sampled. Supported history is a dependency ceiling/registered definition, not a promise of available data at every candle. No additional arbitrary history request is allowed; the explicitly supported crossing operator may read one prior clock with both masks valid. Numbers are rounded for display (six decimals); the implementation manifest should store the defining expressions/full precision, particularly logarithms and strict endpoints.

| ID | Name | Unit | Legacy prior lower | Legacy prior upper | V59 allowed | Next allowed | Next construction min | Next construction max | Basis | Supported history/dependency | Readiness requirement |
|---:|---|---|---:|---:|---|---|---:|---:|---|---|---|
| 0 | `log_open` | log_price | -5 | 10 | no | yes | -9.21034 | 9.21034 | E | Current completed candle only | Positive source OHLC; price/extreme mask; natural-log encoding |
| 1 | `log_high` | log_price | -5 | 10 | no | yes | -9.21034 | 9.21034 | E | Current completed candle only | Positive source OHLC; price/extreme mask; natural-log encoding |
| 2 | `log_low` | log_price | -5 | 10 | no | yes | -9.21034 | 9.21034 | E | Current completed candle only | Positive source OHLC; price/extreme mask; natural-log encoding |
| 3 | `log_close` | log_price | -5 | 10 | no | yes | -9.21034 | 9.21034 | E | Current completed candle only | Positive source OHLC; price/extreme mask; natural-log encoding |
| 4 | `bar_vwap_rel` | ratio | -2 | 2 | yes | yes | -0.99 | 9 | E | Current completed candle only | Source price/extreme/value mask; positive denominator where used |
| 5 | `session_vwap_rel` | ratio | -2 | 2 | yes | yes | -0.99 | 9 | E | Session-to-date accumulated summary | Causal cumulative volume/notional; valid denominator for value |
| 6 | `bar_vwap_valid` | bool | 0 | 1 | yes | yes | 0 | 1 | M | Current completed candle only | Source price/extreme/value mask; positive denominator where used |
| 7 | `session_vwap_valid` | bool | 0 | 1 | yes | yes | 0 | 1 | M | Session-to-date accumulated summary | Causal cumulative volume/notional; valid denominator for value |
| 8 | `log_volume` | log_shares | 0 | 30 | yes | yes | 0 | 20.723266 | E | Current completed candle only | Source price/extreme/value mask; positive denominator where used |
| 9 | `log_trades` | log_count | 0 | 30 | yes | yes | 0 | 13.815512 | E | Current completed candle only | Source price/extreme/value mask; positive denominator where used |
| 10 | `log_volume_60s` | log_shares | 0 | 30 | yes | yes | 0 | 20.723266 | E | Fixed60s rolling activity | Causal activity rows; zero activity allowed |
| 11 | `log_trades_60s` | log_count | 0 | 30 | yes | yes | 0 | 13.815512 | E | Fixed60s rolling activity | Causal activity rows; zero activity allowed |
| 12 | `log_rvol_10s_prev_session` | log_duration | 0 | 20 | yes | yes | 0 | 9.21044 | E | Current10s + matched10s previous session | Previous-session certified same-clock volume denominator >0 |
| 13 | `rvol_10s_available` | bool | 0 | 1 | yes | yes | 0 | 1 | M | Same previous-session10s availability dependency | Boolean availability; not authorization to request extra sessions |
| 14 | `macd_line_rel` | ratio | -2 | 2 | yes | yes | -4 | 4 | E | Prepared indicator state; no raw-history extension | Sealed base producer indicator_available and finite fields |
| 15 | `macd_signal_rel` | ratio | -2 | 2 | yes | yes | -4 | 4 | E | Prepared indicator state; no raw-history extension | Sealed base producer indicator_available and finite fields |
| 16 | `rsi_14` | ratio | 0 | 1 | yes | yes | 0 | 1 | M | Prepared indicator state; no raw-history extension | Sealed base producer indicator_available and finite fields |
| 17 | `atr_14_rel` | ratio | -2 | 2 | yes | yes | 0 | 4 | E | Prepared indicator state; no raw-history extension | Sealed base producer indicator_available and finite fields |
| 18 | `ema_7_rel` | ratio | -2 | 2 | yes | yes | -0.99 | 9 | E | Prepared indicator state; no raw-history extension | Sealed base producer indicator_available and finite fields |
| 19 | `ema_26_rel` | ratio | -2 | 2 | yes | yes | -0.99 | 9 | E | Prepared indicator state; no raw-history extension | Sealed base producer indicator_available and finite fields |
| 20 | `indicator_available` | bool | 0 | 1 | yes | yes | 0 | 1 | M | Prepared indicator state; no raw-history extension | Sealed base producer indicator_available and finite fields |
| 21 | `time_of_day_sin` | ratio | -2 | 2 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 22 | `time_of_day_cos` | ratio | -2 | 2 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 23 | `premarket` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 24 | `regular` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 25 | `after_hours` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 26 | `log_inter_candle_gap` | log_duration | 0 | 30 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 27 | `log_float_shares` | log_shares | 0 | 30 | yes | yes | 0 | 27.631021 | E | Point-in-time reference snapshot; no bar history | Source presence mask and as-of reference provenance |
| 28 | `float_present` | bool | 0 | 1 | yes | yes | 0 | 1 | M | Point-in-time reference snapshot; no bar history | Source presence mask and as-of reference provenance |
| 29 | `log_shares_outstanding` | log_shares | 0 | 30 | yes | yes | 0 | 27.631021 | E | Point-in-time reference snapshot; no bar history | Source presence mask and as-of reference provenance |
| 30 | `shares_present` | bool | 0 | 1 | yes | yes | 0 | 1 | M | Point-in-time reference snapshot; no bar history | Source presence mask and as-of reference provenance |
| 31 | `log_days_since_split` | log_duration | 0 | 30 | no | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 32 | `split_present` | bool | 0 | 1 | no | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 33 | `log_days_since_reverse_split` | log_duration | 0 | 30 | no | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 34 | `reverse_split_present` | bool | 0 | 1 | no | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 35 | `bar_price_valid` | bool | 0 | 1 | yes | yes | 0 | 1 | M | Current completed candle only | Source price/extreme/value mask; positive denominator where used |
| 36 | `bar_extremes_valid` | bool | 0 | 1 | yes | yes | 0 | 1 | M | Current completed candle only | Source price/extreme/value mask; positive denominator where used |
| 37 | `v7.below.1.center_distance_rel` | ratio | -10 | 10 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 38 | `v7.below.1.lower_distance_rel` | ratio | -10 | 10 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 39 | `v7.below.1.upper_distance_rel` | ratio | -10 | 10 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 40 | `v7.below.1.log_observation_count` | log_count | 0 | 30 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 41 | `v7.below.1.log_today_observation_count` | log_count | 0 | 30 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 42 | `v7.below.1.log_age_since_confirmation` | log_duration | 0 | 30 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 43 | `v7.below.1.role_support` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 44 | `v7.below.1.role_resistance` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 45 | `v7.below.1.role_transition` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 46 | `v7.below.1.historical_origin` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 47 | `v7.below.1.present` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 48 | `v7.below.2.center_distance_rel` | ratio | -10 | 10 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 49 | `v7.below.2.lower_distance_rel` | ratio | -10 | 10 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 50 | `v7.below.2.upper_distance_rel` | ratio | -10 | 10 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 51 | `v7.below.2.log_observation_count` | log_count | 0 | 30 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 52 | `v7.below.2.log_today_observation_count` | log_count | 0 | 30 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 53 | `v7.below.2.log_age_since_confirmation` | log_duration | 0 | 30 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 54 | `v7.below.2.role_support` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 55 | `v7.below.2.role_resistance` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 56 | `v7.below.2.role_transition` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 57 | `v7.below.2.historical_origin` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 58 | `v7.below.2.present` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 59 | `v7.below.3.center_distance_rel` | ratio | -10 | 10 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 60 | `v7.below.3.lower_distance_rel` | ratio | -10 | 10 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 61 | `v7.below.3.upper_distance_rel` | ratio | -10 | 10 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 62 | `v7.below.3.log_observation_count` | log_count | 0 | 30 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 63 | `v7.below.3.log_today_observation_count` | log_count | 0 | 30 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 64 | `v7.below.3.log_age_since_confirmation` | log_duration | 0 | 30 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 65 | `v7.below.3.role_support` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 66 | `v7.below.3.role_resistance` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 67 | `v7.below.3.role_transition` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 68 | `v7.below.3.historical_origin` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 69 | `v7.below.3.present` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 70 | `v7.below.4.center_distance_rel` | ratio | -10 | 10 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 71 | `v7.below.4.lower_distance_rel` | ratio | -10 | 10 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 72 | `v7.below.4.upper_distance_rel` | ratio | -10 | 10 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 73 | `v7.below.4.log_observation_count` | log_count | 0 | 30 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 74 | `v7.below.4.log_today_observation_count` | log_count | 0 | 30 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 75 | `v7.below.4.log_age_since_confirmation` | log_duration | 0 | 30 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 76 | `v7.below.4.role_support` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 77 | `v7.below.4.role_resistance` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 78 | `v7.below.4.role_transition` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 79 | `v7.below.4.historical_origin` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 80 | `v7.below.4.present` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 81 | `v7.below.5.center_distance_rel` | ratio | -10 | 10 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 82 | `v7.below.5.lower_distance_rel` | ratio | -10 | 10 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 83 | `v7.below.5.upper_distance_rel` | ratio | -10 | 10 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 84 | `v7.below.5.log_observation_count` | log_count | 0 | 30 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 85 | `v7.below.5.log_today_observation_count` | log_count | 0 | 30 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 86 | `v7.below.5.log_age_since_confirmation` | log_duration | 0 | 30 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 87 | `v7.below.5.role_support` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 88 | `v7.below.5.role_resistance` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 89 | `v7.below.5.role_transition` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 90 | `v7.below.5.historical_origin` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 91 | `v7.below.5.present` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 92 | `v7.above.1.center_distance_rel` | ratio | -10 | 10 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 93 | `v7.above.1.lower_distance_rel` | ratio | -10 | 10 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 94 | `v7.above.1.upper_distance_rel` | ratio | -10 | 10 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 95 | `v7.above.1.log_observation_count` | log_count | 0 | 30 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 96 | `v7.above.1.log_today_observation_count` | log_count | 0 | 30 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 97 | `v7.above.1.log_age_since_confirmation` | log_duration | 0 | 30 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 98 | `v7.above.1.role_support` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 99 | `v7.above.1.role_resistance` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 100 | `v7.above.1.role_transition` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 101 | `v7.above.1.historical_origin` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 102 | `v7.above.1.present` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 103 | `v7.above.2.center_distance_rel` | ratio | -10 | 10 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 104 | `v7.above.2.lower_distance_rel` | ratio | -10 | 10 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 105 | `v7.above.2.upper_distance_rel` | ratio | -10 | 10 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 106 | `v7.above.2.log_observation_count` | log_count | 0 | 30 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 107 | `v7.above.2.log_today_observation_count` | log_count | 0 | 30 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 108 | `v7.above.2.log_age_since_confirmation` | log_duration | 0 | 30 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 109 | `v7.above.2.role_support` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 110 | `v7.above.2.role_resistance` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 111 | `v7.above.2.role_transition` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 112 | `v7.above.2.historical_origin` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 113 | `v7.above.2.present` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 114 | `v7.above.3.center_distance_rel` | ratio | -10 | 10 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 115 | `v7.above.3.lower_distance_rel` | ratio | -10 | 10 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 116 | `v7.above.3.upper_distance_rel` | ratio | -10 | 10 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 117 | `v7.above.3.log_observation_count` | log_count | 0 | 30 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 118 | `v7.above.3.log_today_observation_count` | log_count | 0 | 30 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 119 | `v7.above.3.log_age_since_confirmation` | log_duration | 0 | 30 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 120 | `v7.above.3.role_support` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 121 | `v7.above.3.role_resistance` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 122 | `v7.above.3.role_transition` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 123 | `v7.above.3.historical_origin` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 124 | `v7.above.3.present` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 125 | `v7.above.4.center_distance_rel` | ratio | -10 | 10 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 126 | `v7.above.4.lower_distance_rel` | ratio | -10 | 10 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 127 | `v7.above.4.upper_distance_rel` | ratio | -10 | 10 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 128 | `v7.above.4.log_observation_count` | log_count | 0 | 30 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 129 | `v7.above.4.log_today_observation_count` | log_count | 0 | 30 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 130 | `v7.above.4.log_age_since_confirmation` | log_duration | 0 | 30 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 131 | `v7.above.4.role_support` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 132 | `v7.above.4.role_resistance` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 133 | `v7.above.4.role_transition` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 134 | `v7.above.4.historical_origin` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 135 | `v7.above.4.present` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 136 | `v7.above.5.center_distance_rel` | ratio | -10 | 10 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 137 | `v7.above.5.lower_distance_rel` | ratio | -10 | 10 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 138 | `v7.above.5.upper_distance_rel` | ratio | -10 | 10 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 139 | `v7.above.5.log_observation_count` | log_count | 0 | 30 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 140 | `v7.above.5.log_today_observation_count` | log_count | 0 | 30 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 141 | `v7.above.5.log_age_since_confirmation` | log_duration | 0 | 30 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 142 | `v7.above.5.role_support` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 143 | `v7.above.5.role_resistance` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 144 | `v7.above.5.role_transition` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 145 | `v7.above.5.historical_origin` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 146 | `v7.above.5.present` | bool | 0 | 1 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 147 | `split_this_session` | bool | 0 | 1 | no | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 148 | `reverse_split_this_session` | bool | 0 | 1 | no | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 149 | `history.recent_high.1` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 1s | 1 finite observed high slots |
| 150 | `history.recent_high.2` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 2s | 2 finite observed high slots |
| 151 | `history.recent_high.3` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 3s | 3 finite observed high slots |
| 152 | `history.recent_high.4` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 4s | 4 finite observed high slots |
| 153 | `history.recent_high.5` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 5s | 5 finite observed high slots |
| 154 | `history.recent_high.6` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 6s | 6 finite observed high slots |
| 155 | `history.recent_high.7` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 7s | 7 finite observed high slots |
| 156 | `history.recent_high.8` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 8s | 8 finite observed high slots |
| 157 | `history.recent_high.9` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 9s | 9 finite observed high slots |
| 158 | `history.recent_high.10` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 10s | 10 finite observed high slots |
| 159 | `history.recent_high.11` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 11s | 11 finite observed high slots |
| 160 | `history.recent_high.12` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 12s | 12 finite observed high slots |
| 161 | `history.recent_high.13` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 13s | 13 finite observed high slots |
| 162 | `history.recent_high.14` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 14s | 14 finite observed high slots |
| 163 | `history.recent_high.15` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 15s | 15 finite observed high slots |
| 164 | `history.recent_high.16` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 16s | 16 finite observed high slots |
| 165 | `history.recent_high.17` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 17s | 17 finite observed high slots |
| 166 | `history.recent_high.18` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 18s | 18 finite observed high slots |
| 167 | `history.recent_high.19` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 19s | 19 finite observed high slots |
| 168 | `history.recent_high.20` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 20s | 20 finite observed high slots |
| 169 | `history.recent_high.21` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 21s | 21 finite observed high slots |
| 170 | `history.recent_high.22` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 22s | 22 finite observed high slots |
| 171 | `history.recent_high.23` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 23s | 23 finite observed high slots |
| 172 | `history.recent_high.24` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 24s | 24 finite observed high slots |
| 173 | `history.recent_high.25` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 25s | 25 finite observed high slots |
| 174 | `history.recent_high.26` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 26s | 26 finite observed high slots |
| 175 | `history.recent_high.27` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 27s | 27 finite observed high slots |
| 176 | `history.recent_high.28` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 28s | 28 finite observed high slots |
| 177 | `history.recent_high.29` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 29s | 29 finite observed high slots |
| 178 | `history.recent_high.30` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 30s | 30 finite observed high slots |
| 179 | `history.recent_high.31` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 31s | 31 finite observed high slots |
| 180 | `history.recent_high.32` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 32s | 32 finite observed high slots |
| 181 | `history.recent_high.33` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 33s | 33 finite observed high slots |
| 182 | `history.recent_high.34` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 34s | 34 finite observed high slots |
| 183 | `history.recent_high.35` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 35s | 35 finite observed high slots |
| 184 | `history.recent_high.36` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 36s | 36 finite observed high slots |
| 185 | `history.recent_high.37` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 37s | 37 finite observed high slots |
| 186 | `history.recent_high.38` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 38s | 38 finite observed high slots |
| 187 | `history.recent_high.39` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 39s | 39 finite observed high slots |
| 188 | `history.recent_high.40` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 40s | 40 finite observed high slots |
| 189 | `history.recent_high.41` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 41s | 41 finite observed high slots |
| 190 | `history.recent_high.42` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 42s | 42 finite observed high slots |
| 191 | `history.recent_high.43` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 43s | 43 finite observed high slots |
| 192 | `history.recent_high.44` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 44s | 44 finite observed high slots |
| 193 | `history.recent_high.45` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 45s | 45 finite observed high slots |
| 194 | `history.recent_high.46` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 46s | 46 finite observed high slots |
| 195 | `history.recent_high.47` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 47s | 47 finite observed high slots |
| 196 | `history.recent_high.48` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 48s | 48 finite observed high slots |
| 197 | `history.recent_high.49` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 49s | 49 finite observed high slots |
| 198 | `history.recent_high.50` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 50s | 50 finite observed high slots |
| 199 | `history.recent_high.51` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 51s | 51 finite observed high slots |
| 200 | `history.recent_high.52` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 52s | 52 finite observed high slots |
| 201 | `history.recent_high.53` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 53s | 53 finite observed high slots |
| 202 | `history.recent_high.54` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 54s | 54 finite observed high slots |
| 203 | `history.recent_high.55` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 55s | 55 finite observed high slots |
| 204 | `history.recent_high.56` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 56s | 56 finite observed high slots |
| 205 | `history.recent_high.57` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 57s | 57 finite observed high slots |
| 206 | `history.recent_high.58` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 58s | 58 finite observed high slots |
| 207 | `history.recent_high.59` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 59s | 59 finite observed high slots |
| 208 | `history.recent_high.60` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed previous 60s | 60 finite observed high slots |
| 209 | `history.momentum_close.1` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 1s lag | Lag endpoint and current positive mark valid |
| 210 | `history.momentum_close.2` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 2s lag | Lag endpoint and current positive mark valid |
| 211 | `history.momentum_close.3` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 3s lag | Lag endpoint and current positive mark valid |
| 212 | `history.momentum_close.4` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 4s lag | Lag endpoint and current positive mark valid |
| 213 | `history.momentum_close.5` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 5s lag | Lag endpoint and current positive mark valid |
| 214 | `history.momentum_close.6` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 6s lag | Lag endpoint and current positive mark valid |
| 215 | `history.momentum_close.7` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 7s lag | Lag endpoint and current positive mark valid |
| 216 | `history.momentum_close.8` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 8s lag | Lag endpoint and current positive mark valid |
| 217 | `history.momentum_close.9` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 9s lag | Lag endpoint and current positive mark valid |
| 218 | `history.momentum_close.10` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 10s lag | Lag endpoint and current positive mark valid |
| 219 | `history.momentum_close.11` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 11s lag | Lag endpoint and current positive mark valid |
| 220 | `history.momentum_close.12` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 12s lag | Lag endpoint and current positive mark valid |
| 221 | `history.momentum_close.13` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 13s lag | Lag endpoint and current positive mark valid |
| 222 | `history.momentum_close.14` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 14s lag | Lag endpoint and current positive mark valid |
| 223 | `history.momentum_close.15` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 15s lag | Lag endpoint and current positive mark valid |
| 224 | `history.momentum_close.16` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 16s lag | Lag endpoint and current positive mark valid |
| 225 | `history.momentum_close.17` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 17s lag | Lag endpoint and current positive mark valid |
| 226 | `history.momentum_close.18` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 18s lag | Lag endpoint and current positive mark valid |
| 227 | `history.momentum_close.19` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 19s lag | Lag endpoint and current positive mark valid |
| 228 | `history.momentum_close.20` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 20s lag | Lag endpoint and current positive mark valid |
| 229 | `history.momentum_close.21` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 21s lag | Lag endpoint and current positive mark valid |
| 230 | `history.momentum_close.22` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 22s lag | Lag endpoint and current positive mark valid |
| 231 | `history.momentum_close.23` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 23s lag | Lag endpoint and current positive mark valid |
| 232 | `history.momentum_close.24` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 24s lag | Lag endpoint and current positive mark valid |
| 233 | `history.momentum_close.25` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 25s lag | Lag endpoint and current positive mark valid |
| 234 | `history.momentum_close.26` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 26s lag | Lag endpoint and current positive mark valid |
| 235 | `history.momentum_close.27` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 27s lag | Lag endpoint and current positive mark valid |
| 236 | `history.momentum_close.28` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 28s lag | Lag endpoint and current positive mark valid |
| 237 | `history.momentum_close.29` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 29s lag | Lag endpoint and current positive mark valid |
| 238 | `history.momentum_close.30` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 30s lag | Lag endpoint and current positive mark valid |
| 239 | `history.momentum_close.31` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 31s lag | Lag endpoint and current positive mark valid |
| 240 | `history.momentum_close.32` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 32s lag | Lag endpoint and current positive mark valid |
| 241 | `history.momentum_close.33` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 33s lag | Lag endpoint and current positive mark valid |
| 242 | `history.momentum_close.34` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 34s lag | Lag endpoint and current positive mark valid |
| 243 | `history.momentum_close.35` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 35s lag | Lag endpoint and current positive mark valid |
| 244 | `history.momentum_close.36` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 36s lag | Lag endpoint and current positive mark valid |
| 245 | `history.momentum_close.37` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 37s lag | Lag endpoint and current positive mark valid |
| 246 | `history.momentum_close.38` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 38s lag | Lag endpoint and current positive mark valid |
| 247 | `history.momentum_close.39` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 39s lag | Lag endpoint and current positive mark valid |
| 248 | `history.momentum_close.40` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 40s lag | Lag endpoint and current positive mark valid |
| 249 | `history.momentum_close.41` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 41s lag | Lag endpoint and current positive mark valid |
| 250 | `history.momentum_close.42` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 42s lag | Lag endpoint and current positive mark valid |
| 251 | `history.momentum_close.43` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 43s lag | Lag endpoint and current positive mark valid |
| 252 | `history.momentum_close.44` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 44s lag | Lag endpoint and current positive mark valid |
| 253 | `history.momentum_close.45` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 45s lag | Lag endpoint and current positive mark valid |
| 254 | `history.momentum_close.46` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 46s lag | Lag endpoint and current positive mark valid |
| 255 | `history.momentum_close.47` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 47s lag | Lag endpoint and current positive mark valid |
| 256 | `history.momentum_close.48` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 48s lag | Lag endpoint and current positive mark valid |
| 257 | `history.momentum_close.49` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 49s lag | Lag endpoint and current positive mark valid |
| 258 | `history.momentum_close.50` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 50s lag | Lag endpoint and current positive mark valid |
| 259 | `history.momentum_close.51` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 51s lag | Lag endpoint and current positive mark valid |
| 260 | `history.momentum_close.52` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 52s lag | Lag endpoint and current positive mark valid |
| 261 | `history.momentum_close.53` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 53s lag | Lag endpoint and current positive mark valid |
| 262 | `history.momentum_close.54` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 54s lag | Lag endpoint and current positive mark valid |
| 263 | `history.momentum_close.55` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 55s lag | Lag endpoint and current positive mark valid |
| 264 | `history.momentum_close.56` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 56s lag | Lag endpoint and current positive mark valid |
| 265 | `history.momentum_close.57` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 57s lag | Lag endpoint and current positive mark valid |
| 266 | `history.momentum_close.58` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 58s lag | Lag endpoint and current positive mark valid |
| 267 | `history.momentum_close.59` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 59s lag | Lag endpoint and current positive mark valid |
| 268 | `history.momentum_close.60` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Fixed 60s lag | Lag endpoint and current positive mark valid |
| 269 | `history.attention_mean.1` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 1s | Elapsed window available; causal notional; zero activity allowed |
| 270 | `history.attention_mean.2` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 2s | Elapsed window available; causal notional; zero activity allowed |
| 271 | `history.attention_mean.3` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 3s | Elapsed window available; causal notional; zero activity allowed |
| 272 | `history.attention_mean.4` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 4s | Elapsed window available; causal notional; zero activity allowed |
| 273 | `history.attention_mean.5` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 5s | Elapsed window available; causal notional; zero activity allowed |
| 274 | `history.attention_mean.6` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 6s | Elapsed window available; causal notional; zero activity allowed |
| 275 | `history.attention_mean.7` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 7s | Elapsed window available; causal notional; zero activity allowed |
| 276 | `history.attention_mean.8` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 8s | Elapsed window available; causal notional; zero activity allowed |
| 277 | `history.attention_mean.9` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 9s | Elapsed window available; causal notional; zero activity allowed |
| 278 | `history.attention_mean.10` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 10s | Elapsed window available; causal notional; zero activity allowed |
| 279 | `history.attention_mean.11` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 11s | Elapsed window available; causal notional; zero activity allowed |
| 280 | `history.attention_mean.12` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 12s | Elapsed window available; causal notional; zero activity allowed |
| 281 | `history.attention_mean.13` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 13s | Elapsed window available; causal notional; zero activity allowed |
| 282 | `history.attention_mean.14` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 14s | Elapsed window available; causal notional; zero activity allowed |
| 283 | `history.attention_mean.15` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 15s | Elapsed window available; causal notional; zero activity allowed |
| 284 | `history.attention_mean.16` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 16s | Elapsed window available; causal notional; zero activity allowed |
| 285 | `history.attention_mean.17` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 17s | Elapsed window available; causal notional; zero activity allowed |
| 286 | `history.attention_mean.18` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 18s | Elapsed window available; causal notional; zero activity allowed |
| 287 | `history.attention_mean.19` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 19s | Elapsed window available; causal notional; zero activity allowed |
| 288 | `history.attention_mean.20` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 20s | Elapsed window available; causal notional; zero activity allowed |
| 289 | `history.attention_mean.21` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 21s | Elapsed window available; causal notional; zero activity allowed |
| 290 | `history.attention_mean.22` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 22s | Elapsed window available; causal notional; zero activity allowed |
| 291 | `history.attention_mean.23` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 23s | Elapsed window available; causal notional; zero activity allowed |
| 292 | `history.attention_mean.24` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 24s | Elapsed window available; causal notional; zero activity allowed |
| 293 | `history.attention_mean.25` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 25s | Elapsed window available; causal notional; zero activity allowed |
| 294 | `history.attention_mean.26` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 26s | Elapsed window available; causal notional; zero activity allowed |
| 295 | `history.attention_mean.27` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 27s | Elapsed window available; causal notional; zero activity allowed |
| 296 | `history.attention_mean.28` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 28s | Elapsed window available; causal notional; zero activity allowed |
| 297 | `history.attention_mean.29` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 29s | Elapsed window available; causal notional; zero activity allowed |
| 298 | `history.attention_mean.30` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 30s | Elapsed window available; causal notional; zero activity allowed |
| 299 | `history.attention_mean.31` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 31s | Elapsed window available; causal notional; zero activity allowed |
| 300 | `history.attention_mean.32` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 32s | Elapsed window available; causal notional; zero activity allowed |
| 301 | `history.attention_mean.33` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 33s | Elapsed window available; causal notional; zero activity allowed |
| 302 | `history.attention_mean.34` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 34s | Elapsed window available; causal notional; zero activity allowed |
| 303 | `history.attention_mean.35` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 35s | Elapsed window available; causal notional; zero activity allowed |
| 304 | `history.attention_mean.36` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 36s | Elapsed window available; causal notional; zero activity allowed |
| 305 | `history.attention_mean.37` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 37s | Elapsed window available; causal notional; zero activity allowed |
| 306 | `history.attention_mean.38` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 38s | Elapsed window available; causal notional; zero activity allowed |
| 307 | `history.attention_mean.39` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 39s | Elapsed window available; causal notional; zero activity allowed |
| 308 | `history.attention_mean.40` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 40s | Elapsed window available; causal notional; zero activity allowed |
| 309 | `history.attention_mean.41` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 41s | Elapsed window available; causal notional; zero activity allowed |
| 310 | `history.attention_mean.42` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 42s | Elapsed window available; causal notional; zero activity allowed |
| 311 | `history.attention_mean.43` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 43s | Elapsed window available; causal notional; zero activity allowed |
| 312 | `history.attention_mean.44` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 44s | Elapsed window available; causal notional; zero activity allowed |
| 313 | `history.attention_mean.45` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 45s | Elapsed window available; causal notional; zero activity allowed |
| 314 | `history.attention_mean.46` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 46s | Elapsed window available; causal notional; zero activity allowed |
| 315 | `history.attention_mean.47` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 47s | Elapsed window available; causal notional; zero activity allowed |
| 316 | `history.attention_mean.48` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 48s | Elapsed window available; causal notional; zero activity allowed |
| 317 | `history.attention_mean.49` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 49s | Elapsed window available; causal notional; zero activity allowed |
| 318 | `history.attention_mean.50` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 50s | Elapsed window available; causal notional; zero activity allowed |
| 319 | `history.attention_mean.51` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 51s | Elapsed window available; causal notional; zero activity allowed |
| 320 | `history.attention_mean.52` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 52s | Elapsed window available; causal notional; zero activity allowed |
| 321 | `history.attention_mean.53` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 53s | Elapsed window available; causal notional; zero activity allowed |
| 322 | `history.attention_mean.54` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 54s | Elapsed window available; causal notional; zero activity allowed |
| 323 | `history.attention_mean.55` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 55s | Elapsed window available; causal notional; zero activity allowed |
| 324 | `history.attention_mean.56` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 56s | Elapsed window available; causal notional; zero activity allowed |
| 325 | `history.attention_mean.57` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 57s | Elapsed window available; causal notional; zero activity allowed |
| 326 | `history.attention_mean.58` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 58s | Elapsed window available; causal notional; zero activity allowed |
| 327 | `history.attention_mean.59` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 59s | Elapsed window available; causal notional; zero activity allowed |
| 328 | `history.attention_mean.60` | dollars | 0 | 1e+07 | yes | yes | 0 | 100000000000 | E | Fixed previous 60s | Elapsed window available; causal notional; zero activity allowed |
| 329 | `history.average_move.2` | price | 0 | 1000 | no | yes | 0 | 1000 | E | 2s changes; 3 prices | 3 finite consecutive prices |
| 330 | `history.average_move.3` | price | 0 | 1000 | no | yes | 0 | 1000 | E | 3s changes; 4 prices | 4 finite consecutive prices |
| 331 | `history.average_move.4` | price | 0 | 1000 | no | yes | 0 | 1000 | E | 4s changes; 5 prices | 5 finite consecutive prices |
| 332 | `history.average_move.5` | price | 0 | 1000 | no | yes | 0 | 1000 | E | 5s changes; 6 prices | 6 finite consecutive prices |
| 333 | `history.average_move.6` | price | 0 | 1000 | no | yes | 0 | 1000 | E | 6s changes; 7 prices | 7 finite consecutive prices |
| 334 | `history.average_move.7` | price | 0 | 1000 | no | yes | 0 | 1000 | E | 7s changes; 8 prices | 8 finite consecutive prices |
| 335 | `history.average_move.8` | price | 0 | 1000 | no | yes | 0 | 1000 | E | 8s changes; 9 prices | 9 finite consecutive prices |
| 336 | `history.average_move.9` | price | 0 | 1000 | no | yes | 0 | 1000 | E | 9s changes; 10 prices | 10 finite consecutive prices |
| 337 | `history.average_move.10` | price | 0 | 1000 | no | yes | 0 | 1000 | E | 10s changes; 11 prices | 11 finite consecutive prices |
| 338 | `history.average_move.11` | price | 0 | 1000 | no | yes | 0 | 1000 | E | 11s changes; 12 prices | 12 finite consecutive prices |
| 339 | `history.average_move.12` | price | 0 | 1000 | no | yes | 0 | 1000 | E | 12s changes; 13 prices | 13 finite consecutive prices |
| 340 | `history.average_move.13` | price | 0 | 1000 | no | yes | 0 | 1000 | E | 13s changes; 14 prices | 14 finite consecutive prices |
| 341 | `history.average_move.14` | price | 0 | 1000 | no | yes | 0 | 1000 | E | 14s changes; 15 prices | 15 finite consecutive prices |
| 342 | `history.average_move.15` | price | 0 | 1000 | no | yes | 0 | 1000 | E | 15s changes; 16 prices | 16 finite consecutive prices |
| 343 | `history.average_move.16` | price | 0 | 1000 | no | yes | 0 | 1000 | E | 16s changes; 17 prices | 17 finite consecutive prices |
| 344 | `history.average_move.17` | price | 0 | 1000 | no | yes | 0 | 1000 | E | 17s changes; 18 prices | 18 finite consecutive prices |
| 345 | `history.average_move.18` | price | 0 | 1000 | no | yes | 0 | 1000 | E | 18s changes; 19 prices | 19 finite consecutive prices |
| 346 | `history.average_move.19` | price | 0 | 1000 | no | yes | 0 | 1000 | E | 19s changes; 20 prices | 20 finite consecutive prices |
| 347 | `history.average_move.20` | price | 0 | 1000 | no | yes | 0 | 1000 | E | 20s changes; 21 prices | 21 finite consecutive prices |
| 348 | `history.average_move.21` | price | 0 | 1000 | no | yes | 0 | 1000 | E | 21s changes; 22 prices | 22 finite consecutive prices |
| 349 | `history.average_move.22` | price | 0 | 1000 | no | yes | 0 | 1000 | E | 22s changes; 23 prices | 23 finite consecutive prices |
| 350 | `history.average_move.23` | price | 0 | 1000 | no | yes | 0 | 1000 | E | 23s changes; 24 prices | 24 finite consecutive prices |
| 351 | `history.average_move.24` | price | 0 | 1000 | no | yes | 0 | 1000 | E | 24s changes; 25 prices | 25 finite consecutive prices |
| 352 | `history.average_move.25` | price | 0 | 1000 | no | yes | 0 | 1000 | E | 25s changes; 26 prices | 26 finite consecutive prices |
| 353 | `history.average_move.26` | price | 0 | 1000 | no | yes | 0 | 1000 | E | 26s changes; 27 prices | 27 finite consecutive prices |
| 354 | `history.average_move.27` | price | 0 | 1000 | no | yes | 0 | 1000 | E | 27s changes; 28 prices | 28 finite consecutive prices |
| 355 | `history.average_move.28` | price | 0 | 1000 | no | yes | 0 | 1000 | E | 28s changes; 29 prices | 29 finite consecutive prices |
| 356 | `history.average_move.29` | price | 0 | 1000 | no | yes | 0 | 1000 | E | 29s changes; 30 prices | 30 finite consecutive prices |
| 357 | `history.average_move.30` | price | 0 | 1000 | no | yes | 0 | 1000 | E | 30s changes; 31 prices | 31 finite consecutive prices |
| 358 | `history.average_move.31` | price | 0 | 1000 | no | yes | 0 | 1000 | E | 31s changes; 32 prices | 32 finite consecutive prices |
| 359 | `history.average_move.32` | price | 0 | 1000 | no | yes | 0 | 1000 | E | 32s changes; 33 prices | 33 finite consecutive prices |
| 360 | `history.swing_low.1.1` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 3s formation; carried confirmed pivot | 3 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 361 | `history.swing_low.1.2` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 4s formation; carried confirmed pivot | 4 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 362 | `history.swing_low.1.3` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 5s formation; carried confirmed pivot | 5 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 363 | `history.swing_low.1.4` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 6s formation; carried confirmed pivot | 6 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 364 | `history.swing_low.1.5` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 7s formation; carried confirmed pivot | 7 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 365 | `history.swing_low.1.10` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 12s formation; carried confirmed pivot | 12 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 366 | `history.swing_low.1.15` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 17s formation; carried confirmed pivot | 17 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 367 | `history.swing_low.1.20` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 22s formation; carried confirmed pivot | 22 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 368 | `history.swing_low.1.30` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 32s formation; carried confirmed pivot | 32 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 369 | `history.swing_low.1.45` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 47s formation; carried confirmed pivot | 47 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 370 | `history.swing_low.1.60` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 62s formation; carried confirmed pivot | 62 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 371 | `history.swing_low.2.1` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 4s formation; carried confirmed pivot | 4 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 372 | `history.swing_low.2.2` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 5s formation; carried confirmed pivot | 5 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 373 | `history.swing_low.2.3` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 6s formation; carried confirmed pivot | 6 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 374 | `history.swing_low.2.4` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 7s formation; carried confirmed pivot | 7 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 375 | `history.swing_low.2.5` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 8s formation; carried confirmed pivot | 8 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 376 | `history.swing_low.2.10` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 13s formation; carried confirmed pivot | 13 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 377 | `history.swing_low.2.15` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 18s formation; carried confirmed pivot | 18 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 378 | `history.swing_low.2.20` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 23s formation; carried confirmed pivot | 23 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 379 | `history.swing_low.2.30` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 33s formation; carried confirmed pivot | 33 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 380 | `history.swing_low.2.45` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 48s formation; carried confirmed pivot | 48 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 381 | `history.swing_low.2.60` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 63s formation; carried confirmed pivot | 63 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 382 | `history.swing_low.3.1` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 5s formation; carried confirmed pivot | 5 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 383 | `history.swing_low.3.2` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 6s formation; carried confirmed pivot | 6 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 384 | `history.swing_low.3.3` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 7s formation; carried confirmed pivot | 7 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 385 | `history.swing_low.3.4` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 8s formation; carried confirmed pivot | 8 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 386 | `history.swing_low.3.5` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 9s formation; carried confirmed pivot | 9 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 387 | `history.swing_low.3.10` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 14s formation; carried confirmed pivot | 14 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 388 | `history.swing_low.3.15` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 19s formation; carried confirmed pivot | 19 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 389 | `history.swing_low.3.20` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 24s formation; carried confirmed pivot | 24 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 390 | `history.swing_low.3.30` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 34s formation; carried confirmed pivot | 34 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 391 | `history.swing_low.3.45` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 49s formation; carried confirmed pivot | 49 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 392 | `history.swing_low.3.60` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 64s formation; carried confirmed pivot | 64 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 393 | `history.swing_low.4.1` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 6s formation; carried confirmed pivot | 6 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 394 | `history.swing_low.4.2` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 7s formation; carried confirmed pivot | 7 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 395 | `history.swing_low.4.3` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 8s formation; carried confirmed pivot | 8 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 396 | `history.swing_low.4.4` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 9s formation; carried confirmed pivot | 9 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 397 | `history.swing_low.4.5` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 10s formation; carried confirmed pivot | 10 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 398 | `history.swing_low.4.10` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 15s formation; carried confirmed pivot | 15 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 399 | `history.swing_low.4.15` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 20s formation; carried confirmed pivot | 20 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 400 | `history.swing_low.4.20` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 25s formation; carried confirmed pivot | 25 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 401 | `history.swing_low.4.30` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 35s formation; carried confirmed pivot | 35 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 402 | `history.swing_low.4.45` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 50s formation; carried confirmed pivot | 50 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 403 | `history.swing_low.4.60` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 65s formation; carried confirmed pivot | 65 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 404 | `history.swing_low.5.1` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 7s formation; carried confirmed pivot | 7 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 405 | `history.swing_low.5.2` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 8s formation; carried confirmed pivot | 8 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 406 | `history.swing_low.5.3` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 9s formation; carried confirmed pivot | 9 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 407 | `history.swing_low.5.4` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 10s formation; carried confirmed pivot | 10 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 408 | `history.swing_low.5.5` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 11s formation; carried confirmed pivot | 11 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 409 | `history.swing_low.5.10` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 16s formation; carried confirmed pivot | 16 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 410 | `history.swing_low.5.15` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 21s formation; carried confirmed pivot | 21 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 411 | `history.swing_low.5.20` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 26s formation; carried confirmed pivot | 26 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 412 | `history.swing_low.5.30` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 36s formation; carried confirmed pivot | 36 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 413 | `history.swing_low.5.45` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 51s formation; carried confirmed pivot | 51 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 414 | `history.swing_low.5.60` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 66s formation; carried confirmed pivot | 66 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 415 | `history.swing_low.10.1` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 12s formation; carried confirmed pivot | 12 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 416 | `history.swing_low.10.2` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 13s formation; carried confirmed pivot | 13 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 417 | `history.swing_low.10.3` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 14s formation; carried confirmed pivot | 14 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 418 | `history.swing_low.10.4` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 15s formation; carried confirmed pivot | 15 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 419 | `history.swing_low.10.5` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 16s formation; carried confirmed pivot | 16 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 420 | `history.swing_low.10.10` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 21s formation; carried confirmed pivot | 21 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 421 | `history.swing_low.10.15` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 26s formation; carried confirmed pivot | 26 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 422 | `history.swing_low.10.20` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 31s formation; carried confirmed pivot | 31 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 423 | `history.swing_low.10.30` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 41s formation; carried confirmed pivot | 41 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 424 | `history.swing_low.10.45` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 56s formation; carried confirmed pivot | 56 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 425 | `history.swing_low.10.60` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 71s formation; carried confirmed pivot | 71 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 426 | `history.swing_low.15.1` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 17s formation; carried confirmed pivot | 17 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 427 | `history.swing_low.15.2` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 18s formation; carried confirmed pivot | 18 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 428 | `history.swing_low.15.3` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 19s formation; carried confirmed pivot | 19 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 429 | `history.swing_low.15.4` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 20s formation; carried confirmed pivot | 20 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 430 | `history.swing_low.15.5` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 21s formation; carried confirmed pivot | 21 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 431 | `history.swing_low.15.10` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 26s formation; carried confirmed pivot | 26 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 432 | `history.swing_low.15.15` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 31s formation; carried confirmed pivot | 31 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 433 | `history.swing_low.15.20` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 36s formation; carried confirmed pivot | 36 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 434 | `history.swing_low.15.30` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 46s formation; carried confirmed pivot | 46 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 435 | `history.swing_low.15.45` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 61s formation; carried confirmed pivot | 61 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 436 | `history.swing_low.15.60` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 76s formation; carried confirmed pivot | 76 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 437 | `history.swing_low.20.1` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 22s formation; carried confirmed pivot | 22 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 438 | `history.swing_low.20.2` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 23s formation; carried confirmed pivot | 23 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 439 | `history.swing_low.20.3` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 24s formation; carried confirmed pivot | 24 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 440 | `history.swing_low.20.4` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 25s formation; carried confirmed pivot | 25 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 441 | `history.swing_low.20.5` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 26s formation; carried confirmed pivot | 26 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 442 | `history.swing_low.20.10` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 31s formation; carried confirmed pivot | 31 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 443 | `history.swing_low.20.15` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 36s formation; carried confirmed pivot | 36 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 444 | `history.swing_low.20.20` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 41s formation; carried confirmed pivot | 41 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 445 | `history.swing_low.20.30` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 51s formation; carried confirmed pivot | 51 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 446 | `history.swing_low.20.45` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 66s formation; carried confirmed pivot | 66 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 447 | `history.swing_low.20.60` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 81s formation; carried confirmed pivot | 81 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 448 | `history.swing_low.30.1` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 32s formation; carried confirmed pivot | 32 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 449 | `history.swing_low.30.2` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 33s formation; carried confirmed pivot | 33 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 450 | `history.swing_low.30.3` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 34s formation; carried confirmed pivot | 34 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 451 | `history.swing_low.30.4` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 35s formation; carried confirmed pivot | 35 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 452 | `history.swing_low.30.5` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 36s formation; carried confirmed pivot | 36 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 453 | `history.swing_low.30.10` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 41s formation; carried confirmed pivot | 41 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 454 | `history.swing_low.30.15` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 46s formation; carried confirmed pivot | 46 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 455 | `history.swing_low.30.20` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 51s formation; carried confirmed pivot | 51 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 456 | `history.swing_low.30.30` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 61s formation; carried confirmed pivot | 61 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 457 | `history.swing_low.30.45` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 76s formation; carried confirmed pivot | 76 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 458 | `history.swing_low.30.60` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 91s formation; carried confirmed pivot | 91 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 459 | `history.swing_low.45.1` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 47s formation; carried confirmed pivot | 47 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 460 | `history.swing_low.45.2` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 48s formation; carried confirmed pivot | 48 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 461 | `history.swing_low.45.3` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 49s formation; carried confirmed pivot | 49 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 462 | `history.swing_low.45.4` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 50s formation; carried confirmed pivot | 50 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 463 | `history.swing_low.45.5` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 51s formation; carried confirmed pivot | 51 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 464 | `history.swing_low.45.10` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 56s formation; carried confirmed pivot | 56 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 465 | `history.swing_low.45.15` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 61s formation; carried confirmed pivot | 61 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 466 | `history.swing_low.45.20` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 66s formation; carried confirmed pivot | 66 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 467 | `history.swing_low.45.30` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 76s formation; carried confirmed pivot | 76 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 468 | `history.swing_low.45.45` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 91s formation; carried confirmed pivot | 91 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 469 | `history.swing_low.45.60` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 106s formation; carried confirmed pivot | 106 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 470 | `history.swing_low.60.1` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 62s formation; carried confirmed pivot | 62 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 471 | `history.swing_low.60.2` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 63s formation; carried confirmed pivot | 63 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 472 | `history.swing_low.60.3` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 64s formation; carried confirmed pivot | 64 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 473 | `history.swing_low.60.4` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 65s formation; carried confirmed pivot | 65 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 474 | `history.swing_low.60.5` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 66s formation; carried confirmed pivot | 66 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 475 | `history.swing_low.60.10` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 71s formation; carried confirmed pivot | 71 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 476 | `history.swing_low.60.15` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 76s formation; carried confirmed pivot | 76 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 477 | `history.swing_low.60.20` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 81s formation; carried confirmed pivot | 81 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 478 | `history.swing_low.60.30` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 91s formation; carried confirmed pivot | 91 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 479 | `history.swing_low.60.45` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 106s formation; carried confirmed pivot | 106 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 480 | `history.swing_low.60.60` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | 121s formation; carried confirmed pivot | 121 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 481 | `history.recent_high.1.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 1s | 1 finite observed high slots and valid current mark |
| 482 | `history.recent_high.2.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 2s | 2 finite observed high slots and valid current mark |
| 483 | `history.recent_high.3.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 3s | 3 finite observed high slots and valid current mark |
| 484 | `history.recent_high.4.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 4s | 4 finite observed high slots and valid current mark |
| 485 | `history.recent_high.5.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 5s | 5 finite observed high slots and valid current mark |
| 486 | `history.recent_high.6.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 6s | 6 finite observed high slots and valid current mark |
| 487 | `history.recent_high.7.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 7s | 7 finite observed high slots and valid current mark |
| 488 | `history.recent_high.8.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 8s | 8 finite observed high slots and valid current mark |
| 489 | `history.recent_high.9.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 9s | 9 finite observed high slots and valid current mark |
| 490 | `history.recent_high.10.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 10s | 10 finite observed high slots and valid current mark |
| 491 | `history.recent_high.11.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 11s | 11 finite observed high slots and valid current mark |
| 492 | `history.recent_high.12.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 12s | 12 finite observed high slots and valid current mark |
| 493 | `history.recent_high.13.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 13s | 13 finite observed high slots and valid current mark |
| 494 | `history.recent_high.14.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 14s | 14 finite observed high slots and valid current mark |
| 495 | `history.recent_high.15.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 15s | 15 finite observed high slots and valid current mark |
| 496 | `history.recent_high.16.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 16s | 16 finite observed high slots and valid current mark |
| 497 | `history.recent_high.17.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 17s | 17 finite observed high slots and valid current mark |
| 498 | `history.recent_high.18.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 18s | 18 finite observed high slots and valid current mark |
| 499 | `history.recent_high.19.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 19s | 19 finite observed high slots and valid current mark |
| 500 | `history.recent_high.20.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 20s | 20 finite observed high slots and valid current mark |
| 501 | `history.recent_high.21.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 21s | 21 finite observed high slots and valid current mark |
| 502 | `history.recent_high.22.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 22s | 22 finite observed high slots and valid current mark |
| 503 | `history.recent_high.23.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 23s | 23 finite observed high slots and valid current mark |
| 504 | `history.recent_high.24.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 24s | 24 finite observed high slots and valid current mark |
| 505 | `history.recent_high.25.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 25s | 25 finite observed high slots and valid current mark |
| 506 | `history.recent_high.26.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 26s | 26 finite observed high slots and valid current mark |
| 507 | `history.recent_high.27.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 27s | 27 finite observed high slots and valid current mark |
| 508 | `history.recent_high.28.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 28s | 28 finite observed high slots and valid current mark |
| 509 | `history.recent_high.29.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 29s | 29 finite observed high slots and valid current mark |
| 510 | `history.recent_high.30.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 30s | 30 finite observed high slots and valid current mark |
| 511 | `history.recent_high.31.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 31s | 31 finite observed high slots and valid current mark |
| 512 | `history.recent_high.32.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 32s | 32 finite observed high slots and valid current mark |
| 513 | `history.recent_high.33.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 33s | 33 finite observed high slots and valid current mark |
| 514 | `history.recent_high.34.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 34s | 34 finite observed high slots and valid current mark |
| 515 | `history.recent_high.35.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 35s | 35 finite observed high slots and valid current mark |
| 516 | `history.recent_high.36.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 36s | 36 finite observed high slots and valid current mark |
| 517 | `history.recent_high.37.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 37s | 37 finite observed high slots and valid current mark |
| 518 | `history.recent_high.38.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 38s | 38 finite observed high slots and valid current mark |
| 519 | `history.recent_high.39.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 39s | 39 finite observed high slots and valid current mark |
| 520 | `history.recent_high.40.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 40s | 40 finite observed high slots and valid current mark |
| 521 | `history.recent_high.41.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 41s | 41 finite observed high slots and valid current mark |
| 522 | `history.recent_high.42.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 42s | 42 finite observed high slots and valid current mark |
| 523 | `history.recent_high.43.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 43s | 43 finite observed high slots and valid current mark |
| 524 | `history.recent_high.44.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 44s | 44 finite observed high slots and valid current mark |
| 525 | `history.recent_high.45.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 45s | 45 finite observed high slots and valid current mark |
| 526 | `history.recent_high.46.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 46s | 46 finite observed high slots and valid current mark |
| 527 | `history.recent_high.47.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 47s | 47 finite observed high slots and valid current mark |
| 528 | `history.recent_high.48.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 48s | 48 finite observed high slots and valid current mark |
| 529 | `history.recent_high.49.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 49s | 49 finite observed high slots and valid current mark |
| 530 | `history.recent_high.50.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 50s | 50 finite observed high slots and valid current mark |
| 531 | `history.recent_high.51.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 51s | 51 finite observed high slots and valid current mark |
| 532 | `history.recent_high.52.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 52s | 52 finite observed high slots and valid current mark |
| 533 | `history.recent_high.53.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 53s | 53 finite observed high slots and valid current mark |
| 534 | `history.recent_high.54.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 54s | 54 finite observed high slots and valid current mark |
| 535 | `history.recent_high.55.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 55s | 55 finite observed high slots and valid current mark |
| 536 | `history.recent_high.56.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 56s | 56 finite observed high slots and valid current mark |
| 537 | `history.recent_high.57.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 57s | 57 finite observed high slots and valid current mark |
| 538 | `history.recent_high.58.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 58s | 58 finite observed high slots and valid current mark |
| 539 | `history.recent_high.59.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 59s | 59 finite observed high slots and valid current mark |
| 540 | `history.recent_high.60.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed previous 60s | 60 finite observed high slots and valid current mark |
| 541 | `history.momentum_close.1.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 1s lag | Lag endpoint and current positive mark valid |
| 542 | `history.momentum_close.2.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 2s lag | Lag endpoint and current positive mark valid |
| 543 | `history.momentum_close.3.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 3s lag | Lag endpoint and current positive mark valid |
| 544 | `history.momentum_close.4.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 4s lag | Lag endpoint and current positive mark valid |
| 545 | `history.momentum_close.5.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 5s lag | Lag endpoint and current positive mark valid |
| 546 | `history.momentum_close.6.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 6s lag | Lag endpoint and current positive mark valid |
| 547 | `history.momentum_close.7.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 7s lag | Lag endpoint and current positive mark valid |
| 548 | `history.momentum_close.8.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 8s lag | Lag endpoint and current positive mark valid |
| 549 | `history.momentum_close.9.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 9s lag | Lag endpoint and current positive mark valid |
| 550 | `history.momentum_close.10.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 10s lag | Lag endpoint and current positive mark valid |
| 551 | `history.momentum_close.11.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 11s lag | Lag endpoint and current positive mark valid |
| 552 | `history.momentum_close.12.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 12s lag | Lag endpoint and current positive mark valid |
| 553 | `history.momentum_close.13.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 13s lag | Lag endpoint and current positive mark valid |
| 554 | `history.momentum_close.14.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 14s lag | Lag endpoint and current positive mark valid |
| 555 | `history.momentum_close.15.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 15s lag | Lag endpoint and current positive mark valid |
| 556 | `history.momentum_close.16.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 16s lag | Lag endpoint and current positive mark valid |
| 557 | `history.momentum_close.17.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 17s lag | Lag endpoint and current positive mark valid |
| 558 | `history.momentum_close.18.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 18s lag | Lag endpoint and current positive mark valid |
| 559 | `history.momentum_close.19.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 19s lag | Lag endpoint and current positive mark valid |
| 560 | `history.momentum_close.20.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 20s lag | Lag endpoint and current positive mark valid |
| 561 | `history.momentum_close.21.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 21s lag | Lag endpoint and current positive mark valid |
| 562 | `history.momentum_close.22.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 22s lag | Lag endpoint and current positive mark valid |
| 563 | `history.momentum_close.23.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 23s lag | Lag endpoint and current positive mark valid |
| 564 | `history.momentum_close.24.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 24s lag | Lag endpoint and current positive mark valid |
| 565 | `history.momentum_close.25.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 25s lag | Lag endpoint and current positive mark valid |
| 566 | `history.momentum_close.26.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 26s lag | Lag endpoint and current positive mark valid |
| 567 | `history.momentum_close.27.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 27s lag | Lag endpoint and current positive mark valid |
| 568 | `history.momentum_close.28.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 28s lag | Lag endpoint and current positive mark valid |
| 569 | `history.momentum_close.29.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 29s lag | Lag endpoint and current positive mark valid |
| 570 | `history.momentum_close.30.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 30s lag | Lag endpoint and current positive mark valid |
| 571 | `history.momentum_close.31.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 31s lag | Lag endpoint and current positive mark valid |
| 572 | `history.momentum_close.32.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 32s lag | Lag endpoint and current positive mark valid |
| 573 | `history.momentum_close.33.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 33s lag | Lag endpoint and current positive mark valid |
| 574 | `history.momentum_close.34.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 34s lag | Lag endpoint and current positive mark valid |
| 575 | `history.momentum_close.35.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 35s lag | Lag endpoint and current positive mark valid |
| 576 | `history.momentum_close.36.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 36s lag | Lag endpoint and current positive mark valid |
| 577 | `history.momentum_close.37.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 37s lag | Lag endpoint and current positive mark valid |
| 578 | `history.momentum_close.38.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 38s lag | Lag endpoint and current positive mark valid |
| 579 | `history.momentum_close.39.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 39s lag | Lag endpoint and current positive mark valid |
| 580 | `history.momentum_close.40.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 40s lag | Lag endpoint and current positive mark valid |
| 581 | `history.momentum_close.41.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 41s lag | Lag endpoint and current positive mark valid |
| 582 | `history.momentum_close.42.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 42s lag | Lag endpoint and current positive mark valid |
| 583 | `history.momentum_close.43.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 43s lag | Lag endpoint and current positive mark valid |
| 584 | `history.momentum_close.44.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 44s lag | Lag endpoint and current positive mark valid |
| 585 | `history.momentum_close.45.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 45s lag | Lag endpoint and current positive mark valid |
| 586 | `history.momentum_close.46.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 46s lag | Lag endpoint and current positive mark valid |
| 587 | `history.momentum_close.47.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 47s lag | Lag endpoint and current positive mark valid |
| 588 | `history.momentum_close.48.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 48s lag | Lag endpoint and current positive mark valid |
| 589 | `history.momentum_close.49.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 49s lag | Lag endpoint and current positive mark valid |
| 590 | `history.momentum_close.50.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 50s lag | Lag endpoint and current positive mark valid |
| 591 | `history.momentum_close.51.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 51s lag | Lag endpoint and current positive mark valid |
| 592 | `history.momentum_close.52.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 52s lag | Lag endpoint and current positive mark valid |
| 593 | `history.momentum_close.53.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 53s lag | Lag endpoint and current positive mark valid |
| 594 | `history.momentum_close.54.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 54s lag | Lag endpoint and current positive mark valid |
| 595 | `history.momentum_close.55.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 55s lag | Lag endpoint and current positive mark valid |
| 596 | `history.momentum_close.56.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 56s lag | Lag endpoint and current positive mark valid |
| 597 | `history.momentum_close.57.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 57s lag | Lag endpoint and current positive mark valid |
| 598 | `history.momentum_close.58.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 58s lag | Lag endpoint and current positive mark valid |
| 599 | `history.momentum_close.59.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 59s lag | Lag endpoint and current positive mark valid |
| 600 | `history.momentum_close.60.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Fixed 60s lag | Lag endpoint and current positive mark valid |
| 601 | `history.average_move.2.fraction_of_close` | ratio | 0 | 0.5 | yes | yes | 0 | 4 | E | 2s changes; 3 prices | 3 finite consecutive prices and positive current mark |
| 602 | `history.average_move.3.fraction_of_close` | ratio | 0 | 0.5 | yes | yes | 0 | 4 | E | 3s changes; 4 prices | 4 finite consecutive prices and positive current mark |
| 603 | `history.average_move.4.fraction_of_close` | ratio | 0 | 0.5 | yes | yes | 0 | 4 | E | 4s changes; 5 prices | 5 finite consecutive prices and positive current mark |
| 604 | `history.average_move.5.fraction_of_close` | ratio | 0 | 0.5 | yes | yes | 0 | 4 | E | 5s changes; 6 prices | 6 finite consecutive prices and positive current mark |
| 605 | `history.average_move.6.fraction_of_close` | ratio | 0 | 0.5 | yes | yes | 0 | 4 | E | 6s changes; 7 prices | 7 finite consecutive prices and positive current mark |
| 606 | `history.average_move.7.fraction_of_close` | ratio | 0 | 0.5 | yes | yes | 0 | 4 | E | 7s changes; 8 prices | 8 finite consecutive prices and positive current mark |
| 607 | `history.average_move.8.fraction_of_close` | ratio | 0 | 0.5 | yes | yes | 0 | 4 | E | 8s changes; 9 prices | 9 finite consecutive prices and positive current mark |
| 608 | `history.average_move.9.fraction_of_close` | ratio | 0 | 0.5 | yes | yes | 0 | 4 | E | 9s changes; 10 prices | 10 finite consecutive prices and positive current mark |
| 609 | `history.average_move.10.fraction_of_close` | ratio | 0 | 0.5 | yes | yes | 0 | 4 | E | 10s changes; 11 prices | 11 finite consecutive prices and positive current mark |
| 610 | `history.average_move.11.fraction_of_close` | ratio | 0 | 0.5 | yes | yes | 0 | 4 | E | 11s changes; 12 prices | 12 finite consecutive prices and positive current mark |
| 611 | `history.average_move.12.fraction_of_close` | ratio | 0 | 0.5 | yes | yes | 0 | 4 | E | 12s changes; 13 prices | 13 finite consecutive prices and positive current mark |
| 612 | `history.average_move.13.fraction_of_close` | ratio | 0 | 0.5 | yes | yes | 0 | 4 | E | 13s changes; 14 prices | 14 finite consecutive prices and positive current mark |
| 613 | `history.average_move.14.fraction_of_close` | ratio | 0 | 0.5 | yes | yes | 0 | 4 | E | 14s changes; 15 prices | 15 finite consecutive prices and positive current mark |
| 614 | `history.average_move.15.fraction_of_close` | ratio | 0 | 0.5 | yes | yes | 0 | 4 | E | 15s changes; 16 prices | 16 finite consecutive prices and positive current mark |
| 615 | `history.average_move.16.fraction_of_close` | ratio | 0 | 0.5 | yes | yes | 0 | 4 | E | 16s changes; 17 prices | 17 finite consecutive prices and positive current mark |
| 616 | `history.average_move.17.fraction_of_close` | ratio | 0 | 0.5 | yes | yes | 0 | 4 | E | 17s changes; 18 prices | 18 finite consecutive prices and positive current mark |
| 617 | `history.average_move.18.fraction_of_close` | ratio | 0 | 0.5 | yes | yes | 0 | 4 | E | 18s changes; 19 prices | 19 finite consecutive prices and positive current mark |
| 618 | `history.average_move.19.fraction_of_close` | ratio | 0 | 0.5 | yes | yes | 0 | 4 | E | 19s changes; 20 prices | 20 finite consecutive prices and positive current mark |
| 619 | `history.average_move.20.fraction_of_close` | ratio | 0 | 0.5 | yes | yes | 0 | 4 | E | 20s changes; 21 prices | 21 finite consecutive prices and positive current mark |
| 620 | `history.average_move.21.fraction_of_close` | ratio | 0 | 0.5 | yes | yes | 0 | 4 | E | 21s changes; 22 prices | 22 finite consecutive prices and positive current mark |
| 621 | `history.average_move.22.fraction_of_close` | ratio | 0 | 0.5 | yes | yes | 0 | 4 | E | 22s changes; 23 prices | 23 finite consecutive prices and positive current mark |
| 622 | `history.average_move.23.fraction_of_close` | ratio | 0 | 0.5 | yes | yes | 0 | 4 | E | 23s changes; 24 prices | 24 finite consecutive prices and positive current mark |
| 623 | `history.average_move.24.fraction_of_close` | ratio | 0 | 0.5 | yes | yes | 0 | 4 | E | 24s changes; 25 prices | 25 finite consecutive prices and positive current mark |
| 624 | `history.average_move.25.fraction_of_close` | ratio | 0 | 0.5 | yes | yes | 0 | 4 | E | 25s changes; 26 prices | 26 finite consecutive prices and positive current mark |
| 625 | `history.average_move.26.fraction_of_close` | ratio | 0 | 0.5 | yes | yes | 0 | 4 | E | 26s changes; 27 prices | 27 finite consecutive prices and positive current mark |
| 626 | `history.average_move.27.fraction_of_close` | ratio | 0 | 0.5 | yes | yes | 0 | 4 | E | 27s changes; 28 prices | 28 finite consecutive prices and positive current mark |
| 627 | `history.average_move.28.fraction_of_close` | ratio | 0 | 0.5 | yes | yes | 0 | 4 | E | 28s changes; 29 prices | 29 finite consecutive prices and positive current mark |
| 628 | `history.average_move.29.fraction_of_close` | ratio | 0 | 0.5 | yes | yes | 0 | 4 | E | 29s changes; 30 prices | 30 finite consecutive prices and positive current mark |
| 629 | `history.average_move.30.fraction_of_close` | ratio | 0 | 0.5 | yes | yes | 0 | 4 | E | 30s changes; 31 prices | 31 finite consecutive prices and positive current mark |
| 630 | `history.average_move.31.fraction_of_close` | ratio | 0 | 0.5 | yes | yes | 0 | 4 | E | 31s changes; 32 prices | 32 finite consecutive prices and positive current mark |
| 631 | `history.average_move.32.fraction_of_close` | ratio | 0 | 0.5 | yes | yes | 0 | 4 | E | 32s changes; 33 prices | 33 finite consecutive prices and positive current mark |
| 632 | `history.swing_low.1.1.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 3s formation; carried confirmed pivot | 3 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 633 | `history.swing_low.1.2.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 4s formation; carried confirmed pivot | 4 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 634 | `history.swing_low.1.3.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 5s formation; carried confirmed pivot | 5 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 635 | `history.swing_low.1.4.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 6s formation; carried confirmed pivot | 6 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 636 | `history.swing_low.1.5.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 7s formation; carried confirmed pivot | 7 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 637 | `history.swing_low.1.10.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 12s formation; carried confirmed pivot | 12 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 638 | `history.swing_low.1.15.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 17s formation; carried confirmed pivot | 17 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 639 | `history.swing_low.1.20.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 22s formation; carried confirmed pivot | 22 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 640 | `history.swing_low.1.30.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 32s formation; carried confirmed pivot | 32 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 641 | `history.swing_low.1.45.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 47s formation; carried confirmed pivot | 47 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 642 | `history.swing_low.1.60.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 62s formation; carried confirmed pivot | 62 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 643 | `history.swing_low.2.1.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 4s formation; carried confirmed pivot | 4 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 644 | `history.swing_low.2.2.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 5s formation; carried confirmed pivot | 5 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 645 | `history.swing_low.2.3.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 6s formation; carried confirmed pivot | 6 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 646 | `history.swing_low.2.4.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 7s formation; carried confirmed pivot | 7 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 647 | `history.swing_low.2.5.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 8s formation; carried confirmed pivot | 8 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 648 | `history.swing_low.2.10.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 13s formation; carried confirmed pivot | 13 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 649 | `history.swing_low.2.15.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 18s formation; carried confirmed pivot | 18 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 650 | `history.swing_low.2.20.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 23s formation; carried confirmed pivot | 23 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 651 | `history.swing_low.2.30.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 33s formation; carried confirmed pivot | 33 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 652 | `history.swing_low.2.45.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 48s formation; carried confirmed pivot | 48 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 653 | `history.swing_low.2.60.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 63s formation; carried confirmed pivot | 63 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 654 | `history.swing_low.3.1.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 5s formation; carried confirmed pivot | 5 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 655 | `history.swing_low.3.2.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 6s formation; carried confirmed pivot | 6 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 656 | `history.swing_low.3.3.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 7s formation; carried confirmed pivot | 7 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 657 | `history.swing_low.3.4.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 8s formation; carried confirmed pivot | 8 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 658 | `history.swing_low.3.5.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 9s formation; carried confirmed pivot | 9 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 659 | `history.swing_low.3.10.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 14s formation; carried confirmed pivot | 14 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 660 | `history.swing_low.3.15.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 19s formation; carried confirmed pivot | 19 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 661 | `history.swing_low.3.20.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 24s formation; carried confirmed pivot | 24 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 662 | `history.swing_low.3.30.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 34s formation; carried confirmed pivot | 34 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 663 | `history.swing_low.3.45.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 49s formation; carried confirmed pivot | 49 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 664 | `history.swing_low.3.60.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 64s formation; carried confirmed pivot | 64 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 665 | `history.swing_low.4.1.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 6s formation; carried confirmed pivot | 6 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 666 | `history.swing_low.4.2.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 7s formation; carried confirmed pivot | 7 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 667 | `history.swing_low.4.3.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 8s formation; carried confirmed pivot | 8 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 668 | `history.swing_low.4.4.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 9s formation; carried confirmed pivot | 9 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 669 | `history.swing_low.4.5.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 10s formation; carried confirmed pivot | 10 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 670 | `history.swing_low.4.10.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 15s formation; carried confirmed pivot | 15 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 671 | `history.swing_low.4.15.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 20s formation; carried confirmed pivot | 20 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 672 | `history.swing_low.4.20.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 25s formation; carried confirmed pivot | 25 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 673 | `history.swing_low.4.30.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 35s formation; carried confirmed pivot | 35 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 674 | `history.swing_low.4.45.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 50s formation; carried confirmed pivot | 50 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 675 | `history.swing_low.4.60.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 65s formation; carried confirmed pivot | 65 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 676 | `history.swing_low.5.1.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 7s formation; carried confirmed pivot | 7 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 677 | `history.swing_low.5.2.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 8s formation; carried confirmed pivot | 8 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 678 | `history.swing_low.5.3.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 9s formation; carried confirmed pivot | 9 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 679 | `history.swing_low.5.4.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 10s formation; carried confirmed pivot | 10 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 680 | `history.swing_low.5.5.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 11s formation; carried confirmed pivot | 11 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 681 | `history.swing_low.5.10.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 16s formation; carried confirmed pivot | 16 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 682 | `history.swing_low.5.15.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 21s formation; carried confirmed pivot | 21 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 683 | `history.swing_low.5.20.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 26s formation; carried confirmed pivot | 26 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 684 | `history.swing_low.5.30.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 36s formation; carried confirmed pivot | 36 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 685 | `history.swing_low.5.45.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 51s formation; carried confirmed pivot | 51 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 686 | `history.swing_low.5.60.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 66s formation; carried confirmed pivot | 66 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 687 | `history.swing_low.10.1.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 12s formation; carried confirmed pivot | 12 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 688 | `history.swing_low.10.2.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 13s formation; carried confirmed pivot | 13 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 689 | `history.swing_low.10.3.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 14s formation; carried confirmed pivot | 14 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 690 | `history.swing_low.10.4.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 15s formation; carried confirmed pivot | 15 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 691 | `history.swing_low.10.5.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 16s formation; carried confirmed pivot | 16 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 692 | `history.swing_low.10.10.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 21s formation; carried confirmed pivot | 21 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 693 | `history.swing_low.10.15.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 26s formation; carried confirmed pivot | 26 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 694 | `history.swing_low.10.20.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 31s formation; carried confirmed pivot | 31 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 695 | `history.swing_low.10.30.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 41s formation; carried confirmed pivot | 41 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 696 | `history.swing_low.10.45.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 56s formation; carried confirmed pivot | 56 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 697 | `history.swing_low.10.60.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 71s formation; carried confirmed pivot | 71 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 698 | `history.swing_low.15.1.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 17s formation; carried confirmed pivot | 17 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 699 | `history.swing_low.15.2.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 18s formation; carried confirmed pivot | 18 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 700 | `history.swing_low.15.3.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 19s formation; carried confirmed pivot | 19 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 701 | `history.swing_low.15.4.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 20s formation; carried confirmed pivot | 20 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 702 | `history.swing_low.15.5.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 21s formation; carried confirmed pivot | 21 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 703 | `history.swing_low.15.10.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 26s formation; carried confirmed pivot | 26 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 704 | `history.swing_low.15.15.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 31s formation; carried confirmed pivot | 31 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 705 | `history.swing_low.15.20.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 36s formation; carried confirmed pivot | 36 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 706 | `history.swing_low.15.30.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 46s formation; carried confirmed pivot | 46 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 707 | `history.swing_low.15.45.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 61s formation; carried confirmed pivot | 61 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 708 | `history.swing_low.15.60.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 76s formation; carried confirmed pivot | 76 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 709 | `history.swing_low.20.1.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 22s formation; carried confirmed pivot | 22 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 710 | `history.swing_low.20.2.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 23s formation; carried confirmed pivot | 23 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 711 | `history.swing_low.20.3.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 24s formation; carried confirmed pivot | 24 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 712 | `history.swing_low.20.4.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 25s formation; carried confirmed pivot | 25 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 713 | `history.swing_low.20.5.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 26s formation; carried confirmed pivot | 26 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 714 | `history.swing_low.20.10.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 31s formation; carried confirmed pivot | 31 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 715 | `history.swing_low.20.15.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 36s formation; carried confirmed pivot | 36 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 716 | `history.swing_low.20.20.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 41s formation; carried confirmed pivot | 41 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 717 | `history.swing_low.20.30.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 51s formation; carried confirmed pivot | 51 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 718 | `history.swing_low.20.45.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 66s formation; carried confirmed pivot | 66 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 719 | `history.swing_low.20.60.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 81s formation; carried confirmed pivot | 81 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 720 | `history.swing_low.30.1.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 32s formation; carried confirmed pivot | 32 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 721 | `history.swing_low.30.2.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 33s formation; carried confirmed pivot | 33 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 722 | `history.swing_low.30.3.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 34s formation; carried confirmed pivot | 34 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 723 | `history.swing_low.30.4.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 35s formation; carried confirmed pivot | 35 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 724 | `history.swing_low.30.5.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 36s formation; carried confirmed pivot | 36 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 725 | `history.swing_low.30.10.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 41s formation; carried confirmed pivot | 41 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 726 | `history.swing_low.30.15.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 46s formation; carried confirmed pivot | 46 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 727 | `history.swing_low.30.20.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 51s formation; carried confirmed pivot | 51 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 728 | `history.swing_low.30.30.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 61s formation; carried confirmed pivot | 61 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 729 | `history.swing_low.30.45.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 76s formation; carried confirmed pivot | 76 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 730 | `history.swing_low.30.60.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 91s formation; carried confirmed pivot | 91 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 731 | `history.swing_low.45.1.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 47s formation; carried confirmed pivot | 47 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 732 | `history.swing_low.45.2.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 48s formation; carried confirmed pivot | 48 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 733 | `history.swing_low.45.3.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 49s formation; carried confirmed pivot | 49 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 734 | `history.swing_low.45.4.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 50s formation; carried confirmed pivot | 50 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 735 | `history.swing_low.45.5.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 51s formation; carried confirmed pivot | 51 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 736 | `history.swing_low.45.10.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 56s formation; carried confirmed pivot | 56 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 737 | `history.swing_low.45.15.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 61s formation; carried confirmed pivot | 61 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 738 | `history.swing_low.45.20.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 66s formation; carried confirmed pivot | 66 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 739 | `history.swing_low.45.30.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 76s formation; carried confirmed pivot | 76 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 740 | `history.swing_low.45.45.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 91s formation; carried confirmed pivot | 91 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 741 | `history.swing_low.45.60.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 106s formation; carried confirmed pivot | 106 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 742 | `history.swing_low.60.1.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 62s formation; carried confirmed pivot | 62 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 743 | `history.swing_low.60.2.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 63s formation; carried confirmed pivot | 63 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 744 | `history.swing_low.60.3.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 64s formation; carried confirmed pivot | 64 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 745 | `history.swing_low.60.4.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 65s formation; carried confirmed pivot | 65 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 746 | `history.swing_low.60.5.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 66s formation; carried confirmed pivot | 66 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 747 | `history.swing_low.60.10.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 71s formation; carried confirmed pivot | 71 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 748 | `history.swing_low.60.15.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 76s formation; carried confirmed pivot | 76 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 749 | `history.swing_low.60.20.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 81s formation; carried confirmed pivot | 81 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 750 | `history.swing_low.60.30.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 91s formation; carried confirmed pivot | 91 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 751 | `history.swing_low.60.45.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 106s formation; carried confirmed pivot | 106 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 752 | `history.swing_low.60.60.relative_to_close` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | 121s formation; carried confirmed pivot | 121 consecutive observed slots; prior-clock confirmation; age may exceed window |
| 753 | `market.bid_relative` | ratio | -0.1 | 0.1 | yes | yes | -0.99 | 9 | E | Current mapped source row; no extra bar history | Causal source validity; original quote features may require age<=1s |
| 754 | `market.ask_relative` | ratio | -0.1 | 0.1 | yes | yes | -0.99 | 9 | E | Current mapped source row; no extra bar history | Causal source validity; original quote features may require age<=1s |
| 755 | `market.spread_fraction` | ratio | 0 | 0.1 | yes | yes | 0 | 9 | E | Current mapped source row; no extra bar history | Causal source validity; original quote features may require age<=1s |
| 756 | `market.quote_age_seconds` | duration | 0 | 3600 | yes | no | — | — | forbidden | N/A - forbidden | Cannot be requested |
| 757 | `market.log_execution_volume` | log_shares | 0 | 30 | yes | yes | 0 | 20.723266 | E | Current mapped source row; no extra bar history | Causal source validity; original quote features may require age<=1s |
| 758 | `market.log_execution_notional` | log_dollars | 0 | 30 | yes | yes | 0 | 25.328436 | E | Current mapped source row; no extra bar history | Causal source validity; original quote features may require age<=1s |
| 759 | `market.log_execution_trade_count` | log_count | 0 | 20 | yes | yes | 0 | 13.815512 | E | Current mapped source row; no extra bar history | Causal source validity; original quote features may require age<=1s |
| 760 | `market.bid` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Current mapped source row; no extra history | Positive valid causal book for bid/ask; nonnegative spread; source mask for close; resolve F07 |
| 761 | `market.ask` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Current mapped source row; no extra history | Positive valid causal book for bid/ask; nonnegative spread; source mask for close; resolve F07 |
| 762 | `market.spread` | price | 0 | 100 | no | yes | 0 | 1000 | E | Current mapped source row; no extra history | Positive valid causal book for bid/ask; nonnegative spread; source mask for close; resolve F07 |
| 763 | `market.close` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Current mapped source row; no extra history | Positive valid causal book for bid/ask; nonnegative spread; source mask for close; resolve F07 |
| 764 | `indicator.ema.2` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Period 2; certified running state | At least 2 valid closes; finite state; no raw-history query |
| 765 | `indicator.ema.2.relative` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Period 2; certified running state | At least 2 valid closes; finite state; no raw-history query |
| 766 | `indicator.ema.3` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Period 3; certified running state | At least 3 valid closes; finite state; no raw-history query |
| 767 | `indicator.ema.3.relative` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Period 3; certified running state | At least 3 valid closes; finite state; no raw-history query |
| 768 | `indicator.ema.5` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Period 5; certified running state | At least 5 valid closes; finite state; no raw-history query |
| 769 | `indicator.ema.5.relative` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Period 5; certified running state | At least 5 valid closes; finite state; no raw-history query |
| 770 | `indicator.ema.7` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Period 7; certified running state | At least 7 valid closes; finite state; no raw-history query |
| 771 | `indicator.ema.7.relative` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Period 7; certified running state | At least 7 valid closes; finite state; no raw-history query |
| 772 | `indicator.ema.9` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Period 9; certified running state | At least 9 valid closes; finite state; no raw-history query |
| 773 | `indicator.ema.9.relative` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Period 9; certified running state | At least 9 valid closes; finite state; no raw-history query |
| 774 | `indicator.ema.10` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Period 10; certified running state | At least 10 valid closes; finite state; no raw-history query |
| 775 | `indicator.ema.10.relative` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Period 10; certified running state | At least 10 valid closes; finite state; no raw-history query |
| 776 | `indicator.ema.12` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Period 12; certified running state | At least 12 valid closes; finite state; no raw-history query |
| 777 | `indicator.ema.12.relative` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Period 12; certified running state | At least 12 valid closes; finite state; no raw-history query |
| 778 | `indicator.ema.14` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Period 14; certified running state | At least 14 valid closes; finite state; no raw-history query |
| 779 | `indicator.ema.14.relative` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Period 14; certified running state | At least 14 valid closes; finite state; no raw-history query |
| 780 | `indicator.ema.15` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Period 15; certified running state | At least 15 valid closes; finite state; no raw-history query |
| 781 | `indicator.ema.15.relative` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Period 15; certified running state | At least 15 valid closes; finite state; no raw-history query |
| 782 | `indicator.ema.20` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Period 20; certified running state | At least 20 valid closes; finite state; no raw-history query |
| 783 | `indicator.ema.20.relative` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Period 20; certified running state | At least 20 valid closes; finite state; no raw-history query |
| 784 | `indicator.ema.26` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Period 26; certified running state | At least 26 valid closes; finite state; no raw-history query |
| 785 | `indicator.ema.26.relative` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Period 26; certified running state | At least 26 valid closes; finite state; no raw-history query |
| 786 | `indicator.ema.30` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Period 30; certified running state | At least 30 valid closes; finite state; no raw-history query |
| 787 | `indicator.ema.30.relative` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Period 30; certified running state | At least 30 valid closes; finite state; no raw-history query |
| 788 | `indicator.ema.45` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Period 45; certified running state | At least 45 valid closes; finite state; no raw-history query |
| 789 | `indicator.ema.45.relative` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Period 45; certified running state | At least 45 valid closes; finite state; no raw-history query |
| 790 | `indicator.ema.50` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Period 50; certified running state | At least 50 valid closes; finite state; no raw-history query |
| 791 | `indicator.ema.50.relative` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Period 50; certified running state | At least 50 valid closes; finite state; no raw-history query |
| 792 | `indicator.ema.60` | price | 0 | 1000 | no | yes | 0.0001 | 10000 | E | Period 60; certified running state | At least 60 valid closes; finite state; no raw-history query |
| 793 | `indicator.ema.60.relative` | ratio | -0.5 | 0.5 | yes | yes | -0.99 | 9 | E | Period 60; certified running state | At least 60 valid closes; finite state; no raw-history query |
| 794 | `indicator.rsi.2` | ratio | 0 | 1 | yes | yes | 0 | 1 | M | Period 2; certified running state | At least 2 price changes (3 valid closes); finite state |
| 795 | `indicator.rsi.3` | ratio | 0 | 1 | yes | yes | 0 | 1 | M | Period 3; certified running state | At least 3 price changes (4 valid closes); finite state |
| 796 | `indicator.rsi.5` | ratio | 0 | 1 | yes | yes | 0 | 1 | M | Period 5; certified running state | At least 5 price changes (6 valid closes); finite state |
| 797 | `indicator.rsi.7` | ratio | 0 | 1 | yes | yes | 0 | 1 | M | Period 7; certified running state | At least 7 price changes (8 valid closes); finite state |
| 798 | `indicator.rsi.9` | ratio | 0 | 1 | yes | yes | 0 | 1 | M | Period 9; certified running state | At least 9 price changes (10 valid closes); finite state |
| 799 | `indicator.rsi.10` | ratio | 0 | 1 | yes | yes | 0 | 1 | M | Period 10; certified running state | At least 10 price changes (11 valid closes); finite state |
| 800 | `indicator.rsi.12` | ratio | 0 | 1 | yes | yes | 0 | 1 | M | Period 12; certified running state | At least 12 price changes (13 valid closes); finite state |
| 801 | `indicator.rsi.14` | ratio | 0 | 1 | yes | yes | 0 | 1 | M | Period 14; certified running state | At least 14 price changes (15 valid closes); finite state |
| 802 | `indicator.rsi.15` | ratio | 0 | 1 | yes | yes | 0 | 1 | M | Period 15; certified running state | At least 15 price changes (16 valid closes); finite state |
| 803 | `indicator.rsi.20` | ratio | 0 | 1 | yes | yes | 0 | 1 | M | Period 20; certified running state | At least 20 price changes (21 valid closes); finite state |
| 804 | `indicator.rsi.26` | ratio | 0 | 1 | yes | yes | 0 | 1 | M | Period 26; certified running state | At least 26 price changes (27 valid closes); finite state |
| 805 | `indicator.rsi.30` | ratio | 0 | 1 | yes | yes | 0 | 1 | M | Period 30; certified running state | At least 30 price changes (31 valid closes); finite state |
| 806 | `indicator.rsi.45` | ratio | 0 | 1 | yes | yes | 0 | 1 | M | Period 45; certified running state | At least 45 price changes (46 valid closes); finite state |
| 807 | `indicator.rsi.50` | ratio | 0 | 1 | yes | yes | 0 | 1 | M | Period 50; certified running state | At least 50 price changes (51 valid closes); finite state |
| 808 | `indicator.rsi.60` | ratio | 0 | 1 | yes | yes | 0 | 1 | M | Period 60; certified running state | At least 60 price changes (61 valid closes); finite state |
| 809 | `indicator.atr.2` | price | 0 | 1000 | no | yes | 0 | 1000 | E | Period 2; certified running state | At least 2 valid extreme observations; finite state |
| 810 | `indicator.atr.2.relative` | ratio | 0 | 1 | yes | yes | 0 | 4 | E | Period 2; certified running state | At least 2 valid extreme observations; finite state |
| 811 | `indicator.atr.3` | price | 0 | 1000 | no | yes | 0 | 1000 | E | Period 3; certified running state | At least 3 valid extreme observations; finite state |
| 812 | `indicator.atr.3.relative` | ratio | 0 | 1 | yes | yes | 0 | 4 | E | Period 3; certified running state | At least 3 valid extreme observations; finite state |
| 813 | `indicator.atr.5` | price | 0 | 1000 | no | yes | 0 | 1000 | E | Period 5; certified running state | At least 5 valid extreme observations; finite state |
| 814 | `indicator.atr.5.relative` | ratio | 0 | 1 | yes | yes | 0 | 4 | E | Period 5; certified running state | At least 5 valid extreme observations; finite state |
| 815 | `indicator.atr.7` | price | 0 | 1000 | no | yes | 0 | 1000 | E | Period 7; certified running state | At least 7 valid extreme observations; finite state |
| 816 | `indicator.atr.7.relative` | ratio | 0 | 1 | yes | yes | 0 | 4 | E | Period 7; certified running state | At least 7 valid extreme observations; finite state |
| 817 | `indicator.atr.9` | price | 0 | 1000 | no | yes | 0 | 1000 | E | Period 9; certified running state | At least 9 valid extreme observations; finite state |
| 818 | `indicator.atr.9.relative` | ratio | 0 | 1 | yes | yes | 0 | 4 | E | Period 9; certified running state | At least 9 valid extreme observations; finite state |
| 819 | `indicator.atr.10` | price | 0 | 1000 | no | yes | 0 | 1000 | E | Period 10; certified running state | At least 10 valid extreme observations; finite state |
| 820 | `indicator.atr.10.relative` | ratio | 0 | 1 | yes | yes | 0 | 4 | E | Period 10; certified running state | At least 10 valid extreme observations; finite state |
| 821 | `indicator.atr.12` | price | 0 | 1000 | no | yes | 0 | 1000 | E | Period 12; certified running state | At least 12 valid extreme observations; finite state |
| 822 | `indicator.atr.12.relative` | ratio | 0 | 1 | yes | yes | 0 | 4 | E | Period 12; certified running state | At least 12 valid extreme observations; finite state |
| 823 | `indicator.atr.14` | price | 0 | 1000 | no | yes | 0 | 1000 | E | Period 14; certified running state | At least 14 valid extreme observations; finite state |
| 824 | `indicator.atr.14.relative` | ratio | 0 | 1 | yes | yes | 0 | 4 | E | Period 14; certified running state | At least 14 valid extreme observations; finite state |
| 825 | `indicator.atr.15` | price | 0 | 1000 | no | yes | 0 | 1000 | E | Period 15; certified running state | At least 15 valid extreme observations; finite state |
| 826 | `indicator.atr.15.relative` | ratio | 0 | 1 | yes | yes | 0 | 4 | E | Period 15; certified running state | At least 15 valid extreme observations; finite state |
| 827 | `indicator.atr.20` | price | 0 | 1000 | no | yes | 0 | 1000 | E | Period 20; certified running state | At least 20 valid extreme observations; finite state |
| 828 | `indicator.atr.20.relative` | ratio | 0 | 1 | yes | yes | 0 | 4 | E | Period 20; certified running state | At least 20 valid extreme observations; finite state |
| 829 | `indicator.atr.26` | price | 0 | 1000 | no | yes | 0 | 1000 | E | Period 26; certified running state | At least 26 valid extreme observations; finite state |
| 830 | `indicator.atr.26.relative` | ratio | 0 | 1 | yes | yes | 0 | 4 | E | Period 26; certified running state | At least 26 valid extreme observations; finite state |
| 831 | `indicator.atr.30` | price | 0 | 1000 | no | yes | 0 | 1000 | E | Period 30; certified running state | At least 30 valid extreme observations; finite state |
| 832 | `indicator.atr.30.relative` | ratio | 0 | 1 | yes | yes | 0 | 4 | E | Period 30; certified running state | At least 30 valid extreme observations; finite state |
| 833 | `indicator.atr.45` | price | 0 | 1000 | no | yes | 0 | 1000 | E | Period 45; certified running state | At least 45 valid extreme observations; finite state |
| 834 | `indicator.atr.45.relative` | ratio | 0 | 1 | yes | yes | 0 | 4 | E | Period 45; certified running state | At least 45 valid extreme observations; finite state |
| 835 | `indicator.atr.50` | price | 0 | 1000 | no | yes | 0 | 1000 | E | Period 50; certified running state | At least 50 valid extreme observations; finite state |
| 836 | `indicator.atr.50.relative` | ratio | 0 | 1 | yes | yes | 0 | 4 | E | Period 50; certified running state | At least 50 valid extreme observations; finite state |
| 837 | `indicator.atr.60` | price | 0 | 1000 | no | yes | 0 | 1000 | E | Period 60; certified running state | At least 60 valid extreme observations; finite state |
| 838 | `indicator.atr.60.relative` | ratio | 0 | 1 | yes | yes | 0 | 4 | E | Period 60; certified running state | At least 60 valid extreme observations; finite state |
| 839 | `indicator.macd_line.3.15.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 3/15; running state | At least 15 valid closes per producer; finite state |
| 840 | `indicator.macd_signal.3.15.5.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 3/15/5; running state | At least 19 valid closes per producer; finite state |
| 841 | `indicator.macd_histogram.3.15.5.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 3/15/5; running state | At least 19 valid closes per producer; finite state |
| 842 | `indicator.macd_signal.3.15.9.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 3/15/9; running state | At least 23 valid closes per producer; finite state |
| 843 | `indicator.macd_histogram.3.15.9.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 3/15/9; running state | At least 23 valid closes per producer; finite state |
| 844 | `indicator.macd_signal.3.15.20.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 3/15/20; running state | At least 34 valid closes per producer; finite state |
| 845 | `indicator.macd_histogram.3.15.20.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 3/15/20; running state | At least 34 valid closes per producer; finite state |
| 846 | `indicator.macd_line.3.26.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 3/26; running state | At least 26 valid closes per producer; finite state |
| 847 | `indicator.macd_signal.3.26.5.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 3/26/5; running state | At least 30 valid closes per producer; finite state |
| 848 | `indicator.macd_histogram.3.26.5.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 3/26/5; running state | At least 30 valid closes per producer; finite state |
| 849 | `indicator.macd_signal.3.26.9.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 3/26/9; running state | At least 34 valid closes per producer; finite state |
| 850 | `indicator.macd_histogram.3.26.9.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 3/26/9; running state | At least 34 valid closes per producer; finite state |
| 851 | `indicator.macd_signal.3.26.20.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 3/26/20; running state | At least 45 valid closes per producer; finite state |
| 852 | `indicator.macd_histogram.3.26.20.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 3/26/20; running state | At least 45 valid closes per producer; finite state |
| 853 | `indicator.macd_line.3.50.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 3/50; running state | At least 50 valid closes per producer; finite state |
| 854 | `indicator.macd_signal.3.50.5.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 3/50/5; running state | At least 54 valid closes per producer; finite state |
| 855 | `indicator.macd_histogram.3.50.5.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 3/50/5; running state | At least 54 valid closes per producer; finite state |
| 856 | `indicator.macd_signal.3.50.9.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 3/50/9; running state | At least 58 valid closes per producer; finite state |
| 857 | `indicator.macd_histogram.3.50.9.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 3/50/9; running state | At least 58 valid closes per producer; finite state |
| 858 | `indicator.macd_signal.3.50.20.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 3/50/20; running state | At least 69 valid closes per producer; finite state |
| 859 | `indicator.macd_histogram.3.50.20.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 3/50/20; running state | At least 69 valid closes per producer; finite state |
| 860 | `indicator.macd_line.7.15.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 7/15; running state | At least 15 valid closes per producer; finite state |
| 861 | `indicator.macd_signal.7.15.5.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 7/15/5; running state | At least 19 valid closes per producer; finite state |
| 862 | `indicator.macd_histogram.7.15.5.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 7/15/5; running state | At least 19 valid closes per producer; finite state |
| 863 | `indicator.macd_signal.7.15.9.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 7/15/9; running state | At least 23 valid closes per producer; finite state |
| 864 | `indicator.macd_histogram.7.15.9.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 7/15/9; running state | At least 23 valid closes per producer; finite state |
| 865 | `indicator.macd_signal.7.15.20.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 7/15/20; running state | At least 34 valid closes per producer; finite state |
| 866 | `indicator.macd_histogram.7.15.20.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 7/15/20; running state | At least 34 valid closes per producer; finite state |
| 867 | `indicator.macd_line.7.26.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 7/26; running state | At least 26 valid closes per producer; finite state |
| 868 | `indicator.macd_signal.7.26.5.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 7/26/5; running state | At least 30 valid closes per producer; finite state |
| 869 | `indicator.macd_histogram.7.26.5.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 7/26/5; running state | At least 30 valid closes per producer; finite state |
| 870 | `indicator.macd_signal.7.26.9.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 7/26/9; running state | At least 34 valid closes per producer; finite state |
| 871 | `indicator.macd_histogram.7.26.9.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 7/26/9; running state | At least 34 valid closes per producer; finite state |
| 872 | `indicator.macd_signal.7.26.20.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 7/26/20; running state | At least 45 valid closes per producer; finite state |
| 873 | `indicator.macd_histogram.7.26.20.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 7/26/20; running state | At least 45 valid closes per producer; finite state |
| 874 | `indicator.macd_line.7.50.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 7/50; running state | At least 50 valid closes per producer; finite state |
| 875 | `indicator.macd_signal.7.50.5.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 7/50/5; running state | At least 54 valid closes per producer; finite state |
| 876 | `indicator.macd_histogram.7.50.5.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 7/50/5; running state | At least 54 valid closes per producer; finite state |
| 877 | `indicator.macd_signal.7.50.9.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 7/50/9; running state | At least 58 valid closes per producer; finite state |
| 878 | `indicator.macd_histogram.7.50.9.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 7/50/9; running state | At least 58 valid closes per producer; finite state |
| 879 | `indicator.macd_signal.7.50.20.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 7/50/20; running state | At least 69 valid closes per producer; finite state |
| 880 | `indicator.macd_histogram.7.50.20.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 7/50/20; running state | At least 69 valid closes per producer; finite state |
| 881 | `indicator.macd_line.12.15.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 12/15; running state | At least 15 valid closes per producer; finite state |
| 882 | `indicator.macd_signal.12.15.5.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 12/15/5; running state | At least 19 valid closes per producer; finite state |
| 883 | `indicator.macd_histogram.12.15.5.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 12/15/5; running state | At least 19 valid closes per producer; finite state |
| 884 | `indicator.macd_signal.12.15.9.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 12/15/9; running state | At least 23 valid closes per producer; finite state |
| 885 | `indicator.macd_histogram.12.15.9.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 12/15/9; running state | At least 23 valid closes per producer; finite state |
| 886 | `indicator.macd_signal.12.15.20.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 12/15/20; running state | At least 34 valid closes per producer; finite state |
| 887 | `indicator.macd_histogram.12.15.20.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 12/15/20; running state | At least 34 valid closes per producer; finite state |
| 888 | `indicator.macd_line.12.26.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 12/26; running state | At least 26 valid closes per producer; finite state |
| 889 | `indicator.macd_signal.12.26.5.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 12/26/5; running state | At least 30 valid closes per producer; finite state |
| 890 | `indicator.macd_histogram.12.26.5.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 12/26/5; running state | At least 30 valid closes per producer; finite state |
| 891 | `indicator.macd_signal.12.26.9.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 12/26/9; running state | At least 34 valid closes per producer; finite state |
| 892 | `indicator.macd_histogram.12.26.9.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 12/26/9; running state | At least 34 valid closes per producer; finite state |
| 893 | `indicator.macd_signal.12.26.20.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 12/26/20; running state | At least 45 valid closes per producer; finite state |
| 894 | `indicator.macd_histogram.12.26.20.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 12/26/20; running state | At least 45 valid closes per producer; finite state |
| 895 | `indicator.macd_line.12.50.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 12/50; running state | At least 50 valid closes per producer; finite state |
| 896 | `indicator.macd_signal.12.50.5.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 12/50/5; running state | At least 54 valid closes per producer; finite state |
| 897 | `indicator.macd_histogram.12.50.5.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 12/50/5; running state | At least 54 valid closes per producer; finite state |
| 898 | `indicator.macd_signal.12.50.9.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 12/50/9; running state | At least 58 valid closes per producer; finite state |
| 899 | `indicator.macd_histogram.12.50.9.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 12/50/9; running state | At least 58 valid closes per producer; finite state |
| 900 | `indicator.macd_signal.12.50.20.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 12/50/20; running state | At least 69 valid closes per producer; finite state |
| 901 | `indicator.macd_histogram.12.50.20.relative` | ratio | -0.5 | 0.5 | yes | yes | -4 | 4 | E | Registered 12/50/20; running state | At least 69 valid closes per producer; finite state |
| 902 | `enriched.return.5s` | ratio | -0.5 | 1 | yes | yes | -2.302585 | 2.302585 | E | Fixed 5s (+1s endpoint tolerance) | Both fresh positive endpoints |
| 903 | `enriched.volatility.5s` | ratio | 0 | 1 | yes | yes | 0 | 2.302585 | E | Fixed 5s (+1s endpoint tolerance) | 5 valid one-second returns |
| 904 | `enriched.return.15s` | ratio | -0.5 | 1 | yes | yes | -2.302585 | 2.302585 | E | Fixed 15s (+1s endpoint tolerance) | Both fresh positive endpoints |
| 905 | `enriched.volatility.15s` | ratio | 0 | 1 | yes | yes | 0 | 2.302585 | E | Fixed 15s (+1s endpoint tolerance) | 15 valid one-second returns |
| 906 | `enriched.return.60s` | ratio | -0.5 | 1 | yes | yes | -2.302585 | 2.302585 | E | Fixed 60s (+1s endpoint tolerance) | Both fresh positive endpoints |
| 907 | `enriched.volatility.60s` | ratio | 0 | 1 | yes | yes | 0 | 2.302585 | E | Fixed 60s (+1s endpoint tolerance) | 60 valid one-second returns |
| 908 | `enriched.return.300s` | ratio | -0.5 | 1 | yes | yes | -2.302585 | 2.302585 | E | Fixed 300s (+1s endpoint tolerance) | Both fresh positive endpoints |
| 909 | `enriched.volatility.300s` | ratio | 0 | 1 | yes | yes | 0 | 2.302585 | E | Fixed 300s (+1s endpoint tolerance) | 300 valid one-second returns |
| 910 | `enriched.activity.volume_acceleration` | ratio | -0.5 | 1 | yes | yes | -1 | 11 | M | Fixed 5s and 60s sums | Causal activity sums; zero allowed; no extra window request |
| 911 | `enriched.activity.trade_acceleration` | ratio | -0.5 | 1 | yes | yes | -1 | 11 | M | Fixed 5s and 60s sums | Causal activity sums; zero allowed; no extra window request |
| 912 | `enriched.execution.volatility_fee_ratio` | ratio | 0 | 100 | yes | yes | 0 | 1000 | E | Fixed 15s volatility plus current price/fee assumptions | Complete volatility window; positive sizing denominator |
| 913 | `enriched.candle.5s.body` | ratio | -0.5 | 1 | yes | yes | -9 | 0.99 | E | Fixed 5s completed candle; dependency <10s (+1s return tolerance) | Producer validity for completed bucket; bucket age < resolution |
| 914 | `enriched.candle.5s.range` | ratio | 0 | 1 | yes | yes | 0 | 9 | E | Fixed 5s completed candle; dependency <10s (+1s return tolerance) | Full valid extremes for range/location; bucket age < resolution |
| 915 | `enriched.candle.5s.close_location` | ratio | 0 | 1 | yes | yes | 0 | 1 | M | Fixed 5s completed candle; dependency <10s (+1s return tolerance) | Full valid extremes for range/location; bucket age < resolution |
| 916 | `enriched.candle.5s.return` | ratio | -0.5 | 1 | yes | yes | -2.302585 | 2.302585 | E | Fixed 5s completed candle; dependency <10s (+1s return tolerance) | Producer validity for completed bucket; bucket age < resolution |
| 917 | `enriched.candle.5s.log_volume` | ratio | 0 | 30 | yes | yes | 0 | 20.723266 | E | Fixed 5s completed candle; dependency <10s (+1s return tolerance) | Producer validity for completed bucket; bucket age < resolution |
| 918 | `enriched.candle.5s.log_trades` | ratio | 0 | 30 | yes | yes | 0 | 13.815512 | E | Fixed 5s completed candle; dependency <10s (+1s return tolerance) | Producer validity for completed bucket; bucket age < resolution |
| 919 | `enriched.candle.5s.active_fraction` | ratio | 0 | 1 | yes | yes | 0 | 1 | M | Fixed 5s completed candle; dependency <10s (+1s return tolerance) | Producer validity for completed bucket; bucket age < resolution |
| 920 | `enriched.candle.15s.body` | ratio | -0.5 | 1 | yes | yes | -9 | 0.99 | E | Fixed 15s completed candle; dependency <30s (+1s return tolerance) | Producer validity for completed bucket; bucket age < resolution |
| 921 | `enriched.candle.15s.range` | ratio | 0 | 1 | yes | yes | 0 | 9 | E | Fixed 15s completed candle; dependency <30s (+1s return tolerance) | Full valid extremes for range/location; bucket age < resolution |
| 922 | `enriched.candle.15s.close_location` | ratio | 0 | 1 | yes | yes | 0 | 1 | M | Fixed 15s completed candle; dependency <30s (+1s return tolerance) | Full valid extremes for range/location; bucket age < resolution |
| 923 | `enriched.candle.15s.return` | ratio | -0.5 | 1 | yes | yes | -2.302585 | 2.302585 | E | Fixed 15s completed candle; dependency <30s (+1s return tolerance) | Producer validity for completed bucket; bucket age < resolution |
| 924 | `enriched.candle.15s.log_volume` | ratio | 0 | 30 | yes | yes | 0 | 20.723266 | E | Fixed 15s completed candle; dependency <30s (+1s return tolerance) | Producer validity for completed bucket; bucket age < resolution |
| 925 | `enriched.candle.15s.log_trades` | ratio | 0 | 30 | yes | yes | 0 | 13.815512 | E | Fixed 15s completed candle; dependency <30s (+1s return tolerance) | Producer validity for completed bucket; bucket age < resolution |
| 926 | `enriched.candle.15s.active_fraction` | ratio | 0 | 1 | yes | yes | 0 | 1 | M | Fixed 15s completed candle; dependency <30s (+1s return tolerance) | Producer validity for completed bucket; bucket age < resolution |
| 927 | `enriched.candle.60s.body` | ratio | -0.5 | 1 | yes | yes | -9 | 0.99 | E | Fixed 60s completed candle; dependency <120s (+1s return tolerance) | Producer validity for completed bucket; bucket age < resolution |
| 928 | `enriched.candle.60s.range` | ratio | 0 | 1 | yes | yes | 0 | 9 | E | Fixed 60s completed candle; dependency <120s (+1s return tolerance) | Full valid extremes for range/location; bucket age < resolution |
| 929 | `enriched.candle.60s.close_location` | ratio | 0 | 1 | yes | yes | 0 | 1 | M | Fixed 60s completed candle; dependency <120s (+1s return tolerance) | Full valid extremes for range/location; bucket age < resolution |
| 930 | `enriched.candle.60s.return` | ratio | -0.5 | 1 | yes | yes | -2.302585 | 2.302585 | E | Fixed 60s completed candle; dependency <120s (+1s return tolerance) | Producer validity for completed bucket; bucket age < resolution |
| 931 | `enriched.candle.60s.log_volume` | ratio | 0 | 30 | yes | yes | 0 | 20.723266 | E | Fixed 60s completed candle; dependency <120s (+1s return tolerance) | Producer validity for completed bucket; bucket age < resolution |
| 932 | `enriched.candle.60s.log_trades` | ratio | 0 | 30 | yes | yes | 0 | 13.815512 | E | Fixed 60s completed candle; dependency <120s (+1s return tolerance) | Producer validity for completed bucket; bucket age < resolution |
| 933 | `enriched.candle.60s.active_fraction` | ratio | 0 | 1 | yes | yes | 0 | 1 | M | Fixed 60s completed candle; dependency <120s (+1s return tolerance) | Producer validity for completed bucket; bucket age < resolution |
| 934 | `enriched.context.15s.median_return` | ratio | -0.5 | 1 | yes | yes | -2.302585 | 2.302585 | E | Fixed 15s endpoints (+1s tolerance); current cross-section | Fresh positive endpoints; context validity; >=2 listings for non-coverage statistics |
| 935 | `enriched.context.15s.mean_return` | ratio | -0.5 | 1 | yes | yes | -2.302585 | 2.302585 | E | Fixed 15s endpoints (+1s tolerance); current cross-section | Fresh positive endpoints; context validity; >=2 listings for non-coverage statistics |
| 936 | `enriched.context.15s.advancing_fraction` | ratio | 0 | 1 | yes | yes | 0 | 1 | M | Fixed 15s endpoints (+1s tolerance); current cross-section | Fresh positive endpoints; context validity; >=2 listings for non-coverage statistics |
| 937 | `enriched.context.15s.dispersion` | ratio | 0 | 1 | yes | yes | 0 | 1 | M | Fixed 15s endpoints (+1s tolerance); current cross-section | Fresh positive endpoints; context validity; >=2 listings for non-coverage statistics |
| 938 | `enriched.context.15s.coverage` | ratio | 0 | 1 | yes | yes | 0 | 1 | M | Fixed 15s endpoints (+1s tolerance); current cross-section | Fresh positive endpoints; context validity; >=2 listings for non-coverage statistics |
| 939 | `enriched.context.15s.relative_return` | ratio | -0.5 | 1 | yes | yes | -4.60517 | 4.60517 | E | Fixed 15s endpoints (+1s tolerance); current cross-section | Fresh positive endpoints; context validity; >=2 listings for non-coverage statistics |
| 940 | `enriched.context.15s.return_percentile` | ratio | 0 | 1 | yes | yes | 0 | 1 | M | Fixed 15s endpoints (+1s tolerance); current cross-section | Fresh positive endpoints; context validity; >=2 listings for non-coverage statistics |
| 941 | `enriched.context.60s.median_return` | ratio | -0.5 | 1 | yes | yes | -2.302585 | 2.302585 | E | Fixed 60s endpoints (+1s tolerance); current cross-section | Fresh positive endpoints; context validity; >=2 listings for non-coverage statistics |
| 942 | `enriched.context.60s.mean_return` | ratio | -0.5 | 1 | yes | yes | -2.302585 | 2.302585 | E | Fixed 60s endpoints (+1s tolerance); current cross-section | Fresh positive endpoints; context validity; >=2 listings for non-coverage statistics |
| 943 | `enriched.context.60s.advancing_fraction` | ratio | 0 | 1 | yes | yes | 0 | 1 | M | Fixed 60s endpoints (+1s tolerance); current cross-section | Fresh positive endpoints; context validity; >=2 listings for non-coverage statistics |
| 944 | `enriched.context.60s.dispersion` | ratio | 0 | 1 | yes | yes | 0 | 1 | M | Fixed 60s endpoints (+1s tolerance); current cross-section | Fresh positive endpoints; context validity; >=2 listings for non-coverage statistics |
| 945 | `enriched.context.60s.coverage` | ratio | 0 | 1 | yes | yes | 0 | 1 | M | Fixed 60s endpoints (+1s tolerance); current cross-section | Fresh positive endpoints; context validity; >=2 listings for non-coverage statistics |
| 946 | `enriched.context.60s.relative_return` | ratio | -0.5 | 1 | yes | yes | -4.60517 | 4.60517 | E | Fixed 60s endpoints (+1s tolerance); current cross-section | Fresh positive endpoints; context validity; >=2 listings for non-coverage statistics |
| 947 | `enriched.context.60s.return_percentile` | ratio | 0 | 1 | yes | yes | 0 | 1 | M | Fixed 60s endpoints (+1s tolerance); current cross-section | Fresh positive endpoints; context validity; >=2 listings for non-coverage statistics |
| 948 | `enriched.context.300s.median_return` | ratio | -0.5 | 1 | yes | yes | -2.302585 | 2.302585 | E | Fixed 300s endpoints (+1s tolerance); current cross-section | Fresh positive endpoints; context validity; >=2 listings for non-coverage statistics |
| 949 | `enriched.context.300s.mean_return` | ratio | -0.5 | 1 | yes | yes | -2.302585 | 2.302585 | E | Fixed 300s endpoints (+1s tolerance); current cross-section | Fresh positive endpoints; context validity; >=2 listings for non-coverage statistics |
| 950 | `enriched.context.300s.advancing_fraction` | ratio | 0 | 1 | yes | yes | 0 | 1 | M | Fixed 300s endpoints (+1s tolerance); current cross-section | Fresh positive endpoints; context validity; >=2 listings for non-coverage statistics |
| 951 | `enriched.context.300s.dispersion` | ratio | 0 | 1 | yes | yes | 0 | 1 | M | Fixed 300s endpoints (+1s tolerance); current cross-section | Fresh positive endpoints; context validity; >=2 listings for non-coverage statistics |
| 952 | `enriched.context.300s.coverage` | ratio | 0 | 1 | yes | yes | 0 | 1 | M | Fixed 300s endpoints (+1s tolerance); current cross-section | Fresh positive endpoints; context validity; >=2 listings for non-coverage statistics |
| 953 | `enriched.context.300s.relative_return` | ratio | -0.5 | 1 | yes | yes | -4.60517 | 4.60517 | E | Fixed 300s endpoints (+1s tolerance); current cross-section | Fresh positive endpoints; context validity; >=2 listings for non-coverage statistics |
| 954 | `enriched.context.300s.return_percentile` | ratio | 0 | 1 | yes | yes | 0 | 1 | M | Fixed 300s endpoints (+1s tolerance); current cross-section | Fresh positive endpoints; context validity; >=2 listings for non-coverage statistics |
| 955 | `enriched.rank.dollar_volume_60s_percentile` | ratio | 0 | 1 | yes | yes | 0 | 1 | M | Fixed60s notional ranking | Valid contemporaneous cross-section/rank |
| 956 | `enriched.rank.dollar_volume_60s_change_15s` | ratio | -0.5 | 1 | yes | yes | -1 | 1 | M | 60s rank now and 15s ago; total75s | Both rank observations valid; same causal universe contract |

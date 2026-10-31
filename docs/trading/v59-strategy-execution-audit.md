# V59 strategy, execution and optimization audit

Audit date: 2026-10-31. Source baseline: `99fb181db501dbd3ed8c871be7f85ccb2f465b3a`.

## Outcome and scope

The current implementation does not yet satisfy the agreed strategy/execution contract. The principal problems are incomplete strategy visibility of admission conditions, a stale membership clock in swing projection, branch routing before feasibility, and the absence of limit orders. These are different from legitimate insufficient-liquidity waits. Moving existing checks without reviewing their semantics would preserve the problem.

This is an audit and proposed revision specification, **not an implemented fix**. No optimizer code, inputs, settings, checkpoints or running processes were changed. All six workstreams below were reviewed. Verification comprised source tracing, the current workstation job contract, existing focused tests and bounded CPU probes. No GPU replay, full-generation benchmark, new validation evaluation or exhaustive candidate audit was run during this audit. Remaining acceptance work is explicit below; passing existing tests is not proof that the new contract is implemented.

Runtime authority inspected: workstation `D:\TradingML\runtimes\vectorized_backtest\torch_backtest_v59\global-dust-99fb181db-g200`; source copy `D:\TradingML\codes\quant-research-workbench-v59-99fb181db`. Job source digest: `afa7c3022f7bdab6ee24476872cfc5dd02ff372d65fa9d1ba16a3a3e3bc2f56b`. This is a global-only 24-training/12-display-validation, 200-generation campaign: 6,144 initial founders, 1,024 successors, two replay lanes. Base batch size is 512; adaptive packing can use 1,024. A batch is not a whole founder session.

Twelve core local/deployed source files were compared: network, network_cuda, orders, order_transition, order_liquidity, routing_plan, swing_projection, lineage, robust_score, calibration, order_replay and config. All matched after CRLF/LF normalization. Raw byte hashes differ for eleven due to line endings; no byte-identical deployment claim is made.

The TASK-0223/V50 handoff is historical and is not authority for the active V59 contract. The latest user instructions and the pinned V59 job take precedence.

## Agreed contract

1. Sampling and reproduction construct supported, structurally coherent strategies. They may not silently shrink networks, change the objective, discard valid founders, or screen on validation results.
2. A strategy evaluates causal inputs and its actual position/order state at the requested candle boundary. It emits a valid order intent or an explicit abstention with reasons. Missing required history, invalid initial stop, cooldown and conflicting actions are resolved here, before submission.
3. Execution receives accepted order intents. It does not independently apply strategy profitability, history, stop-distance, risk-budget or network conditions.
4. Buy limits execute only at ask <= limit; sell limits only at bid >= limit. Unmarketable limits remain pending. Available displayed liquidity bounds partial fills and cannot be consumed twice by the same simulated account. Future fills cannot be guaranteed.
5. The strategy receives partial fills, average execution price, outstanding quantity and order status; it can manage owned inventory and keep, amend or cancel the remainder. Cancellation is explicit, not an undocumented execution veto.
6. Initial stops must be positive and below the intended entry. Trailing stops may subsequently exceed entry. Re-entry waits three seconds after final liquidation. The $300 risk and 20% distance selection/execution caps remain disabled.
7. Malformed/nonfinite orders are explicit contract errors. Missing valid quotes and exhausted liquidity are pending states, not fabricated fills or fitness exclusions. Terminal incompleteness is a data/execution-completion issue, not silent candidate removal.
8. Causal data and order-event ordering must be documented. Same-candle execution must not use information from after the decision boundary or reuse earlier consumed liquidity.

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

Catalog `lower/upper` values are descriptive/search metadata, **not universal runtime clipping or guaranteed empirical bounds**. Missing is not the same as zero. Generic nodes OR valid nonzero-weight market ancestors; they do not require every referenced input to exist. Thus a partially missing graph can still fire. Templates use explicit validity predicates; arbitrary random/mutated graphs need not do so.

Raw/log price units and split-age/event features are blocked. `log_float_shares` and `log_shares_outstanding` are transformed to decade buckets before absolute/relative projection. Other allowed channels can still be proxies for liquidity, regime, time or identity; blocking names is not proof against overfitting.

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

Required additions should be explicit, causally timestamped and versioned. Separate model-readable normalized values from exact controller/order fields. At minimum: selected-stop validity/distance; can-enter/can-add/can-reduce/can-exit and reason masks; membership; cooldown/age eligibility; quote validity and executable size; exact pending side/status/quantity/limit, filled quantity and average fill; cancellation/amendment availability; end-of-decision-window state. Keep raw execution prices out of arbitrary model features if the identity-reduction policy remains required; normalized distances plus exact controller fields can satisfy both needs.

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

Revision: version the intent/order schema with side, type, limit, quantity (or explicit dollar-intent conversion), lifetime and amendment identity. Unmarketable limits wait; do not reject them. Missing or malformed limits are schema errors. Test quote improvement, crossing/noncrossing limits, partials across prices and cancellation with accrued fees.

### F04 — Confirmed: admission conditions are not fully visible to the model (high)

`orders.transition` silently gates raw entry on quote/mark validity, stop below ask, stop below bid and mark, membership, action conflict, pending state, three-second cooldown and sizing. The model state has no unified admissibility/reason inputs. Stop below bid and mark are stricter than just stop below intended entry; they encode avoidance of an already-triggered stop and must be explicit strategy rules, not hidden execution policy.

Revision: one vectorized strategy-intent boundary authority shared by sampling probes, CPU oracle and CUDA controller. Distinguish raw neural proposals from accepted requests and record abstention reasons. Review each boundary for necessity; do not simply relocate every old restriction.

### F05 — Confirmed restrictive history definition, policy review required (high)

`v42/certified_history_bank.py:calculate` requires all values in a swing window to be finite; unobserved seconds become NaN. For left60/right3 that is 64 consecutive observed one-second bars. A synthetic 140-second sequence with one absent bar every20 seconds produced **zero** valid 60/3 pivots but **134** valid 2/2 pivots. The earlier real-candidate check found 904 eligible entry-signal moments on July30 with no 60/3 stop.

Revision: decide and name the intended swing definition (elapsed-time observations, observed-bar count, or another explicitly causal rule). These are different strategies; do not silently forward-fill lows or shorten the window. Expose availability and effective lookback. Sampling must use supported semantics and training-only availability diagnostics; it must not remove a candidate merely because it is unprofitable or infrequent. A history change requires a new prepared-data identity and affected replays.

### F06 — Confirmed: cancellation and pending-order policy are implicit action mappings (medium/high)

`orders.transition` maps exit/reduce signals to pending-buy cancellation and add signals to pending-sell cancellation. Add signals can amend a pending buy under cooldown/max-add limits. Partial positions can be managed, and accrued fills/fees are preserved by existing tests. However the strategy has no explicit keep/cancel/amend intent or exact order-specific fill-price state. Minimum ages apply only while held, so a zero-fill pending entry can be cancelled by an exit head that would be age-blocked after its first partial fill.

Revision: define order-management intent and age semantics explicitly; expose required state; test before-first-fill and after-partial-fill transitions. Preserve successful partial-fill accounting. Do not label all current cancellations execution rejections.

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

1. **Freeze the contract and schemas:** specify input availability, strategy intents, limits, partial-state fields, action arbitration, membership clock, history and terminal semantics. Record which are bug fixes versus deliberate behavior changes. Keep current objective/budgets fixed.
2. **Fix proven boundary defects:** current membership swing projection, feasibility-aware branch routing, impossible policy endpoint, and named abstention reasons. Preserve history definition until explicitly revised.
3. **Implement explicit intent/limit/partial-state interfaces:** share CPU/CUDA contracts, retain fills/fees and strategy-controlled management. Version genomes, state packing, execution and replay identities as affected.
4. **Align initialization and reproduction:** joint admissibility probes without profitability filtering, coherent crossover, operator retry accounting, exact scheduled/realized counts and deterministic RNG. Separate policy choices F12–F14 from necessary correctness repairs.
5. **Validate real runnable paths:** causal boundary fixtures; missing-history, invalid-stop, first-admission, routed fallback, exit conflict, cooldown, partial fills, limit waits, cancellation/amendment, cutoff/tail exhaustion, ledger conservation, all-founder inclusion, validation isolation, identity invalidation and deterministic recovery. Include a forced-overflow retry and shared-entry state-dependence check.
6. **Short performance qualification then explicit recovery:** use frozen representative candidates and two contrasting sessions; compare baseline/fixed wall time and all unchanged-case decisions/ledgers. For intentional behavior changes report changed requests/fills/P&L and reasons instead of claiming financial parity. A history/input/execution change invalidates affected financial receipts; retain genomes/RNG where schema-compatible, but replay affected founders before selection. Do not reuse old scores merely because genomes match.

No blanket removal of stop checks, no artificial fills, no forced trading on every day, no smaller networks/populations, no validation-driven repair, and no hidden fee/risk/score changes are acceptable acceleration shortcuts.

## Diagnostic schema to implement

Per candidate/session: raw proposal counts by action; decision opportunities; required-input-missing; stop-unavailable; stop-invalid; entry/exit-conflict; not-member; cooldown/age-block; pending-order-conflict; invalid/zero sizing; accepted intents; pending-unmarketable-limit; pending-missing-book; pending-no-size; partial/complete fills; cancelled remainder and reason; terminal unresolved quantity/orders. Keep unique primary outcomes separate from overlapping condition counts and repeated standing-order observations.

Every record must carry candidate/program identity, session/split, decision/execution contract versions, clock range and complete/partial coverage. Missing data is unavailable, never zero. Aggregate counters should be bounded tensors; sampled details can be persisted by the existing publication lane. UI should distinguish raw proposals, actionable requests, orders and positions.

## Full pinned market-input catalog

The table below is an exact enumeration from the pinned source catalog. `Allowed` applies to all three channels. The declared range is **not** a runtime clamp. The family definitions and availability rules above apply to every row. Input meanings that require detailed indicator/level mathematics remain source-defined by the listed authority; changing them requires a new contract, not relabeling this table.

| ID | Name | Unit | Declared lower | Declared upper | Allowed |
|---:|---|---|---:|---:|---|
| 0 | `log_open` | log_price | -5 | 10 | no |
| 1 | `log_high` | log_price | -5 | 10 | no |
| 2 | `log_low` | log_price | -5 | 10 | no |
| 3 | `log_close` | log_price | -5 | 10 | no |
| 4 | `bar_vwap_rel` | ratio | -2 | 2 | yes |
| 5 | `session_vwap_rel` | ratio | -2 | 2 | yes |
| 6 | `bar_vwap_valid` | bool | 0 | 1 | yes |
| 7 | `session_vwap_valid` | bool | 0 | 1 | yes |
| 8 | `log_volume` | log_shares | 0 | 30 | yes |
| 9 | `log_trades` | log_count | 0 | 30 | yes |
| 10 | `log_volume_60s` | log_shares | 0 | 30 | yes |
| 11 | `log_trades_60s` | log_count | 0 | 30 | yes |
| 12 | `log_rvol_10s_prev_session` | log_duration | 0 | 20 | yes |
| 13 | `rvol_10s_available` | bool | 0 | 1 | yes |
| 14 | `macd_line_rel` | ratio | -2 | 2 | yes |
| 15 | `macd_signal_rel` | ratio | -2 | 2 | yes |
| 16 | `rsi_14` | ratio | 0 | 1 | yes |
| 17 | `atr_14_rel` | ratio | -2 | 2 | yes |
| 18 | `ema_7_rel` | ratio | -2 | 2 | yes |
| 19 | `ema_26_rel` | ratio | -2 | 2 | yes |
| 20 | `indicator_available` | bool | 0 | 1 | yes |
| 21 | `time_of_day_sin` | ratio | -2 | 2 | yes |
| 22 | `time_of_day_cos` | ratio | -2 | 2 | yes |
| 23 | `premarket` | bool | 0 | 1 | yes |
| 24 | `regular` | bool | 0 | 1 | yes |
| 25 | `after_hours` | bool | 0 | 1 | yes |
| 26 | `log_inter_candle_gap` | log_duration | 0 | 30 | yes |
| 27 | `log_float_shares` | log_shares | 0 | 30 | yes |
| 28 | `float_present` | bool | 0 | 1 | yes |
| 29 | `log_shares_outstanding` | log_shares | 0 | 30 | yes |
| 30 | `shares_present` | bool | 0 | 1 | yes |
| 31 | `log_days_since_split` | log_duration | 0 | 30 | no |
| 32 | `split_present` | bool | 0 | 1 | no |
| 33 | `log_days_since_reverse_split` | log_duration | 0 | 30 | no |
| 34 | `reverse_split_present` | bool | 0 | 1 | no |
| 35 | `bar_price_valid` | bool | 0 | 1 | yes |
| 36 | `bar_extremes_valid` | bool | 0 | 1 | yes |
| 37 | `v7.below.1.center_distance_rel` | ratio | -10 | 10 | yes |
| 38 | `v7.below.1.lower_distance_rel` | ratio | -10 | 10 | yes |
| 39 | `v7.below.1.upper_distance_rel` | ratio | -10 | 10 | yes |
| 40 | `v7.below.1.log_observation_count` | log_count | 0 | 30 | yes |
| 41 | `v7.below.1.log_today_observation_count` | log_count | 0 | 30 | yes |
| 42 | `v7.below.1.log_age_since_confirmation` | log_duration | 0 | 30 | yes |
| 43 | `v7.below.1.role_support` | bool | 0 | 1 | yes |
| 44 | `v7.below.1.role_resistance` | bool | 0 | 1 | yes |
| 45 | `v7.below.1.role_transition` | bool | 0 | 1 | yes |
| 46 | `v7.below.1.historical_origin` | bool | 0 | 1 | yes |
| 47 | `v7.below.1.present` | bool | 0 | 1 | yes |
| 48 | `v7.below.2.center_distance_rel` | ratio | -10 | 10 | yes |
| 49 | `v7.below.2.lower_distance_rel` | ratio | -10 | 10 | yes |
| 50 | `v7.below.2.upper_distance_rel` | ratio | -10 | 10 | yes |
| 51 | `v7.below.2.log_observation_count` | log_count | 0 | 30 | yes |
| 52 | `v7.below.2.log_today_observation_count` | log_count | 0 | 30 | yes |
| 53 | `v7.below.2.log_age_since_confirmation` | log_duration | 0 | 30 | yes |
| 54 | `v7.below.2.role_support` | bool | 0 | 1 | yes |
| 55 | `v7.below.2.role_resistance` | bool | 0 | 1 | yes |
| 56 | `v7.below.2.role_transition` | bool | 0 | 1 | yes |
| 57 | `v7.below.2.historical_origin` | bool | 0 | 1 | yes |
| 58 | `v7.below.2.present` | bool | 0 | 1 | yes |
| 59 | `v7.below.3.center_distance_rel` | ratio | -10 | 10 | yes |
| 60 | `v7.below.3.lower_distance_rel` | ratio | -10 | 10 | yes |
| 61 | `v7.below.3.upper_distance_rel` | ratio | -10 | 10 | yes |
| 62 | `v7.below.3.log_observation_count` | log_count | 0 | 30 | yes |
| 63 | `v7.below.3.log_today_observation_count` | log_count | 0 | 30 | yes |
| 64 | `v7.below.3.log_age_since_confirmation` | log_duration | 0 | 30 | yes |
| 65 | `v7.below.3.role_support` | bool | 0 | 1 | yes |
| 66 | `v7.below.3.role_resistance` | bool | 0 | 1 | yes |
| 67 | `v7.below.3.role_transition` | bool | 0 | 1 | yes |
| 68 | `v7.below.3.historical_origin` | bool | 0 | 1 | yes |
| 69 | `v7.below.3.present` | bool | 0 | 1 | yes |
| 70 | `v7.below.4.center_distance_rel` | ratio | -10 | 10 | yes |
| 71 | `v7.below.4.lower_distance_rel` | ratio | -10 | 10 | yes |
| 72 | `v7.below.4.upper_distance_rel` | ratio | -10 | 10 | yes |
| 73 | `v7.below.4.log_observation_count` | log_count | 0 | 30 | yes |
| 74 | `v7.below.4.log_today_observation_count` | log_count | 0 | 30 | yes |
| 75 | `v7.below.4.log_age_since_confirmation` | log_duration | 0 | 30 | yes |
| 76 | `v7.below.4.role_support` | bool | 0 | 1 | yes |
| 77 | `v7.below.4.role_resistance` | bool | 0 | 1 | yes |
| 78 | `v7.below.4.role_transition` | bool | 0 | 1 | yes |
| 79 | `v7.below.4.historical_origin` | bool | 0 | 1 | yes |
| 80 | `v7.below.4.present` | bool | 0 | 1 | yes |
| 81 | `v7.below.5.center_distance_rel` | ratio | -10 | 10 | yes |
| 82 | `v7.below.5.lower_distance_rel` | ratio | -10 | 10 | yes |
| 83 | `v7.below.5.upper_distance_rel` | ratio | -10 | 10 | yes |
| 84 | `v7.below.5.log_observation_count` | log_count | 0 | 30 | yes |
| 85 | `v7.below.5.log_today_observation_count` | log_count | 0 | 30 | yes |
| 86 | `v7.below.5.log_age_since_confirmation` | log_duration | 0 | 30 | yes |
| 87 | `v7.below.5.role_support` | bool | 0 | 1 | yes |
| 88 | `v7.below.5.role_resistance` | bool | 0 | 1 | yes |
| 89 | `v7.below.5.role_transition` | bool | 0 | 1 | yes |
| 90 | `v7.below.5.historical_origin` | bool | 0 | 1 | yes |
| 91 | `v7.below.5.present` | bool | 0 | 1 | yes |
| 92 | `v7.above.1.center_distance_rel` | ratio | -10 | 10 | yes |
| 93 | `v7.above.1.lower_distance_rel` | ratio | -10 | 10 | yes |
| 94 | `v7.above.1.upper_distance_rel` | ratio | -10 | 10 | yes |
| 95 | `v7.above.1.log_observation_count` | log_count | 0 | 30 | yes |
| 96 | `v7.above.1.log_today_observation_count` | log_count | 0 | 30 | yes |
| 97 | `v7.above.1.log_age_since_confirmation` | log_duration | 0 | 30 | yes |
| 98 | `v7.above.1.role_support` | bool | 0 | 1 | yes |
| 99 | `v7.above.1.role_resistance` | bool | 0 | 1 | yes |
| 100 | `v7.above.1.role_transition` | bool | 0 | 1 | yes |
| 101 | `v7.above.1.historical_origin` | bool | 0 | 1 | yes |
| 102 | `v7.above.1.present` | bool | 0 | 1 | yes |
| 103 | `v7.above.2.center_distance_rel` | ratio | -10 | 10 | yes |
| 104 | `v7.above.2.lower_distance_rel` | ratio | -10 | 10 | yes |
| 105 | `v7.above.2.upper_distance_rel` | ratio | -10 | 10 | yes |
| 106 | `v7.above.2.log_observation_count` | log_count | 0 | 30 | yes |
| 107 | `v7.above.2.log_today_observation_count` | log_count | 0 | 30 | yes |
| 108 | `v7.above.2.log_age_since_confirmation` | log_duration | 0 | 30 | yes |
| 109 | `v7.above.2.role_support` | bool | 0 | 1 | yes |
| 110 | `v7.above.2.role_resistance` | bool | 0 | 1 | yes |
| 111 | `v7.above.2.role_transition` | bool | 0 | 1 | yes |
| 112 | `v7.above.2.historical_origin` | bool | 0 | 1 | yes |
| 113 | `v7.above.2.present` | bool | 0 | 1 | yes |
| 114 | `v7.above.3.center_distance_rel` | ratio | -10 | 10 | yes |
| 115 | `v7.above.3.lower_distance_rel` | ratio | -10 | 10 | yes |
| 116 | `v7.above.3.upper_distance_rel` | ratio | -10 | 10 | yes |
| 117 | `v7.above.3.log_observation_count` | log_count | 0 | 30 | yes |
| 118 | `v7.above.3.log_today_observation_count` | log_count | 0 | 30 | yes |
| 119 | `v7.above.3.log_age_since_confirmation` | log_duration | 0 | 30 | yes |
| 120 | `v7.above.3.role_support` | bool | 0 | 1 | yes |
| 121 | `v7.above.3.role_resistance` | bool | 0 | 1 | yes |
| 122 | `v7.above.3.role_transition` | bool | 0 | 1 | yes |
| 123 | `v7.above.3.historical_origin` | bool | 0 | 1 | yes |
| 124 | `v7.above.3.present` | bool | 0 | 1 | yes |
| 125 | `v7.above.4.center_distance_rel` | ratio | -10 | 10 | yes |
| 126 | `v7.above.4.lower_distance_rel` | ratio | -10 | 10 | yes |
| 127 | `v7.above.4.upper_distance_rel` | ratio | -10 | 10 | yes |
| 128 | `v7.above.4.log_observation_count` | log_count | 0 | 30 | yes |
| 129 | `v7.above.4.log_today_observation_count` | log_count | 0 | 30 | yes |
| 130 | `v7.above.4.log_age_since_confirmation` | log_duration | 0 | 30 | yes |
| 131 | `v7.above.4.role_support` | bool | 0 | 1 | yes |
| 132 | `v7.above.4.role_resistance` | bool | 0 | 1 | yes |
| 133 | `v7.above.4.role_transition` | bool | 0 | 1 | yes |
| 134 | `v7.above.4.historical_origin` | bool | 0 | 1 | yes |
| 135 | `v7.above.4.present` | bool | 0 | 1 | yes |
| 136 | `v7.above.5.center_distance_rel` | ratio | -10 | 10 | yes |
| 137 | `v7.above.5.lower_distance_rel` | ratio | -10 | 10 | yes |
| 138 | `v7.above.5.upper_distance_rel` | ratio | -10 | 10 | yes |
| 139 | `v7.above.5.log_observation_count` | log_count | 0 | 30 | yes |
| 140 | `v7.above.5.log_today_observation_count` | log_count | 0 | 30 | yes |
| 141 | `v7.above.5.log_age_since_confirmation` | log_duration | 0 | 30 | yes |
| 142 | `v7.above.5.role_support` | bool | 0 | 1 | yes |
| 143 | `v7.above.5.role_resistance` | bool | 0 | 1 | yes |
| 144 | `v7.above.5.role_transition` | bool | 0 | 1 | yes |
| 145 | `v7.above.5.historical_origin` | bool | 0 | 1 | yes |
| 146 | `v7.above.5.present` | bool | 0 | 1 | yes |
| 147 | `split_this_session` | bool | 0 | 1 | no |
| 148 | `reverse_split_this_session` | bool | 0 | 1 | no |
| 149 | `history.recent_high.1` | price | 0 | 1000 | no |
| 150 | `history.recent_high.2` | price | 0 | 1000 | no |
| 151 | `history.recent_high.3` | price | 0 | 1000 | no |
| 152 | `history.recent_high.4` | price | 0 | 1000 | no |
| 153 | `history.recent_high.5` | price | 0 | 1000 | no |
| 154 | `history.recent_high.6` | price | 0 | 1000 | no |
| 155 | `history.recent_high.7` | price | 0 | 1000 | no |
| 156 | `history.recent_high.8` | price | 0 | 1000 | no |
| 157 | `history.recent_high.9` | price | 0 | 1000 | no |
| 158 | `history.recent_high.10` | price | 0 | 1000 | no |
| 159 | `history.recent_high.11` | price | 0 | 1000 | no |
| 160 | `history.recent_high.12` | price | 0 | 1000 | no |
| 161 | `history.recent_high.13` | price | 0 | 1000 | no |
| 162 | `history.recent_high.14` | price | 0 | 1000 | no |
| 163 | `history.recent_high.15` | price | 0 | 1000 | no |
| 164 | `history.recent_high.16` | price | 0 | 1000 | no |
| 165 | `history.recent_high.17` | price | 0 | 1000 | no |
| 166 | `history.recent_high.18` | price | 0 | 1000 | no |
| 167 | `history.recent_high.19` | price | 0 | 1000 | no |
| 168 | `history.recent_high.20` | price | 0 | 1000 | no |
| 169 | `history.recent_high.21` | price | 0 | 1000 | no |
| 170 | `history.recent_high.22` | price | 0 | 1000 | no |
| 171 | `history.recent_high.23` | price | 0 | 1000 | no |
| 172 | `history.recent_high.24` | price | 0 | 1000 | no |
| 173 | `history.recent_high.25` | price | 0 | 1000 | no |
| 174 | `history.recent_high.26` | price | 0 | 1000 | no |
| 175 | `history.recent_high.27` | price | 0 | 1000 | no |
| 176 | `history.recent_high.28` | price | 0 | 1000 | no |
| 177 | `history.recent_high.29` | price | 0 | 1000 | no |
| 178 | `history.recent_high.30` | price | 0 | 1000 | no |
| 179 | `history.recent_high.31` | price | 0 | 1000 | no |
| 180 | `history.recent_high.32` | price | 0 | 1000 | no |
| 181 | `history.recent_high.33` | price | 0 | 1000 | no |
| 182 | `history.recent_high.34` | price | 0 | 1000 | no |
| 183 | `history.recent_high.35` | price | 0 | 1000 | no |
| 184 | `history.recent_high.36` | price | 0 | 1000 | no |
| 185 | `history.recent_high.37` | price | 0 | 1000 | no |
| 186 | `history.recent_high.38` | price | 0 | 1000 | no |
| 187 | `history.recent_high.39` | price | 0 | 1000 | no |
| 188 | `history.recent_high.40` | price | 0 | 1000 | no |
| 189 | `history.recent_high.41` | price | 0 | 1000 | no |
| 190 | `history.recent_high.42` | price | 0 | 1000 | no |
| 191 | `history.recent_high.43` | price | 0 | 1000 | no |
| 192 | `history.recent_high.44` | price | 0 | 1000 | no |
| 193 | `history.recent_high.45` | price | 0 | 1000 | no |
| 194 | `history.recent_high.46` | price | 0 | 1000 | no |
| 195 | `history.recent_high.47` | price | 0 | 1000 | no |
| 196 | `history.recent_high.48` | price | 0 | 1000 | no |
| 197 | `history.recent_high.49` | price | 0 | 1000 | no |
| 198 | `history.recent_high.50` | price | 0 | 1000 | no |
| 199 | `history.recent_high.51` | price | 0 | 1000 | no |
| 200 | `history.recent_high.52` | price | 0 | 1000 | no |
| 201 | `history.recent_high.53` | price | 0 | 1000 | no |
| 202 | `history.recent_high.54` | price | 0 | 1000 | no |
| 203 | `history.recent_high.55` | price | 0 | 1000 | no |
| 204 | `history.recent_high.56` | price | 0 | 1000 | no |
| 205 | `history.recent_high.57` | price | 0 | 1000 | no |
| 206 | `history.recent_high.58` | price | 0 | 1000 | no |
| 207 | `history.recent_high.59` | price | 0 | 1000 | no |
| 208 | `history.recent_high.60` | price | 0 | 1000 | no |
| 209 | `history.momentum_close.1` | price | 0 | 1000 | no |
| 210 | `history.momentum_close.2` | price | 0 | 1000 | no |
| 211 | `history.momentum_close.3` | price | 0 | 1000 | no |
| 212 | `history.momentum_close.4` | price | 0 | 1000 | no |
| 213 | `history.momentum_close.5` | price | 0 | 1000 | no |
| 214 | `history.momentum_close.6` | price | 0 | 1000 | no |
| 215 | `history.momentum_close.7` | price | 0 | 1000 | no |
| 216 | `history.momentum_close.8` | price | 0 | 1000 | no |
| 217 | `history.momentum_close.9` | price | 0 | 1000 | no |
| 218 | `history.momentum_close.10` | price | 0 | 1000 | no |
| 219 | `history.momentum_close.11` | price | 0 | 1000 | no |
| 220 | `history.momentum_close.12` | price | 0 | 1000 | no |
| 221 | `history.momentum_close.13` | price | 0 | 1000 | no |
| 222 | `history.momentum_close.14` | price | 0 | 1000 | no |
| 223 | `history.momentum_close.15` | price | 0 | 1000 | no |
| 224 | `history.momentum_close.16` | price | 0 | 1000 | no |
| 225 | `history.momentum_close.17` | price | 0 | 1000 | no |
| 226 | `history.momentum_close.18` | price | 0 | 1000 | no |
| 227 | `history.momentum_close.19` | price | 0 | 1000 | no |
| 228 | `history.momentum_close.20` | price | 0 | 1000 | no |
| 229 | `history.momentum_close.21` | price | 0 | 1000 | no |
| 230 | `history.momentum_close.22` | price | 0 | 1000 | no |
| 231 | `history.momentum_close.23` | price | 0 | 1000 | no |
| 232 | `history.momentum_close.24` | price | 0 | 1000 | no |
| 233 | `history.momentum_close.25` | price | 0 | 1000 | no |
| 234 | `history.momentum_close.26` | price | 0 | 1000 | no |
| 235 | `history.momentum_close.27` | price | 0 | 1000 | no |
| 236 | `history.momentum_close.28` | price | 0 | 1000 | no |
| 237 | `history.momentum_close.29` | price | 0 | 1000 | no |
| 238 | `history.momentum_close.30` | price | 0 | 1000 | no |
| 239 | `history.momentum_close.31` | price | 0 | 1000 | no |
| 240 | `history.momentum_close.32` | price | 0 | 1000 | no |
| 241 | `history.momentum_close.33` | price | 0 | 1000 | no |
| 242 | `history.momentum_close.34` | price | 0 | 1000 | no |
| 243 | `history.momentum_close.35` | price | 0 | 1000 | no |
| 244 | `history.momentum_close.36` | price | 0 | 1000 | no |
| 245 | `history.momentum_close.37` | price | 0 | 1000 | no |
| 246 | `history.momentum_close.38` | price | 0 | 1000 | no |
| 247 | `history.momentum_close.39` | price | 0 | 1000 | no |
| 248 | `history.momentum_close.40` | price | 0 | 1000 | no |
| 249 | `history.momentum_close.41` | price | 0 | 1000 | no |
| 250 | `history.momentum_close.42` | price | 0 | 1000 | no |
| 251 | `history.momentum_close.43` | price | 0 | 1000 | no |
| 252 | `history.momentum_close.44` | price | 0 | 1000 | no |
| 253 | `history.momentum_close.45` | price | 0 | 1000 | no |
| 254 | `history.momentum_close.46` | price | 0 | 1000 | no |
| 255 | `history.momentum_close.47` | price | 0 | 1000 | no |
| 256 | `history.momentum_close.48` | price | 0 | 1000 | no |
| 257 | `history.momentum_close.49` | price | 0 | 1000 | no |
| 258 | `history.momentum_close.50` | price | 0 | 1000 | no |
| 259 | `history.momentum_close.51` | price | 0 | 1000 | no |
| 260 | `history.momentum_close.52` | price | 0 | 1000 | no |
| 261 | `history.momentum_close.53` | price | 0 | 1000 | no |
| 262 | `history.momentum_close.54` | price | 0 | 1000 | no |
| 263 | `history.momentum_close.55` | price | 0 | 1000 | no |
| 264 | `history.momentum_close.56` | price | 0 | 1000 | no |
| 265 | `history.momentum_close.57` | price | 0 | 1000 | no |
| 266 | `history.momentum_close.58` | price | 0 | 1000 | no |
| 267 | `history.momentum_close.59` | price | 0 | 1000 | no |
| 268 | `history.momentum_close.60` | price | 0 | 1000 | no |
| 269 | `history.attention_mean.1` | dollars | 0 | 1e+07 | yes |
| 270 | `history.attention_mean.2` | dollars | 0 | 1e+07 | yes |
| 271 | `history.attention_mean.3` | dollars | 0 | 1e+07 | yes |
| 272 | `history.attention_mean.4` | dollars | 0 | 1e+07 | yes |
| 273 | `history.attention_mean.5` | dollars | 0 | 1e+07 | yes |
| 274 | `history.attention_mean.6` | dollars | 0 | 1e+07 | yes |
| 275 | `history.attention_mean.7` | dollars | 0 | 1e+07 | yes |
| 276 | `history.attention_mean.8` | dollars | 0 | 1e+07 | yes |
| 277 | `history.attention_mean.9` | dollars | 0 | 1e+07 | yes |
| 278 | `history.attention_mean.10` | dollars | 0 | 1e+07 | yes |
| 279 | `history.attention_mean.11` | dollars | 0 | 1e+07 | yes |
| 280 | `history.attention_mean.12` | dollars | 0 | 1e+07 | yes |
| 281 | `history.attention_mean.13` | dollars | 0 | 1e+07 | yes |
| 282 | `history.attention_mean.14` | dollars | 0 | 1e+07 | yes |
| 283 | `history.attention_mean.15` | dollars | 0 | 1e+07 | yes |
| 284 | `history.attention_mean.16` | dollars | 0 | 1e+07 | yes |
| 285 | `history.attention_mean.17` | dollars | 0 | 1e+07 | yes |
| 286 | `history.attention_mean.18` | dollars | 0 | 1e+07 | yes |
| 287 | `history.attention_mean.19` | dollars | 0 | 1e+07 | yes |
| 288 | `history.attention_mean.20` | dollars | 0 | 1e+07 | yes |
| 289 | `history.attention_mean.21` | dollars | 0 | 1e+07 | yes |
| 290 | `history.attention_mean.22` | dollars | 0 | 1e+07 | yes |
| 291 | `history.attention_mean.23` | dollars | 0 | 1e+07 | yes |
| 292 | `history.attention_mean.24` | dollars | 0 | 1e+07 | yes |
| 293 | `history.attention_mean.25` | dollars | 0 | 1e+07 | yes |
| 294 | `history.attention_mean.26` | dollars | 0 | 1e+07 | yes |
| 295 | `history.attention_mean.27` | dollars | 0 | 1e+07 | yes |
| 296 | `history.attention_mean.28` | dollars | 0 | 1e+07 | yes |
| 297 | `history.attention_mean.29` | dollars | 0 | 1e+07 | yes |
| 298 | `history.attention_mean.30` | dollars | 0 | 1e+07 | yes |
| 299 | `history.attention_mean.31` | dollars | 0 | 1e+07 | yes |
| 300 | `history.attention_mean.32` | dollars | 0 | 1e+07 | yes |
| 301 | `history.attention_mean.33` | dollars | 0 | 1e+07 | yes |
| 302 | `history.attention_mean.34` | dollars | 0 | 1e+07 | yes |
| 303 | `history.attention_mean.35` | dollars | 0 | 1e+07 | yes |
| 304 | `history.attention_mean.36` | dollars | 0 | 1e+07 | yes |
| 305 | `history.attention_mean.37` | dollars | 0 | 1e+07 | yes |
| 306 | `history.attention_mean.38` | dollars | 0 | 1e+07 | yes |
| 307 | `history.attention_mean.39` | dollars | 0 | 1e+07 | yes |
| 308 | `history.attention_mean.40` | dollars | 0 | 1e+07 | yes |
| 309 | `history.attention_mean.41` | dollars | 0 | 1e+07 | yes |
| 310 | `history.attention_mean.42` | dollars | 0 | 1e+07 | yes |
| 311 | `history.attention_mean.43` | dollars | 0 | 1e+07 | yes |
| 312 | `history.attention_mean.44` | dollars | 0 | 1e+07 | yes |
| 313 | `history.attention_mean.45` | dollars | 0 | 1e+07 | yes |
| 314 | `history.attention_mean.46` | dollars | 0 | 1e+07 | yes |
| 315 | `history.attention_mean.47` | dollars | 0 | 1e+07 | yes |
| 316 | `history.attention_mean.48` | dollars | 0 | 1e+07 | yes |
| 317 | `history.attention_mean.49` | dollars | 0 | 1e+07 | yes |
| 318 | `history.attention_mean.50` | dollars | 0 | 1e+07 | yes |
| 319 | `history.attention_mean.51` | dollars | 0 | 1e+07 | yes |
| 320 | `history.attention_mean.52` | dollars | 0 | 1e+07 | yes |
| 321 | `history.attention_mean.53` | dollars | 0 | 1e+07 | yes |
| 322 | `history.attention_mean.54` | dollars | 0 | 1e+07 | yes |
| 323 | `history.attention_mean.55` | dollars | 0 | 1e+07 | yes |
| 324 | `history.attention_mean.56` | dollars | 0 | 1e+07 | yes |
| 325 | `history.attention_mean.57` | dollars | 0 | 1e+07 | yes |
| 326 | `history.attention_mean.58` | dollars | 0 | 1e+07 | yes |
| 327 | `history.attention_mean.59` | dollars | 0 | 1e+07 | yes |
| 328 | `history.attention_mean.60` | dollars | 0 | 1e+07 | yes |
| 329 | `history.average_move.2` | price | 0 | 1000 | no |
| 330 | `history.average_move.3` | price | 0 | 1000 | no |
| 331 | `history.average_move.4` | price | 0 | 1000 | no |
| 332 | `history.average_move.5` | price | 0 | 1000 | no |
| 333 | `history.average_move.6` | price | 0 | 1000 | no |
| 334 | `history.average_move.7` | price | 0 | 1000 | no |
| 335 | `history.average_move.8` | price | 0 | 1000 | no |
| 336 | `history.average_move.9` | price | 0 | 1000 | no |
| 337 | `history.average_move.10` | price | 0 | 1000 | no |
| 338 | `history.average_move.11` | price | 0 | 1000 | no |
| 339 | `history.average_move.12` | price | 0 | 1000 | no |
| 340 | `history.average_move.13` | price | 0 | 1000 | no |
| 341 | `history.average_move.14` | price | 0 | 1000 | no |
| 342 | `history.average_move.15` | price | 0 | 1000 | no |
| 343 | `history.average_move.16` | price | 0 | 1000 | no |
| 344 | `history.average_move.17` | price | 0 | 1000 | no |
| 345 | `history.average_move.18` | price | 0 | 1000 | no |
| 346 | `history.average_move.19` | price | 0 | 1000 | no |
| 347 | `history.average_move.20` | price | 0 | 1000 | no |
| 348 | `history.average_move.21` | price | 0 | 1000 | no |
| 349 | `history.average_move.22` | price | 0 | 1000 | no |
| 350 | `history.average_move.23` | price | 0 | 1000 | no |
| 351 | `history.average_move.24` | price | 0 | 1000 | no |
| 352 | `history.average_move.25` | price | 0 | 1000 | no |
| 353 | `history.average_move.26` | price | 0 | 1000 | no |
| 354 | `history.average_move.27` | price | 0 | 1000 | no |
| 355 | `history.average_move.28` | price | 0 | 1000 | no |
| 356 | `history.average_move.29` | price | 0 | 1000 | no |
| 357 | `history.average_move.30` | price | 0 | 1000 | no |
| 358 | `history.average_move.31` | price | 0 | 1000 | no |
| 359 | `history.average_move.32` | price | 0 | 1000 | no |
| 360 | `history.swing_low.1.1` | price | 0 | 1000 | no |
| 361 | `history.swing_low.1.2` | price | 0 | 1000 | no |
| 362 | `history.swing_low.1.3` | price | 0 | 1000 | no |
| 363 | `history.swing_low.1.4` | price | 0 | 1000 | no |
| 364 | `history.swing_low.1.5` | price | 0 | 1000 | no |
| 365 | `history.swing_low.1.10` | price | 0 | 1000 | no |
| 366 | `history.swing_low.1.15` | price | 0 | 1000 | no |
| 367 | `history.swing_low.1.20` | price | 0 | 1000 | no |
| 368 | `history.swing_low.1.30` | price | 0 | 1000 | no |
| 369 | `history.swing_low.1.45` | price | 0 | 1000 | no |
| 370 | `history.swing_low.1.60` | price | 0 | 1000 | no |
| 371 | `history.swing_low.2.1` | price | 0 | 1000 | no |
| 372 | `history.swing_low.2.2` | price | 0 | 1000 | no |
| 373 | `history.swing_low.2.3` | price | 0 | 1000 | no |
| 374 | `history.swing_low.2.4` | price | 0 | 1000 | no |
| 375 | `history.swing_low.2.5` | price | 0 | 1000 | no |
| 376 | `history.swing_low.2.10` | price | 0 | 1000 | no |
| 377 | `history.swing_low.2.15` | price | 0 | 1000 | no |
| 378 | `history.swing_low.2.20` | price | 0 | 1000 | no |
| 379 | `history.swing_low.2.30` | price | 0 | 1000 | no |
| 380 | `history.swing_low.2.45` | price | 0 | 1000 | no |
| 381 | `history.swing_low.2.60` | price | 0 | 1000 | no |
| 382 | `history.swing_low.3.1` | price | 0 | 1000 | no |
| 383 | `history.swing_low.3.2` | price | 0 | 1000 | no |
| 384 | `history.swing_low.3.3` | price | 0 | 1000 | no |
| 385 | `history.swing_low.3.4` | price | 0 | 1000 | no |
| 386 | `history.swing_low.3.5` | price | 0 | 1000 | no |
| 387 | `history.swing_low.3.10` | price | 0 | 1000 | no |
| 388 | `history.swing_low.3.15` | price | 0 | 1000 | no |
| 389 | `history.swing_low.3.20` | price | 0 | 1000 | no |
| 390 | `history.swing_low.3.30` | price | 0 | 1000 | no |
| 391 | `history.swing_low.3.45` | price | 0 | 1000 | no |
| 392 | `history.swing_low.3.60` | price | 0 | 1000 | no |
| 393 | `history.swing_low.4.1` | price | 0 | 1000 | no |
| 394 | `history.swing_low.4.2` | price | 0 | 1000 | no |
| 395 | `history.swing_low.4.3` | price | 0 | 1000 | no |
| 396 | `history.swing_low.4.4` | price | 0 | 1000 | no |
| 397 | `history.swing_low.4.5` | price | 0 | 1000 | no |
| 398 | `history.swing_low.4.10` | price | 0 | 1000 | no |
| 399 | `history.swing_low.4.15` | price | 0 | 1000 | no |
| 400 | `history.swing_low.4.20` | price | 0 | 1000 | no |
| 401 | `history.swing_low.4.30` | price | 0 | 1000 | no |
| 402 | `history.swing_low.4.45` | price | 0 | 1000 | no |
| 403 | `history.swing_low.4.60` | price | 0 | 1000 | no |
| 404 | `history.swing_low.5.1` | price | 0 | 1000 | no |
| 405 | `history.swing_low.5.2` | price | 0 | 1000 | no |
| 406 | `history.swing_low.5.3` | price | 0 | 1000 | no |
| 407 | `history.swing_low.5.4` | price | 0 | 1000 | no |
| 408 | `history.swing_low.5.5` | price | 0 | 1000 | no |
| 409 | `history.swing_low.5.10` | price | 0 | 1000 | no |
| 410 | `history.swing_low.5.15` | price | 0 | 1000 | no |
| 411 | `history.swing_low.5.20` | price | 0 | 1000 | no |
| 412 | `history.swing_low.5.30` | price | 0 | 1000 | no |
| 413 | `history.swing_low.5.45` | price | 0 | 1000 | no |
| 414 | `history.swing_low.5.60` | price | 0 | 1000 | no |
| 415 | `history.swing_low.10.1` | price | 0 | 1000 | no |
| 416 | `history.swing_low.10.2` | price | 0 | 1000 | no |
| 417 | `history.swing_low.10.3` | price | 0 | 1000 | no |
| 418 | `history.swing_low.10.4` | price | 0 | 1000 | no |
| 419 | `history.swing_low.10.5` | price | 0 | 1000 | no |
| 420 | `history.swing_low.10.10` | price | 0 | 1000 | no |
| 421 | `history.swing_low.10.15` | price | 0 | 1000 | no |
| 422 | `history.swing_low.10.20` | price | 0 | 1000 | no |
| 423 | `history.swing_low.10.30` | price | 0 | 1000 | no |
| 424 | `history.swing_low.10.45` | price | 0 | 1000 | no |
| 425 | `history.swing_low.10.60` | price | 0 | 1000 | no |
| 426 | `history.swing_low.15.1` | price | 0 | 1000 | no |
| 427 | `history.swing_low.15.2` | price | 0 | 1000 | no |
| 428 | `history.swing_low.15.3` | price | 0 | 1000 | no |
| 429 | `history.swing_low.15.4` | price | 0 | 1000 | no |
| 430 | `history.swing_low.15.5` | price | 0 | 1000 | no |
| 431 | `history.swing_low.15.10` | price | 0 | 1000 | no |
| 432 | `history.swing_low.15.15` | price | 0 | 1000 | no |
| 433 | `history.swing_low.15.20` | price | 0 | 1000 | no |
| 434 | `history.swing_low.15.30` | price | 0 | 1000 | no |
| 435 | `history.swing_low.15.45` | price | 0 | 1000 | no |
| 436 | `history.swing_low.15.60` | price | 0 | 1000 | no |
| 437 | `history.swing_low.20.1` | price | 0 | 1000 | no |
| 438 | `history.swing_low.20.2` | price | 0 | 1000 | no |
| 439 | `history.swing_low.20.3` | price | 0 | 1000 | no |
| 440 | `history.swing_low.20.4` | price | 0 | 1000 | no |
| 441 | `history.swing_low.20.5` | price | 0 | 1000 | no |
| 442 | `history.swing_low.20.10` | price | 0 | 1000 | no |
| 443 | `history.swing_low.20.15` | price | 0 | 1000 | no |
| 444 | `history.swing_low.20.20` | price | 0 | 1000 | no |
| 445 | `history.swing_low.20.30` | price | 0 | 1000 | no |
| 446 | `history.swing_low.20.45` | price | 0 | 1000 | no |
| 447 | `history.swing_low.20.60` | price | 0 | 1000 | no |
| 448 | `history.swing_low.30.1` | price | 0 | 1000 | no |
| 449 | `history.swing_low.30.2` | price | 0 | 1000 | no |
| 450 | `history.swing_low.30.3` | price | 0 | 1000 | no |
| 451 | `history.swing_low.30.4` | price | 0 | 1000 | no |
| 452 | `history.swing_low.30.5` | price | 0 | 1000 | no |
| 453 | `history.swing_low.30.10` | price | 0 | 1000 | no |
| 454 | `history.swing_low.30.15` | price | 0 | 1000 | no |
| 455 | `history.swing_low.30.20` | price | 0 | 1000 | no |
| 456 | `history.swing_low.30.30` | price | 0 | 1000 | no |
| 457 | `history.swing_low.30.45` | price | 0 | 1000 | no |
| 458 | `history.swing_low.30.60` | price | 0 | 1000 | no |
| 459 | `history.swing_low.45.1` | price | 0 | 1000 | no |
| 460 | `history.swing_low.45.2` | price | 0 | 1000 | no |
| 461 | `history.swing_low.45.3` | price | 0 | 1000 | no |
| 462 | `history.swing_low.45.4` | price | 0 | 1000 | no |
| 463 | `history.swing_low.45.5` | price | 0 | 1000 | no |
| 464 | `history.swing_low.45.10` | price | 0 | 1000 | no |
| 465 | `history.swing_low.45.15` | price | 0 | 1000 | no |
| 466 | `history.swing_low.45.20` | price | 0 | 1000 | no |
| 467 | `history.swing_low.45.30` | price | 0 | 1000 | no |
| 468 | `history.swing_low.45.45` | price | 0 | 1000 | no |
| 469 | `history.swing_low.45.60` | price | 0 | 1000 | no |
| 470 | `history.swing_low.60.1` | price | 0 | 1000 | no |
| 471 | `history.swing_low.60.2` | price | 0 | 1000 | no |
| 472 | `history.swing_low.60.3` | price | 0 | 1000 | no |
| 473 | `history.swing_low.60.4` | price | 0 | 1000 | no |
| 474 | `history.swing_low.60.5` | price | 0 | 1000 | no |
| 475 | `history.swing_low.60.10` | price | 0 | 1000 | no |
| 476 | `history.swing_low.60.15` | price | 0 | 1000 | no |
| 477 | `history.swing_low.60.20` | price | 0 | 1000 | no |
| 478 | `history.swing_low.60.30` | price | 0 | 1000 | no |
| 479 | `history.swing_low.60.45` | price | 0 | 1000 | no |
| 480 | `history.swing_low.60.60` | price | 0 | 1000 | no |
| 481 | `history.recent_high.1.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 482 | `history.recent_high.2.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 483 | `history.recent_high.3.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 484 | `history.recent_high.4.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 485 | `history.recent_high.5.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 486 | `history.recent_high.6.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 487 | `history.recent_high.7.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 488 | `history.recent_high.8.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 489 | `history.recent_high.9.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 490 | `history.recent_high.10.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 491 | `history.recent_high.11.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 492 | `history.recent_high.12.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 493 | `history.recent_high.13.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 494 | `history.recent_high.14.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 495 | `history.recent_high.15.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 496 | `history.recent_high.16.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 497 | `history.recent_high.17.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 498 | `history.recent_high.18.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 499 | `history.recent_high.19.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 500 | `history.recent_high.20.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 501 | `history.recent_high.21.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 502 | `history.recent_high.22.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 503 | `history.recent_high.23.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 504 | `history.recent_high.24.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 505 | `history.recent_high.25.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 506 | `history.recent_high.26.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 507 | `history.recent_high.27.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 508 | `history.recent_high.28.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 509 | `history.recent_high.29.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 510 | `history.recent_high.30.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 511 | `history.recent_high.31.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 512 | `history.recent_high.32.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 513 | `history.recent_high.33.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 514 | `history.recent_high.34.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 515 | `history.recent_high.35.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 516 | `history.recent_high.36.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 517 | `history.recent_high.37.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 518 | `history.recent_high.38.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 519 | `history.recent_high.39.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 520 | `history.recent_high.40.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 521 | `history.recent_high.41.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 522 | `history.recent_high.42.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 523 | `history.recent_high.43.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 524 | `history.recent_high.44.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 525 | `history.recent_high.45.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 526 | `history.recent_high.46.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 527 | `history.recent_high.47.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 528 | `history.recent_high.48.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 529 | `history.recent_high.49.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 530 | `history.recent_high.50.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 531 | `history.recent_high.51.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 532 | `history.recent_high.52.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 533 | `history.recent_high.53.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 534 | `history.recent_high.54.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 535 | `history.recent_high.55.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 536 | `history.recent_high.56.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 537 | `history.recent_high.57.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 538 | `history.recent_high.58.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 539 | `history.recent_high.59.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 540 | `history.recent_high.60.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 541 | `history.momentum_close.1.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 542 | `history.momentum_close.2.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 543 | `history.momentum_close.3.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 544 | `history.momentum_close.4.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 545 | `history.momentum_close.5.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 546 | `history.momentum_close.6.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 547 | `history.momentum_close.7.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 548 | `history.momentum_close.8.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 549 | `history.momentum_close.9.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 550 | `history.momentum_close.10.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 551 | `history.momentum_close.11.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 552 | `history.momentum_close.12.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 553 | `history.momentum_close.13.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 554 | `history.momentum_close.14.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 555 | `history.momentum_close.15.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 556 | `history.momentum_close.16.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 557 | `history.momentum_close.17.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 558 | `history.momentum_close.18.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 559 | `history.momentum_close.19.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 560 | `history.momentum_close.20.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 561 | `history.momentum_close.21.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 562 | `history.momentum_close.22.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 563 | `history.momentum_close.23.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 564 | `history.momentum_close.24.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 565 | `history.momentum_close.25.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 566 | `history.momentum_close.26.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 567 | `history.momentum_close.27.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 568 | `history.momentum_close.28.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 569 | `history.momentum_close.29.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 570 | `history.momentum_close.30.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 571 | `history.momentum_close.31.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 572 | `history.momentum_close.32.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 573 | `history.momentum_close.33.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 574 | `history.momentum_close.34.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 575 | `history.momentum_close.35.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 576 | `history.momentum_close.36.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 577 | `history.momentum_close.37.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 578 | `history.momentum_close.38.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 579 | `history.momentum_close.39.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 580 | `history.momentum_close.40.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 581 | `history.momentum_close.41.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 582 | `history.momentum_close.42.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 583 | `history.momentum_close.43.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 584 | `history.momentum_close.44.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 585 | `history.momentum_close.45.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 586 | `history.momentum_close.46.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 587 | `history.momentum_close.47.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 588 | `history.momentum_close.48.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 589 | `history.momentum_close.49.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 590 | `history.momentum_close.50.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 591 | `history.momentum_close.51.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 592 | `history.momentum_close.52.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 593 | `history.momentum_close.53.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 594 | `history.momentum_close.54.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 595 | `history.momentum_close.55.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 596 | `history.momentum_close.56.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 597 | `history.momentum_close.57.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 598 | `history.momentum_close.58.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 599 | `history.momentum_close.59.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 600 | `history.momentum_close.60.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 601 | `history.average_move.2.fraction_of_close` | ratio | 0 | 0.5 | yes |
| 602 | `history.average_move.3.fraction_of_close` | ratio | 0 | 0.5 | yes |
| 603 | `history.average_move.4.fraction_of_close` | ratio | 0 | 0.5 | yes |
| 604 | `history.average_move.5.fraction_of_close` | ratio | 0 | 0.5 | yes |
| 605 | `history.average_move.6.fraction_of_close` | ratio | 0 | 0.5 | yes |
| 606 | `history.average_move.7.fraction_of_close` | ratio | 0 | 0.5 | yes |
| 607 | `history.average_move.8.fraction_of_close` | ratio | 0 | 0.5 | yes |
| 608 | `history.average_move.9.fraction_of_close` | ratio | 0 | 0.5 | yes |
| 609 | `history.average_move.10.fraction_of_close` | ratio | 0 | 0.5 | yes |
| 610 | `history.average_move.11.fraction_of_close` | ratio | 0 | 0.5 | yes |
| 611 | `history.average_move.12.fraction_of_close` | ratio | 0 | 0.5 | yes |
| 612 | `history.average_move.13.fraction_of_close` | ratio | 0 | 0.5 | yes |
| 613 | `history.average_move.14.fraction_of_close` | ratio | 0 | 0.5 | yes |
| 614 | `history.average_move.15.fraction_of_close` | ratio | 0 | 0.5 | yes |
| 615 | `history.average_move.16.fraction_of_close` | ratio | 0 | 0.5 | yes |
| 616 | `history.average_move.17.fraction_of_close` | ratio | 0 | 0.5 | yes |
| 617 | `history.average_move.18.fraction_of_close` | ratio | 0 | 0.5 | yes |
| 618 | `history.average_move.19.fraction_of_close` | ratio | 0 | 0.5 | yes |
| 619 | `history.average_move.20.fraction_of_close` | ratio | 0 | 0.5 | yes |
| 620 | `history.average_move.21.fraction_of_close` | ratio | 0 | 0.5 | yes |
| 621 | `history.average_move.22.fraction_of_close` | ratio | 0 | 0.5 | yes |
| 622 | `history.average_move.23.fraction_of_close` | ratio | 0 | 0.5 | yes |
| 623 | `history.average_move.24.fraction_of_close` | ratio | 0 | 0.5 | yes |
| 624 | `history.average_move.25.fraction_of_close` | ratio | 0 | 0.5 | yes |
| 625 | `history.average_move.26.fraction_of_close` | ratio | 0 | 0.5 | yes |
| 626 | `history.average_move.27.fraction_of_close` | ratio | 0 | 0.5 | yes |
| 627 | `history.average_move.28.fraction_of_close` | ratio | 0 | 0.5 | yes |
| 628 | `history.average_move.29.fraction_of_close` | ratio | 0 | 0.5 | yes |
| 629 | `history.average_move.30.fraction_of_close` | ratio | 0 | 0.5 | yes |
| 630 | `history.average_move.31.fraction_of_close` | ratio | 0 | 0.5 | yes |
| 631 | `history.average_move.32.fraction_of_close` | ratio | 0 | 0.5 | yes |
| 632 | `history.swing_low.1.1.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 633 | `history.swing_low.1.2.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 634 | `history.swing_low.1.3.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 635 | `history.swing_low.1.4.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 636 | `history.swing_low.1.5.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 637 | `history.swing_low.1.10.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 638 | `history.swing_low.1.15.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 639 | `history.swing_low.1.20.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 640 | `history.swing_low.1.30.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 641 | `history.swing_low.1.45.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 642 | `history.swing_low.1.60.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 643 | `history.swing_low.2.1.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 644 | `history.swing_low.2.2.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 645 | `history.swing_low.2.3.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 646 | `history.swing_low.2.4.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 647 | `history.swing_low.2.5.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 648 | `history.swing_low.2.10.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 649 | `history.swing_low.2.15.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 650 | `history.swing_low.2.20.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 651 | `history.swing_low.2.30.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 652 | `history.swing_low.2.45.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 653 | `history.swing_low.2.60.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 654 | `history.swing_low.3.1.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 655 | `history.swing_low.3.2.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 656 | `history.swing_low.3.3.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 657 | `history.swing_low.3.4.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 658 | `history.swing_low.3.5.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 659 | `history.swing_low.3.10.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 660 | `history.swing_low.3.15.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 661 | `history.swing_low.3.20.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 662 | `history.swing_low.3.30.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 663 | `history.swing_low.3.45.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 664 | `history.swing_low.3.60.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 665 | `history.swing_low.4.1.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 666 | `history.swing_low.4.2.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 667 | `history.swing_low.4.3.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 668 | `history.swing_low.4.4.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 669 | `history.swing_low.4.5.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 670 | `history.swing_low.4.10.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 671 | `history.swing_low.4.15.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 672 | `history.swing_low.4.20.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 673 | `history.swing_low.4.30.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 674 | `history.swing_low.4.45.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 675 | `history.swing_low.4.60.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 676 | `history.swing_low.5.1.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 677 | `history.swing_low.5.2.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 678 | `history.swing_low.5.3.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 679 | `history.swing_low.5.4.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 680 | `history.swing_low.5.5.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 681 | `history.swing_low.5.10.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 682 | `history.swing_low.5.15.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 683 | `history.swing_low.5.20.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 684 | `history.swing_low.5.30.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 685 | `history.swing_low.5.45.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 686 | `history.swing_low.5.60.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 687 | `history.swing_low.10.1.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 688 | `history.swing_low.10.2.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 689 | `history.swing_low.10.3.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 690 | `history.swing_low.10.4.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 691 | `history.swing_low.10.5.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 692 | `history.swing_low.10.10.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 693 | `history.swing_low.10.15.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 694 | `history.swing_low.10.20.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 695 | `history.swing_low.10.30.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 696 | `history.swing_low.10.45.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 697 | `history.swing_low.10.60.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 698 | `history.swing_low.15.1.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 699 | `history.swing_low.15.2.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 700 | `history.swing_low.15.3.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 701 | `history.swing_low.15.4.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 702 | `history.swing_low.15.5.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 703 | `history.swing_low.15.10.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 704 | `history.swing_low.15.15.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 705 | `history.swing_low.15.20.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 706 | `history.swing_low.15.30.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 707 | `history.swing_low.15.45.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 708 | `history.swing_low.15.60.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 709 | `history.swing_low.20.1.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 710 | `history.swing_low.20.2.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 711 | `history.swing_low.20.3.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 712 | `history.swing_low.20.4.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 713 | `history.swing_low.20.5.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 714 | `history.swing_low.20.10.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 715 | `history.swing_low.20.15.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 716 | `history.swing_low.20.20.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 717 | `history.swing_low.20.30.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 718 | `history.swing_low.20.45.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 719 | `history.swing_low.20.60.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 720 | `history.swing_low.30.1.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 721 | `history.swing_low.30.2.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 722 | `history.swing_low.30.3.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 723 | `history.swing_low.30.4.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 724 | `history.swing_low.30.5.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 725 | `history.swing_low.30.10.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 726 | `history.swing_low.30.15.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 727 | `history.swing_low.30.20.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 728 | `history.swing_low.30.30.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 729 | `history.swing_low.30.45.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 730 | `history.swing_low.30.60.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 731 | `history.swing_low.45.1.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 732 | `history.swing_low.45.2.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 733 | `history.swing_low.45.3.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 734 | `history.swing_low.45.4.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 735 | `history.swing_low.45.5.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 736 | `history.swing_low.45.10.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 737 | `history.swing_low.45.15.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 738 | `history.swing_low.45.20.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 739 | `history.swing_low.45.30.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 740 | `history.swing_low.45.45.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 741 | `history.swing_low.45.60.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 742 | `history.swing_low.60.1.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 743 | `history.swing_low.60.2.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 744 | `history.swing_low.60.3.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 745 | `history.swing_low.60.4.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 746 | `history.swing_low.60.5.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 747 | `history.swing_low.60.10.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 748 | `history.swing_low.60.15.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 749 | `history.swing_low.60.20.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 750 | `history.swing_low.60.30.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 751 | `history.swing_low.60.45.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 752 | `history.swing_low.60.60.relative_to_close` | ratio | -0.5 | 0.5 | yes |
| 753 | `market.bid_relative` | ratio | -0.1 | 0.1 | yes |
| 754 | `market.ask_relative` | ratio | -0.1 | 0.1 | yes |
| 755 | `market.spread_fraction` | ratio | 0 | 0.1 | yes |
| 756 | `market.quote_age_seconds` | duration | 0 | 3600 | yes |
| 757 | `market.log_execution_volume` | log_shares | 0 | 30 | yes |
| 758 | `market.log_execution_notional` | log_dollars | 0 | 30 | yes |
| 759 | `market.log_execution_trade_count` | log_count | 0 | 20 | yes |
| 760 | `market.bid` | price | 0 | 1000 | no |
| 761 | `market.ask` | price | 0 | 1000 | no |
| 762 | `market.spread` | price | 0 | 100 | no |
| 763 | `market.close` | price | 0 | 1000 | no |
| 764 | `indicator.ema.2` | price | 0 | 1000 | no |
| 765 | `indicator.ema.2.relative` | ratio | -0.5 | 0.5 | yes |
| 766 | `indicator.ema.3` | price | 0 | 1000 | no |
| 767 | `indicator.ema.3.relative` | ratio | -0.5 | 0.5 | yes |
| 768 | `indicator.ema.5` | price | 0 | 1000 | no |
| 769 | `indicator.ema.5.relative` | ratio | -0.5 | 0.5 | yes |
| 770 | `indicator.ema.7` | price | 0 | 1000 | no |
| 771 | `indicator.ema.7.relative` | ratio | -0.5 | 0.5 | yes |
| 772 | `indicator.ema.9` | price | 0 | 1000 | no |
| 773 | `indicator.ema.9.relative` | ratio | -0.5 | 0.5 | yes |
| 774 | `indicator.ema.10` | price | 0 | 1000 | no |
| 775 | `indicator.ema.10.relative` | ratio | -0.5 | 0.5 | yes |
| 776 | `indicator.ema.12` | price | 0 | 1000 | no |
| 777 | `indicator.ema.12.relative` | ratio | -0.5 | 0.5 | yes |
| 778 | `indicator.ema.14` | price | 0 | 1000 | no |
| 779 | `indicator.ema.14.relative` | ratio | -0.5 | 0.5 | yes |
| 780 | `indicator.ema.15` | price | 0 | 1000 | no |
| 781 | `indicator.ema.15.relative` | ratio | -0.5 | 0.5 | yes |
| 782 | `indicator.ema.20` | price | 0 | 1000 | no |
| 783 | `indicator.ema.20.relative` | ratio | -0.5 | 0.5 | yes |
| 784 | `indicator.ema.26` | price | 0 | 1000 | no |
| 785 | `indicator.ema.26.relative` | ratio | -0.5 | 0.5 | yes |
| 786 | `indicator.ema.30` | price | 0 | 1000 | no |
| 787 | `indicator.ema.30.relative` | ratio | -0.5 | 0.5 | yes |
| 788 | `indicator.ema.45` | price | 0 | 1000 | no |
| 789 | `indicator.ema.45.relative` | ratio | -0.5 | 0.5 | yes |
| 790 | `indicator.ema.50` | price | 0 | 1000 | no |
| 791 | `indicator.ema.50.relative` | ratio | -0.5 | 0.5 | yes |
| 792 | `indicator.ema.60` | price | 0 | 1000 | no |
| 793 | `indicator.ema.60.relative` | ratio | -0.5 | 0.5 | yes |
| 794 | `indicator.rsi.2` | ratio | 0 | 1 | yes |
| 795 | `indicator.rsi.3` | ratio | 0 | 1 | yes |
| 796 | `indicator.rsi.5` | ratio | 0 | 1 | yes |
| 797 | `indicator.rsi.7` | ratio | 0 | 1 | yes |
| 798 | `indicator.rsi.9` | ratio | 0 | 1 | yes |
| 799 | `indicator.rsi.10` | ratio | 0 | 1 | yes |
| 800 | `indicator.rsi.12` | ratio | 0 | 1 | yes |
| 801 | `indicator.rsi.14` | ratio | 0 | 1 | yes |
| 802 | `indicator.rsi.15` | ratio | 0 | 1 | yes |
| 803 | `indicator.rsi.20` | ratio | 0 | 1 | yes |
| 804 | `indicator.rsi.26` | ratio | 0 | 1 | yes |
| 805 | `indicator.rsi.30` | ratio | 0 | 1 | yes |
| 806 | `indicator.rsi.45` | ratio | 0 | 1 | yes |
| 807 | `indicator.rsi.50` | ratio | 0 | 1 | yes |
| 808 | `indicator.rsi.60` | ratio | 0 | 1 | yes |
| 809 | `indicator.atr.2` | price | 0 | 1000 | no |
| 810 | `indicator.atr.2.relative` | ratio | 0 | 1 | yes |
| 811 | `indicator.atr.3` | price | 0 | 1000 | no |
| 812 | `indicator.atr.3.relative` | ratio | 0 | 1 | yes |
| 813 | `indicator.atr.5` | price | 0 | 1000 | no |
| 814 | `indicator.atr.5.relative` | ratio | 0 | 1 | yes |
| 815 | `indicator.atr.7` | price | 0 | 1000 | no |
| 816 | `indicator.atr.7.relative` | ratio | 0 | 1 | yes |
| 817 | `indicator.atr.9` | price | 0 | 1000 | no |
| 818 | `indicator.atr.9.relative` | ratio | 0 | 1 | yes |
| 819 | `indicator.atr.10` | price | 0 | 1000 | no |
| 820 | `indicator.atr.10.relative` | ratio | 0 | 1 | yes |
| 821 | `indicator.atr.12` | price | 0 | 1000 | no |
| 822 | `indicator.atr.12.relative` | ratio | 0 | 1 | yes |
| 823 | `indicator.atr.14` | price | 0 | 1000 | no |
| 824 | `indicator.atr.14.relative` | ratio | 0 | 1 | yes |
| 825 | `indicator.atr.15` | price | 0 | 1000 | no |
| 826 | `indicator.atr.15.relative` | ratio | 0 | 1 | yes |
| 827 | `indicator.atr.20` | price | 0 | 1000 | no |
| 828 | `indicator.atr.20.relative` | ratio | 0 | 1 | yes |
| 829 | `indicator.atr.26` | price | 0 | 1000 | no |
| 830 | `indicator.atr.26.relative` | ratio | 0 | 1 | yes |
| 831 | `indicator.atr.30` | price | 0 | 1000 | no |
| 832 | `indicator.atr.30.relative` | ratio | 0 | 1 | yes |
| 833 | `indicator.atr.45` | price | 0 | 1000 | no |
| 834 | `indicator.atr.45.relative` | ratio | 0 | 1 | yes |
| 835 | `indicator.atr.50` | price | 0 | 1000 | no |
| 836 | `indicator.atr.50.relative` | ratio | 0 | 1 | yes |
| 837 | `indicator.atr.60` | price | 0 | 1000 | no |
| 838 | `indicator.atr.60.relative` | ratio | 0 | 1 | yes |
| 839 | `indicator.macd_line.3.15.relative` | ratio | -0.5 | 0.5 | yes |
| 840 | `indicator.macd_signal.3.15.5.relative` | ratio | -0.5 | 0.5 | yes |
| 841 | `indicator.macd_histogram.3.15.5.relative` | ratio | -0.5 | 0.5 | yes |
| 842 | `indicator.macd_signal.3.15.9.relative` | ratio | -0.5 | 0.5 | yes |
| 843 | `indicator.macd_histogram.3.15.9.relative` | ratio | -0.5 | 0.5 | yes |
| 844 | `indicator.macd_signal.3.15.20.relative` | ratio | -0.5 | 0.5 | yes |
| 845 | `indicator.macd_histogram.3.15.20.relative` | ratio | -0.5 | 0.5 | yes |
| 846 | `indicator.macd_line.3.26.relative` | ratio | -0.5 | 0.5 | yes |
| 847 | `indicator.macd_signal.3.26.5.relative` | ratio | -0.5 | 0.5 | yes |
| 848 | `indicator.macd_histogram.3.26.5.relative` | ratio | -0.5 | 0.5 | yes |
| 849 | `indicator.macd_signal.3.26.9.relative` | ratio | -0.5 | 0.5 | yes |
| 850 | `indicator.macd_histogram.3.26.9.relative` | ratio | -0.5 | 0.5 | yes |
| 851 | `indicator.macd_signal.3.26.20.relative` | ratio | -0.5 | 0.5 | yes |
| 852 | `indicator.macd_histogram.3.26.20.relative` | ratio | -0.5 | 0.5 | yes |
| 853 | `indicator.macd_line.3.50.relative` | ratio | -0.5 | 0.5 | yes |
| 854 | `indicator.macd_signal.3.50.5.relative` | ratio | -0.5 | 0.5 | yes |
| 855 | `indicator.macd_histogram.3.50.5.relative` | ratio | -0.5 | 0.5 | yes |
| 856 | `indicator.macd_signal.3.50.9.relative` | ratio | -0.5 | 0.5 | yes |
| 857 | `indicator.macd_histogram.3.50.9.relative` | ratio | -0.5 | 0.5 | yes |
| 858 | `indicator.macd_signal.3.50.20.relative` | ratio | -0.5 | 0.5 | yes |
| 859 | `indicator.macd_histogram.3.50.20.relative` | ratio | -0.5 | 0.5 | yes |
| 860 | `indicator.macd_line.7.15.relative` | ratio | -0.5 | 0.5 | yes |
| 861 | `indicator.macd_signal.7.15.5.relative` | ratio | -0.5 | 0.5 | yes |
| 862 | `indicator.macd_histogram.7.15.5.relative` | ratio | -0.5 | 0.5 | yes |
| 863 | `indicator.macd_signal.7.15.9.relative` | ratio | -0.5 | 0.5 | yes |
| 864 | `indicator.macd_histogram.7.15.9.relative` | ratio | -0.5 | 0.5 | yes |
| 865 | `indicator.macd_signal.7.15.20.relative` | ratio | -0.5 | 0.5 | yes |
| 866 | `indicator.macd_histogram.7.15.20.relative` | ratio | -0.5 | 0.5 | yes |
| 867 | `indicator.macd_line.7.26.relative` | ratio | -0.5 | 0.5 | yes |
| 868 | `indicator.macd_signal.7.26.5.relative` | ratio | -0.5 | 0.5 | yes |
| 869 | `indicator.macd_histogram.7.26.5.relative` | ratio | -0.5 | 0.5 | yes |
| 870 | `indicator.macd_signal.7.26.9.relative` | ratio | -0.5 | 0.5 | yes |
| 871 | `indicator.macd_histogram.7.26.9.relative` | ratio | -0.5 | 0.5 | yes |
| 872 | `indicator.macd_signal.7.26.20.relative` | ratio | -0.5 | 0.5 | yes |
| 873 | `indicator.macd_histogram.7.26.20.relative` | ratio | -0.5 | 0.5 | yes |
| 874 | `indicator.macd_line.7.50.relative` | ratio | -0.5 | 0.5 | yes |
| 875 | `indicator.macd_signal.7.50.5.relative` | ratio | -0.5 | 0.5 | yes |
| 876 | `indicator.macd_histogram.7.50.5.relative` | ratio | -0.5 | 0.5 | yes |
| 877 | `indicator.macd_signal.7.50.9.relative` | ratio | -0.5 | 0.5 | yes |
| 878 | `indicator.macd_histogram.7.50.9.relative` | ratio | -0.5 | 0.5 | yes |
| 879 | `indicator.macd_signal.7.50.20.relative` | ratio | -0.5 | 0.5 | yes |
| 880 | `indicator.macd_histogram.7.50.20.relative` | ratio | -0.5 | 0.5 | yes |
| 881 | `indicator.macd_line.12.15.relative` | ratio | -0.5 | 0.5 | yes |
| 882 | `indicator.macd_signal.12.15.5.relative` | ratio | -0.5 | 0.5 | yes |
| 883 | `indicator.macd_histogram.12.15.5.relative` | ratio | -0.5 | 0.5 | yes |
| 884 | `indicator.macd_signal.12.15.9.relative` | ratio | -0.5 | 0.5 | yes |
| 885 | `indicator.macd_histogram.12.15.9.relative` | ratio | -0.5 | 0.5 | yes |
| 886 | `indicator.macd_signal.12.15.20.relative` | ratio | -0.5 | 0.5 | yes |
| 887 | `indicator.macd_histogram.12.15.20.relative` | ratio | -0.5 | 0.5 | yes |
| 888 | `indicator.macd_line.12.26.relative` | ratio | -0.5 | 0.5 | yes |
| 889 | `indicator.macd_signal.12.26.5.relative` | ratio | -0.5 | 0.5 | yes |
| 890 | `indicator.macd_histogram.12.26.5.relative` | ratio | -0.5 | 0.5 | yes |
| 891 | `indicator.macd_signal.12.26.9.relative` | ratio | -0.5 | 0.5 | yes |
| 892 | `indicator.macd_histogram.12.26.9.relative` | ratio | -0.5 | 0.5 | yes |
| 893 | `indicator.macd_signal.12.26.20.relative` | ratio | -0.5 | 0.5 | yes |
| 894 | `indicator.macd_histogram.12.26.20.relative` | ratio | -0.5 | 0.5 | yes |
| 895 | `indicator.macd_line.12.50.relative` | ratio | -0.5 | 0.5 | yes |
| 896 | `indicator.macd_signal.12.50.5.relative` | ratio | -0.5 | 0.5 | yes |
| 897 | `indicator.macd_histogram.12.50.5.relative` | ratio | -0.5 | 0.5 | yes |
| 898 | `indicator.macd_signal.12.50.9.relative` | ratio | -0.5 | 0.5 | yes |
| 899 | `indicator.macd_histogram.12.50.9.relative` | ratio | -0.5 | 0.5 | yes |
| 900 | `indicator.macd_signal.12.50.20.relative` | ratio | -0.5 | 0.5 | yes |
| 901 | `indicator.macd_histogram.12.50.20.relative` | ratio | -0.5 | 0.5 | yes |
| 902 | `enriched.return.5s` | ratio | -0.5 | 1 | yes |
| 903 | `enriched.volatility.5s` | ratio | 0 | 1 | yes |
| 904 | `enriched.return.15s` | ratio | -0.5 | 1 | yes |
| 905 | `enriched.volatility.15s` | ratio | 0 | 1 | yes |
| 906 | `enriched.return.60s` | ratio | -0.5 | 1 | yes |
| 907 | `enriched.volatility.60s` | ratio | 0 | 1 | yes |
| 908 | `enriched.return.300s` | ratio | -0.5 | 1 | yes |
| 909 | `enriched.volatility.300s` | ratio | 0 | 1 | yes |
| 910 | `enriched.activity.volume_acceleration` | ratio | -0.5 | 1 | yes |
| 911 | `enriched.activity.trade_acceleration` | ratio | -0.5 | 1 | yes |
| 912 | `enriched.execution.volatility_fee_ratio` | ratio | 0 | 100 | yes |
| 913 | `enriched.candle.5s.body` | ratio | -0.5 | 1 | yes |
| 914 | `enriched.candle.5s.range` | ratio | 0 | 1 | yes |
| 915 | `enriched.candle.5s.close_location` | ratio | 0 | 1 | yes |
| 916 | `enriched.candle.5s.return` | ratio | -0.5 | 1 | yes |
| 917 | `enriched.candle.5s.log_volume` | ratio | 0 | 30 | yes |
| 918 | `enriched.candle.5s.log_trades` | ratio | 0 | 30 | yes |
| 919 | `enriched.candle.5s.active_fraction` | ratio | 0 | 1 | yes |
| 920 | `enriched.candle.15s.body` | ratio | -0.5 | 1 | yes |
| 921 | `enriched.candle.15s.range` | ratio | 0 | 1 | yes |
| 922 | `enriched.candle.15s.close_location` | ratio | 0 | 1 | yes |
| 923 | `enriched.candle.15s.return` | ratio | -0.5 | 1 | yes |
| 924 | `enriched.candle.15s.log_volume` | ratio | 0 | 30 | yes |
| 925 | `enriched.candle.15s.log_trades` | ratio | 0 | 30 | yes |
| 926 | `enriched.candle.15s.active_fraction` | ratio | 0 | 1 | yes |
| 927 | `enriched.candle.60s.body` | ratio | -0.5 | 1 | yes |
| 928 | `enriched.candle.60s.range` | ratio | 0 | 1 | yes |
| 929 | `enriched.candle.60s.close_location` | ratio | 0 | 1 | yes |
| 930 | `enriched.candle.60s.return` | ratio | -0.5 | 1 | yes |
| 931 | `enriched.candle.60s.log_volume` | ratio | 0 | 30 | yes |
| 932 | `enriched.candle.60s.log_trades` | ratio | 0 | 30 | yes |
| 933 | `enriched.candle.60s.active_fraction` | ratio | 0 | 1 | yes |
| 934 | `enriched.context.15s.median_return` | ratio | -0.5 | 1 | yes |
| 935 | `enriched.context.15s.mean_return` | ratio | -0.5 | 1 | yes |
| 936 | `enriched.context.15s.advancing_fraction` | ratio | 0 | 1 | yes |
| 937 | `enriched.context.15s.dispersion` | ratio | 0 | 1 | yes |
| 938 | `enriched.context.15s.coverage` | ratio | 0 | 1 | yes |
| 939 | `enriched.context.15s.relative_return` | ratio | -0.5 | 1 | yes |
| 940 | `enriched.context.15s.return_percentile` | ratio | 0 | 1 | yes |
| 941 | `enriched.context.60s.median_return` | ratio | -0.5 | 1 | yes |
| 942 | `enriched.context.60s.mean_return` | ratio | -0.5 | 1 | yes |
| 943 | `enriched.context.60s.advancing_fraction` | ratio | 0 | 1 | yes |
| 944 | `enriched.context.60s.dispersion` | ratio | 0 | 1 | yes |
| 945 | `enriched.context.60s.coverage` | ratio | 0 | 1 | yes |
| 946 | `enriched.context.60s.relative_return` | ratio | -0.5 | 1 | yes |
| 947 | `enriched.context.60s.return_percentile` | ratio | 0 | 1 | yes |
| 948 | `enriched.context.300s.median_return` | ratio | -0.5 | 1 | yes |
| 949 | `enriched.context.300s.mean_return` | ratio | -0.5 | 1 | yes |
| 950 | `enriched.context.300s.advancing_fraction` | ratio | 0 | 1 | yes |
| 951 | `enriched.context.300s.dispersion` | ratio | 0 | 1 | yes |
| 952 | `enriched.context.300s.coverage` | ratio | 0 | 1 | yes |
| 953 | `enriched.context.300s.relative_return` | ratio | -0.5 | 1 | yes |
| 954 | `enriched.context.300s.return_percentile` | ratio | 0 | 1 | yes |
| 955 | `enriched.rank.dollar_volume_60s_percentile` | ratio | 0 | 1 | yes |
| 956 | `enriched.rank.dollar_volume_60s_change_15s` | ratio | -0.5 | 1 | yes |

# V59 strategy, execution and optimization audit

Audit date: 2026-10-31. Source baseline: `99fb181db501dbd3ed8c871be7f85ccb2f465b3a`.

Design revision: 2026-10-31, following the user's clarification after the audit. The minimal close-time execution design below supersedes the earlier proposal to make candidates manage pending orders. Existing-code findings remain pinned to the source baseline; proposed interfaces are not deployed.

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

### Minimal design to implement (not current V59)

| Layer | Responsibility | Excluded responsibility |
|---|---|---|
| Candidate, feed-forward | Action, requested size, candidate-defined limit, stop and target prices; may update permitted position levels | No order queue, partial-fill management, cancellation/amendment policy or recurrent hidden state |
| Strategy boundary/controller | Check observable history/price feasibility, arbitrate conflicting actions/branches, enforce explicit cooldown and lifecycle rules, translate outputs into coherent intents | No invisible profitability/fitness filter or replacement stop invented after a fill |
| Minimal executor | At candle close: side/limit compatibility, positive finite valid quote, available size, inventory conservation and fees; report fills | No intrabar neural replay, hidden $300/20% admission caps, network/history reevaluation or market-impact/queue model |
| Deterministic position/order ledger | Maintain actual quantity, weighted entry, realized/unrealized P&L, age, stop, target and unfilled remainder | No optimization over order-management protocols |

Price outputs should be represented as causal relative offsets/ratios and converted to positive finite currency prices by the controller. Do not add arbitrary raw-price identifiers to the existing searchable market catalog. For the current long-only strategy, initial stop must be below the intended entry; target is a profit level above its entry basis. Define how the stop is checked against allowed fills when a buy receives price improvement; do not silently move the candidate's stop. Level updates and request/fill ordering require explicit tests. Target distances may legitimately exceed100%; old policy fraction bounds are not automatically the new output bounds.

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
4. **Align initialization and reproduction:** jointly sample/mutate/cross action and price outputs with admissibility probes, without profitability filtering; coherent branch selection, retry accounting, exact scheduled/realized counts and deterministic RNG. Enforce the new per-feature construction caps in the table through a versioned feature-aware constructor, mutations and crossover checks; use formula/domain checks and bounded training-only coverage to prevent impossible, duplicate or uninformative proposals before replay. Do not use legacy priors as value limits or silently clip market observations. Separate policy choices F12–F14 from necessary correctness repairs.
5. **Validate real runnable paths:** causal boundary fixtures; missing-history, invalid-stop, first-admission, routed fallback, exit conflict, cooldown, partial fills, limit waits, cancellation/amendment, cutoff/tail exhaustion, ledger conservation, all-founder inclusion, validation isolation, identity invalidation and deterministic recovery. Include a forced-overflow retry and shared-entry state-dependence check.
6. **Short performance qualification then explicit recovery:** use frozen representative candidates and two contrasting sessions; compare baseline/fixed wall time and all unchanged-case decisions/ledgers. For intentional behavior changes report changed requests/fills/P&L and reasons instead of claiming financial parity. A history/input/execution change invalidates affected financial receipts; retain genomes/RNG where schema-compatible, but replay affected founders before selection. Do not reuse old scores merely because genomes match.

No blanket removal of stop checks, no artificial fills, no forced trading on every day, no smaller networks/populations, no validation-driven repair, and no hidden fee/risk/score changes are acceptable acceleration shortcuts.

## Diagnostic schema to implement

Per candidate/session: raw proposal counts by action; decision opportunities; required-input-missing; stop-unavailable; stop-invalid; entry/exit-conflict; not-member; cooldown/age-block; pending-order-conflict; invalid/zero sizing; accepted intents; pending-unmarketable-limit; pending-missing-book; pending-no-size; partial/complete fills; cancelled remainder and reason; terminal unresolved quantity/orders. Keep unique primary outcomes separate from overlapping condition counts and repeated standing-order observations.

Every record must carry candidate/program identity, session/split, decision/execution contract versions, clock range and complete/partial coverage. Missing data is unavailable, never zero. Aggregate counters should be bounded tensors; sampled details can be persisted by the existing publication lane. UI should distinguish raw proposals, actionable requests, orders and positions.

## Full pinned market-input catalog

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

The table enumerates the pinned source catalog and adds proposed next-version construction caps. `Allowed` applies to all three channels. **Legacy prior lower/upper are not permitted-value boundaries.** New caps constrain candidate construction only as specified above. M/E label the basis; `forbidden` rows cannot be sampled. Numbers are rounded for display (six decimals); the implementation manifest should store the defining expressions/full precision, particularly logarithms and strict endpoints.

| ID | Name | Unit | Legacy prior lower | Legacy prior upper | Allowed | Next construction min | Next construction max | Basis |
|---:|---|---|---:|---:|---|---:|---:|---|
| 0 | `log_open` | log_price | -5 | 10 | no | — | — | forbidden |
| 1 | `log_high` | log_price | -5 | 10 | no | — | — | forbidden |
| 2 | `log_low` | log_price | -5 | 10 | no | — | — | forbidden |
| 3 | `log_close` | log_price | -5 | 10 | no | — | — | forbidden |
| 4 | `bar_vwap_rel` | ratio | -2 | 2 | yes | -0.99 | 9 | E |
| 5 | `session_vwap_rel` | ratio | -2 | 2 | yes | -0.99 | 9 | E |
| 6 | `bar_vwap_valid` | bool | 0 | 1 | yes | 0 | 1 | M |
| 7 | `session_vwap_valid` | bool | 0 | 1 | yes | 0 | 1 | M |
| 8 | `log_volume` | log_shares | 0 | 30 | yes | 0 | 20.723266 | E |
| 9 | `log_trades` | log_count | 0 | 30 | yes | 0 | 13.815512 | E |
| 10 | `log_volume_60s` | log_shares | 0 | 30 | yes | 0 | 20.723266 | E |
| 11 | `log_trades_60s` | log_count | 0 | 30 | yes | 0 | 13.815512 | E |
| 12 | `log_rvol_10s_prev_session` | log_duration | 0 | 20 | yes | 0 | 9.21044 | E |
| 13 | `rvol_10s_available` | bool | 0 | 1 | yes | 0 | 1 | M |
| 14 | `macd_line_rel` | ratio | -2 | 2 | yes | -4 | 4 | E |
| 15 | `macd_signal_rel` | ratio | -2 | 2 | yes | -4 | 4 | E |
| 16 | `rsi_14` | ratio | 0 | 1 | yes | 0 | 1 | M |
| 17 | `atr_14_rel` | ratio | -2 | 2 | yes | 0 | 4 | E |
| 18 | `ema_7_rel` | ratio | -2 | 2 | yes | -0.99 | 9 | E |
| 19 | `ema_26_rel` | ratio | -2 | 2 | yes | -0.99 | 9 | E |
| 20 | `indicator_available` | bool | 0 | 1 | yes | 0 | 1 | M |
| 21 | `time_of_day_sin` | ratio | -2 | 2 | yes | -1 | 1 | M |
| 22 | `time_of_day_cos` | ratio | -2 | 2 | yes | -1 | 1 | M |
| 23 | `premarket` | bool | 0 | 1 | yes | 0 | 1 | M |
| 24 | `regular` | bool | 0 | 1 | yes | 0 | 1 | M |
| 25 | `after_hours` | bool | 0 | 1 | yes | 0 | 1 | M |
| 26 | `log_inter_candle_gap` | log_duration | 0 | 30 | yes | 0 | 10.961295 | E |
| 27 | `log_float_shares` | log_shares | 0 | 30 | yes | 0 | 27.631021 | E |
| 28 | `float_present` | bool | 0 | 1 | yes | 0 | 1 | M |
| 29 | `log_shares_outstanding` | log_shares | 0 | 30 | yes | 0 | 27.631021 | E |
| 30 | `shares_present` | bool | 0 | 1 | yes | 0 | 1 | M |
| 31 | `log_days_since_split` | log_duration | 0 | 30 | no | — | — | forbidden |
| 32 | `split_present` | bool | 0 | 1 | no | — | — | forbidden |
| 33 | `log_days_since_reverse_split` | log_duration | 0 | 30 | no | — | — | forbidden |
| 34 | `reverse_split_present` | bool | 0 | 1 | no | — | — | forbidden |
| 35 | `bar_price_valid` | bool | 0 | 1 | yes | 0 | 1 | M |
| 36 | `bar_extremes_valid` | bool | 0 | 1 | yes | 0 | 1 | M |
| 37 | `v7.below.1.center_distance_rel` | ratio | -10 | 10 | yes | -0.99 | 0 | E |
| 38 | `v7.below.1.lower_distance_rel` | ratio | -10 | 10 | yes | -0.99 | 9 | E |
| 39 | `v7.below.1.upper_distance_rel` | ratio | -10 | 10 | yes | -0.99 | 9 | E |
| 40 | `v7.below.1.log_observation_count` | log_count | 0 | 30 | yes | 0 | 13.815512 | E |
| 41 | `v7.below.1.log_today_observation_count` | log_count | 0 | 30 | yes | 0 | 13.815512 | E |
| 42 | `v7.below.1.log_age_since_confirmation` | log_duration | 0 | 30 | yes | 0 | 19.56991 | E |
| 43 | `v7.below.1.role_support` | bool | 0 | 1 | yes | 0 | 1 | M |
| 44 | `v7.below.1.role_resistance` | bool | 0 | 1 | yes | 0 | 1 | M |
| 45 | `v7.below.1.role_transition` | bool | 0 | 1 | yes | 0 | 1 | M |
| 46 | `v7.below.1.historical_origin` | bool | 0 | 1 | yes | 0 | 1 | M |
| 47 | `v7.below.1.present` | bool | 0 | 1 | yes | 0 | 1 | M |
| 48 | `v7.below.2.center_distance_rel` | ratio | -10 | 10 | yes | -0.99 | 0 | E |
| 49 | `v7.below.2.lower_distance_rel` | ratio | -10 | 10 | yes | -0.99 | 9 | E |
| 50 | `v7.below.2.upper_distance_rel` | ratio | -10 | 10 | yes | -0.99 | 9 | E |
| 51 | `v7.below.2.log_observation_count` | log_count | 0 | 30 | yes | 0 | 13.815512 | E |
| 52 | `v7.below.2.log_today_observation_count` | log_count | 0 | 30 | yes | 0 | 13.815512 | E |
| 53 | `v7.below.2.log_age_since_confirmation` | log_duration | 0 | 30 | yes | 0 | 19.56991 | E |
| 54 | `v7.below.2.role_support` | bool | 0 | 1 | yes | 0 | 1 | M |
| 55 | `v7.below.2.role_resistance` | bool | 0 | 1 | yes | 0 | 1 | M |
| 56 | `v7.below.2.role_transition` | bool | 0 | 1 | yes | 0 | 1 | M |
| 57 | `v7.below.2.historical_origin` | bool | 0 | 1 | yes | 0 | 1 | M |
| 58 | `v7.below.2.present` | bool | 0 | 1 | yes | 0 | 1 | M |
| 59 | `v7.below.3.center_distance_rel` | ratio | -10 | 10 | yes | -0.99 | 0 | E |
| 60 | `v7.below.3.lower_distance_rel` | ratio | -10 | 10 | yes | -0.99 | 9 | E |
| 61 | `v7.below.3.upper_distance_rel` | ratio | -10 | 10 | yes | -0.99 | 9 | E |
| 62 | `v7.below.3.log_observation_count` | log_count | 0 | 30 | yes | 0 | 13.815512 | E |
| 63 | `v7.below.3.log_today_observation_count` | log_count | 0 | 30 | yes | 0 | 13.815512 | E |
| 64 | `v7.below.3.log_age_since_confirmation` | log_duration | 0 | 30 | yes | 0 | 19.56991 | E |
| 65 | `v7.below.3.role_support` | bool | 0 | 1 | yes | 0 | 1 | M |
| 66 | `v7.below.3.role_resistance` | bool | 0 | 1 | yes | 0 | 1 | M |
| 67 | `v7.below.3.role_transition` | bool | 0 | 1 | yes | 0 | 1 | M |
| 68 | `v7.below.3.historical_origin` | bool | 0 | 1 | yes | 0 | 1 | M |
| 69 | `v7.below.3.present` | bool | 0 | 1 | yes | 0 | 1 | M |
| 70 | `v7.below.4.center_distance_rel` | ratio | -10 | 10 | yes | -0.99 | 0 | E |
| 71 | `v7.below.4.lower_distance_rel` | ratio | -10 | 10 | yes | -0.99 | 9 | E |
| 72 | `v7.below.4.upper_distance_rel` | ratio | -10 | 10 | yes | -0.99 | 9 | E |
| 73 | `v7.below.4.log_observation_count` | log_count | 0 | 30 | yes | 0 | 13.815512 | E |
| 74 | `v7.below.4.log_today_observation_count` | log_count | 0 | 30 | yes | 0 | 13.815512 | E |
| 75 | `v7.below.4.log_age_since_confirmation` | log_duration | 0 | 30 | yes | 0 | 19.56991 | E |
| 76 | `v7.below.4.role_support` | bool | 0 | 1 | yes | 0 | 1 | M |
| 77 | `v7.below.4.role_resistance` | bool | 0 | 1 | yes | 0 | 1 | M |
| 78 | `v7.below.4.role_transition` | bool | 0 | 1 | yes | 0 | 1 | M |
| 79 | `v7.below.4.historical_origin` | bool | 0 | 1 | yes | 0 | 1 | M |
| 80 | `v7.below.4.present` | bool | 0 | 1 | yes | 0 | 1 | M |
| 81 | `v7.below.5.center_distance_rel` | ratio | -10 | 10 | yes | -0.99 | 0 | E |
| 82 | `v7.below.5.lower_distance_rel` | ratio | -10 | 10 | yes | -0.99 | 9 | E |
| 83 | `v7.below.5.upper_distance_rel` | ratio | -10 | 10 | yes | -0.99 | 9 | E |
| 84 | `v7.below.5.log_observation_count` | log_count | 0 | 30 | yes | 0 | 13.815512 | E |
| 85 | `v7.below.5.log_today_observation_count` | log_count | 0 | 30 | yes | 0 | 13.815512 | E |
| 86 | `v7.below.5.log_age_since_confirmation` | log_duration | 0 | 30 | yes | 0 | 19.56991 | E |
| 87 | `v7.below.5.role_support` | bool | 0 | 1 | yes | 0 | 1 | M |
| 88 | `v7.below.5.role_resistance` | bool | 0 | 1 | yes | 0 | 1 | M |
| 89 | `v7.below.5.role_transition` | bool | 0 | 1 | yes | 0 | 1 | M |
| 90 | `v7.below.5.historical_origin` | bool | 0 | 1 | yes | 0 | 1 | M |
| 91 | `v7.below.5.present` | bool | 0 | 1 | yes | 0 | 1 | M |
| 92 | `v7.above.1.center_distance_rel` | ratio | -10 | 10 | yes | 0 | 9 | E |
| 93 | `v7.above.1.lower_distance_rel` | ratio | -10 | 10 | yes | -0.99 | 9 | E |
| 94 | `v7.above.1.upper_distance_rel` | ratio | -10 | 10 | yes | -0.99 | 9 | E |
| 95 | `v7.above.1.log_observation_count` | log_count | 0 | 30 | yes | 0 | 13.815512 | E |
| 96 | `v7.above.1.log_today_observation_count` | log_count | 0 | 30 | yes | 0 | 13.815512 | E |
| 97 | `v7.above.1.log_age_since_confirmation` | log_duration | 0 | 30 | yes | 0 | 19.56991 | E |
| 98 | `v7.above.1.role_support` | bool | 0 | 1 | yes | 0 | 1 | M |
| 99 | `v7.above.1.role_resistance` | bool | 0 | 1 | yes | 0 | 1 | M |
| 100 | `v7.above.1.role_transition` | bool | 0 | 1 | yes | 0 | 1 | M |
| 101 | `v7.above.1.historical_origin` | bool | 0 | 1 | yes | 0 | 1 | M |
| 102 | `v7.above.1.present` | bool | 0 | 1 | yes | 0 | 1 | M |
| 103 | `v7.above.2.center_distance_rel` | ratio | -10 | 10 | yes | 0 | 9 | E |
| 104 | `v7.above.2.lower_distance_rel` | ratio | -10 | 10 | yes | -0.99 | 9 | E |
| 105 | `v7.above.2.upper_distance_rel` | ratio | -10 | 10 | yes | -0.99 | 9 | E |
| 106 | `v7.above.2.log_observation_count` | log_count | 0 | 30 | yes | 0 | 13.815512 | E |
| 107 | `v7.above.2.log_today_observation_count` | log_count | 0 | 30 | yes | 0 | 13.815512 | E |
| 108 | `v7.above.2.log_age_since_confirmation` | log_duration | 0 | 30 | yes | 0 | 19.56991 | E |
| 109 | `v7.above.2.role_support` | bool | 0 | 1 | yes | 0 | 1 | M |
| 110 | `v7.above.2.role_resistance` | bool | 0 | 1 | yes | 0 | 1 | M |
| 111 | `v7.above.2.role_transition` | bool | 0 | 1 | yes | 0 | 1 | M |
| 112 | `v7.above.2.historical_origin` | bool | 0 | 1 | yes | 0 | 1 | M |
| 113 | `v7.above.2.present` | bool | 0 | 1 | yes | 0 | 1 | M |
| 114 | `v7.above.3.center_distance_rel` | ratio | -10 | 10 | yes | 0 | 9 | E |
| 115 | `v7.above.3.lower_distance_rel` | ratio | -10 | 10 | yes | -0.99 | 9 | E |
| 116 | `v7.above.3.upper_distance_rel` | ratio | -10 | 10 | yes | -0.99 | 9 | E |
| 117 | `v7.above.3.log_observation_count` | log_count | 0 | 30 | yes | 0 | 13.815512 | E |
| 118 | `v7.above.3.log_today_observation_count` | log_count | 0 | 30 | yes | 0 | 13.815512 | E |
| 119 | `v7.above.3.log_age_since_confirmation` | log_duration | 0 | 30 | yes | 0 | 19.56991 | E |
| 120 | `v7.above.3.role_support` | bool | 0 | 1 | yes | 0 | 1 | M |
| 121 | `v7.above.3.role_resistance` | bool | 0 | 1 | yes | 0 | 1 | M |
| 122 | `v7.above.3.role_transition` | bool | 0 | 1 | yes | 0 | 1 | M |
| 123 | `v7.above.3.historical_origin` | bool | 0 | 1 | yes | 0 | 1 | M |
| 124 | `v7.above.3.present` | bool | 0 | 1 | yes | 0 | 1 | M |
| 125 | `v7.above.4.center_distance_rel` | ratio | -10 | 10 | yes | 0 | 9 | E |
| 126 | `v7.above.4.lower_distance_rel` | ratio | -10 | 10 | yes | -0.99 | 9 | E |
| 127 | `v7.above.4.upper_distance_rel` | ratio | -10 | 10 | yes | -0.99 | 9 | E |
| 128 | `v7.above.4.log_observation_count` | log_count | 0 | 30 | yes | 0 | 13.815512 | E |
| 129 | `v7.above.4.log_today_observation_count` | log_count | 0 | 30 | yes | 0 | 13.815512 | E |
| 130 | `v7.above.4.log_age_since_confirmation` | log_duration | 0 | 30 | yes | 0 | 19.56991 | E |
| 131 | `v7.above.4.role_support` | bool | 0 | 1 | yes | 0 | 1 | M |
| 132 | `v7.above.4.role_resistance` | bool | 0 | 1 | yes | 0 | 1 | M |
| 133 | `v7.above.4.role_transition` | bool | 0 | 1 | yes | 0 | 1 | M |
| 134 | `v7.above.4.historical_origin` | bool | 0 | 1 | yes | 0 | 1 | M |
| 135 | `v7.above.4.present` | bool | 0 | 1 | yes | 0 | 1 | M |
| 136 | `v7.above.5.center_distance_rel` | ratio | -10 | 10 | yes | 0 | 9 | E |
| 137 | `v7.above.5.lower_distance_rel` | ratio | -10 | 10 | yes | -0.99 | 9 | E |
| 138 | `v7.above.5.upper_distance_rel` | ratio | -10 | 10 | yes | -0.99 | 9 | E |
| 139 | `v7.above.5.log_observation_count` | log_count | 0 | 30 | yes | 0 | 13.815512 | E |
| 140 | `v7.above.5.log_today_observation_count` | log_count | 0 | 30 | yes | 0 | 13.815512 | E |
| 141 | `v7.above.5.log_age_since_confirmation` | log_duration | 0 | 30 | yes | 0 | 19.56991 | E |
| 142 | `v7.above.5.role_support` | bool | 0 | 1 | yes | 0 | 1 | M |
| 143 | `v7.above.5.role_resistance` | bool | 0 | 1 | yes | 0 | 1 | M |
| 144 | `v7.above.5.role_transition` | bool | 0 | 1 | yes | 0 | 1 | M |
| 145 | `v7.above.5.historical_origin` | bool | 0 | 1 | yes | 0 | 1 | M |
| 146 | `v7.above.5.present` | bool | 0 | 1 | yes | 0 | 1 | M |
| 147 | `split_this_session` | bool | 0 | 1 | no | — | — | forbidden |
| 148 | `reverse_split_this_session` | bool | 0 | 1 | no | — | — | forbidden |
| 149 | `history.recent_high.1` | price | 0 | 1000 | no | — | — | forbidden |
| 150 | `history.recent_high.2` | price | 0 | 1000 | no | — | — | forbidden |
| 151 | `history.recent_high.3` | price | 0 | 1000 | no | — | — | forbidden |
| 152 | `history.recent_high.4` | price | 0 | 1000 | no | — | — | forbidden |
| 153 | `history.recent_high.5` | price | 0 | 1000 | no | — | — | forbidden |
| 154 | `history.recent_high.6` | price | 0 | 1000 | no | — | — | forbidden |
| 155 | `history.recent_high.7` | price | 0 | 1000 | no | — | — | forbidden |
| 156 | `history.recent_high.8` | price | 0 | 1000 | no | — | — | forbidden |
| 157 | `history.recent_high.9` | price | 0 | 1000 | no | — | — | forbidden |
| 158 | `history.recent_high.10` | price | 0 | 1000 | no | — | — | forbidden |
| 159 | `history.recent_high.11` | price | 0 | 1000 | no | — | — | forbidden |
| 160 | `history.recent_high.12` | price | 0 | 1000 | no | — | — | forbidden |
| 161 | `history.recent_high.13` | price | 0 | 1000 | no | — | — | forbidden |
| 162 | `history.recent_high.14` | price | 0 | 1000 | no | — | — | forbidden |
| 163 | `history.recent_high.15` | price | 0 | 1000 | no | — | — | forbidden |
| 164 | `history.recent_high.16` | price | 0 | 1000 | no | — | — | forbidden |
| 165 | `history.recent_high.17` | price | 0 | 1000 | no | — | — | forbidden |
| 166 | `history.recent_high.18` | price | 0 | 1000 | no | — | — | forbidden |
| 167 | `history.recent_high.19` | price | 0 | 1000 | no | — | — | forbidden |
| 168 | `history.recent_high.20` | price | 0 | 1000 | no | — | — | forbidden |
| 169 | `history.recent_high.21` | price | 0 | 1000 | no | — | — | forbidden |
| 170 | `history.recent_high.22` | price | 0 | 1000 | no | — | — | forbidden |
| 171 | `history.recent_high.23` | price | 0 | 1000 | no | — | — | forbidden |
| 172 | `history.recent_high.24` | price | 0 | 1000 | no | — | — | forbidden |
| 173 | `history.recent_high.25` | price | 0 | 1000 | no | — | — | forbidden |
| 174 | `history.recent_high.26` | price | 0 | 1000 | no | — | — | forbidden |
| 175 | `history.recent_high.27` | price | 0 | 1000 | no | — | — | forbidden |
| 176 | `history.recent_high.28` | price | 0 | 1000 | no | — | — | forbidden |
| 177 | `history.recent_high.29` | price | 0 | 1000 | no | — | — | forbidden |
| 178 | `history.recent_high.30` | price | 0 | 1000 | no | — | — | forbidden |
| 179 | `history.recent_high.31` | price | 0 | 1000 | no | — | — | forbidden |
| 180 | `history.recent_high.32` | price | 0 | 1000 | no | — | — | forbidden |
| 181 | `history.recent_high.33` | price | 0 | 1000 | no | — | — | forbidden |
| 182 | `history.recent_high.34` | price | 0 | 1000 | no | — | — | forbidden |
| 183 | `history.recent_high.35` | price | 0 | 1000 | no | — | — | forbidden |
| 184 | `history.recent_high.36` | price | 0 | 1000 | no | — | — | forbidden |
| 185 | `history.recent_high.37` | price | 0 | 1000 | no | — | — | forbidden |
| 186 | `history.recent_high.38` | price | 0 | 1000 | no | — | — | forbidden |
| 187 | `history.recent_high.39` | price | 0 | 1000 | no | — | — | forbidden |
| 188 | `history.recent_high.40` | price | 0 | 1000 | no | — | — | forbidden |
| 189 | `history.recent_high.41` | price | 0 | 1000 | no | — | — | forbidden |
| 190 | `history.recent_high.42` | price | 0 | 1000 | no | — | — | forbidden |
| 191 | `history.recent_high.43` | price | 0 | 1000 | no | — | — | forbidden |
| 192 | `history.recent_high.44` | price | 0 | 1000 | no | — | — | forbidden |
| 193 | `history.recent_high.45` | price | 0 | 1000 | no | — | — | forbidden |
| 194 | `history.recent_high.46` | price | 0 | 1000 | no | — | — | forbidden |
| 195 | `history.recent_high.47` | price | 0 | 1000 | no | — | — | forbidden |
| 196 | `history.recent_high.48` | price | 0 | 1000 | no | — | — | forbidden |
| 197 | `history.recent_high.49` | price | 0 | 1000 | no | — | — | forbidden |
| 198 | `history.recent_high.50` | price | 0 | 1000 | no | — | — | forbidden |
| 199 | `history.recent_high.51` | price | 0 | 1000 | no | — | — | forbidden |
| 200 | `history.recent_high.52` | price | 0 | 1000 | no | — | — | forbidden |
| 201 | `history.recent_high.53` | price | 0 | 1000 | no | — | — | forbidden |
| 202 | `history.recent_high.54` | price | 0 | 1000 | no | — | — | forbidden |
| 203 | `history.recent_high.55` | price | 0 | 1000 | no | — | — | forbidden |
| 204 | `history.recent_high.56` | price | 0 | 1000 | no | — | — | forbidden |
| 205 | `history.recent_high.57` | price | 0 | 1000 | no | — | — | forbidden |
| 206 | `history.recent_high.58` | price | 0 | 1000 | no | — | — | forbidden |
| 207 | `history.recent_high.59` | price | 0 | 1000 | no | — | — | forbidden |
| 208 | `history.recent_high.60` | price | 0 | 1000 | no | — | — | forbidden |
| 209 | `history.momentum_close.1` | price | 0 | 1000 | no | — | — | forbidden |
| 210 | `history.momentum_close.2` | price | 0 | 1000 | no | — | — | forbidden |
| 211 | `history.momentum_close.3` | price | 0 | 1000 | no | — | — | forbidden |
| 212 | `history.momentum_close.4` | price | 0 | 1000 | no | — | — | forbidden |
| 213 | `history.momentum_close.5` | price | 0 | 1000 | no | — | — | forbidden |
| 214 | `history.momentum_close.6` | price | 0 | 1000 | no | — | — | forbidden |
| 215 | `history.momentum_close.7` | price | 0 | 1000 | no | — | — | forbidden |
| 216 | `history.momentum_close.8` | price | 0 | 1000 | no | — | — | forbidden |
| 217 | `history.momentum_close.9` | price | 0 | 1000 | no | — | — | forbidden |
| 218 | `history.momentum_close.10` | price | 0 | 1000 | no | — | — | forbidden |
| 219 | `history.momentum_close.11` | price | 0 | 1000 | no | — | — | forbidden |
| 220 | `history.momentum_close.12` | price | 0 | 1000 | no | — | — | forbidden |
| 221 | `history.momentum_close.13` | price | 0 | 1000 | no | — | — | forbidden |
| 222 | `history.momentum_close.14` | price | 0 | 1000 | no | — | — | forbidden |
| 223 | `history.momentum_close.15` | price | 0 | 1000 | no | — | — | forbidden |
| 224 | `history.momentum_close.16` | price | 0 | 1000 | no | — | — | forbidden |
| 225 | `history.momentum_close.17` | price | 0 | 1000 | no | — | — | forbidden |
| 226 | `history.momentum_close.18` | price | 0 | 1000 | no | — | — | forbidden |
| 227 | `history.momentum_close.19` | price | 0 | 1000 | no | — | — | forbidden |
| 228 | `history.momentum_close.20` | price | 0 | 1000 | no | — | — | forbidden |
| 229 | `history.momentum_close.21` | price | 0 | 1000 | no | — | — | forbidden |
| 230 | `history.momentum_close.22` | price | 0 | 1000 | no | — | — | forbidden |
| 231 | `history.momentum_close.23` | price | 0 | 1000 | no | — | — | forbidden |
| 232 | `history.momentum_close.24` | price | 0 | 1000 | no | — | — | forbidden |
| 233 | `history.momentum_close.25` | price | 0 | 1000 | no | — | — | forbidden |
| 234 | `history.momentum_close.26` | price | 0 | 1000 | no | — | — | forbidden |
| 235 | `history.momentum_close.27` | price | 0 | 1000 | no | — | — | forbidden |
| 236 | `history.momentum_close.28` | price | 0 | 1000 | no | — | — | forbidden |
| 237 | `history.momentum_close.29` | price | 0 | 1000 | no | — | — | forbidden |
| 238 | `history.momentum_close.30` | price | 0 | 1000 | no | — | — | forbidden |
| 239 | `history.momentum_close.31` | price | 0 | 1000 | no | — | — | forbidden |
| 240 | `history.momentum_close.32` | price | 0 | 1000 | no | — | — | forbidden |
| 241 | `history.momentum_close.33` | price | 0 | 1000 | no | — | — | forbidden |
| 242 | `history.momentum_close.34` | price | 0 | 1000 | no | — | — | forbidden |
| 243 | `history.momentum_close.35` | price | 0 | 1000 | no | — | — | forbidden |
| 244 | `history.momentum_close.36` | price | 0 | 1000 | no | — | — | forbidden |
| 245 | `history.momentum_close.37` | price | 0 | 1000 | no | — | — | forbidden |
| 246 | `history.momentum_close.38` | price | 0 | 1000 | no | — | — | forbidden |
| 247 | `history.momentum_close.39` | price | 0 | 1000 | no | — | — | forbidden |
| 248 | `history.momentum_close.40` | price | 0 | 1000 | no | — | — | forbidden |
| 249 | `history.momentum_close.41` | price | 0 | 1000 | no | — | — | forbidden |
| 250 | `history.momentum_close.42` | price | 0 | 1000 | no | — | — | forbidden |
| 251 | `history.momentum_close.43` | price | 0 | 1000 | no | — | — | forbidden |
| 252 | `history.momentum_close.44` | price | 0 | 1000 | no | — | — | forbidden |
| 253 | `history.momentum_close.45` | price | 0 | 1000 | no | — | — | forbidden |
| 254 | `history.momentum_close.46` | price | 0 | 1000 | no | — | — | forbidden |
| 255 | `history.momentum_close.47` | price | 0 | 1000 | no | — | — | forbidden |
| 256 | `history.momentum_close.48` | price | 0 | 1000 | no | — | — | forbidden |
| 257 | `history.momentum_close.49` | price | 0 | 1000 | no | — | — | forbidden |
| 258 | `history.momentum_close.50` | price | 0 | 1000 | no | — | — | forbidden |
| 259 | `history.momentum_close.51` | price | 0 | 1000 | no | — | — | forbidden |
| 260 | `history.momentum_close.52` | price | 0 | 1000 | no | — | — | forbidden |
| 261 | `history.momentum_close.53` | price | 0 | 1000 | no | — | — | forbidden |
| 262 | `history.momentum_close.54` | price | 0 | 1000 | no | — | — | forbidden |
| 263 | `history.momentum_close.55` | price | 0 | 1000 | no | — | — | forbidden |
| 264 | `history.momentum_close.56` | price | 0 | 1000 | no | — | — | forbidden |
| 265 | `history.momentum_close.57` | price | 0 | 1000 | no | — | — | forbidden |
| 266 | `history.momentum_close.58` | price | 0 | 1000 | no | — | — | forbidden |
| 267 | `history.momentum_close.59` | price | 0 | 1000 | no | — | — | forbidden |
| 268 | `history.momentum_close.60` | price | 0 | 1000 | no | — | — | forbidden |
| 269 | `history.attention_mean.1` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 270 | `history.attention_mean.2` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 271 | `history.attention_mean.3` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 272 | `history.attention_mean.4` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 273 | `history.attention_mean.5` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 274 | `history.attention_mean.6` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 275 | `history.attention_mean.7` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 276 | `history.attention_mean.8` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 277 | `history.attention_mean.9` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 278 | `history.attention_mean.10` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 279 | `history.attention_mean.11` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 280 | `history.attention_mean.12` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 281 | `history.attention_mean.13` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 282 | `history.attention_mean.14` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 283 | `history.attention_mean.15` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 284 | `history.attention_mean.16` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 285 | `history.attention_mean.17` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 286 | `history.attention_mean.18` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 287 | `history.attention_mean.19` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 288 | `history.attention_mean.20` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 289 | `history.attention_mean.21` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 290 | `history.attention_mean.22` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 291 | `history.attention_mean.23` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 292 | `history.attention_mean.24` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 293 | `history.attention_mean.25` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 294 | `history.attention_mean.26` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 295 | `history.attention_mean.27` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 296 | `history.attention_mean.28` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 297 | `history.attention_mean.29` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 298 | `history.attention_mean.30` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 299 | `history.attention_mean.31` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 300 | `history.attention_mean.32` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 301 | `history.attention_mean.33` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 302 | `history.attention_mean.34` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 303 | `history.attention_mean.35` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 304 | `history.attention_mean.36` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 305 | `history.attention_mean.37` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 306 | `history.attention_mean.38` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 307 | `history.attention_mean.39` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 308 | `history.attention_mean.40` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 309 | `history.attention_mean.41` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 310 | `history.attention_mean.42` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 311 | `history.attention_mean.43` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 312 | `history.attention_mean.44` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 313 | `history.attention_mean.45` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 314 | `history.attention_mean.46` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 315 | `history.attention_mean.47` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 316 | `history.attention_mean.48` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 317 | `history.attention_mean.49` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 318 | `history.attention_mean.50` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 319 | `history.attention_mean.51` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 320 | `history.attention_mean.52` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 321 | `history.attention_mean.53` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 322 | `history.attention_mean.54` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 323 | `history.attention_mean.55` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 324 | `history.attention_mean.56` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 325 | `history.attention_mean.57` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 326 | `history.attention_mean.58` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 327 | `history.attention_mean.59` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 328 | `history.attention_mean.60` | dollars | 0 | 1e+07 | yes | 0 | 100000000000 | E |
| 329 | `history.average_move.2` | price | 0 | 1000 | no | — | — | forbidden |
| 330 | `history.average_move.3` | price | 0 | 1000 | no | — | — | forbidden |
| 331 | `history.average_move.4` | price | 0 | 1000 | no | — | — | forbidden |
| 332 | `history.average_move.5` | price | 0 | 1000 | no | — | — | forbidden |
| 333 | `history.average_move.6` | price | 0 | 1000 | no | — | — | forbidden |
| 334 | `history.average_move.7` | price | 0 | 1000 | no | — | — | forbidden |
| 335 | `history.average_move.8` | price | 0 | 1000 | no | — | — | forbidden |
| 336 | `history.average_move.9` | price | 0 | 1000 | no | — | — | forbidden |
| 337 | `history.average_move.10` | price | 0 | 1000 | no | — | — | forbidden |
| 338 | `history.average_move.11` | price | 0 | 1000 | no | — | — | forbidden |
| 339 | `history.average_move.12` | price | 0 | 1000 | no | — | — | forbidden |
| 340 | `history.average_move.13` | price | 0 | 1000 | no | — | — | forbidden |
| 341 | `history.average_move.14` | price | 0 | 1000 | no | — | — | forbidden |
| 342 | `history.average_move.15` | price | 0 | 1000 | no | — | — | forbidden |
| 343 | `history.average_move.16` | price | 0 | 1000 | no | — | — | forbidden |
| 344 | `history.average_move.17` | price | 0 | 1000 | no | — | — | forbidden |
| 345 | `history.average_move.18` | price | 0 | 1000 | no | — | — | forbidden |
| 346 | `history.average_move.19` | price | 0 | 1000 | no | — | — | forbidden |
| 347 | `history.average_move.20` | price | 0 | 1000 | no | — | — | forbidden |
| 348 | `history.average_move.21` | price | 0 | 1000 | no | — | — | forbidden |
| 349 | `history.average_move.22` | price | 0 | 1000 | no | — | — | forbidden |
| 350 | `history.average_move.23` | price | 0 | 1000 | no | — | — | forbidden |
| 351 | `history.average_move.24` | price | 0 | 1000 | no | — | — | forbidden |
| 352 | `history.average_move.25` | price | 0 | 1000 | no | — | — | forbidden |
| 353 | `history.average_move.26` | price | 0 | 1000 | no | — | — | forbidden |
| 354 | `history.average_move.27` | price | 0 | 1000 | no | — | — | forbidden |
| 355 | `history.average_move.28` | price | 0 | 1000 | no | — | — | forbidden |
| 356 | `history.average_move.29` | price | 0 | 1000 | no | — | — | forbidden |
| 357 | `history.average_move.30` | price | 0 | 1000 | no | — | — | forbidden |
| 358 | `history.average_move.31` | price | 0 | 1000 | no | — | — | forbidden |
| 359 | `history.average_move.32` | price | 0 | 1000 | no | — | — | forbidden |
| 360 | `history.swing_low.1.1` | price | 0 | 1000 | no | — | — | forbidden |
| 361 | `history.swing_low.1.2` | price | 0 | 1000 | no | — | — | forbidden |
| 362 | `history.swing_low.1.3` | price | 0 | 1000 | no | — | — | forbidden |
| 363 | `history.swing_low.1.4` | price | 0 | 1000 | no | — | — | forbidden |
| 364 | `history.swing_low.1.5` | price | 0 | 1000 | no | — | — | forbidden |
| 365 | `history.swing_low.1.10` | price | 0 | 1000 | no | — | — | forbidden |
| 366 | `history.swing_low.1.15` | price | 0 | 1000 | no | — | — | forbidden |
| 367 | `history.swing_low.1.20` | price | 0 | 1000 | no | — | — | forbidden |
| 368 | `history.swing_low.1.30` | price | 0 | 1000 | no | — | — | forbidden |
| 369 | `history.swing_low.1.45` | price | 0 | 1000 | no | — | — | forbidden |
| 370 | `history.swing_low.1.60` | price | 0 | 1000 | no | — | — | forbidden |
| 371 | `history.swing_low.2.1` | price | 0 | 1000 | no | — | — | forbidden |
| 372 | `history.swing_low.2.2` | price | 0 | 1000 | no | — | — | forbidden |
| 373 | `history.swing_low.2.3` | price | 0 | 1000 | no | — | — | forbidden |
| 374 | `history.swing_low.2.4` | price | 0 | 1000 | no | — | — | forbidden |
| 375 | `history.swing_low.2.5` | price | 0 | 1000 | no | — | — | forbidden |
| 376 | `history.swing_low.2.10` | price | 0 | 1000 | no | — | — | forbidden |
| 377 | `history.swing_low.2.15` | price | 0 | 1000 | no | — | — | forbidden |
| 378 | `history.swing_low.2.20` | price | 0 | 1000 | no | — | — | forbidden |
| 379 | `history.swing_low.2.30` | price | 0 | 1000 | no | — | — | forbidden |
| 380 | `history.swing_low.2.45` | price | 0 | 1000 | no | — | — | forbidden |
| 381 | `history.swing_low.2.60` | price | 0 | 1000 | no | — | — | forbidden |
| 382 | `history.swing_low.3.1` | price | 0 | 1000 | no | — | — | forbidden |
| 383 | `history.swing_low.3.2` | price | 0 | 1000 | no | — | — | forbidden |
| 384 | `history.swing_low.3.3` | price | 0 | 1000 | no | — | — | forbidden |
| 385 | `history.swing_low.3.4` | price | 0 | 1000 | no | — | — | forbidden |
| 386 | `history.swing_low.3.5` | price | 0 | 1000 | no | — | — | forbidden |
| 387 | `history.swing_low.3.10` | price | 0 | 1000 | no | — | — | forbidden |
| 388 | `history.swing_low.3.15` | price | 0 | 1000 | no | — | — | forbidden |
| 389 | `history.swing_low.3.20` | price | 0 | 1000 | no | — | — | forbidden |
| 390 | `history.swing_low.3.30` | price | 0 | 1000 | no | — | — | forbidden |
| 391 | `history.swing_low.3.45` | price | 0 | 1000 | no | — | — | forbidden |
| 392 | `history.swing_low.3.60` | price | 0 | 1000 | no | — | — | forbidden |
| 393 | `history.swing_low.4.1` | price | 0 | 1000 | no | — | — | forbidden |
| 394 | `history.swing_low.4.2` | price | 0 | 1000 | no | — | — | forbidden |
| 395 | `history.swing_low.4.3` | price | 0 | 1000 | no | — | — | forbidden |
| 396 | `history.swing_low.4.4` | price | 0 | 1000 | no | — | — | forbidden |
| 397 | `history.swing_low.4.5` | price | 0 | 1000 | no | — | — | forbidden |
| 398 | `history.swing_low.4.10` | price | 0 | 1000 | no | — | — | forbidden |
| 399 | `history.swing_low.4.15` | price | 0 | 1000 | no | — | — | forbidden |
| 400 | `history.swing_low.4.20` | price | 0 | 1000 | no | — | — | forbidden |
| 401 | `history.swing_low.4.30` | price | 0 | 1000 | no | — | — | forbidden |
| 402 | `history.swing_low.4.45` | price | 0 | 1000 | no | — | — | forbidden |
| 403 | `history.swing_low.4.60` | price | 0 | 1000 | no | — | — | forbidden |
| 404 | `history.swing_low.5.1` | price | 0 | 1000 | no | — | — | forbidden |
| 405 | `history.swing_low.5.2` | price | 0 | 1000 | no | — | — | forbidden |
| 406 | `history.swing_low.5.3` | price | 0 | 1000 | no | — | — | forbidden |
| 407 | `history.swing_low.5.4` | price | 0 | 1000 | no | — | — | forbidden |
| 408 | `history.swing_low.5.5` | price | 0 | 1000 | no | — | — | forbidden |
| 409 | `history.swing_low.5.10` | price | 0 | 1000 | no | — | — | forbidden |
| 410 | `history.swing_low.5.15` | price | 0 | 1000 | no | — | — | forbidden |
| 411 | `history.swing_low.5.20` | price | 0 | 1000 | no | — | — | forbidden |
| 412 | `history.swing_low.5.30` | price | 0 | 1000 | no | — | — | forbidden |
| 413 | `history.swing_low.5.45` | price | 0 | 1000 | no | — | — | forbidden |
| 414 | `history.swing_low.5.60` | price | 0 | 1000 | no | — | — | forbidden |
| 415 | `history.swing_low.10.1` | price | 0 | 1000 | no | — | — | forbidden |
| 416 | `history.swing_low.10.2` | price | 0 | 1000 | no | — | — | forbidden |
| 417 | `history.swing_low.10.3` | price | 0 | 1000 | no | — | — | forbidden |
| 418 | `history.swing_low.10.4` | price | 0 | 1000 | no | — | — | forbidden |
| 419 | `history.swing_low.10.5` | price | 0 | 1000 | no | — | — | forbidden |
| 420 | `history.swing_low.10.10` | price | 0 | 1000 | no | — | — | forbidden |
| 421 | `history.swing_low.10.15` | price | 0 | 1000 | no | — | — | forbidden |
| 422 | `history.swing_low.10.20` | price | 0 | 1000 | no | — | — | forbidden |
| 423 | `history.swing_low.10.30` | price | 0 | 1000 | no | — | — | forbidden |
| 424 | `history.swing_low.10.45` | price | 0 | 1000 | no | — | — | forbidden |
| 425 | `history.swing_low.10.60` | price | 0 | 1000 | no | — | — | forbidden |
| 426 | `history.swing_low.15.1` | price | 0 | 1000 | no | — | — | forbidden |
| 427 | `history.swing_low.15.2` | price | 0 | 1000 | no | — | — | forbidden |
| 428 | `history.swing_low.15.3` | price | 0 | 1000 | no | — | — | forbidden |
| 429 | `history.swing_low.15.4` | price | 0 | 1000 | no | — | — | forbidden |
| 430 | `history.swing_low.15.5` | price | 0 | 1000 | no | — | — | forbidden |
| 431 | `history.swing_low.15.10` | price | 0 | 1000 | no | — | — | forbidden |
| 432 | `history.swing_low.15.15` | price | 0 | 1000 | no | — | — | forbidden |
| 433 | `history.swing_low.15.20` | price | 0 | 1000 | no | — | — | forbidden |
| 434 | `history.swing_low.15.30` | price | 0 | 1000 | no | — | — | forbidden |
| 435 | `history.swing_low.15.45` | price | 0 | 1000 | no | — | — | forbidden |
| 436 | `history.swing_low.15.60` | price | 0 | 1000 | no | — | — | forbidden |
| 437 | `history.swing_low.20.1` | price | 0 | 1000 | no | — | — | forbidden |
| 438 | `history.swing_low.20.2` | price | 0 | 1000 | no | — | — | forbidden |
| 439 | `history.swing_low.20.3` | price | 0 | 1000 | no | — | — | forbidden |
| 440 | `history.swing_low.20.4` | price | 0 | 1000 | no | — | — | forbidden |
| 441 | `history.swing_low.20.5` | price | 0 | 1000 | no | — | — | forbidden |
| 442 | `history.swing_low.20.10` | price | 0 | 1000 | no | — | — | forbidden |
| 443 | `history.swing_low.20.15` | price | 0 | 1000 | no | — | — | forbidden |
| 444 | `history.swing_low.20.20` | price | 0 | 1000 | no | — | — | forbidden |
| 445 | `history.swing_low.20.30` | price | 0 | 1000 | no | — | — | forbidden |
| 446 | `history.swing_low.20.45` | price | 0 | 1000 | no | — | — | forbidden |
| 447 | `history.swing_low.20.60` | price | 0 | 1000 | no | — | — | forbidden |
| 448 | `history.swing_low.30.1` | price | 0 | 1000 | no | — | — | forbidden |
| 449 | `history.swing_low.30.2` | price | 0 | 1000 | no | — | — | forbidden |
| 450 | `history.swing_low.30.3` | price | 0 | 1000 | no | — | — | forbidden |
| 451 | `history.swing_low.30.4` | price | 0 | 1000 | no | — | — | forbidden |
| 452 | `history.swing_low.30.5` | price | 0 | 1000 | no | — | — | forbidden |
| 453 | `history.swing_low.30.10` | price | 0 | 1000 | no | — | — | forbidden |
| 454 | `history.swing_low.30.15` | price | 0 | 1000 | no | — | — | forbidden |
| 455 | `history.swing_low.30.20` | price | 0 | 1000 | no | — | — | forbidden |
| 456 | `history.swing_low.30.30` | price | 0 | 1000 | no | — | — | forbidden |
| 457 | `history.swing_low.30.45` | price | 0 | 1000 | no | — | — | forbidden |
| 458 | `history.swing_low.30.60` | price | 0 | 1000 | no | — | — | forbidden |
| 459 | `history.swing_low.45.1` | price | 0 | 1000 | no | — | — | forbidden |
| 460 | `history.swing_low.45.2` | price | 0 | 1000 | no | — | — | forbidden |
| 461 | `history.swing_low.45.3` | price | 0 | 1000 | no | — | — | forbidden |
| 462 | `history.swing_low.45.4` | price | 0 | 1000 | no | — | — | forbidden |
| 463 | `history.swing_low.45.5` | price | 0 | 1000 | no | — | — | forbidden |
| 464 | `history.swing_low.45.10` | price | 0 | 1000 | no | — | — | forbidden |
| 465 | `history.swing_low.45.15` | price | 0 | 1000 | no | — | — | forbidden |
| 466 | `history.swing_low.45.20` | price | 0 | 1000 | no | — | — | forbidden |
| 467 | `history.swing_low.45.30` | price | 0 | 1000 | no | — | — | forbidden |
| 468 | `history.swing_low.45.45` | price | 0 | 1000 | no | — | — | forbidden |
| 469 | `history.swing_low.45.60` | price | 0 | 1000 | no | — | — | forbidden |
| 470 | `history.swing_low.60.1` | price | 0 | 1000 | no | — | — | forbidden |
| 471 | `history.swing_low.60.2` | price | 0 | 1000 | no | — | — | forbidden |
| 472 | `history.swing_low.60.3` | price | 0 | 1000 | no | — | — | forbidden |
| 473 | `history.swing_low.60.4` | price | 0 | 1000 | no | — | — | forbidden |
| 474 | `history.swing_low.60.5` | price | 0 | 1000 | no | — | — | forbidden |
| 475 | `history.swing_low.60.10` | price | 0 | 1000 | no | — | — | forbidden |
| 476 | `history.swing_low.60.15` | price | 0 | 1000 | no | — | — | forbidden |
| 477 | `history.swing_low.60.20` | price | 0 | 1000 | no | — | — | forbidden |
| 478 | `history.swing_low.60.30` | price | 0 | 1000 | no | — | — | forbidden |
| 479 | `history.swing_low.60.45` | price | 0 | 1000 | no | — | — | forbidden |
| 480 | `history.swing_low.60.60` | price | 0 | 1000 | no | — | — | forbidden |
| 481 | `history.recent_high.1.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 482 | `history.recent_high.2.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 483 | `history.recent_high.3.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 484 | `history.recent_high.4.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 485 | `history.recent_high.5.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 486 | `history.recent_high.6.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 487 | `history.recent_high.7.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 488 | `history.recent_high.8.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 489 | `history.recent_high.9.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 490 | `history.recent_high.10.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 491 | `history.recent_high.11.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 492 | `history.recent_high.12.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 493 | `history.recent_high.13.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 494 | `history.recent_high.14.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 495 | `history.recent_high.15.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 496 | `history.recent_high.16.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 497 | `history.recent_high.17.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 498 | `history.recent_high.18.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 499 | `history.recent_high.19.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 500 | `history.recent_high.20.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 501 | `history.recent_high.21.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 502 | `history.recent_high.22.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 503 | `history.recent_high.23.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 504 | `history.recent_high.24.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 505 | `history.recent_high.25.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 506 | `history.recent_high.26.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 507 | `history.recent_high.27.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 508 | `history.recent_high.28.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 509 | `history.recent_high.29.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 510 | `history.recent_high.30.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 511 | `history.recent_high.31.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 512 | `history.recent_high.32.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 513 | `history.recent_high.33.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 514 | `history.recent_high.34.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 515 | `history.recent_high.35.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 516 | `history.recent_high.36.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 517 | `history.recent_high.37.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 518 | `history.recent_high.38.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 519 | `history.recent_high.39.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 520 | `history.recent_high.40.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 521 | `history.recent_high.41.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 522 | `history.recent_high.42.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 523 | `history.recent_high.43.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 524 | `history.recent_high.44.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 525 | `history.recent_high.45.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 526 | `history.recent_high.46.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 527 | `history.recent_high.47.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 528 | `history.recent_high.48.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 529 | `history.recent_high.49.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 530 | `history.recent_high.50.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 531 | `history.recent_high.51.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 532 | `history.recent_high.52.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 533 | `history.recent_high.53.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 534 | `history.recent_high.54.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 535 | `history.recent_high.55.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 536 | `history.recent_high.56.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 537 | `history.recent_high.57.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 538 | `history.recent_high.58.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 539 | `history.recent_high.59.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 540 | `history.recent_high.60.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 541 | `history.momentum_close.1.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 542 | `history.momentum_close.2.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 543 | `history.momentum_close.3.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 544 | `history.momentum_close.4.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 545 | `history.momentum_close.5.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 546 | `history.momentum_close.6.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 547 | `history.momentum_close.7.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 548 | `history.momentum_close.8.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 549 | `history.momentum_close.9.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 550 | `history.momentum_close.10.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 551 | `history.momentum_close.11.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 552 | `history.momentum_close.12.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 553 | `history.momentum_close.13.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 554 | `history.momentum_close.14.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 555 | `history.momentum_close.15.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 556 | `history.momentum_close.16.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 557 | `history.momentum_close.17.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 558 | `history.momentum_close.18.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 559 | `history.momentum_close.19.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 560 | `history.momentum_close.20.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 561 | `history.momentum_close.21.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 562 | `history.momentum_close.22.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 563 | `history.momentum_close.23.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 564 | `history.momentum_close.24.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 565 | `history.momentum_close.25.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 566 | `history.momentum_close.26.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 567 | `history.momentum_close.27.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 568 | `history.momentum_close.28.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 569 | `history.momentum_close.29.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 570 | `history.momentum_close.30.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 571 | `history.momentum_close.31.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 572 | `history.momentum_close.32.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 573 | `history.momentum_close.33.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 574 | `history.momentum_close.34.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 575 | `history.momentum_close.35.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 576 | `history.momentum_close.36.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 577 | `history.momentum_close.37.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 578 | `history.momentum_close.38.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 579 | `history.momentum_close.39.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 580 | `history.momentum_close.40.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 581 | `history.momentum_close.41.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 582 | `history.momentum_close.42.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 583 | `history.momentum_close.43.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 584 | `history.momentum_close.44.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 585 | `history.momentum_close.45.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 586 | `history.momentum_close.46.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 587 | `history.momentum_close.47.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 588 | `history.momentum_close.48.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 589 | `history.momentum_close.49.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 590 | `history.momentum_close.50.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 591 | `history.momentum_close.51.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 592 | `history.momentum_close.52.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 593 | `history.momentum_close.53.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 594 | `history.momentum_close.54.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 595 | `history.momentum_close.55.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 596 | `history.momentum_close.56.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 597 | `history.momentum_close.57.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 598 | `history.momentum_close.58.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 599 | `history.momentum_close.59.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 600 | `history.momentum_close.60.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 601 | `history.average_move.2.fraction_of_close` | ratio | 0 | 0.5 | yes | 0 | 4 | E |
| 602 | `history.average_move.3.fraction_of_close` | ratio | 0 | 0.5 | yes | 0 | 4 | E |
| 603 | `history.average_move.4.fraction_of_close` | ratio | 0 | 0.5 | yes | 0 | 4 | E |
| 604 | `history.average_move.5.fraction_of_close` | ratio | 0 | 0.5 | yes | 0 | 4 | E |
| 605 | `history.average_move.6.fraction_of_close` | ratio | 0 | 0.5 | yes | 0 | 4 | E |
| 606 | `history.average_move.7.fraction_of_close` | ratio | 0 | 0.5 | yes | 0 | 4 | E |
| 607 | `history.average_move.8.fraction_of_close` | ratio | 0 | 0.5 | yes | 0 | 4 | E |
| 608 | `history.average_move.9.fraction_of_close` | ratio | 0 | 0.5 | yes | 0 | 4 | E |
| 609 | `history.average_move.10.fraction_of_close` | ratio | 0 | 0.5 | yes | 0 | 4 | E |
| 610 | `history.average_move.11.fraction_of_close` | ratio | 0 | 0.5 | yes | 0 | 4 | E |
| 611 | `history.average_move.12.fraction_of_close` | ratio | 0 | 0.5 | yes | 0 | 4 | E |
| 612 | `history.average_move.13.fraction_of_close` | ratio | 0 | 0.5 | yes | 0 | 4 | E |
| 613 | `history.average_move.14.fraction_of_close` | ratio | 0 | 0.5 | yes | 0 | 4 | E |
| 614 | `history.average_move.15.fraction_of_close` | ratio | 0 | 0.5 | yes | 0 | 4 | E |
| 615 | `history.average_move.16.fraction_of_close` | ratio | 0 | 0.5 | yes | 0 | 4 | E |
| 616 | `history.average_move.17.fraction_of_close` | ratio | 0 | 0.5 | yes | 0 | 4 | E |
| 617 | `history.average_move.18.fraction_of_close` | ratio | 0 | 0.5 | yes | 0 | 4 | E |
| 618 | `history.average_move.19.fraction_of_close` | ratio | 0 | 0.5 | yes | 0 | 4 | E |
| 619 | `history.average_move.20.fraction_of_close` | ratio | 0 | 0.5 | yes | 0 | 4 | E |
| 620 | `history.average_move.21.fraction_of_close` | ratio | 0 | 0.5 | yes | 0 | 4 | E |
| 621 | `history.average_move.22.fraction_of_close` | ratio | 0 | 0.5 | yes | 0 | 4 | E |
| 622 | `history.average_move.23.fraction_of_close` | ratio | 0 | 0.5 | yes | 0 | 4 | E |
| 623 | `history.average_move.24.fraction_of_close` | ratio | 0 | 0.5 | yes | 0 | 4 | E |
| 624 | `history.average_move.25.fraction_of_close` | ratio | 0 | 0.5 | yes | 0 | 4 | E |
| 625 | `history.average_move.26.fraction_of_close` | ratio | 0 | 0.5 | yes | 0 | 4 | E |
| 626 | `history.average_move.27.fraction_of_close` | ratio | 0 | 0.5 | yes | 0 | 4 | E |
| 627 | `history.average_move.28.fraction_of_close` | ratio | 0 | 0.5 | yes | 0 | 4 | E |
| 628 | `history.average_move.29.fraction_of_close` | ratio | 0 | 0.5 | yes | 0 | 4 | E |
| 629 | `history.average_move.30.fraction_of_close` | ratio | 0 | 0.5 | yes | 0 | 4 | E |
| 630 | `history.average_move.31.fraction_of_close` | ratio | 0 | 0.5 | yes | 0 | 4 | E |
| 631 | `history.average_move.32.fraction_of_close` | ratio | 0 | 0.5 | yes | 0 | 4 | E |
| 632 | `history.swing_low.1.1.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 633 | `history.swing_low.1.2.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 634 | `history.swing_low.1.3.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 635 | `history.swing_low.1.4.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 636 | `history.swing_low.1.5.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 637 | `history.swing_low.1.10.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 638 | `history.swing_low.1.15.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 639 | `history.swing_low.1.20.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 640 | `history.swing_low.1.30.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 641 | `history.swing_low.1.45.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 642 | `history.swing_low.1.60.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 643 | `history.swing_low.2.1.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 644 | `history.swing_low.2.2.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 645 | `history.swing_low.2.3.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 646 | `history.swing_low.2.4.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 647 | `history.swing_low.2.5.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 648 | `history.swing_low.2.10.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 649 | `history.swing_low.2.15.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 650 | `history.swing_low.2.20.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 651 | `history.swing_low.2.30.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 652 | `history.swing_low.2.45.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 653 | `history.swing_low.2.60.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 654 | `history.swing_low.3.1.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 655 | `history.swing_low.3.2.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 656 | `history.swing_low.3.3.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 657 | `history.swing_low.3.4.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 658 | `history.swing_low.3.5.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 659 | `history.swing_low.3.10.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 660 | `history.swing_low.3.15.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 661 | `history.swing_low.3.20.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 662 | `history.swing_low.3.30.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 663 | `history.swing_low.3.45.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 664 | `history.swing_low.3.60.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 665 | `history.swing_low.4.1.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 666 | `history.swing_low.4.2.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 667 | `history.swing_low.4.3.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 668 | `history.swing_low.4.4.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 669 | `history.swing_low.4.5.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 670 | `history.swing_low.4.10.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 671 | `history.swing_low.4.15.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 672 | `history.swing_low.4.20.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 673 | `history.swing_low.4.30.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 674 | `history.swing_low.4.45.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 675 | `history.swing_low.4.60.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 676 | `history.swing_low.5.1.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 677 | `history.swing_low.5.2.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 678 | `history.swing_low.5.3.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 679 | `history.swing_low.5.4.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 680 | `history.swing_low.5.5.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 681 | `history.swing_low.5.10.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 682 | `history.swing_low.5.15.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 683 | `history.swing_low.5.20.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 684 | `history.swing_low.5.30.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 685 | `history.swing_low.5.45.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 686 | `history.swing_low.5.60.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 687 | `history.swing_low.10.1.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 688 | `history.swing_low.10.2.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 689 | `history.swing_low.10.3.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 690 | `history.swing_low.10.4.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 691 | `history.swing_low.10.5.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 692 | `history.swing_low.10.10.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 693 | `history.swing_low.10.15.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 694 | `history.swing_low.10.20.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 695 | `history.swing_low.10.30.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 696 | `history.swing_low.10.45.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 697 | `history.swing_low.10.60.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 698 | `history.swing_low.15.1.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 699 | `history.swing_low.15.2.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 700 | `history.swing_low.15.3.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 701 | `history.swing_low.15.4.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 702 | `history.swing_low.15.5.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 703 | `history.swing_low.15.10.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 704 | `history.swing_low.15.15.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 705 | `history.swing_low.15.20.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 706 | `history.swing_low.15.30.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 707 | `history.swing_low.15.45.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 708 | `history.swing_low.15.60.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 709 | `history.swing_low.20.1.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 710 | `history.swing_low.20.2.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 711 | `history.swing_low.20.3.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 712 | `history.swing_low.20.4.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 713 | `history.swing_low.20.5.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 714 | `history.swing_low.20.10.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 715 | `history.swing_low.20.15.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 716 | `history.swing_low.20.20.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 717 | `history.swing_low.20.30.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 718 | `history.swing_low.20.45.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 719 | `history.swing_low.20.60.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 720 | `history.swing_low.30.1.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 721 | `history.swing_low.30.2.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 722 | `history.swing_low.30.3.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 723 | `history.swing_low.30.4.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 724 | `history.swing_low.30.5.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 725 | `history.swing_low.30.10.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 726 | `history.swing_low.30.15.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 727 | `history.swing_low.30.20.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 728 | `history.swing_low.30.30.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 729 | `history.swing_low.30.45.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 730 | `history.swing_low.30.60.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 731 | `history.swing_low.45.1.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 732 | `history.swing_low.45.2.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 733 | `history.swing_low.45.3.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 734 | `history.swing_low.45.4.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 735 | `history.swing_low.45.5.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 736 | `history.swing_low.45.10.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 737 | `history.swing_low.45.15.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 738 | `history.swing_low.45.20.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 739 | `history.swing_low.45.30.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 740 | `history.swing_low.45.45.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 741 | `history.swing_low.45.60.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 742 | `history.swing_low.60.1.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 743 | `history.swing_low.60.2.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 744 | `history.swing_low.60.3.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 745 | `history.swing_low.60.4.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 746 | `history.swing_low.60.5.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 747 | `history.swing_low.60.10.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 748 | `history.swing_low.60.15.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 749 | `history.swing_low.60.20.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 750 | `history.swing_low.60.30.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 751 | `history.swing_low.60.45.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 752 | `history.swing_low.60.60.relative_to_close` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 753 | `market.bid_relative` | ratio | -0.1 | 0.1 | yes | -0.99 | 9 | E |
| 754 | `market.ask_relative` | ratio | -0.1 | 0.1 | yes | -0.99 | 9 | E |
| 755 | `market.spread_fraction` | ratio | 0 | 0.1 | yes | 0 | 9 | E |
| 756 | `market.quote_age_seconds` | duration | 0 | 3600 | yes | 0 | 57600 | E |
| 757 | `market.log_execution_volume` | log_shares | 0 | 30 | yes | 0 | 20.723266 | E |
| 758 | `market.log_execution_notional` | log_dollars | 0 | 30 | yes | 0 | 25.328436 | E |
| 759 | `market.log_execution_trade_count` | log_count | 0 | 20 | yes | 0 | 13.815512 | E |
| 760 | `market.bid` | price | 0 | 1000 | no | — | — | forbidden |
| 761 | `market.ask` | price | 0 | 1000 | no | — | — | forbidden |
| 762 | `market.spread` | price | 0 | 100 | no | — | — | forbidden |
| 763 | `market.close` | price | 0 | 1000 | no | — | — | forbidden |
| 764 | `indicator.ema.2` | price | 0 | 1000 | no | — | — | forbidden |
| 765 | `indicator.ema.2.relative` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 766 | `indicator.ema.3` | price | 0 | 1000 | no | — | — | forbidden |
| 767 | `indicator.ema.3.relative` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 768 | `indicator.ema.5` | price | 0 | 1000 | no | — | — | forbidden |
| 769 | `indicator.ema.5.relative` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 770 | `indicator.ema.7` | price | 0 | 1000 | no | — | — | forbidden |
| 771 | `indicator.ema.7.relative` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 772 | `indicator.ema.9` | price | 0 | 1000 | no | — | — | forbidden |
| 773 | `indicator.ema.9.relative` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 774 | `indicator.ema.10` | price | 0 | 1000 | no | — | — | forbidden |
| 775 | `indicator.ema.10.relative` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 776 | `indicator.ema.12` | price | 0 | 1000 | no | — | — | forbidden |
| 777 | `indicator.ema.12.relative` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 778 | `indicator.ema.14` | price | 0 | 1000 | no | — | — | forbidden |
| 779 | `indicator.ema.14.relative` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 780 | `indicator.ema.15` | price | 0 | 1000 | no | — | — | forbidden |
| 781 | `indicator.ema.15.relative` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 782 | `indicator.ema.20` | price | 0 | 1000 | no | — | — | forbidden |
| 783 | `indicator.ema.20.relative` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 784 | `indicator.ema.26` | price | 0 | 1000 | no | — | — | forbidden |
| 785 | `indicator.ema.26.relative` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 786 | `indicator.ema.30` | price | 0 | 1000 | no | — | — | forbidden |
| 787 | `indicator.ema.30.relative` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 788 | `indicator.ema.45` | price | 0 | 1000 | no | — | — | forbidden |
| 789 | `indicator.ema.45.relative` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 790 | `indicator.ema.50` | price | 0 | 1000 | no | — | — | forbidden |
| 791 | `indicator.ema.50.relative` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 792 | `indicator.ema.60` | price | 0 | 1000 | no | — | — | forbidden |
| 793 | `indicator.ema.60.relative` | ratio | -0.5 | 0.5 | yes | -0.99 | 9 | E |
| 794 | `indicator.rsi.2` | ratio | 0 | 1 | yes | 0 | 1 | M |
| 795 | `indicator.rsi.3` | ratio | 0 | 1 | yes | 0 | 1 | M |
| 796 | `indicator.rsi.5` | ratio | 0 | 1 | yes | 0 | 1 | M |
| 797 | `indicator.rsi.7` | ratio | 0 | 1 | yes | 0 | 1 | M |
| 798 | `indicator.rsi.9` | ratio | 0 | 1 | yes | 0 | 1 | M |
| 799 | `indicator.rsi.10` | ratio | 0 | 1 | yes | 0 | 1 | M |
| 800 | `indicator.rsi.12` | ratio | 0 | 1 | yes | 0 | 1 | M |
| 801 | `indicator.rsi.14` | ratio | 0 | 1 | yes | 0 | 1 | M |
| 802 | `indicator.rsi.15` | ratio | 0 | 1 | yes | 0 | 1 | M |
| 803 | `indicator.rsi.20` | ratio | 0 | 1 | yes | 0 | 1 | M |
| 804 | `indicator.rsi.26` | ratio | 0 | 1 | yes | 0 | 1 | M |
| 805 | `indicator.rsi.30` | ratio | 0 | 1 | yes | 0 | 1 | M |
| 806 | `indicator.rsi.45` | ratio | 0 | 1 | yes | 0 | 1 | M |
| 807 | `indicator.rsi.50` | ratio | 0 | 1 | yes | 0 | 1 | M |
| 808 | `indicator.rsi.60` | ratio | 0 | 1 | yes | 0 | 1 | M |
| 809 | `indicator.atr.2` | price | 0 | 1000 | no | — | — | forbidden |
| 810 | `indicator.atr.2.relative` | ratio | 0 | 1 | yes | 0 | 4 | E |
| 811 | `indicator.atr.3` | price | 0 | 1000 | no | — | — | forbidden |
| 812 | `indicator.atr.3.relative` | ratio | 0 | 1 | yes | 0 | 4 | E |
| 813 | `indicator.atr.5` | price | 0 | 1000 | no | — | — | forbidden |
| 814 | `indicator.atr.5.relative` | ratio | 0 | 1 | yes | 0 | 4 | E |
| 815 | `indicator.atr.7` | price | 0 | 1000 | no | — | — | forbidden |
| 816 | `indicator.atr.7.relative` | ratio | 0 | 1 | yes | 0 | 4 | E |
| 817 | `indicator.atr.9` | price | 0 | 1000 | no | — | — | forbidden |
| 818 | `indicator.atr.9.relative` | ratio | 0 | 1 | yes | 0 | 4 | E |
| 819 | `indicator.atr.10` | price | 0 | 1000 | no | — | — | forbidden |
| 820 | `indicator.atr.10.relative` | ratio | 0 | 1 | yes | 0 | 4 | E |
| 821 | `indicator.atr.12` | price | 0 | 1000 | no | — | — | forbidden |
| 822 | `indicator.atr.12.relative` | ratio | 0 | 1 | yes | 0 | 4 | E |
| 823 | `indicator.atr.14` | price | 0 | 1000 | no | — | — | forbidden |
| 824 | `indicator.atr.14.relative` | ratio | 0 | 1 | yes | 0 | 4 | E |
| 825 | `indicator.atr.15` | price | 0 | 1000 | no | — | — | forbidden |
| 826 | `indicator.atr.15.relative` | ratio | 0 | 1 | yes | 0 | 4 | E |
| 827 | `indicator.atr.20` | price | 0 | 1000 | no | — | — | forbidden |
| 828 | `indicator.atr.20.relative` | ratio | 0 | 1 | yes | 0 | 4 | E |
| 829 | `indicator.atr.26` | price | 0 | 1000 | no | — | — | forbidden |
| 830 | `indicator.atr.26.relative` | ratio | 0 | 1 | yes | 0 | 4 | E |
| 831 | `indicator.atr.30` | price | 0 | 1000 | no | — | — | forbidden |
| 832 | `indicator.atr.30.relative` | ratio | 0 | 1 | yes | 0 | 4 | E |
| 833 | `indicator.atr.45` | price | 0 | 1000 | no | — | — | forbidden |
| 834 | `indicator.atr.45.relative` | ratio | 0 | 1 | yes | 0 | 4 | E |
| 835 | `indicator.atr.50` | price | 0 | 1000 | no | — | — | forbidden |
| 836 | `indicator.atr.50.relative` | ratio | 0 | 1 | yes | 0 | 4 | E |
| 837 | `indicator.atr.60` | price | 0 | 1000 | no | — | — | forbidden |
| 838 | `indicator.atr.60.relative` | ratio | 0 | 1 | yes | 0 | 4 | E |
| 839 | `indicator.macd_line.3.15.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 840 | `indicator.macd_signal.3.15.5.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 841 | `indicator.macd_histogram.3.15.5.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 842 | `indicator.macd_signal.3.15.9.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 843 | `indicator.macd_histogram.3.15.9.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 844 | `indicator.macd_signal.3.15.20.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 845 | `indicator.macd_histogram.3.15.20.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 846 | `indicator.macd_line.3.26.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 847 | `indicator.macd_signal.3.26.5.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 848 | `indicator.macd_histogram.3.26.5.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 849 | `indicator.macd_signal.3.26.9.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 850 | `indicator.macd_histogram.3.26.9.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 851 | `indicator.macd_signal.3.26.20.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 852 | `indicator.macd_histogram.3.26.20.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 853 | `indicator.macd_line.3.50.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 854 | `indicator.macd_signal.3.50.5.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 855 | `indicator.macd_histogram.3.50.5.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 856 | `indicator.macd_signal.3.50.9.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 857 | `indicator.macd_histogram.3.50.9.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 858 | `indicator.macd_signal.3.50.20.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 859 | `indicator.macd_histogram.3.50.20.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 860 | `indicator.macd_line.7.15.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 861 | `indicator.macd_signal.7.15.5.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 862 | `indicator.macd_histogram.7.15.5.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 863 | `indicator.macd_signal.7.15.9.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 864 | `indicator.macd_histogram.7.15.9.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 865 | `indicator.macd_signal.7.15.20.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 866 | `indicator.macd_histogram.7.15.20.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 867 | `indicator.macd_line.7.26.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 868 | `indicator.macd_signal.7.26.5.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 869 | `indicator.macd_histogram.7.26.5.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 870 | `indicator.macd_signal.7.26.9.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 871 | `indicator.macd_histogram.7.26.9.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 872 | `indicator.macd_signal.7.26.20.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 873 | `indicator.macd_histogram.7.26.20.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 874 | `indicator.macd_line.7.50.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 875 | `indicator.macd_signal.7.50.5.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 876 | `indicator.macd_histogram.7.50.5.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 877 | `indicator.macd_signal.7.50.9.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 878 | `indicator.macd_histogram.7.50.9.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 879 | `indicator.macd_signal.7.50.20.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 880 | `indicator.macd_histogram.7.50.20.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 881 | `indicator.macd_line.12.15.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 882 | `indicator.macd_signal.12.15.5.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 883 | `indicator.macd_histogram.12.15.5.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 884 | `indicator.macd_signal.12.15.9.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 885 | `indicator.macd_histogram.12.15.9.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 886 | `indicator.macd_signal.12.15.20.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 887 | `indicator.macd_histogram.12.15.20.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 888 | `indicator.macd_line.12.26.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 889 | `indicator.macd_signal.12.26.5.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 890 | `indicator.macd_histogram.12.26.5.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 891 | `indicator.macd_signal.12.26.9.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 892 | `indicator.macd_histogram.12.26.9.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 893 | `indicator.macd_signal.12.26.20.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 894 | `indicator.macd_histogram.12.26.20.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 895 | `indicator.macd_line.12.50.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 896 | `indicator.macd_signal.12.50.5.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 897 | `indicator.macd_histogram.12.50.5.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 898 | `indicator.macd_signal.12.50.9.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 899 | `indicator.macd_histogram.12.50.9.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 900 | `indicator.macd_signal.12.50.20.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 901 | `indicator.macd_histogram.12.50.20.relative` | ratio | -0.5 | 0.5 | yes | -4 | 4 | E |
| 902 | `enriched.return.5s` | ratio | -0.5 | 1 | yes | -2.302585 | 2.302585 | E |
| 903 | `enriched.volatility.5s` | ratio | 0 | 1 | yes | 0 | 2.302585 | E |
| 904 | `enriched.return.15s` | ratio | -0.5 | 1 | yes | -2.302585 | 2.302585 | E |
| 905 | `enriched.volatility.15s` | ratio | 0 | 1 | yes | 0 | 2.302585 | E |
| 906 | `enriched.return.60s` | ratio | -0.5 | 1 | yes | -2.302585 | 2.302585 | E |
| 907 | `enriched.volatility.60s` | ratio | 0 | 1 | yes | 0 | 2.302585 | E |
| 908 | `enriched.return.300s` | ratio | -0.5 | 1 | yes | -2.302585 | 2.302585 | E |
| 909 | `enriched.volatility.300s` | ratio | 0 | 1 | yes | 0 | 2.302585 | E |
| 910 | `enriched.activity.volume_acceleration` | ratio | -0.5 | 1 | yes | -1 | 11 | M |
| 911 | `enriched.activity.trade_acceleration` | ratio | -0.5 | 1 | yes | -1 | 11 | M |
| 912 | `enriched.execution.volatility_fee_ratio` | ratio | 0 | 100 | yes | 0 | 1000 | E |
| 913 | `enriched.candle.5s.body` | ratio | -0.5 | 1 | yes | -9 | 0.99 | E |
| 914 | `enriched.candle.5s.range` | ratio | 0 | 1 | yes | 0 | 9 | E |
| 915 | `enriched.candle.5s.close_location` | ratio | 0 | 1 | yes | 0 | 1 | M |
| 916 | `enriched.candle.5s.return` | ratio | -0.5 | 1 | yes | -2.302585 | 2.302585 | E |
| 917 | `enriched.candle.5s.log_volume` | ratio | 0 | 30 | yes | 0 | 20.723266 | E |
| 918 | `enriched.candle.5s.log_trades` | ratio | 0 | 30 | yes | 0 | 13.815512 | E |
| 919 | `enriched.candle.5s.active_fraction` | ratio | 0 | 1 | yes | 0 | 1 | M |
| 920 | `enriched.candle.15s.body` | ratio | -0.5 | 1 | yes | -9 | 0.99 | E |
| 921 | `enriched.candle.15s.range` | ratio | 0 | 1 | yes | 0 | 9 | E |
| 922 | `enriched.candle.15s.close_location` | ratio | 0 | 1 | yes | 0 | 1 | M |
| 923 | `enriched.candle.15s.return` | ratio | -0.5 | 1 | yes | -2.302585 | 2.302585 | E |
| 924 | `enriched.candle.15s.log_volume` | ratio | 0 | 30 | yes | 0 | 20.723266 | E |
| 925 | `enriched.candle.15s.log_trades` | ratio | 0 | 30 | yes | 0 | 13.815512 | E |
| 926 | `enriched.candle.15s.active_fraction` | ratio | 0 | 1 | yes | 0 | 1 | M |
| 927 | `enriched.candle.60s.body` | ratio | -0.5 | 1 | yes | -9 | 0.99 | E |
| 928 | `enriched.candle.60s.range` | ratio | 0 | 1 | yes | 0 | 9 | E |
| 929 | `enriched.candle.60s.close_location` | ratio | 0 | 1 | yes | 0 | 1 | M |
| 930 | `enriched.candle.60s.return` | ratio | -0.5 | 1 | yes | -2.302585 | 2.302585 | E |
| 931 | `enriched.candle.60s.log_volume` | ratio | 0 | 30 | yes | 0 | 20.723266 | E |
| 932 | `enriched.candle.60s.log_trades` | ratio | 0 | 30 | yes | 0 | 13.815512 | E |
| 933 | `enriched.candle.60s.active_fraction` | ratio | 0 | 1 | yes | 0 | 1 | M |
| 934 | `enriched.context.15s.median_return` | ratio | -0.5 | 1 | yes | -2.302585 | 2.302585 | E |
| 935 | `enriched.context.15s.mean_return` | ratio | -0.5 | 1 | yes | -2.302585 | 2.302585 | E |
| 936 | `enriched.context.15s.advancing_fraction` | ratio | 0 | 1 | yes | 0 | 1 | M |
| 937 | `enriched.context.15s.dispersion` | ratio | 0 | 1 | yes | 0 | 1 | M |
| 938 | `enriched.context.15s.coverage` | ratio | 0 | 1 | yes | 0 | 1 | M |
| 939 | `enriched.context.15s.relative_return` | ratio | -0.5 | 1 | yes | -4.60517 | 4.60517 | E |
| 940 | `enriched.context.15s.return_percentile` | ratio | 0 | 1 | yes | 0 | 1 | M |
| 941 | `enriched.context.60s.median_return` | ratio | -0.5 | 1 | yes | -2.302585 | 2.302585 | E |
| 942 | `enriched.context.60s.mean_return` | ratio | -0.5 | 1 | yes | -2.302585 | 2.302585 | E |
| 943 | `enriched.context.60s.advancing_fraction` | ratio | 0 | 1 | yes | 0 | 1 | M |
| 944 | `enriched.context.60s.dispersion` | ratio | 0 | 1 | yes | 0 | 1 | M |
| 945 | `enriched.context.60s.coverage` | ratio | 0 | 1 | yes | 0 | 1 | M |
| 946 | `enriched.context.60s.relative_return` | ratio | -0.5 | 1 | yes | -4.60517 | 4.60517 | E |
| 947 | `enriched.context.60s.return_percentile` | ratio | 0 | 1 | yes | 0 | 1 | M |
| 948 | `enriched.context.300s.median_return` | ratio | -0.5 | 1 | yes | -2.302585 | 2.302585 | E |
| 949 | `enriched.context.300s.mean_return` | ratio | -0.5 | 1 | yes | -2.302585 | 2.302585 | E |
| 950 | `enriched.context.300s.advancing_fraction` | ratio | 0 | 1 | yes | 0 | 1 | M |
| 951 | `enriched.context.300s.dispersion` | ratio | 0 | 1 | yes | 0 | 1 | M |
| 952 | `enriched.context.300s.coverage` | ratio | 0 | 1 | yes | 0 | 1 | M |
| 953 | `enriched.context.300s.relative_return` | ratio | -0.5 | 1 | yes | -4.60517 | 4.60517 | E |
| 954 | `enriched.context.300s.return_percentile` | ratio | 0 | 1 | yes | 0 | 1 | M |
| 955 | `enriched.rank.dollar_volume_60s_percentile` | ratio | 0 | 1 | yes | 0 | 1 | M |
| 956 | `enriched.rank.dollar_volume_60s_change_15s` | ratio | -0.5 | 1 | yes | -1 | 1 | M |

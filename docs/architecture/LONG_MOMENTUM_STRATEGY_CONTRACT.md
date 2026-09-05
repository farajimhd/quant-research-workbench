# Long Momentum Strategy: Intended Behavior and System Repair Plan

Last updated: 2026-09-05

Related task: TASK-0014

Status: Revision 43 implementation reference. Focused verification is separate
from market-run acceptance. The user authorized a simultaneous JUNS and SUGP
backtest for August 21, 2026, 04:00-09:30 Eastern, with shared account cash,
including stopping, fixing defects and rerunning when necessary.

## 1. Authority and purpose

Revision 43 accepts a completed non-red close above the current R3 without
requiring another crossover when other entry conditions become ready later.
R1 is nearest HOD, R2 second, and R3 third downward. Three qualified levels
are required. Every entry decision rechecks the current causal R3 and all
other gates; a saved acceptance does not authorize entry below a changed R3.
Dojis remain allowed and flat-position entry remains on completed 1s bars.
No revision-43 market backtest has been performed as part of this correction.

Revision 42 adds the completed non-red candle gate, current-book multi-level
crossing selection, and fresh validation of deferred target amendments.
Saved earlier revisions retain their original behavior. No revision-42 market
backtest has been performed as part of this correction.

Revision 41 also reconciles a retired or newly unqualified target frontier
against the current qualified producer ladder. A missing old identity cannot
freeze targets forever. The surviving first resistance must still be passed
by a completed 1s close before the new third resistance becomes the target.
The canonical SUGP book at 04:11:01 omitted the previously tracked $4.071
identity; its surviving $4.125 resistance was below the $4.18 close. The
repaired calculation advances the target from $4.195 to $4.2555.

Revision 40 preserves the first exit fill's origin throughout liquidation.
When a protective stop partially fills and a managed sell finishes the
remainder, the flat position retains the protective-exit re-entry policy.
The last execution route must not turn that campaign into a completed
assignment. Re-entry permissions, `after_protective_exit`, and explicit
exit-and-stop commands still apply. Origin state resets at each new entry;
duplicate final fill notifications must not increment re-entry counts twice.
This repairs the first trade in run `45d60046-7fc0-48f3-8403-67d711ff7b4a`,
which was stopped at the user's request after becoming inactive at 04:03:12.

This document records the user's intended long momentum strategy, including
the corrections supplied during the September 4 code and backtest review.
It is a reference for implementation and acceptance, not a claim that the
current code implements every rule or that the strategy is profitable.

The user's latest corrections supersede conflicting descriptions in
[Long Momentum Campaign](LONG_MOMENTUM_CAMPAIGN.md), older chat summaries, and
existing implementation behavior. Earlier immutable strategy revisions and
completed runs retain their original meaning; this document does not migrate
them. Unresolved mechanics are identified explicitly in section 10 rather
than filled in with new trading rules.

The intended policy is a long momentum breakout strategy over a continuously
updated structural level book. It requests entry when a qualified resistance
is crossed with valid momentum and execution evidence. Portfolio allocates
capital and quantity. OMS completes that allocation, protects actual exposure,
and maintains resting profit targets intended to catch long wicks and sudden
upward moves.

## 2. Ownership across the system

| Concern | Authority | Required boundary |
|---|---|---|
| Eligible market events, quotes, VWAP, MACD, and structural levels | Shared market-data/QMD producers | Strategy consumes causal observations; it must not invent a second indicator or structural book. |
| Admission, entry, stop policy, target policy, exit, and re-entry | Strategy | Emits semantic requests and their evidence, not broker commands or independently sized account orders. |
| Account selection, allocation, quantity, and reservations | Portfolio | Applies account settings, competing requests, working orders, existing positions, cash, risk, and other constraints. |
| Placement, amendment, cancellation, fill reconciliation, and protection execution | OMS | Executes each approved account allocation and manages its unfilled remainder. |
| Accepted orders, fills, cash, and positions | Broker; simulated broker in backtest | An intent or local acknowledgement is not a fill. |
| Chart and activity presentation | Shared journal projections | Shows what the decision and broker actually knew at each event; does not drive execution. |

Structure algorithm 17 calculates session and opening-range highs/lows using
the shared `update_high_low` eligibility flag, independently of `update_last`.
Timely, otherwise eligible Form-T trades remain included during extended hours.
Delayed reports remain excluded from current structural calculations. Cached
structure from earlier algorithm versions is not valid for the corrected rule.

Entry presentation uses the entry decision's recorded resistance-selection
snapshot: a solid black HOD line and black dashed R1/R2/R3 lines, numbered
downward from HOD: R1 is the nearest selected resistance below HOD,
then R2 and R3 at successively lower prices. These entry references remain fixed
for that position's historical display; they are not the dynamic profit-target
ladder. General structural levels and target candidates must never fill missing
entry references. Older records can use their recorded prior selection and
its HOD; missing evidence stays unavailable rather than being reconstructed
from later chart bars. Dark themes add a light outline for legibility.

Each chart's **Strategy Presentation** configuration, also available in Canvas
configuration, exposes separate HOD and entry R1–R3 line/label controls for
visibility, color, opacity, width, dash style, and label styling. Settings persist
per chart instance. TP and protective-stop styles remain independent.

One strategy opportunity may result in approved allocations on multiple
accounts. Each allocation has its own quantity, reservation, broker orders,
fills, protection, and outcome. A rejected or delayed allocation on one account
must not be presented as the result for every account.

The strategy's request is not an instruction to invest all money in one
account. Portfolio may approve, resize, defer, or reject allocations according
to its configured policy. The inspected configuration's 8% planned/open-risk
ceilings and 10,000-share cap are versioned configuration values, not a reason
to hardcode sizing into Strategy or OMS. The 8% risk ceiling is distinct from
the distance between entry and a protective stop.

## 3. Admission and continuous structural monitoring

An Early Squeeze Move admits a ticker into the campaign. Admission includes
the configured price, session volume, dollar volume, activity, and liquidity
requirements. Once admitted, the ticker remains monitored according to the
campaign lifecycle; every subsequent trade need not create another admission.

Admission does not waive order-time requirements. At a potential entry, the
strategy still requires the configured current spread, trade activity, VWAP,
MACD, veto, and freshness checks.

The structural book must incorporate current-day events and recent qualified
levels as they become causally available. New levels, role changes, qualified
level updates, and the changing session high must reach Strategy before the
corresponding decision. Refreshing trade price while retaining an older book
is insufficient. This requirement does not discard valid historical levels
or authorize a current-day-only filter that the user has not specified.

Entry, stop, and target levels use the agreed
`ticker_relative_quality_score >= 0.20` gate. Missing, non-finite, or
below-threshold scores do not qualify. Current-day level eligibility must be
reconciled with the producer's frozen-reference normalization contract; it
must not be achieved by silently bypassing quality qualification.

Every decision needs the exact book revision/as-of time, source-event identity,
level IDs, level roles, prices/zones, qualification evidence, and session high.
Old values must retain their source timestamps when reused; a new decision
timestamp does not make an old structural or indicator value fresh.

## 4. Entry rule

### 4.1 Candidate resistance set

At the current causal event:

1. Take the current qualified resistance records from the shared level book.
2. Restrict entry candidates to prices at or below the current session high.
3. Select the three highest such resistance prices: R1 nearest HOD, R2 second,
   and R3 third downward. Wait if fewer than three qualify.
4. A completed one-second non-red close strictly above R3 establishes entry
   eligibility. The prior close need not be below R3. Later completed candles
   above the current R3 can enter when the other conditions pass. Close must
   be >= open; equality to R3 does not qualify.

This set must follow changes in the current-day book and high of day. A wick
can establish the high of day and can contribute a resistance through the
shared level-book calculation/update function. That producer decides whether
the level exists and how it is qualified. Strategy neither creates the level
itself nor excludes it because it came from a wick. The candidate set is not
a fixed snapshot from campaign admission or the last entry.

Entry candidates are different from the profit-target ladder. The former are
the highest resistances at/below high of day; the latter are ordered qualified
resistances above the relevant position/target reference. Evidence and chart
labels must identify which set is being shown.

### 4.2 Confirmation at the crossing

Entry waits for a completed one-second candle. An intrabar crossing or wick
is insufficient, and a red candle (close < open) blocks entry. A non-red close
above R3 establishes acceptance, retained for the same qualified R3 while
completed closes remain above it. A changed R3 requires confirmation against
its current producer price; missing or unqualified R3, or a close at/below R3,
invalidates acceptance. This check uses the dynamic current book, not a fixed
entry snapshot. Other conditions are:

- One-second MACD line is above its signal line.
- MACD line is above zero; the signal line need not be above zero.
- Price is above executable VWAP.
- Configured liquidity, activity, volume, veto, and freshness gates pass.

Entry uses the completed one-second MACD available at that candle boundary.
Forming one-second MACD remains available for immediate exit evaluation.
A later candle's favorable MACD can authorize entry at that later boundary
while its non-red close is still above current R3; another crossover is not
required. It cannot retroactively authorize an earlier order. The configured
gates and observed values must be recorded so an
entry or missed entry can be explained without reconstructing it from a later
chart image.

After an entry request is submitted, actual exit conditions remain active,
including while **zero shares have filled**. If an exit becomes true, cancel
the entry or its unfilled remainder and close the actual held position. Exit
intent remains latched through partial fills, cancellation acknowledgements,
and late entry fills. A cancellation request alone does not prove cancellation
or a flat position. Existing working exit quantity must be counted before
ordering any newly acquired residual, so repeated evaluations cannot oversell.

### 4.3 One entry request

A valid opportunity creates one logical entry request. Portfolio determines
the approved allocation for each account. Further fills of that allocation
are completion of the same entry, not new Strategy adds.

Crossing another candidate resistance while the entry is being acquired must
not generate structural-tranche `add_long` requests. The rejected one-third
tranche design and its residual add machinery are not part of this strategy.
Repeated market events, acknowledgements, retries, or reconnects must not
duplicate the logical entry request or its allocation.

## 5. Portfolio allocation and OMS entry completion

Concurrent entries share one account admission fence. Portfolio allocates
remaining free cash after all working reservations, fees and configured risk
limits. Working entries count toward the position-count limit before filling.
Re-delivery of an allocated request cannot create another reservation or order.

An otherwise valid entry with no executable capacity remains a deferred
Portfolio request. It retains its original request ID and breakout witness.
Strategy revalidates current structure, completed non-red candle, momentum,
liquidity and exit conditions before funding; it does not require a second
breakout merely because capital was occupied. A breached exit condition or
invalidated producer level withdraws that request. Released capital can fund
the still-valid waiting request on a subsequent eligible candle. This is not
permission to execute a stale signal after liquidation.

Cash funding and risk-budget accounting are separate: investing cash in an
existing position must not shrink the account risk-budget basis and then
subtract that position's risk again. The risk basis includes funded position
cost; available cash still excludes cash already spent or reserved.

The simulator consumes each causal event's displayed liquidity once across
competing orders, including immediate rematching and checkpoint restoration.
At actual fill time it enforces nonnegative account cash including cumulative
order commissions. More shares require new observed liquidity and funding;
the simulator cannot fabricate depth or instant full fills.

Portfolio evaluates the opportunity against the latest authoritative account
state, competing entry requests, working allocations, existing positions,
reservations, and configured constraints. Account allocations must be
identified separately and reserved without spending the same funds twice.

In **backtest**, Portfolio can allocate all eligible account cash, subject to
the configured cash reserve, fee allowance, position, risk, competing-order,
and other constraints. In **paper and live trading**, sizing additionally uses
the configured strategy allocation percentage of that account's current cash.
Account equity is not a substitute for cash in this percentage calculation.
Each account is evaluated separately; the same opportunity may have different
approved quantities or outcomes across accounts.

For each approved allocation, OMS owns this lifecycle:

1. Submit the authorized entry order and record broker acknowledgement.
2. Reconcile actual fills, including fills received during an amendment or
   cancellation race.
3. Compute the remaining approved quantity from cumulative fills and any
   explicit Portfolio amendment.
4. While the entry remains authorized and incomplete, update the remaining buy
   limit to the fresh current ask; sells follow the fresh current bid.
5. Continue the bounded execution loop until filled, an actual Strategy exit
   or authorized cancellation occurs, or an explicit allocation/broker
   constraint requires resolution.

For an unchanged allocation:

`remaining quantity = approved total quantity - cumulative confirmed entry fills`

When the broker modification API expects total quantity, OMS must retain that
API convention while separately tracking the remainder. Sending the remainder
as a new total must not accidentally cancel approved shares or fall below
already-filled quantity.

Both the initial buy and subsequent remainder amendments use the executable
ask. Sell completion uses the executable bid. Orders remain working until
completion or genuine cancellation/exit authority; a short adaptive deadline
must not abandon the remainder. Actual displayed liquidity, cash, and broker
constraints still apply; neither Strategy nor the simulator may fabricate fills.

OMS must not interpret a partial fill as permission to stop working the rest,
nor use a new Strategy entry/add to recover the missing quantity. A cash or
risk limit cannot be ignored in order to complete the order. If a new price
requires more funding than authorized, OMS and Portfolio must reconcile the
remaining allocation/reservation and record the resulting decision. OMS must
not silently resize the mandate or loop indefinitely on the same rejection.

Bound request frequency and in-flight amendments, deduplicate unchanged prices,
and reconcile ambiguous broker outcomes before resubmitting. The exact retry
cadence is an execution parameter, not an artificial Strategy holding delay.

Actual partial exposure must remain protected while acquisition continues.
When an exit becomes authoritative, stop further acquisition, reconcile
late fills, and liquidate the actual remaining position. No late buy may
silently reopen a completed lifecycle.

## 6. Support-based trailing protection

The user confirmed that protection is both **support-based and trailing**.
Trailing protection itself is not a defect and must not be removed on that
premise.

The retained initial selection rule is the second nearest qualified support
below entry. A resistance record must not be substituted for long support,
and OMS must preserve Strategy's valid protection selection rather than
replacing it with a generic hybrid/ATR stop.

`protection.trailing.mode` selects one of two policies:

| Value | Behavior |
|---|---|
| `qualified_support` (default) | Start at the second qualified support below entry. Advance only when a newer qualified support selection permits a tighter stop. Price rising alone cannot move it. |
| `support_distance` (alternative) | Freeze the entry-price minus initial-support-stop distance. Trail that distance behind the favorable price high, without widening the active stop. |

Both retain `protection.trailing.enabled` and `activation_gain_pct`. The
default activation is immediate. In the default mode, reselect the second
nearest qualified support below current price. Its confirmation must be later
than entry and the last accepted support confirmation, and no later than the
decision. A lower, older, missing, or future support cannot loosen or advance
the stop. OMS amends the broker-held fixed stop; a native price trail would
violate this mode. A failed amendment retains/reconciles the actual broker
protection and leaves the desired update eligible for retry.

The alternative uses `distance = entry reference price - initial support
stop`, then `stop = max(previous stop, favorable high - distance)` for the
long position. This option deliberately allows price-driven tightening. It
does not reselect a distance on each later event. Broker-native amount trailing
is used when the selected protection profile supports that contract.

Neither mode substitutes a percentage stop for missing structure. Fewer than
two qualifying supports defers a new entry. A distant valid support remains
the stop; Portfolio may reduce or reject the allocation for excessive risk.
The old 15% stop cap/fallback is not applied by revision 37.

Initial and subsequent protection evidence must retain the selected support,
support qualification, anchor/distance calculation, active stop, actual
broker protection, and causal reason/time for every modification. Broker-native
orders are suitable only if their behavior matches the resolved policy.

## 7. Resting structural profit target

### 7.1 Purpose and initial placement

The profit target is intended to be hit by a long wick or sudden upward move.
It must already be working at the broker when that move occurs. Reaching the
target price is not the time to begin synthesizing the target order.

For a long position, order qualified resistances above the relevant entry
reference from nearest to farthest: R1, R2, R3, R4, and so on. Place the initial
profit-taking limit at R3. Retain the actual level IDs, prices, qualification,
and ordering used for that selection.

### 7.2 Advancement after a completed, non-red one-second close

Only a completed 1s candle with `close >= open` can reconcile the target.
A red candle, intrabar update, wick-only crossing, or close exactly on the
resistance cannot advance it. Existing target fills and protective exits
remain active on red candles and between candle closes.

Use the producer's qualified level book as of that candle boundary. R1, R2,
R3, and later positions are dynamic roles, not a saved admission ladder.
New levels, changed prices and quality, removed levels, and role flips must
be considered. The strategy does not manufacture resistance from a wick or
high of day. A known resistance that now appears as support is evaluated
using its current producer price and qualification, never its old price.

Compare the previous completed close with the current qualified resistance
prices. For all boundaries with `previous close <= resistance < close`,
select the highest crossed boundary. Place the replacement at the third
qualified resistance strictly above that boundary in the current book.
For example, with levels 4.10, 4.20, 4.30, 4.40, and 4.50, a non-red close
crossing both 4.10 and 4.20 selects 4.50 in one amendment. It must not stop
at 4.40. Reordering the former first level cannot hide another crossing.

Record the candle open, close, previous close, current crossed level IDs and
prices, highest crossed resistance, and replacement selection. Consume each
completed candle once. Red candles still advance observed close history,
but authorize no target reconciliation. Stop and target updates caused by
one eligible event can both be emitted.

When fewer than three replacement levels qualify, retain the broker-held
target. A pending amendment can retry only on a later completed non-red
candle while its current producer level still exists, still qualifies, and
remains strictly below that candle's close. Rebuild the selection and record
fresh candle acceptance. A red candle, retired level, or invalid confirmation
clears that pending acceptance; never replay the old close as current proof.
Targets only advance, never move down.

The broker processes already-working targets before the resulting strategy
decision. An executable wick or gap fill is not erased by a later amendment.
This ordering also applies when a candle crosses multiple resistances.

Rejection of an add, reduction, or exit must never delete an already-held
position's target frontier, entry price, or support stop.

### 7.3 Fill and replacement races

OMS must reconcile fills of the existing target before considering its
replacement acknowledged. A wick may fill the existing target while an
advancement request is in flight. A local intent to move the target cannot
erase that fill, create additional sell capacity, or assume the new price was
already working.

Target quantities and protective coverage follow actual remaining exposure.
The previously agreed incomplete-target liquidation behavior remains a
separate full-exit authority: reconcile the partial target fill and liquidate
the outstanding position without overselling. This is not discretionary
profit pocketing, which remains disabled for this strategy.

## 8. Exit, re-entry, and session behavior

The earlier accepted exit authorities remain in scope unless explicitly
changed: support-based trailing protection; forming one-second MACD closure;
loss of executable VWAP under its configured downside rule; incomplete-target
liquidation; and configured session/operator controls. Bearish CHOCH and
discretionary profit-pocket adds/reductions are not restored by this document.

Forming MACD can change within a second. A later completed candle is not proof
that an earlier event-time exit was invalid. Conversely, a recorded exit
reason is not proof of correctness without the contemporaneous operands,
source timestamps, position, and policy.

Premarket liquidation uses limit orders at fresh executable bids. Regular
session liquidation may use market orders where the configured policy allows.
These sell-side semantics are separate from the requested buy-at-ask remainder
policy for entry completion.

Re-entry is a new opportunity only after the prior applicable account lifecycle
is confirmed flat and outstanding acquisition/exit work is reconciled. It
requires another valid current crossing and applicable gates. It is not the
remaining shares from the previous entry. Do not add an arbitrary re-entry cap
or ticker-specific suppression to improve a particular backtest result.

## 9. Backtest, live, and chart consistency

Use shared Strategy, Portfolio, and OMS contracts across modes. Historical and
live execution use their respective market-event providers and isolated
resources, not separate strategy implementations.

Historical market events come exclusively from certified
`market_sip_compact.events_YYYY`. Inherited daily structural checkpoints retain
their declared SIP-availability approximation. Post-checkpoint advancement,
for both chart and strategy, requires the canonical execution-clock sidecar.
Delayed overnight trades reported after 04:00 remain audit events and must not
raise current session HOD. Missing advancement-window coverage fails closed;
there is no raw-flatfile fallback or full-history rebuild in this repair.

The event flow must let Strategy see a causally coherent structural snapshot,
session high, trade, and indicator state. Correct event-native Strategy code
cannot compensate for a book supplied only after the relevant crossing.

Simulation must preserve causal order submission, quote availability, bounded
liquidity participation, partial fills, amendments, and fill/cancel races.
Do not make fills instantaneous to conceal order-management defects. The
25% participation setting remains an explicit model assumption, not a proof
that every observed delay is legitimate.

Charts must distinguish Strategy intent, Portfolio approval/rejection, OMS
submission/amendment, partial/final fill, protection movement, and exit.
Display entry candidates separately from target resistances, with original
snapshots available. Show account/allocation identity, fill quantities and
prices, remaining quantity, and flat-to-flat P&L including fees. Never redraw
an earlier decision using a later structural book.

## 10. Explicit implementation choices and configuration

| Detail | Revision 39 contract |
|---|---|
| Support-based trailing formula | Default newer-support advancement; selectable support-derived distance alternative, as specified in section 6. |
| Insufficient/distant support | Defer without two qualified supports. Preserve a distant support and let Portfolio constrain quantity. |
| Target advancement | Completed one-second close strictly above the tracked first resistance's current producer price; no intrabar advancement. |
| Level qualification | Minimum ticker-relative quality remains 20%; maximum break probability remains 100% (`1.0`). No lifetime break-count ceiling for entry or target levels. |
| Ladder mutation and gaps | Follow producer identities and current prices, consume the hit once, and preserve prior broker fills. See section 7. |
| Fewer than three target resistances | Defer new entry; keep existing protection for held exposure. |
| Initial entry execution price | Buy at the current ask and reprice the approved remainder to later asks; persistent sell completion follows bids. |
| Affordability during execution | Portfolio reauthorizes the same remaining reservation under its account/group admission fence. It includes a configurable `entry_fee_buffer_bps` (default 50 bps for managed-completion entries) in funding. No second allocation or Strategy add is created. |

If the ask cannot fund the entire authorized remainder within current constraints,
keep the last accepted order price and record a deferred amendment. Re-evaluate
capacity on the bounded execution cadence. Deduplicate unchanged deferral
messages; broker errors receive backoff rather than a tight retry loop.
Deferral is explicit: the mandate cannot spend unavailable cash to guarantee a fill.
The fee allowance is a sizing reserve, not the simulated or broker fee model.

The two trailing choices appear in the existing Strategy management parameter
editor as `protection.trailing.mode`. Select the value in the new versioned
configuration before a separately authorized trial. Existing revision-36 runs
and configurations remain historical evidence, not revision-38 acceptance.

## 11. Verified pre-repair baseline

The reviewed completed run was `29e5e0a9-903c-4fa8-9e90-a3c077532bd4`,
SUGP on 2026-08-21 from 04:00 to 04:30 ET, strategy revision 36,
configuration revision 62. Its immutable artifacts are under
`D:\TradingML\runtimes\trading\backtest\29e5e0a9-903c-4fa8-9e90-a3c077532bd4`.

Journal reconstruction yielded 14 flat-to-flat positions, 456 fills,
$301.095 commissions, -$551.227 net P&L, and a flat ending position. This is
observed behavior, not acceptance of the intended contract.

| Finding | Evidence and interpretation |
|---|---|
| Repricing cannot complete some allocations | 2,409 cash-rejected reprices across four entry groups. The unfilled quantity at higher prices plus fees exceeded available cash; OMS repeatedly retried. This is not the old double-counting of filled quantity. |
| Trade-event observation retains structural inputs | `ReplayRunService._process_strategy_market_event` updates price, quotes, and forming MACD while inheriting structural levels/session high from its base observation. Structural population occurs on the frame path. This is a concrete source of timing mismatch; exact missed entries need event-level attribution. |
| Residual structural adds remain | `LongMomentumStrategyEngine._structural_entry_tranche_add_result` emits separate `add_long` requests. The run had nine add intents; eight were rejected and one approved for three shares. These are not OMS remainder retries. |
| Target management state can be lost | In c46dabeb, rejected structural adds at 04:10:38.440 and 04:10:42.299 erased the held position's target/frontier. The one-second close clock is retained by the latest clarification; preserving the moving frontier and actual protection is required. |
| Protection used price-distance trailing | Initial stops in all 14 inspected entries selected support. Subsequent stop ratcheting used high-water price minus a trailing amount; missing/distant-support probes exposed percentage fallback. The newer-support default and distance alternative above supersede that ambiguity. |
| Existing descriptions conflict | The old architecture page mixes obsolete revisions, tranche behavior, and exit rules. This document supplies the current intended reference without rewriting historical runs. |

## 12. Fundamental repair sequence

This sequence identifies the fundamental repairs and the evidence required
to accept them. Revision 38 implements these seams; market behavior still
requires a separately authorized trial.

### A. Publish one versioned behavioral contract

Apply section 10 and encode the strategy, execution policy, Portfolio
constraints, and structural event semantics as explicit versioned contracts.
Remove residual tranche/add behavior from this strategy's new revision.
Keep old candidates reproducible; require the effective run configuration to
match the intended revision rather than silently merging old defaults.

### B. Deliver causal structure before evaluating the opportunity

Connect the shared structural producer's event advancement/publication to the
decision sequence. Refresh session high and qualified recent levels, retain
the pre-event identities needed to recognize a crossing, and supply the
coherent post-event state. Preserve existing historical data authority and
avoid a second Strategy-owned structural calculation.

Primary seam: [replay_run_service.py](../../src/backend/replay_run_service.py)
and its shared structural producer/observation contracts. Verify the analogous
live path rather than assuming a backtest-only fix establishes parity.

### C. Make entry acquisition one durable allocation lifecycle

Strategy requests the opportunity once. Portfolio creates and reserves each
account allocation under competing demand. OMS owns ask-following buy completion and bid-following sell completion,
fill accounting, idempotent amendments, and constraint feedback to Portfolio.
Remove fresh Strategy add requests from the completion path. Make cash/fee
affordability and allocation amendment explicit so rejections cannot become
unbounded no-progress loops.

Primary seams: [strategy_engine.py](../../src/trading_runtime/strategy_engine.py),
[portfolio.py](../../src/trading_runtime/portfolio.py),
[order_management.py](../../src/trading_runtime/order_management.py), and
[simulated_broker.py](../../src/trading_runtime/simulated_broker.py).

### D. Confirm moving resistance advancement on completed one-second candles

Track the active qualified resistance ladder, first unconsumed resistance,
resting R3 target, and exact closed-candle evidence. Advance as the resolved
one-step rule prescribes, without intrabar movement or duplicate amendments.
Reconcile broker target fills/amendments against actual exposure, including
wick/gap events and partial fills.

### E. Implement the resolved support-based trailing contract end to end

Make Strategy's protection evidence sufficient to reproduce every stop.
Portfolio sizes using that risk. OMS preserves the policy and keeps partial
exposure protected; native trailing orders are used only where their semantics
match. Eliminate undocumented fallback or overwrite behavior.

### F. Validate decisions, account allocations, and broker effects together

Focused regression scenarios must cover:

- A recent resistance or new session high arriving between completed frames;
  crossing is recognized causally, without waiting for a later recross.
- A failed entry gate at crossing; a later favorable indicator does not
  retroactively validate it.
- One opportunity allocated across two accounts while other tickers compete
  for funds; no duplicate reservation or cross-account fill attribution.
- Many partial fills and changing asks/bids; no new Strategy adds, quantity
  duplication, unsupported cash use, or silent abandonment of the remainder.
- A fill during modify/cancel, reconnect replay, and an exit during acquisition;
  no oversell, unprotected quantity, duplicate order, or unintended reopening.
- A first-resistance hit moving R3 one level, no hit leaving it unchanged,
  duplicate events doing nothing, and a wick filling the previously working
  target while an amendment is in flight.
- New support, missing support, and excessive stop distance under the resolved
  trailing policy; broker and Strategy protection agree.

**No backtest is to be run as part of this repair.** If separately authorized
later, repeat the same bounded SUGP window with a new immutable
candidate and compare every entry, allocation, fill, stop, target change, exit,
and net lifecycle P&L against the original journal. Explain all rejected and
deferred actions. Do not expand the ticker/session scope or infer profitability
from a passing engine test. Multi-account correctness can first be exercised
with deterministic fixtures before separately approved broader market runs.

## 13. Implementation verification and activation

Revision 38 adds the final candle-close clarification and removes the lifetime
break-count veto. Revision 37 retains the earlier trade-hit behavior for
historical comparison; revision 36 retains its original strategy behavior.
Rejected non-entry requests now preserve held-position state across revisions.
Validation completed with 223 focused Python tests and 101 Rust library tests.
The historical gateway release build passed, as did the canonical new-release
builder with revision 38. No backtest was run.

The confirmed HOD defect was loss of execution timestamps in historical
structure advancement. Canonical SUGP records reported after 04:00 include
overnight executions above 3.62; the prepared one-second candles correctly
show a maximum of 3.62 through 04:10:23. Both post-checkpoint chart and strategy
advancement now retain execution timestamps, allowing the existing delayed
report guard to exclude those prints from current structure.

The reported 47% stop is not recorded as an engine crash: c46dabeb completed
82,406 events through 04:30 with no run error. Its browser failure was not
reproduced, and this repair does not claim to fix an unidentified UI exception.

Before a later authorized trial, activate the updated historical gateway and
backend together and use a newly built revision-39 candidate. The per-trade
client requires both sequence-aware responses and canonical execution-clock
provenance; an older gateway fails closed. Existing runs are never relabeled
as revision-39 acceptance. The earlier UI capture covered a loading screen,
so dropdown interaction has not been claimed as visually verified.

## 14. Current-code backtest launch and execution resources

New Backtest launches require the selected Long Momentum executor revision to
match the current strategy revision. Preflight also compares the running QMD
History binary's build-time source SHA-256 with the workspace Rust source and
checks that backend/trading-runtime Python source has not changed since backend
startup. A mismatch blocks launch with the required rebuild/restart or new
candidate action. Use `scripts/services.ps1` to manage those services. Existing
saved candidates and results remain immutable; selecting an old candidate does
not silently upgrade its strategy or relabel its results.

The completed-one-second-candle veto cadence follows the strategy definition
and candle configuration, including renamed and cloned profiles. It must not
depend on the built-in profile's name.

QMD History uses mimalloc and allocation-free sorting where the structural
comparison has a deterministic total order. Historical structure batches run
on blocking workers with two execution permits, separate from chart build
capacity. A batch owns its checkpoint checkout and commit/rollback, even if
the HTTP caller disconnects. Replay prefetch defaults to 16 completed frame
boundaries (configurable from 1 through 64); it preserves every boundary and
all intervening canonical events. Run status exposes each ticker's current
prefetch interval and elapsed completion time. These changes reduce contention
and progress stalls without weakening causal ordering or fill accounting.
Frames before the requested run start still warm the indicator memory, but
do not request unused per-bar structural snapshots. The first required QMD
snapshot seeds through every earlier canonical event normally.

Structure version 17 can seed from a certified version-16 checkpoint only when
that checkpoint belongs to a completed earlier session. The original certificate
and hash are verified first. Migration preserves persistent levels, tracks,
quality evidence, and source cursors while resetting the four session extrema
changed by version 17. Provenance records the migration and original hash.
Intraday version-16 migration is rejected; old HOD values cannot enter the new
session. Historical events continue to come exclusively from canonical imported
ClickHouse authority.

## 15. Supporting context

- [September 3-4 execution UAT summary](../codex/chat-summaries/2026/CHAT-20260903-1451-long-strategy-execution-backtest-uat.md)
- [Earlier freshness and trailing repair](../codex/chat-summaries/2026/CHAT-20260902-UNKNOWN-strategy-freshness-structural-trailing-repair.md)
- [Structural checkpoint and strategy UAT context](../codex/chat-summaries/2026/CHAT-20260901-1959-structural-checkpoint-campaign-strategy-debug-uat.md)
- [Task ledger](../../TASK_HISTORY.csv), TASK-0014; history remains unchanged pending the user's request to update it.

The September 4 user corrections in the review conversation take precedence
over these older summaries wherever their rules differ.

### Revision 39 follow-up: failed run 329d8a3f

The later run `329d8a3f-fced-40ed-90dc-b5b9c6c3693f` actually failed under
revision 38 at 04:12:41 ET, after 30,407 processed events, with `long protective
stop must be below the reference price`. This differs from completed run
c46dabeb and must not be dismissed as the earlier unconfirmed UI symptom.

The confirmed trailing-stop amendment was retained in the protection profile,
but quantity repair attempted to resolve it against the original entry price.
A valid stop above entry therefore raised an exception. Repair now retains the
broker-confirmed absolute stop. When a missing stop still has an existing
target sibling, the repair transfers target capacity before installing the
replacement pair and rechecks position changes during that transfer.

Revision 38 also issued repeated exit intents while a sell already covered
the position. Revision 39 reads working OMS exit quantity before deciding:
covered holdings produce a pending-exit hold; uncovered late fills produce
only the additional liquidation needed. Full exits latch cancellation of
entry acquisition, and limit execution remains persistent.

Portfolio completion restores the current acquisition's deployed notional to
the risk-budget basis rather than shrinking that basis after each own fill.
Actual available cash, reservations, account constraints, and existing risk
remain enforced. Risk transferred from a remaining reservation to filled
allocation is proportional to the remaining quantity, avoiding a second
partial-fill accounting drift.

The canonical event feed now includes QMD-owned price/high-low/volume
eligibility plus its revision. Strategy and simulated execution no longer
consume price-ineligible prints as actionable prices. The observed 4.15
stop-triggering print at 04:11:02.710401 carried conditions 14/12/37/41.
The canonical condition reference confirms condition 37 excludes last-price
and high/low updates while retaining volume. The following round-lot 4.14
print at 04:11:02.710402 can still legitimately breach the 4.151 stop; this
repair does not suppress valid protection. Both consumers use the producer
contract, not an independent Python condition list. Historical revision-39 execution fails clearly when an
old gateway omits this contract. Structural advancement requests snapshots
only at actionable trade boundaries; the producer still consumes intervening
source events, preserving exact causal state and ordering.

At 04:11:01 the journal records a target amendment from 4.195 to 4.2555 at a
completed close of 4.18. The later position was exited by 04:11:05.918; no held
position remained to advance a target at 04:11:11. The intrabar 04:11:03.574
entry was allowed by revision 38, but conflicts with the latest entry-close
instruction and is rejected by revision 39 until a qualifying candle closes.
An uncrossed target frontier is retained across book changes and tracked by
producer identity/current price until its first resistance is actually passed.

Progress publication is wall-time throttled instead of waiting only for a
hundred-event boundary. Failed/stopped status is published before terminal
broker/checkpoint cleanup, so cleanup cannot leave the UI claiming the engine
is running. These are bounded engineering repairs; end-to-end speed and
strategy acceptance still require a separately authorized fresh run. No
backtest is launched as part of this repair.

Revision-39 verification: 233 focused Python tests and 102 Rust library tests
passed; the release gateway build and canonical configuration builder passed.
No backtest, service restart, or live publication was performed.

Read-only canonical sample, SUGP 04:11-04:12 ET: 5,881 trade reports, 1,350
price-eligible reports, 4,531 excluded price boundaries (77%). This measures
reduced strategy/snapshot work, not full-run wall-time speedup. The target
amendment journal uses the candle bar-end boundary, not its chart start label.

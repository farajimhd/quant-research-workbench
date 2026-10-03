# Decision-time and optimization contract

This is the maintained contract for `semantic-squeeze-search-v3-3`, not a reinterpretation of
array indices. The machine authority is `timing.py`, plus `StrategySpace.manifest()`.
Tape provenance contains the exact time contract and its SHA-256 seal. The same
contract/seal is in the search identity. Validation rejects missing, different
or corrupt time declarations. This records the convention; a hash alone does
not prove that an external provider correctly labelled its data. The certified
loader and boundary tests supply that evidence for this implementation.

Time-contract version: `completed-boundary-v3-1`.
SHA-256: `f583138ac469674abbb68680b7e5cd89645d376faaa8511ac28dbd3be151153e`.

## Clock convention: end-labelled rows

All clocks are integer UTC seconds. Δ is fixed at 1 second. At decision t:

| Item | Allowed interval/availability |
| --- | --- |
| Current tape row, read by `_row` | Completed `[t−Δ,t)` interval |
| The same candle under opening-time indexing | Candle `t−1`, not candle `t` |
| OHLC, volume, trade count, VWAP and atomic features | Only completed evidence with interval end ≤ t |
| 5s/10s/30s indicators | Most recent completed timeframe ending ≤ t; never the unfinished timeframe |
| V7 book | Prior-session seed plus completed current-session bars through t |
| Candle beginning at t | `[t,t+Δ)` is inaccessible to the decision; even its open is not an input in this implementation |
| Order submitted at t | First eligible execution interval `[t,t+Δ)`, processed at t+Δ |
| Ledger timestamp | End of the simulated fill interval, not an exact intrabar fill time |

For example, at **09:00:01** the visible 1s candle began at **09:00:00**.
Its close/high/low/volume are known. A new order can compete only in
**09:00:01–09:00:02**; the receipt is stamped **09:00:02**. Calling the prepared
row “candle 09:00:01” without saying *end-labelled* is incorrect.

Opening-time indexed OHLC must be converted to completion/availability timestamps
before constructing a tape. An extra shift of the already end-labelled tape
would delay all decisions one additional second; it is not the current contract.
A future extension that uses the current open must introduce a separate,
explicitly available opening-price input, not expose the current candle's
remaining fields alongside it.

The research assumption is zero publication/inference latency at completion.
This proves an event-time cutoff, not that a real data feed publishes every
indicator instantaneously. A delayed producer needs explicit availability
stamps and a revised contract; do not label late information as available at t.

## Fixed order of processing at t

1. Broker consumes `[t−Δ,t)` price/liquidity evidence for previously pending
   orders and previously activated positions. Strategy cannot access a fill
   from the next interval.
2. Completed evidence is exposed to rules, entry modes, ranking and causal V7
   geometry. New submissions and protection amendments are made at t.
3. Histories/state advance. Decisions become eligible only in later intervals.

An existing protective stop/target hit is detected using the completed
interval's extrema and queues an exit for the next interval. Stop wins when
both extrema cross. Newly filled child protection cannot trigger in its own
fill interval. Trailing amendments cannot retrospectively stop a position
using the high/low that produced the amendment. These are coarse simulation
conventions, not exact broker stop-order or intrabar sequencing claims.

The broker may use the full completed execution interval's VWAP, volume and
last quote to approximate fills of previously submitted orders. That evidence
must never be reused to price an order submitted at the interval's end into
that same interval. Buy eligibility explicitly requires `now > buy_submitted`.

SQL timestamps bars/indicators with `origin + (bucket_index+1)*resolution`.
100ms execution rows are aggregated into a 1s row labelled with its end.
As-of alignment is backward; equal timestamps refer to completed boundaries.
Squeeze admission is rounded upward to the first compatible 1s decision clock.
V7 consumes the completed bar before projecting geometry as of that boundary;
it never initializes a session from its own end-of-day checkpoint.

## Sessions, warmup and missing evidence

- Historical tapes retain the same day's prefix beginning at 04:00 New York,
  with the first completed clock at 04:00:01. Trading is gated at the requested
  start; the final clock is the requested end. No post-end interval is replayed.
- Signal entry is eligible exactly at admission. An admission before the selected
  trading start does not create a deferred signal entry at that start. Other
  entry modes still require their own causal conditions.
- Trading dates use the New York market date, including timezone/DST conversion.
  Inputs must be certified, nonempty, uniquely keyed and ordered. No raw-flatfile
  fallback, market writes or fabricated missing features are allowed.
- Missing current bars give zero execution capacity and invalid trading evidence.
  A prior valid close may be carried for valuation only. Quote freshness is
  capped at one second. Enabled atomic clauses with missing evidence fail closed,
  even when combined by OR. Lag/reduction history cannot use future slots.
- A prior-session V7 seed and current completed structural clock are required.
  Missing levels reject structural geometry; full nearest-level evidence is not
  replaced with a future book or an invented target.

## Searchable representation

The tensor is `[B,77]`: 10 class/count coordinates, 40 bounded policy coordinates (including remainder class IDs),
four six-coordinate atomic clauses and three AND/OR connectors. Float64 storage
never permits fractional categorical IDs. Unknown classes fail before repair.

Entry classes: signal, hold, retest, MACD. Hold duration: 1..60 seconds when active.
MACD masks select any nonempty subset of the certified 1s/5s/10s/30s lanes, with
ANY or ALL. A singleton has one canonical mode. Positions per ticker: 1..15.
Allocation: equal, inverse-log or log; target: percentage or structural; stop:
percentage or confirmed swing; trailing: step or adaptive; rotation: off/on.
This is a bounded policy grammar, not arbitrary executable Python or arbitrary
arithmetic expression trees.

Each atomic clause selects enabled flag, comparison ID (`>`, `>=`, `<`, `<=`),
input ID, temporal operation (current, lag, min, max, mean), lookback and threshold.
All lookbacks are integers in 1..12. Lag k excludes the current completed bar;
reductions k include it. Absolute clock/identity registers cannot be threshold
inputs: elapsed duration is a different type from UTC time, and price difference
is different from absolute price. Thresholds are bounded literals of the input's
semantic kind, not references to an arbitrary account register.

| Atomic input | Threshold range | Kind |
| --- | ---: | --- |
| `close` | 0.01..1000 | absolute_price |
| `distance_above_vwap` | -0.5..0.5 | signed_return |
| `spread_fraction` | 0..0.1 | nonnegative_fraction |
| `interval_notional` | 0..1e+07 | money |
| `interval_trades` | 0..100000 | count |
| `episode_age_seconds` | 0..57600 | elapsed_duration |
| `macd_1s_gap` | -10..10 | price_difference |
| `interval_volume` | 0..1e+07 | shares |
| `resistance_1_distance_return` | 0..10 | signed_return |
| `resistance_2_distance_return` | 0..10 | signed_return |
| `resistance_3_distance_return` | 0..10 | signed_return |
| `resistance_4_distance_return` | 0..10 | signed_return |
| `resistance_5_distance_return` | 0..10 | signed_return |

### Numeric gene bounds (inclusive)

Fractions are dimensionless: 0.01 means 1%, or 100 bps. Durations and history
lengths are in seconds on the 1s clock. Money is USD; score weights/scales are
not cash allocations or objective weights.

| Field | Lower | Upper | Integer |
| --- | ---: | ---: | --- |
| `maximum_spread_fraction` | 0.0001 | 0.05 | no |
| `minimum_dollar_volume` | 0 | 1e+06 | no |
| `minimum_trade_count` | 0 | 1000 | yes |
| `initial_stop_fraction` | 0.002 | 0.2 | no |
| `target_step_fraction` | 0.005 | 0.2 | no |
| `adaptive_window` | 2 | 32 | yes |
| `adaptive_multiplier` | 0.5 | 10 | no |
| `minimum_trail_fraction` | 0.001 | 0.1 | no |
| `trail_up_fraction` | 0.005 | 0.2 | no |
| `trail_stop_fraction` | 0.001 | 0.1 | no |
| `entry_deadline_seconds` | 1 | 30 | yes |
| `maximum_signal_age_seconds` | 1 | 57600 | yes |
| `maximum_entry_drift_fraction` | 0.0001 | 0.05 | no |
| `retest_tolerance_fraction` | 0.0001 | 0.05 | no |
| `retest_timeout_seconds` | 1 | 120 | yes |
| `swing_left_seconds` | 1 | 5 | yes |
| `swing_right_seconds` | 1 | 5 | yes |
| `replacement_margin` | 0 | 1 | no |
| `replacement_confirm_seconds` | 1 | 30 | yes |
| `replacement_cooldown_seconds` | 0 | 300 | yes |
| `terminal_exit_lead_seconds` | 1 | 60 | yes |
| `retest_lookback_seconds` | 1 | 12 | yes |
| `momentum_lookback_seconds` | 1 | 12 | yes |
| `attention_lookback_seconds` | 1 | 12 | yes |
| `momentum_scale` | 0.001 | 0.2 | no |
| `strength_scale` | 0.001 | 0.2 | no |
| `attention_cap` | 1 | 10 | no |
| `momentum_weight` | 0 | 1 | no |
| `strength_weight` | 0 | 1 | no |
| `attention_weight` | 0 | 1 | no |
| `liquidity_weight` | 0 | 1 | no |
| `reward_risk_weight` | 0 | 1 | no |
| `stagnation_weight` | 0 | 1 | no |
| `stagnation_seconds` | 1 | 300 | yes |

Legal numeric proposals may be clipped to their bounds; integer values are
rounded. Trail stop-step fraction must not exceed trail up-step fraction.
Inactive hold/MACD genes are zeroed; activating an empty duration/mask supplies
one. All repair counts are recorded. Score weights need not sum to one.

History allocation caps are fixed: atomic/retest/momentum/attention 12,
adaptive 32, swing 11. These caps allocate storage only. Candidate masks choose
their actual windows, and adaptive activation occurs at first-fill age ≥ that
candidate's window, not age ≥32. Missing movement evidence also blocks activation.

## Runtime financial constraints (not gene clipping)

- Each ticker permits one acquisition batch in a session. It can contain up to
  15 child positions; later partial fills are allowed, later acquisition batches
  are not. All candidate accounts and their liquidity budgets are independent.
- Initial stops must be finite, positive and below bid. Every required target
  must be finite and above ask times `(1+maximum_entry_drift_fraction)`. Insufficient
  structural levels or impossible target geometry reject that entry. There is
  no static guarantee that a structurally valid genome finds a feasible market
  opportunity; geometry remains a causal runtime gate.
- Cash, parent reservations and fees must fit the account; quantities are whole
  shares. Buys and sells in one account share the interval's 25% volume capacity.
  Orders respect their limits/deadlines. Stops ratchet upward only. Financial
  invariant failures or ledger overflow fail the evaluation; nothing is truncated.
- New entries stop `terminal_exit_lead_seconds` before the final boundary, and
  remaining entry orders are cancelled. Terminal exits still need real simulated
  liquidity. Residual open positions produce null fitness with a rejection reason;
  the engine does not invent a final liquidation price.

## Fixed optimization boundary and objective

Fees (0.005 USD/share, minimum 1 USD/order), participation (25%), price tick
(0.01 USD), initial cash (10,000 USD), 1s common clock, source validity, certified
indicators and the released 100ms squeeze/price-envelope funnel remain fixed.
Only downstream policy is searched; price-envelope or indicator-calculation
parameters are not genes. Prepared population/source coverage cannot be narrowed
using a proposed candidate if doing so would remove another candidate's evidence.

One phase evaluates the entire ordered training set. At least one later,
disjoint evaluation date is required. The workstation launcher reserves the last
six available dates by default. Every session resets to an independent $10,000
account; cash evolves inside each session, without inter-session carry. The
initial tensor is random: neither default nor a prior winner is injected.
Evaluation tapes are constructed after `winner.json` is frozen. Preobserved
evaluation is labelled and never drives tuning, selection or constraint changes.

Default objective: mean net return −0.5×mean normalized maximum drawdown
−0.25×population standard deviation of session returns −0.05×mean excess batch
count above20 (divided by20) −0.01×mean overdue capital-hours. Overdue capital-hours
integrate marked USD exposure after300s, divided by initial cash×3600. Optional
fixed child-position and position-hour costs remain available. Costs are fixed
before training and must be finite/nonnegative.

Historical search requires at least one actually filled **acquisition batch on
each session**. Splitting a batch into child positions or partial-fill events
cannot satisfy additional activity. Inactivity or residual terminal exposure
makes fitness null. Infeasible candidates receive feasibility ranks for genetic
selection, with feasible candidates always dominating; financial fitness is never
published for an infeasible winner. A budget exhausted without a feasible policy
fails explicitly. There is no hard drawdown cap or minimum risk/reward ratio.

Three-second minimum holding age is measured from each child position's first
fill. Discretionary target triggers and replacement decisions are gated until
age≥3s, with resulting fills eligible in a subsequent interval. Protective stops
and terminal liquidation are exempt. Entry-mode `hold_seconds` is a separate
condition on a pre-entry VWAP cross, not this position holding constraint.
The fixed long-hold threshold defaults to300s and is not a gene.

Population bounds4..1024, generations1..100; workstation defaults64×50.
Actual populations must fit measured resource guards. The GA uses two elites,
tournament3, coordinate crossover50%, mutation15% and random immigration20%,
increasing to50% after three stagnant generations, within the fixed budget.
It does not certify a global optimum. Wide nonnegative threshold/activity values
use log1p random sampling within unchanged inclusive bounds; other values are
uniform. Classes, inputs, operations, windows and thresholds remain searchable.

Completed per-session receipts support mid-generation restart. Selection requires
all training results. Resume requires identical code, grammar, split, budget,
objective and certified source fingerprints. Artifacts live under the configured
runtime root. Read the [workstation contract](WORKSTATION_OPTIMIZATION.md) for
resource limits, profiling and desktop process ownership.

## Executable witnesses

`test_boundary_t_reads_completed_candle_not_candle_opening_at_t` demonstrates
both sides of the feature cutoff, no same-interval entry fill and ledger time.
`test_tape_requires_declared_end_labelled_clock_contract` rejects undeclared,
open-labelled and corrupted timing contracts.
`test_adaptive_activation_uses_each_candidate_window_before_capacity` tests
windows2/5 at their exact equality boundaries, then compares compiled GPU and
CPU ledgers. Existing future-market/V7 prefix, reset and checkpoint tests remain
part of the full suite. The schema/version seal is evidence for this documented
contract, not a substitute for those behavioral tests.

## Validation record for this correction

The full suite passed 100 tests, including compiled CUDA ledger parity. With a first fill at boundary 9, candidate adaptive windows of 2 and 5 seconds activate at boundaries 11 and 14 respectively, even though both share a 32-slot buffer. Changing the future interval [8, 9) does not change the decision at boundary 8; changing the completed interval [7, 8) can. An order submitted at boundary 8 first fills with a boundary-9 receipt. No historical optimization was run as part of this correction.

The optimizer scores completed training outcomes after replay. This use of training outcomes is distinct from permitting future observations inside a replay decision. Parameters remain fixed throughout each replay; evaluation is diagnostic only after the winners are frozen.


## Partial-fill remainder policy (semantic-squeeze-search-v3-4)

The searchable tensor is now [B,77]. Its six new bounded fields are:

| Field | Search domain | Default |
| --- | --- | --- |
| remainder_policy_id | 0 cancel partial, 1 retain, 2 reprice, 3 resubmit | 1 |
| retry_interval_seconds | 1–30 seconds | 1 |
| maximum_retries | 0–30 | 5 |
| maximum_total_order_age_seconds | 1–300 seconds | 30 |
| maximum_chase_bps | 0–500 bps from original submission ask | 100 |
| require_signal_valid | 0 ignore, 1 cancel invalid continuation | 0 |

Retain preserves the original entry deadline. Cancel removes the remainder after
its first partial fill. Reprice revises a partially filled parent's limit at the
retry interval and renews its bounded deadline. Resubmit keeps the limit, waits
until expiry and its retry cooldown, and renews the deadline. Unfilled parents do
not gain retry privileges. All policies have a hard total-age bound; a completed
parent, protective exit or terminal liquidation cannot be revived. A policy's
maximum age may deliberately be shorter than the original entry deadline.

A retry is a logical continuation, preserving requested quantity, cumulative
fills, average cost, commission history and original price reference. It does
not manufacture another parent or reset minimum commission. Reprice limits are
bounded by the original reference and available account cash after outstanding
buy/fee/protection reserves; simultaneous price increases share that headroom.
Retries share the same ticker/interval broker liquidity budget. Counters expose
entry_retry_count, policy_cancelled_entry_shares and expired_entry_shares;
requested = filled + pending + all cancellation reasons still reconciles.

At decision boundary t, the broker FIRST processes previously active orders over
[t−1s,t). The policy then amends/cancels/retries using completed evidence only.
A revised order submitted at t cannot fill until a subsequent interval. This is
also true when its previous incarnation received a partial fill at t.
Signal validity means the ongoing completed-data envelope (active squeeze age,
above VWAP, quote/spread/activity checks and atomic rule program). It does not
repeat the one-shot entry crossing or the already-consumed watchlist flag.
A failed continuation check cancels prospectively, never erases earlier fills.
New state is included in reset/checkpoint buffers and the source/code/genome
version prevents old campaigns from being resumed as the new representation.

## Approved approximate broker contract

The GPU broker uses a fixed 25% of each ticker's eligible completed-interval
trade volume. Buys, sells and partial-fill continuations share that capacity
within each counterfactual account. Retries never replenish an interval budget.
Decisions use completed observations and activate in a later interval.
Prices remain quote-bound interval VWAP/spread approximations.

The user approved approximation rather than exact app replication. This is not
its separate passive/marketable event model and grants no 100% marketable sweep
of aggregate volume. Participation is fixed outside the searchable genome.
Source hashes/settings bind the contract. Old 10% campaigns cannot qualify this
run; fresh real-session qualification is required before optimization.

The v3-4 wide-market remainder witness additionally reproduced padded-layout
broadcast codegen failure for the policy class at [64,833,15]. The same scoped
`comprehensive_padding=False` graph option now applies to all ledger variants,
including the diagnostic atomic comparator. Numerical/causal rules are unchanged;
reference-versus-native ledger/account parity remains a required regression.

Validation for v3-4: the complete laptop suite passed143 tests, including compiled
GPU [64,833,15] ledger/account parity and all remainder policy IDs. A subsequent
objective-receipt check also passed and verifies retry counters plus requested =
filled + pending + cancellation reasons in the actual optimization objective path.
Source qualification/real-session timing has not been repeated for this version.
The final implementation uses compact column-contiguous policy values and an
explicit native mutation boundary; the experimental per-order policy expansion
was removed. Failed fusion experiments remain as runtime diagnostics, not active
fallbacks or financial changes. The stopped run is preserved; restart requires fresh qualification of this approximation.

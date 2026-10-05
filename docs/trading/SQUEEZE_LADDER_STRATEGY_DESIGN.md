# Causal squeeze breakout with independent target lots

Status: proposed behavior specification. Not registered, published, backtested,
or admitted to live trading. Existing Strategies 1–42 remain unchanged. The
withdrawn app comparisons described in TASK-0221 must not be restored. A new
implementation follows `docs/architecture/STRATEGY_CREATION_STANDARD.md`.

## Research purpose and evidence

Capture extended-session continuation after a causal Early Squeeze admission,
reduce exposure when the breakout fails, and compare percentage versus
structural target ladders. Initial capital remains $10,000 per session. Existing
approved sizing, exposure, fee and fill contracts remain authoritative; this
proposal does not authorize spending all account cash on one ticker.

The related independent Torch v2 experiment is an approximate 1s simulator.
Its September 3 experiment had 1,046 fully exited configurations, all losing,
out of 4,320; the rest retained terminal exposure and were ineligible for
fitness. See TASK-0221's linked narrative. Neither that experiment nor the
earlier Strategy 38 development profit proves this proposal profitable.

September 1–2 were exposed by the related research. They are exposed test days
for this idea, not untouched holdouts. Select and freeze two later certified,
uninspected sessions before final validation. Do not inspect their strategy
trades, logs, features or charts. Preparation integrity checks are separate
from inspecting strategy outcomes. Keep regular hours outside trading tests.

## Activation and entry

1. A certified Signal Stream event admits a ticker to a bounded watchlist.
   Retain admission boundary, source build, original MACD episode and original
   first-setup anchor. Observation does not itself authorize an order.
2. Require an observed, completed below-to-above VWAP crossing after admission.
   Quotes and completed indicators must meet their source freshness rules.
   Missing or sparse price evidence cannot prove a cross or a continuous hold.
3. Compare two entry definitions separately: the evidenced VWAP cross itself,
   and a subsequent break of the first V7 resistance above VWAP. For the latter,
   freeze the resistance identity and geometry when the VWAP setup qualifies;
   do not silently change to a nearer resistance or retrospectively select one.
   If that resistance disappears or source continuity fails, invalidate the
   setup and journal the reason.
4. Resistance entry requires a previous completed close at or below its upper
   edge, then a completed close above that edge plus a tick buffer. Require a
   fresh executable quote, spread gate, eligible dollar volume and trade-count
   gates at that same decision boundary. A forming candle is not confirmation.
   A quote-only bucket cannot stand in for the completed price crossing.
5. Choose among simultaneous survivors deterministically using a frozen
   causal opportunity score and stable security identity for ties. Route the
   winning proposal through Portfolio approval and OMS; never order directly.

Use the native completed 100ms execution clock and declared next-boundary
order activation. Higher-timeframe source values become available only at
their completed boundaries. Do not approximate absent indicators in Strategy.
Threshold values must be recorded in an immutable release before execution;
the independent simulator's $1,000/5-trade thresholds are hypotheses, not
automatically approved native thresholds.

## Batch, lots and cash

Start with n=3 protected lots. Compare n=2 and n=5 only after the three-lot
route is qualified; additional orders have material minimum fees. The broker
still owns one net position per ticker. Distinct allocation IDs attribute
quantity, fills, fees, realized P&L, remaining protection and rotation to lots.
Three lots are not three independent accounts or three copies of available cash.

The batch budget is the minimum of the configured available-cash fraction and
existing Portfolio capacity, after pending reservations and fees. Apply the
existing planned-risk cap using the common initial stop. Reserve the complete
approved batch before any order dispatch. Round quantities down to whole shares;
retain residual cash. Reject an infeasible batch rather than silently deleting
legs or increasing risk. Share participation capacity across all orders.

For target ordinal j=1..n, compare normalized weights 1, 1/log(1+j), and
log(1+j). Weights are fixed at admission. Splitting a quantity cannot duplicate
minimum fee reserves or assign the same filled share to two lots.

Each lot has its own parent entry and stop/target OCA pair. Never put all lots'
targets into one OCA group, which would cancel other lots' protection. Partial
fills activate protection only for their actual quantity. Define the batch as
one durable submission group; broker acceptance and fills are not atomic.
Partial rejection cancels remaining parents and reconciles filled/protected
quantities through OMS. Persist the ticker's consumed admission after accepted
submission, not after the final fill. No second batch or additions in that
ticker/session, including after exits, rotation or zero fills.

## Targets, stops and failure exits

The initial three-lot percentage ladder is +2.5%, +5%, +10% from each lot's
actual average fill. Structural targets are three distinct overhead resistance
identities frozen at submission, with executable prices below the lower edge
by the declared tick buffer. Require strictly increasing targets above the
entry limit and sufficient reward after spread and fees. Missing geometry
rejects the structural setup; do not substitute percentage targets.

Targets remain fixed. Test initial protection at the last confirmed swing low,
then at that swing low minus a declared buffer. A swing is usable only after
its right-hand confirmation closes; its product must carry that availability
boundary. If no certified swing product exists, this variant is unavailable
until an explicit producer implementation and preflight are qualified.

Begin with no trail as an attribution baseline. Then compare a price-confirmed
higher-low ratchet, armed after favorable progress of at least 1R. The new stop
must increase, remain below a fresh executable bid and retain the declared
buffer. Do not move stops merely because elapsed time increased. Do not move
targets up. Follow the existing broker's stop/target ambiguity contract.

Test a distinct early-failure exit after the existing parent-priority exits:
price loses the frozen breakout/VWAP level while eligible recent trade count
and dollar volume decline relative to their preceding windows. Require wholly
post-fill completed observations, a fresh quote and no pending exit. Price
weakness and liquidity weakness must coexist; elapsed time or a single red
candle alone is insufficient. Missing liquidity is not zero liquidity.

## Optional Portfolio rotation

First qualify the strategy with rotation disabled. Rotation is a Portfolio
allocation decision, not permission for Strategy to bypass sizing or submit a
replacement buy on anticipated sale proceeds.

Use comparable, bounded features for incoming setups and held lots: current
price momentum, breakout/VWAP retention, recent versus preceding eligible
dollar volume and trades, spread, executable capacity, and reward to remaining
target divided by remaining stop risk. Include incremental fees and spread
costs. Do not call a heuristic score an estimated dollar edge. Float/RVOL are
diagnostic only until publication-time authority is proven; no current snapshot
substitution, future peak, later liquidity or future episode outcomes.

Only consider rotation when an incoming setup passes all entry gates, capacity
is binding, and a held lot shows confirmed weakening. Require a sustained score
margin, stable incoming/outgoing identities and a cooldown. Holding age may
penalize stagnation but cannot force an exit by itself. Freeze exact transforms,
weights, margin and observation windows before testing this variant.

Cancel outgoing acquisition/protection safely, submit its exit, then wait for
actual fills and durable Portfolio reconciliation. Release only actual cash
and risk capacity. Revalidate the incoming setup after the sale; if it expired,
hold cash. An outgoing ticker remains locked for the session. Partial sales,
quote loss and cancellation races must not leave shares unprotected or create
double-sold quantities.

## Qualification and incremental experiments

Required before native release: producer/clock/freshness dependency matrix,
bounded columnar candidate masks, native lot attribution and independent
bracket ownership, complete-batch cash reservation, shared-liquidity fills,
normalized typed batch/lot/lock/rotation facts, Keeper fencing, restart recovery
and cold saved-result verification. Reuse Portfolio and OMS authority. Add new
typed schemas only where the existing contracts cannot express these facts.
No per-decision SQL, ticker Python scans, fake fills or run-local journal.

AH preparation must start with the prior-day causal V7 checkpoint and advance
through regular-session completion before evaluating 16:00–20:00 ET. PM is
04:00–09:30 ET. Explicitly retain LGHL exclusion and certified population seals.

Incremental order: three-lot equal allocation without trailing/rotation;
percentage versus structural targets; entry comparison; allocation comparison;
stop buffers and higher-low ratchet; early failure exit; rotation last. Each
native behavioral change gets its own immutable number and source proof.
Research candidates have immutable technical identities and are not app numbers.

Report per-session net P&L, fees, broker-observed and closed-trade drawdown
separately, open/pending terminal exposure, admission/rejection counts, fills,
lot exits, missed large moves and profit giveback. Invalid/unclosed cases are
never ranked as realized profit. Preserve identical source populations and
execution costs for comparisons. Development diagnostics may use future move
labels explicitly as hindsight; those labels never enter executable features.

Registration, historical runs and profitability acceptance remain unfinished.

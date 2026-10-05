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

The user clarified that this work targets a new immutable app strategy, not a
revision of the independent simulator. Continue the Strategy 42 development
baseline alongside the new design. The campaign target is now at least +$500
net in every development and validation session, with reduced drawdown. This
is an acceptance target, not an achieved result or an optimization guarantee.

The related independent Torch v2 experiment is an approximate 1s simulator.
Its September 3 experiment had 1,046 fully exited configurations, all losing,
out of 4,320; the rest retained terminal exposure and were ineligible for
fitness. See TASK-0221's linked narrative. Neither that experiment nor the
earlier Strategy 38 development profit proves this proposal profitable.
The user also reported cash concentration, unfinished exits, missing timing,
structural-break entry and liquidity-threshold defects in those research runs.
Audit source and order/fill evidence before interpreting their losses as a
comparison of strategy quality. Do not import their execution assumptions into
the app or restore withdrawn implementation files to shortcut that audit.

### Development audit: first eleven Strategy 42 sessions

Cold verified reports contain 71 closed positions. Follow-through failure exits
account for 22 positions and -$3,216.03 net; stop exits account for 17 positions
and -$2,188.21 net. Nineteen target exits contribute +$5,287.18 net. These are
observed exit groups, not estimates of what replacement exits would earn.
The runtime receipt is `strategy42-development-trade-audit-v1.json`, under the
strategy optimization runtime root; each row retains its report source hash.

Several large losses had substantial entry activity: ATPC Aug 4 PM had 991
eligible trades and 96,985 shares in the last completed minute; BJDX Aug 4 AH
had 1,743 trades and 232,952 shares. Entry liquidity alone does not distinguish
these failures. First effective stop distances were about 17.42% and 10.81%,
respectively. Audit original proposal and subsequent protection separately;
these distances are measured from actual average entry to first effective stop.

A blanket maximum stop-distance entry veto is not supported by these reports:
29 positions with first effective stops more than 10% away contributed
+$2,942.14 net, including thirteen winners. The best YJ, BTCT and GNPX positions
also had wide initial stops. This fixed-trade grouping is diagnostic only and
does not simulate changed cash allocation, orders or subsequent opportunities.
Prefer testing confirmed breakout invalidation and post-entry activity decay
before a blanket veto. No MFE/MAE or causal float evidence is available in this
receipt, so it cannot establish earlier-exit profit or fundamental predictors.

September 1–2 were exposed by the related research. They are exposed test days
for this idea, not untouched holdouts. Select and freeze two later certified,
uninspected sessions before final validation. Do not inspect their strategy
trades, logs, features or charts. Preparation integrity checks are separate
from inspecting strategy outcomes. Keep regular hours outside trading tests.

### Completed Strategy 42 development baseline

All 26 development PM/AH runs completed with 138 closed positions. The cold
reports sum to +$1,883.96 net across independently funded $10,000 sessions,
with $1,639.65 fees. Seven sessions meet +$500; thirteen lose money. This is
not a continuous bankroll curve or evidence that the campaign goal is met.
The largest broker-observed drawdown is $1,523.08; asynchronous account marks
do not establish a continuous exact drawdown curve.

The immutable runtime receipts are
`strategy42-complete-development-baseline-v1.json` and
`strategy42-development-trade-audit-v2.json`. The latter records all 138
positions, sorted by net P&L, with each report's source hash. Forty-two target
exits contribute +$11,726.46; forty follow-through failure exits contribute
-$6,538.92; thirty-three stops contribute -$4,755.01. Fifteen profit-giveback
exits contribute +$354.51, six liquidity-fade failures -$504.18, one confirmed
AH failure -$201.40, and one session exit +$1,802.50.

Heavy activity also occurs at failures: CDTG Aug 26 PM entered with 3,925
eligible trades and 1,006,123 shares in its last completed minute, then lost
$435.85 net; FTFT Aug 28 AH entered with 4,692 trades and 1,241,108 shares,
then lost $366.48. These observations prioritize causal retention and decay
tests; they do not justify ticker-specific exclusions or hypothetical profit
from deleting losing trades. Validation outcomes were not inspected.

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

### Prepared native components and remaining source binding

`squeeze_ladder_cross.py` now supplies a bounded vectorized completed-clock
predicate with compact rejection reasons. It rejects missing price buckets,
nonadjacent observations, future/stale references and crosses whose preceding
observation predates admission. This is a necessary condition only: the
watchlist, frozen V7 identity, liquidity gates and Portfolio approval remain
sequential admission responsibilities. It is not registered to any release.

Native fixed Backtest already exposes completed execution VWAP and eligible
liquidity in `backtest_market_data.py` and `backtest_strategy_one_loader.py`.
The original columnar gate proves price above VWAP, not a crossing after
admission. `completed_vwap_crossings` preserves its Float64 multiply-by-10000
comparison contract. Flooring that scaled threshold is equivalent for integer
closes and integer buffers within the exact Float64 integer domain; this does
not create a rounded VWAP source product. Tests cover fractional thresholds
and neighboring Float64 values. Retain original source bits in evidence.
These prepared predicates are not yet integrated into historical runs.

The existing Strategy 1 candidate product is not the ladder's complete
observation stream: `prepare_strategy_one_entries` already filters completed
rows through four bullish MACDs, VWAP, prior-close, liquidity and 30s-stop
rules. Its sparse entry-evidence gate adds BOS and protection requirements.
Reusing those survivors would silently impose unrelated rules and omit some
VWAP crossings. Bind the ladder mask to full certified completed liquidity/bar
columns after certified squeeze admission, then advance only its survivors
through sequential frozen V7/pivot setup state. Do not infer a preceding close
from sparse candidates. Existing signal occurrence certification and native
source projection can be reused; the old candidate mask cannot be substituted.

`squeeze_ladder_columnar.py` now implements that separate prepared observation
gate. It accepts explicit frozen liquidity, price, quote-freshness, admission
TTL and extended-session acquisition thresholds. It emits market survivors
and completed VWAP-cross survivors, retaining latest certified admission
clocks. Crosses cannot straddle admissions. Missing completed buckets invalidate
rolling trade-count history until the gap expires; they are not invented zero
activity. There is no MACD, prior-close or 30s-stop entry requirement in this
gate. Frozen V7 selection, pivot stop binding, consumed-session locks and
Portfolio approval remain subsequent sequential responsibilities.

`backtest_squeeze_ladder_loader.py` binds the gate to full native certified
Arrow observations in one bounded query for up to eight tickers. It verifies
the canonical squeeze scan's query and content proof, source scope and prefix,
and unique native bar/technical/broker attempts. Original VWAP and liquidity
columns remain in the prepared Arrow table for evidence binding. It exposes no
writer and does not read the Strategy 1 candidate product. The caller still
needs the release's complete preflight, checked read-only grants and normalized
decision persistence; this prepared reader is not registered to the app.

Focused source/entry/OMS regression validation passed 69 tests. A synthetic
576,000-row full-day typed-column gate took 0.173 seconds locally. This measures
mask computation only, excluding source I/O, sequential setup, financial
execution and journal writes; it is not a session-backtest throughput result.

`squeeze_ladder_protection.py` supplies percentage/structural target geometry
and normalized allocation weights using existing independent protection
slices. Real planner tests verify three bracket parents share one approved
total quantity and normalized journal round-trip preserves all three slices.
They do not prove fee budgeting, partial-fill lifecycle, once-per-session
admission recovery or selective rotation. Those remain publication gates.

An additional actual OMS unit integration submits all nine three-lot bracket
orders and recovers their submission boundaries, order identities and lot maps
after closing the original manager. Its local unit journal is not the native
app's durability authority; V4/Keeper recovery still needs qualification.
`strategy_one_intent.py` requests `mandate_fraction=1/3` for the original entry.
Retain that aggregate request for the first ladder comparison and split only
the resulting Portfolio-approved quantity. Do not substitute the illustrative
25% policy ceiling for the actual inherited entry request.

## Batch, lots and cash

The prepared `freeze_ladder_resistance` rule defines the first resistance
above VWAP as the nearest resistance band with its lower edge strictly above
the qualified VWAP, ordered by lower edge, upper edge and level ID. This is an
explicit conservative geometry choice, to be sealed before execution. A band
containing VWAP does not qualify as wholly above it. The frozen record retains
both bounds, confirmation epoch, original VWAP bits and the completed-cross
comparison threshold. A future confirmation is a source error. Missing or
changed selected geometry invalidates the setup rather than selecting another
band. A native completed-second V7 lookup and completed-cross integration test
qualifies the frozen witness against a later geometry change. This prepared
rule still needs coordinator binding, normalized setup persistence and full
preflight before it can authorize a numbered strategy's entry proposal.

`backtest_squeeze_ladder_setup.py` now binds only prepared VWAP-cross survivors
to the certified native V7 and pivot plans. It requires the same build, session,
market-plan token and exact bars attempt across dependencies. Each survivor
returns a typed result with source row/admission/qualification clocks, scan and
dependency plan identities, frozen resistance, frozen stop and an explicit
qualification/rejection reason. Missing active geometry is not silently dropped.
The native pivot cursor advances only to the qualification boundary. A real
Arrow-mask/native-plan integration test demonstrates that later V7 geometry
cannot replace the earlier band, and expired pivots reject the setup. Forty-one
source/setup regression tests pass. Coordinator entry proposals, normalized
setup fact persistence, complete release preflight and financial execution
remain unimplemented; this binding does not register or publish a strategy.

`backtest_squeeze_ladder_entry.py` now produces a prepared structural entry
proposal from that bound setup and a later exact completed observation. It
requires adjacent price evidence, retained VWAP, unchanged selected V7 interval
through the setup, continuous completed V7 source clocks, current quote/liquidity
eligibility and the same unexpired admission. A completed close at/below the
frozen upper comparison edge must precede a close strictly above that edge plus
the declared tick buffer. Expired/replaced admissions are rejected before
reading a setup prefix; the compiled TTL bounds eligible prefixes to 300 seconds.
No future price or V7 update chooses or replaces the frozen entry band.

The structural variant freezes the nearest complete set of distinct overhead
targets at proposal time, tick-floored one tick below each lower edge. It rejects
incomplete or collapsed target geometry. The original qualification stop is
retained. Tests connect certified Arrow observations, native V7/pivot lookup,
the breakout witness and three independent fixed-price protection slices;
68 source/setup/protection/OMS regression tests pass. This is a market proposal,
not financial authorization: coordinator session locks/permissions, aggregate
capital request, normalized decision publication, release sealing/preflight and
full-session financial runs remain required before app publication.

`backtest_squeeze_ladder_admission.py` now supplies prepared financial-state
admission to a single inherited one-third mandate capital request. It rejects
held positions, pending entry/exit/capital requests, closed permissions and
accepted/unresolved ticker-session locks. Future OMS snapshots are source
errors. The caller must supply the verified current-run as-of snapshot prefix;
an empty tuple is valid only when that prefix proves no earlier acquisitions.
The helper reserves no cash and submits no orders. Its deterministic intent
has zero requested shares, one protection profile and no replacement request
or opaque evidence metadata. Native intent projection round-trips the request
and three slices; an example approved total of 101 shares plans as 34/34/33
with independent OCA pairs. This tests representation/planning, not actual
Portfolio budgeting or native Keeper execution. Ninety-seven admission,
source, geometry, OMS and projection regression tests pass. Complete normalized
market-decision evidence, verified-prefix coordinator binding and full native
release qualification remain required before publication.

`backtest_squeeze_ladder_evidence.py` defines separate prepared normalized
setup and target-evidence families. The setup stores admission/qualification/
entry clocks, source-plan identities, original binary V7/VWAP geometry,
confirmed pivot identity/clocks, fixed stop and adjacent breakout closes.
Distinct target children preserve level and slice IDs, price and allocation
bits, ordinal and exact intent-event parent identity. These are named scalar
columns, not Strategy 1 BOS/frozen-gap placeholders or generic payload blobs.
Projection verifies the admitted intent's session clock and protection; exact
comparison against independently reconstructed expected rows rejects missing
lots and rehashed altered targets. One hundred source/admission/OMS/projection
regression tests pass. This is projection validation only: operator-owned table
installation, independent source reconstruction at writer admission, V4 family
commit/readback, Keeper recovery and coordinator use are not yet implemented.
No new database tables or journal rows have been created by this stage.

`backtest_squeeze_ladder_readback.py` now independently reconstructs prepared
evidence from supplied certified observation/V7/pivot plans and the declared
policy. It selects the exact qualification clock, rebinds setup geometry,
rechecks the breakout and financial admission, and compares every saved row
against the reconstructed result. Observation reads are truncated to the entry
boundary before setup reconstruction. A source-integrated test recovers the
same intent and rejects a false target despite its recomputed hash. Four focused
readback/evidence tests pass. Native writer integration must still supply its
verified current-run financial prefix, sealed release policy and validated
intent/event parent; these caller-provided objects are not a new durability
authority. No native writer, table installation or financial run is enabled.

`backtest_squeeze_ladder_journal_admission.py` additionally verifies the exact
native typed intent/event parent and all protection slices against the
source-reconstructed intent before returning prepared evidence families. It
requires one running acquisition, matching account/record identity and exact
native event/intent/slice rows. Tests reject an altered intent price and a
foreign slice even when market evidence remains valid. Five focused parent,
readback and evidence tests pass. This is a pre-publication verifier only;
the native writer envelope, independent verified context binding, Keeper-fenced
family publication and cold recovery are still required and not enabled.

The ladder table definitions now reside in shared
`arte_squeeze_ladder_schema.py`, avoiding a backend/journal-writer import cycle.
The native writer's scalar contract registry recognizes those families for
encoding only; it does not admit their publication or install their tables.
Native encoding matches the prepared hashes and round-trips unsigned 64-bit
bit patterns from stored decimal strings. A shared validator defect was fixed:
`FixedString(64)` now requires exactly 64 UTF-8 bytes before hashing, preventing
ClickHouse padding from changing the persisted representation. Existing writer
and V4 commit regressions plus ladder checks pass 138 tests. Complete ladder
commit/source authority and cold family readback remain required before runs.

Prepared journal admission now requires a native `V4CommittedPrefix` receipt
for the same run, in running status, whose final batch and sequence exactly
precede the entry batch. Missing, foreign, terminal and mismatched receipts
reject before source reconstruction. The native writer must obtain that receipt
through its verified prefix reader, then bind the historical financial and OMS
context to that prefix; manually constructing the dataclass does not prove
durability or source membership. Sixty-two focused source/evidence and existing
V4 commit tests pass. Writer dispatch, context reconstruction from committed
facts and actual Keeper-backed publication/recovery remain unfinished.

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

`ladder_admission_lock` consumes normalized frozen OMS snapshots to distinguish
an acknowledged acquisition from a filled-trade count. Accepted parents retain
the lock after cancellation with zero fills; unresolved submissions block a new
proposal. PM and AH have separate New York session identities. This consumer
is prepared but not yet wired into native ladder entry admission. Its caller
must provide the current run's verified, as-of snapshot prefix; a mutable
sidecar file cannot supply lock authority.

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

The repository already defines a producer-owned candidate swing source:
`arte.strategy_one_pivot_interval_v1` with coverage in
`arte.strategy_one_pivot_coverage_v1`. `PivotTimeline` exposes active pivots
only after their confirmation and validity boundaries. This confirms a typed
contract exists; it does not prove coverage for every campaign session.
`freeze_ladder_stop` consumes that active tuple at VWAP qualification and
freezes the latest confirmed low, tick-floored with an explicit buffer.
Missing lows or wrong-side latest lows reject the setup; a future pivot is a
source-contract error. Do not substitute an older low, completed-bar low or
later confirmation to rescue it. Coverage/attempt binding remains a native
preflight requirement before registration.

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

### Native repair gap found during integration

The existing OMS missing-protection branch aggregates group exposure and picks
the first available profile target for repair. That is insufficient for three
distinct target lots. Its inactive attached-child coverage rules also differ
from the existing mandatory full-target profile. Do not enable the prepared
ladder by merely marking all targets mandatory: that does not fix attribution.
`ladder_lot_exposure` now reduces owned cumulative fills separately per lot,
rejecting duplicate order observations, unknown ownership and exits exceeding
that lot's acquisitions. The prepared profile now routes through
`squeeze_ladder_oms.py`: each partial parent receives its own target/stop pair,
repeat reconciliation preserves capacity, and active original brackets retire
their temporary repair pairs. An actual simulated-broker/OMS integration
qualifies a partial second-lot fill, its own target, complete-parent retirement
and unit-journal cold recovery. Normalized ARTE projection separately preserves
lot IDs and two-order repair batch boundaries. Orphan capacity transfer,
cancellation/fill races and full native
V4/Keeper cold recovery still require qualification before publication.
Unit integration now also covers a broker accepting both repair legs while
returning only one acknowledgement: cold recovery restores planned closing
roles without another submission. Missing observations keep the outcome
unknown; changed observed command fields fail closed. Native recovery binds
the prepared profile's role from its matched persisted command before fill
accounting, rather than classifying parentless repairs as new entries.
Older profiles retain their existing reconciliation path.

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

### Complete development entry-feature diagnostic

The immutable runtime receipt
`strategy42-development-entry-feature-diagnostic-v1.json` groups all 138
positions from the 26 development runs using native entry-minute activity and
the first effective stop. Its input audit SHA256 is
`60da154c19ad54515f56af6cdce89284e01d20d1a3ab2daad934e7bca13ebc9e`.
Decimal aggregation reconciles exactly to the baseline net $1,883.9613.

| Entry-minute trades | Positions | Winning positions | Net P&L | Represented runs |
| --- | ---: | ---: | ---: | ---: |
| 100–999 | 39 | 15 | -$678.74 | 17 |
| 1,000–4,999 | 69 | 27 | -$617.85 | 24 |
| At least 5,000 | 30 | 16 | +$3,180.55 | 14 |

This supports comparing a stronger trade-count gate, not claiming its
portfolio result. Discarding the lower bands would also discard $8,454.86 of
winning-trade profit; changed admission changes shared cash and later entries.
The high-count band covers only 14 runs, so this table cannot establish the
per-session target. Entry-minute share volume is not monotonic: the
100,000–999,999 band totals +$2,699.69, whereas at least one million shares
totals -$208.57. First-effective-stop distances above 20% total +$1,788.48;
the 5–10% band totals -$2,127.52. A blanket wide-stop exclusion is unsupported.

These are descriptive development groups, not counterfactual backtests or
executable policy approval. Post-entry activity retention, point-in-time float
and RVOL are absent from this input and must not be imputed. Prioritize native
comparisons that separate entry trade count from post-entry activity decay and
structural breakout quality. Validation records were not read.

Market-source reconstruction now has a separate prepared entry point,
`reconstruct_ladder_market_decision`. It reconstructs the causal setup and
breakout from certified plans without reading current account state. It is
not financial authorization or a complete cold verifier: callers must compare
the evidence and parent intent and verify committed Portfolio/OMS lineage.
The existing writer-side admission check still requires historical pre-entry
financial state. The assignment-command table records status changes but
does not persist permission fields; it cannot alone prove historical entry
permission. Never manufacture permissions or substitute post-entry state.

`verify_ladder_market_evidence` now recomputes and compares every setup and
target row from the certified market plans, then returns the original unapproved
proposal through `build_ladder_proposal_intent`, the same serializer used by
financial admission. It takes no financial view. Independently verified parent
account/assignment identities and frozen policy remain caller requirements;
the proposal is not an approval or an order. A valid rehash cannot authorize a
changed target, and a foreign assignment fails the source projection comparison.
Native cold parent-row verification and committed Portfolio/OMS financial
lineage are still required before publication or full saved-result acceptance.

`verify_ladder_intent_parent` compares the reconstructed original proposal to
the exact native event, intent and independent protection-slice rows. Its cold
mode verifies every native row hash and uses the shared typed codec for stored
UTC DateTime64, decimals and other scalar fields; it does not round timestamps
or treat hashes as source authority. Writer-side admission reuses this check.
Only the three proposal families are accepted. Cold callers must independently
verify commit membership and source context first, then verify financial
lineage separately. Native cold-reader wiring remains unfinished.

### Connected native source probe: sparse-history mismatch

Read-only CDTG August 26 premarket probes
`ladder-cdtg-aug26-native-source-probe-v1.json` and `v2.json` independently
certified published Strategy42 configuration and a one-ticker native market
plan, then loaded the exact completed source and Signal Stream admissions.
The build was `1521ba7702a9ee0783916f706f4885a24a3f32a91630b04ff738a90e65bc9dd5`;
market plan token was
`b991bb9fef655c332fa8e3382c3293336b928d41bb112ccc14aaf8a372961f53`.
There were 28,884 observations and 45 squeeze admissions; both probes completed
in under six seconds excluding interpreter startup. No financial replay ran.

The explicit diagnostic gate used minimum price $1, session shares 10,000,
session notional $10,000, completed 10s/60s trade rates 3 per second, maximum
spread 200 bps, quote age at most one second and a five-minute admission TTL.
It produced zero market survivors and zero VWAP crossings. The history bit
rejected 28,883 rows; 4,771 rows passed every other gate. The prepared mask
expects dense 100ms history, whereas the native observation source is sparse.
Thus the fixture-qualified loader is not yet usable as an entry source for
this actual development case. This is a consumer/source contract mismatch,
not evidence that CDTG had no qualifying activity or that zero-trade profit
is an optimized result.

Do not remove the history gate or fill missing buckets with invented counts.
Before native execution, bind rolling activity to producer-owned complete
window evidence or explicitly prove the sparse prefix's completeness from
the certified source contract. Retain rejection for genuinely unavailable
history and retain completed-price crossing requirements. These probes read
development sources only and made no source, journal or order mutations.

The native producer contract in
`pipelines/market_sip/events/market_day_sql.py` groups canonical events by
occupied bucket; it does not generate an empty-grid row. The loader now marks
activity history available through the explicitly requested completed prefix
only after consuming the entire bounded, origin-starting Arrow query for the
certified source plan. `certified_history_through_ms` is that availability
bound, not an independent certificate: the caller must still independently
verify the market plan and read-only source authority. Standalone masks without
this bound continue to reject gaps. Counts are sums of persisted observations
over elapsed 10s/60s windows; no synthetic row, count or indicator is created.
VWAP crossing still requires adjacent completed valid price rows. Cold source
reconstruction truncates the availability bound with the causal prefix.

Connected probe `ladder-cdtg-aug26-native-source-probe-v3.json`, using identical
source token, scan content hash and policy, retained all 28,884 observations,
produced 4,771 market survivors and 66 VWAP crossings, and completed in 5.17
seconds. The original v1/v2 receipts remain immutable evidence of the defect.
Sparse-count tests reject a threshold that the actual persisted counts cannot
meet, missing crossing candles still reject, and unverified history still
rejects. This is one-ticker source/gate qualification, not setup completeness,
full-session financial execution or proof of an improved strategy.

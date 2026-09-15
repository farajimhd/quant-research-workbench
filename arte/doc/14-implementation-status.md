# Implementation status

Status: partial implementation. This is not the complete ARTE system.

The user has set an active goal to finish the entire implementation. This status
file tracks progress; an intermediate commit does not close that goal. Service
tests remain prohibited until the user copies ARTE to its separate repository.

## User instructions for this implementation

- Use the latest strategy source as the starting point. Pin it because it is changing.
- Implement primarily in Rust with bounded concurrency and efficient array operations.
- Do not run any service for testing before the user copies ARTE to the new repository.

No service, gateway, container, database writer, broker session, or browser server
was started during this implementation. Existing application files remain unchanged.

## Implemented

| Area | Working source |
|---|---|
| Workspace | Independent Cargo workspace and lockfile |
| Events | Exact decimal fields, source clocks, identity, payload deduplication, as-known reads |
| Coverage | Half-open missing-range calculation and declared dependency readiness |
| Market state | Causal sparse bars, checkpoint round trips, EMA and forming MACD preview |
| Concurrency | Bounded parallel market replay; account-owned reservation locks |
| Latency | Warning/block/recovery states and repeated unresolved alerts |
| Risk | Complete bracket, directional tick checks, expiry and buffered official LULD checks |
| Order state | Durable-envelope identity, unknown submission handling and cumulative fills |
| Historical seed contract | Historical-only publication, object references and availability gates |
| Strategy primitives | Causal preceding range and candle quality |
| Strategy evidence | Overhead encounters, trade-only next-opening rejection, grouped recovery and sparse range/progress gates |
| Strategy target/lifecycle components | Causal resistance targets, synthetic ladder, stop/swing selection, position phase and re-entry recovery |
| Position management | Frozen contiguous resistance attempts, protective-base updates, rejection-recovery exits and exit-priority evidence |
| V7 primitives | Student-t analytic objective/gradient and array-based level association |
| V7 historical evidence | Fixed-band encounters, role timelines, reaction annotation and split-adjusted nonoverlapping observations |
| V7 historical extraction | Gap-separated extrema, profile peaks, bounded-span candidate clustering and auditable role-based selection |
| V7 numerical fit | Versioned projected-BFGS Student-t fit, fitted band geometry and two-component BIC partition |
| Historical MLE seeds | Completed-session builder, predecessor continuity, retained evidence, split audit and availability checks |
| Seed persistence | Immutable object graph, manifest-last publication, reconstruction checks and ClickHouse adapter methods |
| Causal streaming V7 | Prior-seed initialization, rolling noise, contact outcomes, directional proposals, refits and recovery |
| Provider adapter | REST/WS field normalization and bounded REST pagination implementation |
| Persistence adapter | ClickHouse identifier checks, policy/part checks and synchronous inserts |
| Broker adapter | IBKR bracket request construction and confirmation classification |
| Offline CLI | Help/version and market-event replay, explicitly not a full strategy backtest |
| Scripts | Offline validation, CLI release packaging, blocked service-start plan |

Adapter network functions compile but have not been called. Pure adapter tests do
not prove provider, broker, or ClickHouse compatibility.

## Incomplete implementation

- Representative historical-seed validation and full streaming/fit/partition source-decision parity.
- Complete selected strategy lifecycle, admission, position management and exits.
- Effective configuration export from the selected current candidate.
- WebSocket receiver and integration of the complete in-process live path.
- Durable maintenance jobs, source certification and repair/publication integration.
- Final event schema and migrations after source-identity validation.
- Complete broker session, warning chain, pacing, protection and restart integration.
- Reference service, broker gateway and browser-login dependency packaging.
- Full strategy Backtest worker, fill model, audit persistence and resume.
- Copied frontend, observer/control API and Backtest/Debug/Live pages.
- Approved device profiles and selective service deployment/restart.

These are missing code, not merely tests deferred by the user. Do not label this
release operational or claim the full implementation request is complete.

## Frozen baseline

Source snapshots pin the latest available strategy/V7 files from source commit
`4fcc094e3668db068ac84199581092821c707eb3`. The profile builder names
`v7-setup-recovery-v9`. The full persisted effective configuration has not been
retrieved from a service. Reference snapshots are never loaded by production code.

## Validation boundaries

Checks executed for this slice:

- 133 in-process Rust tests passed (111 core and 22 adapter tests).
- Peak prominence matched direct scanning over all 2,187 seven-sample ternary sequences.
- Frozen-source extractor comparison passed 70 cases and 374 selected/rejected levels.
- Student-t fit comparison passed 87 cases. Maximum observed difference: 0.001406 ticks.
- Cargo format checks passed.
- Clippy passed with warnings denied.
- All 11 frozen source hashes matched the origin manifest.
- Three PowerShell scripts parsed without execution.
- The actual offline CLI help and a two-event market replay ran successfully.
- No service-backed or network validation ran. Deploy/run scripts were not executed.

Use `scripts/validate.ps1` with an explicit existing external runtime directory.
It runs Rust format checks, locked offline unit tests, Clippy, and reference hashes.
No network integration tests are included. Cargo output belongs outside source.

Do not run `deploy.ps1` or service-backed tests as part of this restricted validation.
The offline deploy script packages only the CLI; it does not satisfy the final
service-release contract. `run.ps1 -Action Start` rejects startup deliberately.

Performance is not benchmarked on representative market sessions. The numerical
primitive tests do not certify V7 parity. Passing domain tests does not establish
strategy profitability or broker protection under actual partial fills.

## Current implementation continuation

The historical evidence port follows the frozen `reaction_center` and
`historical_session_levels` references. It uses completed epoch seconds for
encounter evaluation and nanoseconds for the normalized reaction contract.
Callers must convert those units explicitly. The historical evaluator reuses
session arrays and prefix volume sums across candidate bands. Floating-point
sum differences still require tolerance-based comparison against the source.

Candidate extraction is now implemented. It preserves the frozen source's noise
geometry; this is not a fallback for a failed Student-t fit. Its typed ARTE input
hash has a separate contract version and is not the legacy Python JSON digest.
Its generated level identity retains the source version and formatted geometry.

Peak detection uses strict-higher monotone boundaries and a range-minimum tree.
This avoids quadratic rescans on periodic inputs without limiting prominence
to a local window. Semantic references: SciPy
[find_peaks](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.find_peaks.html)
and [peak_prominences](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.peak_prominences.html).

The offline source comparison covers geometry, level identity, selection, encounters,
role segments, profile volume and rejection evidence. It excludes the deliberately
different typed-input hash and does not establish full-session capacity.

The frozen streaming source requires `historical-session-reaction-mle-1`.
The fixed-noise extractor produces `historical-session-reaction-zones-2`.
These are incompatible seed geometries. In the current parent implementation,
`historical_checkpoint` exports streaming rows as the MLE daily book. ARTE must not
copy that publication path: the user requires a completed-session historical
producer and forbids promotion of streaming state to authoritative daily seeds.

The fitter preserves the df=4 objective, tick-coordinate bounds, scale floor and
three quantile starts. Its solver is `arte-projected-bfgs-2d-1`, not SciPy L-BFGS-B.
Numerical acceptance uses `max(0.001 ticks, 1e-8 * expected value)` and exact status
and floor-classification equality on the current 87-case panel. This is not
bitwise parity or proof of convergence on every session. Band partition currently
has unit tests but does not yet have full frozen-source component parity.

The initial completed-session MLE seed builder now exists. It is independently
versioned and tested for input integrity, next-session availability, predecessor
immutability, split adjustment and explicit capacity failure. Database publication
methods now compile but have not connected to ClickHouse. Maintenance integration
is still missing. See the owning V7 design document for
its exact algorithm and validation limits.

Seed objects contain one level each, plus a small root envelope. The envelope
does not duplicate the levels. Unchanged level objects reuse their hashes.
Changed levels still serialize their complete observation history; finer-grained
observation sharing and batched database reads remain performance work.
The initial two-table seed migration is source-only and has not been applied.
The adapter writes objects, checks readback, and publishes the manifest last.
Readers reject missing objects and distinct payload conflicts. A single publisher
must own a seed; a distributed publication lease is not implemented.

The first storage round-trip test exposed floating-point JSON parse drift.
Enabling exact `float_roundtrip` parsing restored the original seed hash.
Offline tests now cover interrupted object staging, retries and corruption.
They do not prove ClickHouse power-loss durability or connected recovery.

Streaming `arte-causal-v7-1` now initializes from ARTE historical seeds and advances
on ordered completed seconds. It tracks gap/timeout outcomes, role changes,
directional reversals, association and MLE refits. Noise uses balanced heaps.
Recovery is hash-bound to the exact historical seed. Processing failures block
further updates, actionable projections and recovery publication. No method exports
a daily historical seed from streaming state.

Four focused tests cover checkpoint continuation, predecessor immutability,
invalid-input rejection, gap outcomes, capacity failure and recovery integrity.
These are not complete frozen-source streaming parity or representative latency
benchmarks. The actual WebSocket/market/strategy process is not integrated yet.

The strategy encounter port separates completed-bar updates from quote/trade
updates. Quotes cannot consume the next-opening warning. Level confirmation must
precede the current candle. Blocked overhead levels recover together. Activity
checks retain sparse observed candles independently of the short consolidation
window; the 60-second price reference must be no more than five seconds stale.
These paths have focused unit tests, not full source-decision parity.

Target selection now preserves real-resistance priority and the source's synthetic
ladder behavior. It produces price candidates only; OMS bracket/LULD checks remain
mandatory. Setup lifecycle code keeps fill evidence, early-failure checks and
post-exit recovery separate from a new position's breakout phase. Reconciled
position observations cannot rewind state. Main strategy evaluation, complete
admission/add/exit management and source-config parity are still missing.

The position-management component now covers the frozen source's resistance
failure, higher-low base updates, repeated base failures, bearish structural
confirmation and failed rejection recovery. Resistance-failure exits retain the
frozen level and both candles. Departed attempts cannot turn unrelated later red
candles into an exit. Duplicate completed bars do not advance failure counters.
Future event rejection and capacity errors leave the previous management state
unchanged. Main evaluator wiring and end-to-end strategy parity are still missing.

Early-stop components track three rising green candles across position boundaries,
arm the second candle's close, and activate on the first completed red candle.
Gaps disarm pending patterns. Re-entry permission requires a reconciled fill of
the still-active matching stop. Graduation does not erase that stop identity.
Reclaim confirmation consumes one opening opportunity within a half-open one-second
window. Failed-resistance confirmation separately requires adjacent red candles
and cancels on band reclaim. These components are not yet wired to the main
evaluator or broker fill stream; tests cover local state transitions only.

The setup/recovery entry evaluator composes causal range/zone checks, early-base
assessment, prior acquisition-level selection, support and target selection,
recovery permission, and real-quote clearance into a typed proposal. It leaves
initial fill price and risk unset until execution reconciliation. Regular-session
targets require an explicit supplied value; no historical LULD estimate is created.
Admission, activity and official-band producers are not wired yet. This evaluator
does not authorize an order. Five offline tests cover the composed entry path,
quote feasibility, missing regular targets, early-base behavior and causal guards.
Whole-source strategy parity, dispatcher integration and effective
configuration binding remain incomplete.

Resistance-add progression now uses position-owned frozen pending levels and a
frontier reset to the entry close. It selects one nearest crossed resistance or
transition per completed candle. Red breaks can wait for a non-red confirmation.
Gaps clear pending breaks. A non-red opportunity is consumed even when admission
or execution geometry rejects it. Tranche counts track proposals, not fills.
Capacity errors preserve the prior state. Four offline tests cover selection,
one-shot consumption, red/gap behavior and capacity rollback. Admission producers,
portfolio allocation and bracket authorization still need dispatcher integration.

Protection management now composes confirmed swing trailing, fill-risk progress,
current-gain guards, target-break confirmation and regular/extended target changes.
It emits replacement proposals without changing the broker-confirmed stop or target.
This deliberately separates intent from acknowledgment where the frozen source
updated active protection optimistically. OMS reconciliation must supply active
protection on each evaluation. Missing official targets are never synthesized in
regular hours. Five offline tests cover proposals, quote guards, detector freshness,
session target policy and red-to-green target confirmation. Broker modification
safety and dispatcher wiring remain unimplemented.

The shared dispatch contract now wraps entry, add, protection and exit intents in
one versioned Live/Paper/Backtest envelope. Identity binds run, account, instrument,
code/config, input boundary, safety state, actions and evidence. Exit-first arbitration
skips the lazy downstream evaluator when an exit is required. Pending-only entries
are cancelled without a zero-quantity exit. Same-sequence retries require identical
causal and safety evidence. Four offline tests cover priority, phase handling,
pending-entry cancellation and cross-mode schema/retry behavior. This is an
arbitration layer, not the full runtime loop or durable journal writer.

Decision journaling now validates canonical envelopes and bounded contiguous
batches. The ClickHouse adapter checks predecessor availability, rejects conflicting
scope/sequence slots, inserts missing rows and verifies readback. The source schema
stores a scope hash, decision sequence and compressed canonical payload. It does
not create a market-event ordinal. Batches are limited to 256 records and 8 MiB.
Two offline tests cover identical retries, missing records, altered evidence and
sequence gaps. The migration has not been applied; adapter methods have not run.
Exclusive writer ownership, durable order-submission gating and power-loss
durability acceptance remain required. Insert/readback acknowledgment alone does
not prove power-loss durability.

The transaction runner now prepares bounded account-owned strategy state on a copy,
uses exit-first dispatch and commits only after matching journal readback. Ambiguous
writes retain one immutable pending decision. Exact retries do not rerun strategy
calculation; later inputs cannot overtake the pending acknowledgment. Calculation
errors and incomplete readbacks leave committed state unchanged. The ClickHouse
adapter connects append/readback to this commit boundary. Three offline tests cover
write failure, calculation failure and retries after commit. This runner is generic;
the complete selected-strategy state and live/backtest event loops are not yet wired.
Prepared state is memory-only; crash recovery and durable OMS submission gating
remain incomplete. Market arrays must not be placed in the cloned strategy state.

The account-owned candidate now composes completed-bar entry, reconciled fill-risk
freezing, setup phase/failure tracking, recovery observation, protection proposals
and resistance adds. Market frames are borrowed; only position state is copied.
Shared geometry policies must agree. Broker revisions cannot rewind or change
contents. One integrated offline test exercises entry preparation, journal commit,
fill reconciliation and rollback on conflicting broker evidence. It does not prove
full-session strategy parity. Intrabar observations, pending-capital invalidation,
encounter reset integration, exit/fill dispatch ordering and restart recovery still
need wiring into the event loop. Global exit arbitration remains a separate stage.

Transaction preparation now supports an observation stage before global exit
arbitration. Reconciled fill state can therefore survive a required exit that skips
strategy calculation. Observation and calculation changes still commit together
only after journal readback. Exact retries skip both stages. The candidate exposes
an atomic reconciled-position observer for this path. Two offline tests cover
fill-plus-immediate-exit ordering and rollback on observation failure. Provider and
broker event routing must bind complete observation hashes and use this API;
the live event loop has not yet been connected.

Intrabar acquisition checks now cover confirmation expiration, MACD age/episode,
VWAP, real ask ceiling, tradability and encounter cancellation. Encounter notices
are latched until the encounter clears. Cancelling acquisition on a held position
does not suppress protection management. Three offline tests cover exact expiry,
stale MACD, encounter latching and future-clock rejection. The frozen source's
capital-wait versus submitted-entry distinction remains explicit. These checks do
not replace OMS deadlines for submitted orders and are not yet called by live
WebSocket routing. Cancellation intents do not change broker fill/order state.

The Massive WebSocket receiver now implements one explicit real-time stocks
connection, authentication, trade/quote subscription, bounded frames, timeouts,
ping handling and cancellation health. Receive UTC and run-relative monotonic
time are captured before JSON classification. Overflow returns the undelivered
frame and fails feed health. It never reconnects without the caller's repair gate.
Four offline tests cover subscription validation, protocol status, cancellation
and queue overflow. Transport health is not trading readiness. Provider connection,
subscription completeness, latency integration, normalization fan-out and repair
handoff remain untested or unwired. No WebSocket connection was opened.
The protocol was checked against the official
[Massive WebSocket quickstart](https://massive.com/docs/websocket/quickstart).

The live decoder now connects received frames to the compact event normalizer.
It assigns distinct application sequences while preserving each frame's receive
UTC and monotonic stamp. Identity resolution receives source and knowledge times.
Per-instrument/channel monitors assess SIP age, optional participant age and local
queue delay. Required missing participant timestamps block exposure without
fabrication. A failed frame publishes no partial events and latches decoder failure;
the caller must retain the raw frame and establish a validated recovery run.
Four offline tests cover sequencing, missing clocks, queue latency and unresolved
identity. Alert delivery, persistence fan-out,
provider-overlap identity acceptance and runtime exposure-gate wiring remain open.

Latency recovery now counts only advancing source-session/sequence observations
within each instrument/channel. Repeated or older deliveries can worsen health but
cannot establish recovery. A caller-driven silence audit ages the original receipt
and repeats unresolved alerts without manufacturing events. Processing clocks cannot
rewind. Three new offline tests cover repeated samples, delayed duplicates and
silence alert cadence. The periodic caller, observer delivery and trading gate still
need event-loop integration. Source identity assumptions retain their overlap gate.

The order ledger now requires a borrowed market-readiness check at both bracket
authorization and submission. Both trade and quote lanes must have fresh, permitted
evidence. Disconnect clears that evidence. Reconnection alone cannot restore it.
The decoder feeds normalized-event and silence assessments into the same gate;
decode or gate-update failures clear readiness. Missing required participant clocks
remain blocking during silence audits. Three offline tests cover channel readiness,
disconnect invalidation, submission rechecks and decoder-to-gate behavior.
The live loop must still bind transport health, schedule audits and use the current
monotonic clock for each ledger check. This gate does not replace coverage, seed,
broker reconciliation, strategy approval or bracket validation.

The ingestion actor now consumes the receiver's bounded frame queue, normalizes
events, updates market readiness and schedules silence audits. It emits bounded
domain batches without waiting for a consumer. Overflow returns the undelivered
batch and original frame. Decode errors retain the frame. Input remains borrowed,
so queued frames remain available to the supervisor after failure or shutdown.
Transport failure, actor cancellation and shutdown disarm the shared readiness
handle. Restart requires a new actor and an explicitly certified recovery boundary.
Four offline tests cover overflow, failed transport, cancellation and periodic
silence blocking with repeated alerts. No connection was opened.

Readiness checks borrow the gate under a short shared lock. They must not perform
I/O or wait. Lock contention fails closed; representative concurrency and throughput
acceptance remain open. Clock-quality freshness is supplied by the supervisor and
must fail if evidence is missing. Receiver supervision, clock synchronization,
certified handover, ticker fan-out, persistence and observer delivery remain unwired.
The actor is not yet an executable Live service. It does not make queued strategy
operands fresh or replace revalidation at actual broker submission.

Next: full strategy entry/position/exit lifecycle and its effective configuration,
alongside streaming/partition source parity and batched seed persistence.
Do not substitute the current fixed-noise
extractor for the full historical MLE seed pipeline.

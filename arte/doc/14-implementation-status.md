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

- 76 in-process Rust tests passed (69 core and 7 adapter tests).
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

Next: full strategy entry/position/exit lifecycle and its effective configuration,
alongside streaming/partition source parity and batched seed persistence.
Do not substitute the current fixed-noise
extractor for the full historical MLE seed pipeline.

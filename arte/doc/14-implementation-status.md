# Implementation status

Status: partial implementation. This is not the complete ARTE system.

Execution cadence is now a required field in the new shared Rust computation
contract. It supports real-time events or a fixed 100 ms multiple. Its identity
hash changes when cadence changes. The contract covers strategy, Watchlist,
signal stream, scanner, rule set, indicator, level book, and named computations.
Strategy 350's new purchase-price gate pins event cadence. This gate implements
only the causal prior-close price ceiling, purchase price floor, and latched
late-mode prior-HOD zone. It is not an entry authorization or a full strategy
port. Startup dependency definitions and plan nodes now carry explicit cadence;
the plan identity pins it for signal streams, Watchlists, and other dependencies.
The causal scheduler boundary now has a validated cadence check. It routes
source trades and quotes to event computations and only a matching completed
bar to fixed-cadence computations. The shared test checks the bar and trade
boundaries. This is routing plumbing; no general signal or Watchlist calculator
is registered with it yet. A runnable system must reject any executable
definition without its own validated interval.
The completed-bar tape can now emit every 100 ms boundary or only aligned
fixed-interval boundaries, including empty buckets. It refuses to represent
event cadence using bars. This is backtest clock plumbing, not full evaluation.
The partial Strategy 350 gate now checks ordered source time and sequence, not
receipt order. Equal receive timestamps and out-of-order arrival times can be
processed after the ordered lane releases them. Late mode latches before the
purchase-price floor is applied. The gate now accepts independent causal session
context updates. They can latch late mode without a trade decision, enforce a
monotonic session high and source order, and fail on conflicting context. The
eligible-trade context builder now exists as a bounded per-ticker Rust component.
It derives open, high, and strictly prior high from source-ordered eligible trades.
The trade policy hash and source order are pinned; equal availability times do
not erase prior-event evidence. An uncertified session start cannot mark context
complete. External callers can only create an unready streaming builder or a
maintenance-only historical replay. The latter requires a fully verified REST
certificate, exact session scope, each pinned readback batch in order, and exact
row counts. It publishes causal contexts to a maintenance callback one batch at
a time. Actual REST acquisition times are not backtest decision timestamps.
Live handover authority, bar-backtest adaptation, and the remaining Strategy 350
rules are still unimplemented.
The first conservative Strategy 350 100 ms bar screen now emits contiguous
candidate-refinement masks. It preserves buckets whose intrabar order could
create a new HOD and subsequent qualifying pullback; it never authorizes a
trade. Full scanner/signal/Watchlist calculation, selected-candidate event
replay, and throughput validation remain unimplemented.
An in-memory Boolean calculation catalogue now verifies complete dense
readback, declared execution cadence, source-bar identity, and distinct unknown
state. A Strategy 350 join requires an aligned signal product; Watchlist is
required only under an explicit membership policy. The current source uses
`not_required`. The join
produces only an event-refinement mask. Sparse fixed-cadence transition storage
is now authored in migration 022, with an offline-tested reader that verifies
SSD policy and part placement before use, exact coverage and transition digest,
and full dense expansion. A prepared-product path now checks the pinned source
bar generation, full dense input, fixed-cadence state changes, and exact sparse
digest. The ClickHouse publisher checks acceptance and ownership, compares
immutable transition pages, publishes coverage last, and rereads the product.
Only pure preparation and decoding received focused unit tests. The schema was
not applied, no database was opened, and connected writer/readback behavior is
unverified. The historical Early Squeeze formula is partial; other signal and
Watchlist algorithms remain unimplemented.
The fixed-cadence Boolean producer now validates a complete vectorized result
grid against its certified 100 ms bar source. It preserves unknown versus
false and enters the sparse publication preparation path. The first Strategy
350 Early Squeeze historical formula and first-occurrence session latch now
feed that path. The formula uses exact close-ratio, trade-count, and volume
comparisons against the prior non-empty 100 ms bar. It does not supply full live
episode semantics, Watchlist formulas, a pinned effective Strategy 350
configuration, source parity, or connected ClickHouse publication.
The same Early Squeeze state now has separate historical and live modes. Live
observation requires a genuine availability clock; historical projection never
substitutes one. The bounded immutable checkpoint pins its source scope and
formula at state creation, verifies its content hash, and rejects clock or
geometry mismatch on restore. Migration 023 and a ClickHouse adapter now
provide immutable scoped signal-checkpoint publication, exact readback, and
latest-as-of restore. In-process unit tests cover ambiguous insert retry,
causal latest selection, and conflicting slots. The migration was not applied
and no database was opened. The MDE live actor, continuous empty-bucket
advancement, and feed coverage remain required for live use.
The inspected live scheduler currently exposes floating-point bars. Feeding
those into the exact integer signal by rounding would not establish parity at
its threshold. An exact compact 100 ms live bar builder remains required.

The user has set an active goal to finish the entire implementation. This status
file tracks progress; an intermediate commit does not close that goal. Service
tests remain prohibited until the user copies ARTE to its separate repository.

## New backtest requirements and first contract

Strategy 350 is now the replacement candidate. The design starts historical
preparation from vectorized completed 100 ms bar batches, driven by the scanner,
data catalogue and rule sets. It requires pinned ClickHouse bars, indicators,
historical levels, signals and Watchlist products, a validated compact bar
contract, and compact ClickHouse-only logs and run evidence. SQLite is forbidden.
The inspected Strategy 350 builder states a Watchlist prior-close fix but does
not visibly remove a rule in its body. Effective-configuration confirmation is
required before parity claims; no prior candidate compatibility path is planned.
The first Rust compact-bar contract now defines a bounded column selection and
100 ms grid with exact scaled integer prices, sizes and notional. It validates
bucket values, exact request and coverage identity, source knowledge cutoff,
and complete ordered readback for all requested instruments. Five core unit
tests pass. An adapter unit test verifies query projection and sparse expansion.
The ClickHouse reader preflights storage policy
and part placement, reads a pinned coverage payload, projects required columns,
pages sparse nonempty buckets, and reconstructs dense 100 ms arrays. Precision
is pinned per ticker, including wholly empty pages. Migration 021 authors the
sparse table and compressed coverage table; it is not applied. A single-instrument
materializer now consumes a verified REST trade source and the pinned condition
policy. It sorts one ticker's trades and rejects duplicate source identities.
accumulates exact scaled OHLCV/notional values, and leaves empty buckets sparse.
The publisher re-verifies the source certificate, requires acceptance and an
ownership lease, compares each persisted page before making coverage visible,
and verifies the final manifest readback. Three focused adapter unit tests pass.
Connected writer/readback behavior and compression remain untested. The
multi-ticker catalogue executor, scanner/rule evaluator, reference-pinned
precision supply, Strategy 350 port, and runnable bar-based backtest remain
unimplemented. No throughput benchmark, database connection, or service test ran.
The first typed Strategy 350 dependency planner now separates broad bar/signal
screening from selected-candidate trade/quote refinement. It rejects missing
product definitions and duplicate ticker scopes. Two focused unit tests pass.
This does not evaluate scanner rules or port Strategy 350 trading behavior.
A sealed complete-bar result now feeds a deterministic 100 ms tape. Its unit
test checks cross-ticker ties, sparse decision dispatch and overlapping-product
rejection. The tape is not wired to the strategy or simulated OMS yet.
No backward compatibility with obsolete strategy or storage formats is required.

## Indexed bootstrap and historical gap projection

Fresh backtest bootstrap now uses verified indexed trade loading. Indexed startup
rejects unaligned domains and invalid capacities before source metadata reads.
It retains the ordinary plain loader for callers that do not request gap evidence;
that path never grants continuity authority.

Historical empty-span projection binds verified evidence to the exact run,
source catalog, source certificate and delay model. Actual publication time stays
separate from modeled interval-end availability. Wrong scope, run, cutoff or
modeled clock fails. The token is historical-backtest-only, not live authority.

All 443 offline tests, formatting, Clippy and copied-source checks pass. Bootstrap
tests exercise indexed evidence and both clocks. No services or database writes
ran; source parity was not rerun. Swing-continuity consumption and executable
orchestration remain unfinished.

## Verified empty-trade evidence

Added bounded per-second trade occupancy built through the existing batch
verifier. Only completed readback verification can issue an index. Empty spans
pin certificate, scope, interval and publication time; occupied, unaligned,
out-of-domain or not-yet-known spans fail. All recorded trades count regardless
of calculation eligibility. Evidence never derives from live silence.

The explicit indexed source loader builds this index without fetching batches
twice. Plain loading grants no gap authority, and indexing failures do not fall
back. Tests cover boundaries, empty pages, incomplete/corrupt scope, capacity,
knowledge-time rejection and exactly-once batch reads.

Startup selection, historical clock/provenance binding and swing-continuity
consumption remain unfinished. No fabricated historical availability or automatic
continuity was introduced.

All 442 offline tests, formatting, Clippy and copied-source checks pass. No
services, network checks or database writes ran. Source parity was not rerun.

## Owned swing entry assembly

Configured replay entry and live completed-candidate preparation now consume the
feature owner's swing array. Their production input type has no swing field.
The shared assembler checks the exact completed boundary and borrows the array
without per-account copies. Explicit-evidence APIs remain for isolated algorithm
and execution tests, not configured production runs.

Existing coordinator tests now exercise owned assembly. Feature tests check array
identity, unchanged external restrictions and rejection of mismatched boundaries.
This does not establish profitable strategy behavior or complete executable
orchestration. Certified-empty-gap handling also remains unfinished.

All 439 offline tests, formatting, Clippy and copied-source checks pass. No
services, network checks or database writes ran. Source parity was not rerun.

## Shared local-swing ownership and recovery

The feature owner now computes local swings at completed one-second boundaries.
Required settings participate in feature identity version 4. Feature recovery
version 3 embeds both swing and encounter state, carried by existing whole-run
checkpoint graphs. A boundary-checked accessor exposes the owned swing array.

Swing recovery pins scope, configuration, context and the last consumed candle.
It validates clocks, extremes, rolling volatility, levels, pending retests and
canonical bytes. Tests restore after every candle, including gaps and pending
break states. A combined test caught and corrected higher-timeframe recovery
selecting an equal-time one-second candle that had not yet been consumed.

Candidate entry assembly still accepts explicit swing inputs. Switching the
production path to owned evidence and adding certified-empty-gap support remain
required. Executable loops and complete live orchestration are unfinished.

All 439 offline tests, formatting, Clippy and copied-source checks pass. No
services or database writes ran. Source-parity suites were not rerun this turn.

## Causal local-swing component

Implemented the strategy-local directional-change swing projection in Rust.
Frozen source is recorded at the existing strategy reference commit with SHA-256.
The component preserves separate pivot/confirmation clocks, prior-only volatility,
anchored geometry, break/retest role changes, candle-count expiry and gap resets.
Major visual swings and score/segment presentation are deliberately out of scope
for this local projection. ARTE assigns separately namespaced local identities.

All 437 offline unit tests, formatting, Clippy and copied-source checks passed.
The standalone frozen-source comparison passed
for 3,000 candles in six deterministic paths. It compares geometry, clocks, roles,
activity and order, not trading decisions or profit. Existing full source-parity
suites were not rerun. No services or database writers started.

Feature ownership, validated recovery and certified-empty-gap support remain to
be connected before these swings can replace explicit strategy evidence inputs.

## Encounter ownership and configuration integration

The shared feature owner now owns encounter state for live and replay. Required
settings participate in feature identity version 3 and effective strategy hashes.
Candidate configuration rejects differing entry/encounter tick sizes. Missing
encounter settings fail decoding instead of receiving defaults.

Feature recovery version 2 embeds the encounter object. Candidate and whole-run
recovery graphs already carry that feature child. Existing recovery tests now
exercise the combined image; changed encounter settings cannot restore it.

Completed admission and intrabar acquisition preserve external restrictions and
add internal encounter blocking. Replay decision wrappers and the live completed
wrapper merge encounter exits before dispatch. Low-level evaluator APIs remain
injected-evidence interfaces, not substitutes for boundary-owned orchestration.

All 435 offline tests, formatting, Clippy and copied-source checks pass. Tests
cover configuration identity, required settings, restriction merging and combined
recovery. No services, migrations, network checks or source-parity checks ran.
Executable loops, swing evidence and full live orchestration remain unfinished.

## Encounter recovery

Encounter state now has bounded immutable recovery objects. They pin the market
image, configuration, context and pending boundary. Restore verifies hashes,
canonical bytes, geometry, thresholds, timestamps and snapshot consistency.
Warnings retain whether opening evidence was consumed. Failure state survives.

The scheduler fixture now restores at every boundary. Two new tests cover warning
continuation, failed-state recovery and invalid images or pins. All 434 offline
tests, formatting, Clippy and copied-source checks pass. No services started,
migrations ran or source-parity checks ran.

This completes component recovery, not executable integration. Candidate/live
ownership, effective configuration binding and recovery-graph publication remain
required. The whole ARTE goal remains active.

## Boundary-owned encounter state

The shared encounter runtime now consumes sequential scheduler boundaries. It
uses prior structural levels for completed one-second bars. Quotes and excluded
trades cannot consume next-opening-trade evidence. Exact retries are no-ops;
changed retries, skipped boundaries and invalid clocks fail closed.

Its restriction adapter adds entry blocking and encounter exits without clearing
external restrictions. Four new tests cover real scheduler progression, retry
identity, failure handling and synthetic warning routing. This is a component,
not completed executable integration. Checkpoint recovery, effective strategy
configuration binding and candidate/live ownership remain unfinished.

All 432 offline tests, formatting, Clippy and copied-source checks pass. No
services started or migrations ran. Source parity was not rerun.

## Causal market admission assembly

Completed-bar admission now derives detector identity, MACD state and activity
restrictions from the shared boundary-aligned feature owner. Live and replay use
the same assembler. Detector identity binds actual qualified V7 geometry and
source/configuration identity; it no longer needs a test-only placeholder.

External permissions, session state, tradability and encounter restrictions remain
explicit inputs and are preserved unchanged. Future authority timestamps and
wrong boundary kinds/identities fail. Existing live and multi-account coordinator
fixtures now exercise this path, deterministic repetition and restriction
preservation. External-authority and encounter/swing orchestration remain open.

All 428 offline tests, formatting, Clippy and copied-source checks pass. Existing
fixtures were extended; no services started or migrations ran. Source parity was
not rerun.

## Connected historical initialization

The initialization adapter now connects pinned historical seed loading, certified
source assembly, checked market construction and the existing strategy/account
session publication path. It verifies startup identity and acceptance/ownership
before reads. Strategy/account validation precedes the first startup write.
Successful publication returns a paused session, source input and seed graph.
There is no implicit resume, broker call or checkpoint recovery.

Two fixtures exercise read-only bootstrap into a paused market run and rejection
of changed domain/seed inputs before source loading. The fresh-session publication
components retain their existing offline tests. The combined connected database
path has not run. The executable loop and durable market-run-plan binding remain
unfinished.

All 428 offline tests, formatting, Clippy and copied-source checks pass. No
services started or migrations ran. Source parity was not rerun.

## Historical source startup assembly

A read-only assembler now connects pinned certificate reads, trade-policy loading,
verified channel batches and historical projection. Certificate metadata and policy
scope/cutoffs are checked before batch I/O. Each event batch is read once through
the existing verifier. Raw acquisition timestamps remain unchanged.

The source channels retain separate budgets and load sequentially. Each channel
uses bounded concurrent batch reads. Projection limits apply to combined events.
Failure returns no partial input and performs no writes. A metadata-only
ClickHouse certificate read is explicitly not a verified-coverage receipt.

Four fixtures cover both empty and populated quote input, exact batch-read counts,
wrong identities, future evidence, budgets and missing/corrupt data. Executable
coordination remains unfinished; connected ClickHouse reads remain untested.
All 426 offline tests, formatting, Clippy and copied-source checks pass. No
services started or migrations ran. Source parity was not rerun.

## Pinned trade-policy persistence

Trade policies now have exact-hash startup reads and guarded ClickHouse publication.
Quote and trade policies use one shared persistence implementation. It preserves
provider/cutoff checks, bounded canonical payloads, conflict detection, storage
verification, extraction/durability acceptance and cooperative ownership.

Migration 020 defines the trade-policy table on `live_market_ssd` and remains
unapplied. Two new readback fixtures cover absent/future/conflicting/malformed
policies, identity changes and maximum supported policy size. Connected publication
and durability acceptance remain untested; no service or database was started.
All 422 offline tests, formatting, Clippy and copied-source checks pass. Source
parity was not rerun.

## Shared trade eligibility

Live ingestion and historical projection now share a pinned trade-condition
evaluator. Policies declare allowed codes, known excluded codes, empty-condition
behavior, provider, validity interval, availability and source identity. Unknown
codes fail rather than becoming silently eligible or ineligible. Marked
corrections require a separate causal contract and remain rejected.

The live lane requires a policy and no longer accepts an eligibility boolean.
Missing/unknown rules fail the lane before enqueueing the affected trade.
Historical preparation requires policy availability at interval start, derives
all decisions and pins their identity. Known exclusions remain observable input.

This is the current all-or-none calculation contract, not a complete interpreter
of independent provider OHLC/volume rules. Policy certification and executable
startup wiring remain open. All 420 offline tests, formatting, Clippy
and copied-source checks pass. No services started or migrations ran. Source
parity was not rerun.

## Checked backtest market startup

A portable market-startup document now assembles the shared market/V7 scheduler
and account run. It binds the run, historical seed manifest, market configuration,
quote policy, split adjustment and scheduler limits. Prepared input must match
the session domain and final watermark. Quote policy availability is checked at
session start. Seed hydration verifies the complete historical object graph.

Assembly returns a paused single-instrument run without I/O. It does not replace
the seed/acquisition publication checks or the strategy/account startup document.
The executable coordinator and multi-instrument session integration remain open.

Three fixtures cover portable decoding and empty-interval completion, wrong pins,
changed or incomplete seeds, future policies, incompatible source domains and
bounded input. All 415 offline tests, formatting, Clippy and copied-source checks
pass. No services started or migrations ran. Source parity was not rerun.

## Explicit historical clock projection

Certified trade and quote sources can now produce a pinned historical catalog and
prepared replay frames. The projection keeps original source objects unchanged.
Simulation-only copies use a declared fixed SIP-to-availability delay. No receive
or participant timestamp is invented. Certificate IDs, the timing model and all
caller-supplied trade-eligibility decisions contribute to identity.

Preparation requires matching channel intervals and scopes. It rejects missing
eligibility, duplicate identities, marked corrections, overflow and exceeded
budgets. Equal-SIP groups release after their final bounded chunk. Empty coverage
advances the final watermark without inventing events. This remains retrospective
simulation, not reconstruction of the original as-known tape.

The shared evaluator above can now derive eligibility decisions. Its provider
rule source still needs certification and wiring by the executable runner.
No service or connected acceptance test is authorized yet.

Five projection fixtures cover timing/provenance, tied groups, empty coverage,
invalid inputs, explicit ineligibility, correction rejection and conflicting quote
versions. All 412 offline tests, formatting, Clippy and copied-source checks pass.
No services started or migrations ran. Source parity was not rerun.

## Certified historical source loading

A read-only loader now reconstructs observations from the batches named by one
acquisition certificate. It checks the independent certificate hash and knowledge
cutoff before I/O. Batch reads run with bounded concurrency; verification follows
the certified pagination order. Batch, event and retained serialized-byte budgets
are enforced. In-flight batch memory is additional and bounded by concurrency and
the existing event-batch contract.

The result contains only verified complete input. Errors return no partial source.
Stored timestamps and missing receive/participant times are unchanged. Certified
empty intervals return empty input without invented events. The loader does not
merge revisions, infer eligibility, create replay frames or certify upstream
provider completeness.

Three fixtures cover concurrent ordered loading, preserved clocks, empty coverage,
missing/changed batches, knowledge cutoff and budgets. All 407 offline tests,
formatting, Clippy and copied-source checks pass. No services started or migrations
ran. Source parity was not rerun.

REST observations carry acquisition-time availability, not the historical
simulated decision clock. The projection above is available; executable runner
integration remains unfinished. It must never present modeled latency as measured.

## Independent rejection readback during recovery

The controller now verifies retained sizing rejections through a read-only
journal interface. Each batch is bounded to at most 4096 records and reports
per-action outcomes. Only exact readback verifies an action. Missing records,
conflicts and read errors retain pending work. Already verified records are
skipped on retry. No rejection is recalculated or republished through this path.

Session loading uses batches of 256 before returning the paused restored session.
The existing funding-absence and decision-ownership checks still apply. The
shared-cash recovery fixture covers missing/conflicting records, cancellation,
invalid batch bounds and idempotent verification. It preserves checkpoint identity
and reservations before acknowledgment.

All 404 offline tests, formatting, Clippy and copied-source checks pass. No
services started or migrations ran. Source parity was not rerun. Connected
recovery acceptance and the executable runner remain unfinished.

## Startup-bound session recovery

Common-cut recovery root version 2 now pins startup identity for assembled
sessions. Restore requires the matching startup document, configuration map and
cost model. The restored controller retains that identity on subsequent captures.
`Session::restore` returns current checkpoint state paused; it does not rebuild
initial balances. Component-only graphs cannot be restored as sessions. Version 1
common-cut roots are rejected without implicit migration.

`load_backtest_session` independently loads the startup document from ClickHouse
before restoring the checkpoint. That database-backed entry point has compiled
but has not been exercised against a service.

The offline fixture preserves changed current cash, verifies paused recovery and
identical recapture, and rejects missing/changed startup identity and old roots.
All 404 offline tests, formatting, Clippy and copied-source checks pass. No
services started or migrations ran. Source parity was not rerun. The executable
runner, source-loading orchestration and multi-instrument recovery remain open.

## Persisted fresh-session creation

`create_backtest_session` connects semantic session assembly to startup storage.
It validates the document, policies, execution models and accounts before any
write. It then publishes and verifies the startup document under the startup
lease. Only successful publication returns the still-paused session. The existing
extraction and durability gates apply before this operation.

The offline session fixture now uses this path. Invalid account budgets and
missing ownership produce no writes. An ambiguous root write returns no session;
rebuilding the unused prepared run and retrying the same document succeeds
without extra writes. The resulting session retains its startup hash and account
budgets and still requires explicit resume and all boundary decisions.

All 404 offline tests, formatting, Clippy and copied-source checks pass. No
services started or migrations ran. Source parity was not rerun. This API does
not replace checkpoint recovery. Source loading, recovery-root linkage and the
top-level executable backtest loop remain unfinished.

## Immutable startup-document persistence

The ClickHouse adapter can publish and load the startup document. It uses 1 MiB
content-addressed chunks, exact chunk verification and root-last publication.
The root slot is stable per manifest. A different startup document cannot replace
it. Storage policy checks and cooperative lease checks remain mandatory, along
with extraction and durability acceptance. Migration 019 is unapplied.

In-memory tests cover failed chunks, an ambiguous root write, exact retry without
extra writes, conflicting startup inputs, missing/corrupt chunks, wrong expected
identity, noncanonical roots and lost ownership. A multi-chunk transport fixture
uses synthetic extra accounts; it is not a valid session or strategy fixture.

All 404 offline tests, formatting, Clippy and copied-source checks pass. No
services started or migrations ran. Source parity was not rerun. The executable
runner and recovery roots do not yet require this persisted startup identity.

## Pinned fresh-session startup inputs

Production session construction now consumes a versioned startup document and
an independently supplied expected hash. The document includes the manifest
identity, strategy configurations, initial account balances, price precision,
execution models and resource limits. The assembled session retains its startup
hash. The previous unpinned constructor is available only in unit tests.

Parsing bounds input to 16 MiB and maps to 4096 entries. Duplicate account or
configuration keys cannot silently overwrite earlier values. The roundtrip and
mutation fixture checks balances, capacities, precision, model settings, manifest,
version, unknown fields, duplicate keys, oversized input and wrong expected hash.
All 404 offline tests, formatting, Clippy and copied-source checks pass. No
services started or migrations ran. Source parity was not rerun.

The startup persistence adapter is now implemented as described above. Recovery
root linkage and source loading remain necessary before exposing a reproducible
executable strategy-backtest command.

## Fresh backtest session assembly

A production constructor now assembles a prepared account run, simulator, fill
projection, configured candidates and portfolio. It returns a paused session.
It performs no I/O and has no broker capability. Recovery remains a separate
common-cut operation; an advanced run cannot be passed off as a fresh session.

The constructor requires an exact account set, run-scoped simulation balances,
empty initial reservations and matching cost currency. Existing authorities
validate source, effective strategy-policy, quote-policy, fill-model and cost
bindings. Resource limits remain explicit. Accounts retain separate cash budgets;
strategies sharing an account use the same portfolio reservation authority.

Three offline fixtures cover successful construction, shared-account funding and
16 invalid-input cases. All 403 offline tests, formatting, Clippy and copied-source
checks pass. Source parity was not rerun. No services started or migrations ran.

This currently assembles one instrument with multiple account/strategy consumers.
Cross-instrument portfolio orchestration, complete startup-input provenance,
source loading and the executable strategy-backtest command remain unfinished.
The existing `replay-market` command is not a strategy backtest.

## Combined checkpoint publication and boundary advancement

The ClickHouse adapter exposes `commit_backtest_boundary`. It holds exclusive
controller, candidate and portfolio references across publication. It recaptures
the finalized graph before any write and rejects owner drift. After verified
publication it checks ownership again and acknowledges without another await.
Callers retain the finalized graph for exact retry after cancellation or failure.

The candidate fixture exercises this same commit path with an in-memory store.
Failed chunks, cancellation after root insertion and ownership loss during retry
leave acknowledgment unchanged. An exact retry advances once. A changed account
budget is rejected before any storage write. Existing publication and duplicate
acknowledgment checks remain in place.

All 400 offline tests, formatting, Clippy and copied-source checks pass. No
services started or migrations ran. Source parity was not rerun. The top-level
runner must still assemble this operation with its recovery evidence and lease;
the adapter and fixture do not establish complete runnable-service acceptance.

## Finalized boundary publication and acknowledgment

The common-cut ClickHouse publisher now requires a typed finalized graph.
Missing decisions or unfinished actions prevent its capture. This tightens the
previous publisher contract: partial graphs remain available for in-memory
recovery validation, but cannot occupy the immutable persisted boundary slot.
The slot remains unique by manifest and boundary sequence. It cannot be replaced
with a different graph after action completion.

Publication returns a typed receipt only after full graph and journal validation,
root-last storage publication, exact readback and ownership checks. The guarded
acknowledgment recaptures the controller, candidates and portfolio with exclusive
references. Its root must equal the published root before the boundary advances.
The receipt is not proof of power-loss durability. The independent durability
acceptance gate remains mandatory.

Offline fixtures cover missing decisions, pending actions, failed chunk writes,
ambiguous root writes, exact retries, changed account budgets, unrelated funding
and duplicate acknowledgment. All 400 offline tests, formatting, Clippy and
copied-source checks pass. Source parity was not rerun. No services started or
migrations ran.

Remaining integration: the full runner must use this guarded handoff instead of
the low-level component acknowledgment. Restart orchestration must restore the
last finalized cut and replay subsequent journaled work. This change does not
claim that orchestration or multi-instrument recovery is complete.

## Funded coordinator lifecycle coverage

Two additional fixtures route funded entry, protection replacement and terminal
settlement through the boundary coordinator. One exits through an explicit exit
action. The other fills the replaced profit target. Both accounts retain their
own sizing and cash limits. Actions and settlement are processed one at a time.

The fixtures verify ownership, reservations, exact retries, fees, realized cash,
checkpoint recovery and checkpoint-required cuts. Fill journal publication uses
the established lifecycle fixture; coordinator-specific fill retry behavior is
covered separately below. Strategy intents are deliberately supplied by the
execution fixture, not generated by the candidate algorithm. These tests do not
prove strategy profitability or complete candidate-to-runner acceptance.

All 400 offline tests, formatting, Clippy and copied-source checks pass. Source
parity was not rerun. No services started or migrations ran. Full runner assembly
and live integration remain unfinished.

## Coordinator fill and funding-failure coverage

New offline coordinator fixtures exercise two account fill scopes. Missing scope
publishers, ambiguous journal writes and cancellation preserve pending fills.
Each scope retries its exact records. Candidate features remain unobserved until
both scopes commit. Projection quantities and fees are checked, and market
acknowledgment remains blocked while decisions are missing.

These are explicitly seeded plumbing orders, not strategy-approved entries.
The tests verify that they cannot claim strategy-owned account state. A second
fixture expires seeded orders without valid funding ownership. The coordinator
reports each funding error on retry, keeps balances unchanged and does not move
on to feature observation or evaluation. The funded fixtures above now cover
successful coordinator settlement. Production guards were not weakened to
accommodate these fixtures.

All 398 offline tests, formatting, Clippy and copied-source checks pass. Source
parity was not rerun. No services started or migrations ran.

## Bounded market-boundary coordinator

The playback controller now services one bounded phase of a dispatched boundary
per call. It validates candidate ownership and decision-publisher scopes before
publication. It then selects work from existing authoritative component state:

1. Publish one pending fill batch using its execution-position scope.
2. Reconcile a bounded batch of terminal-order funding before new sizing.
3. Observe market features and report scopes that need evaluation evidence.
4. Publish prepared candidate decisions with bounded concurrency.
5. Resolve a bounded action batch, including journaled sizing rejections.
6. Return the exact cut that requires checkpoint publication.

The coordinator does not advance market data or claim checkpoint durability.
The caller still publishes the common-cut graph and acknowledges the boundary.
It does not fabricate admission, permission, reference or strategy evidence.
Prepared and committed-but-unregistered decisions are retried without requesting
a second calculation. Per-account and per-order failures remain visible.

The candidate fixture now uses this coordinator to request evaluation, publish
Hold/Wait decisions and reach the checkpoint boundary without acknowledgment.
Coordinator-level rejection still needs direct coverage; its underlying
components have separate offline fixtures. This is not
the complete CLI loop, live runner or automatic evidence assembly.
All 396 offline tests, formatting, Clippy and copied-source checks pass. Source
parity was not rerun. No services started or migrations ran.

## Journal-aware bounded action dispatch

One asynchronous dispatcher now handles sized entries, retained-allocation retries,
protection changes, cancellations, exits and terminal sizing rejections. It uses
the same input preflight as existing dispatch. Batch size remains bounded. A
failed action blocks later actions in that decision within the selected batch;
independent selected decisions can continue.

Results distinguish simulated submission, applied action and journaled rejection.
Submission here means simulator acceptance, not live broker acknowledgment.
Rejected entries retain their original evidence before journal publication.
Cancellation and failed publication leave that evidence available for exact retry.
Funded submission failures retain their allocation and cannot become rejections.

Lifecycle fixtures now use this dispatcher for normal execution, shared-cash
rejection and simulator-capacity failures. The rejection case cancels a pending
publication, retries unavailable and ambiguous journals, and verifies the same
evidence hash before successful resolution and checkpoint recovery.
All 396 offline tests, formatting, Clippy and copied-source checks pass.
Source-oracle parity was not rerun.

This integrates action resolution, not the whole outer runner. Configuration,
reference and candidate-evidence assembly, complete CLI orchestration and live
execution integration remain unfinished. No service or network test ran.

## Controller-owned rejection completion and recovery

The playback controller can now capture an unfundable entry's rejection once and
publish it through the rejection journal. Its evidence hash binds the committed
decision, owned quote, account snapshot, sizing/cash policies, session, bands,
latency and quote-age policy. Retries retain this evidence rather than recomputing
it after cash changes. A prepared rejection blocks sizing, funding and submission
of that action.

Exact journal readback resolves the action. Preparation and confirmation both
check that the command has no controller funding or completion, portfolio
reservation, simulated order, execution ownership, cash state or released funding.
The simulation run and currency scale must match. No reservation is released.
An ambiguous submission cannot be converted to a sizing rejection.

Controller checkpoints are version 5. They retain the rejection record and
journal-publication progress, but do not persist journal verification authority.
Restored rejection actions remain unresolved until independently read journal
receipts are confirmed. Conflicting receipts fail. Earlier controller checkpoint
versions are rejected; no implicit migration is provided.

The shared-cash lifecycle fixture now publishes a rejection, retries an ambiguous
write, restores the controller, verifies that acknowledgment is blocked, then
confirms independent readback and advances. It checks unchanged reservations,
idempotence, conflicting evidence, exact checkpoint recapture and funding guards.
The simulator-capacity failure remains funded and cannot take this rejection path.
All 396 offline tests, formatting, Clippy and copied-source checks pass. Source
parity was not rerun. No services started and no migrations were applied.

The journal-aware dispatcher above now drives this workflow alongside normal
actions. The complete outer runner and live integration remain unfinished.

## Decision-bound rejection journal

Sizing rejections now have a shared versioned journal contract. Each immutable
slot identifies one committed decision and action. Validation rejects a different
decision, invalid clock, unsupported action, fundable assessment, changed stop or
entry above the strategy cap. The record includes an evidence hash; that hash
does not itself certify the quote, account snapshot or policy provenance.

Publication retains the pending record across cancellation, transport failure and
conflicting readback. Only exact readback creates a receipt. Recovery can validate
a separately loaded row against its expected hash and committed decision.

The ClickHouse publisher requires extraction and durability acceptance, an owned
strategy scope, and storage-policy/part-placement checks. Reads reject conflicting
or noncanonical payloads. An ambiguous insert retries the same slot. Migration
`018-action-rejections.sql` is authored and unapplied. Ownership remains
cooperative single-host locking, not distributed fencing.

The controller integration described above supersedes the initial journal-only
limitation. Journal receipts alone still cannot release funds or erase orders.

The existing shared-cash lifecycle fixture exercises the contract and an in-memory
journal: cancellation, ambiguous writes, conflicting readback, exact retry,
duplicate acknowledgment, malformed records, and Live/Paper/Backtest clocks.
No database connection, service start or migration execution is involved.
All 396 offline Rust tests, formatting, Clippy and copied-source checks pass.
Source-oracle parity was not rerun.

## Typed sizing assessments

Shared sizing now distinguishes three business outcomes: cash cannot cover fees,
risk budget cannot cover fees, and no approved lot fits the limits. Invalid
operands remain errors. Integer sizing and the existing quantity API retain their
previous behavior. Live and historical callers use the same calculation.

A versioned assessment stores the operands and result. Reading its outcome
recomputes the calculation and rejects a mismatched result or unknown version.
This record does not certify input provenance, freshness or execution authority.

The playback controller exposes a read-only assessment API. It uses its owned
quote, committed action and current portfolio snapshot. Successful sizing still
passes submission preflight. A cash rejection does not reserve funds, complete
an action, release existing funds or allow playback to advance. Durable rejection
publication, decision binding and recovery integration are still required before
the runner can dismiss an unfundable entry. Transient errors are not reclassified
by message text.

All 396 offline Rust tests, formatting, Clippy and copied-source checks pass.
Tests cover typed reasons, malformed inputs, forged assessment results, extreme
integer operands and shared-account cash exhaustion. Source-oracle parity was
not rerun. No service or network test ran.

## Automatic sized-action dispatch

Bounded dispatch now accepts declared-account sizing and cash policies directly.
New entries are sized immediately before funding. Funded entries retry the
controller-owned allocation instead of recalculating quantity. Both paths use the
same action queue, bracket checks, reservation authority and completion records.
Protection changes, exits and cancellations retain their existing dispatch path.

Missing entry policies produce per-action errors. Undeclared policy accounts fail
preflight. Successful actions are excluded from later batches. A failed funded
submission retains its reservation and allocation. No strategy decision is
silently dropped to let playback advance.

Lifecycle fixtures now drain work through automatic sized dispatch. They continue
through fills, settlement and checkpoint recovery. The capacity-failure fixture
also retries through this API and confirms unchanged allocation and reservation.
All 393 offline Rust tests, formatting, Clippy and copied-source checks pass.
Source-oracle parity was not rerun. No service or network test ran.

The runner still needs verified configuration/reference assembly, candidate
evidence assembly and its outer control loop. This is not a complete backtest CLI
or a live-trading release.

## Controller-owned allocation recovery

The controller retains the allocation when an entry is funded. Callers can read
that allocation for retry after a submission failure. The retained copy is bound
to the existing funded-request fingerprint; changed retries still fail.

Controller checkpoints are version 4. They include retained allocations beside
action progress. Recovery checks account/instrument scope, positive quantity,
price precision, tick alignment and deadline. Completed allocations must match
the actual submitted order, including quantity, price, tick and deadline.
Allocation presence must agree with funded-request progress. Earlier controller
checkpoint versions are rejected; no implicit migration is provided.

The lifecycle tests compare retained allocations before and after recovery. They
also reject a validly hashed checkpoint with a changed allocation quantity and
confirm that failed submissions expose the retained allocation. All 393 offline
Rust tests, formatting, Clippy and copied-source checks pass. Source-oracle parity
was not rerun. No service or network test ran.

Common-cut recovery still rejects reserved-but-unsubmitted portfolio funding.
This change does not weaken that check or complete the runner's failure-resolution
workflow. Runner, multi-instrument and runtime/UI integration remain unfinished.

## Sequential sizing and funded retries

The controller now offers a size-and-submit operation. Sizing runs immediately
before reservation, so sequential entries observe earlier account reservations.
Portfolio still arbitrates concurrent lane races at reservation time. This is
not an atomic snapshot-and-reserve transaction across threads.

A preparation error occurs before funding. Once sizing succeeds, the operation
returns the allocation together with the submission result. Callers retain that
allocation for exact retry if submission fails. They must not rerun sizing after
the reservation has reduced available cash. The existing funded-request pin and
bracket checks remain authoritative.

New offline fixtures cover two strategy instances sharing account cash while a
second account remains independent. The first strategy reserves cash, the second
is rejected without another reservation, and the other account can submit. A
separate simulator-capacity failure proves that the allocation remains available
for retry and the reservation is neither duplicated nor silently released.
The one-entry-per-decision strategy contract remains unchanged.

All 393 offline Rust tests, formatting, Clippy and copied-source checks pass.
Source-oracle parity was not rerun. No service or network test ran. The runner
now has controller-owned allocation recovery as described above. Failure-resolution
and complete runner wiring remain open.

## Quote-backed portfolio allocation

The controller now proposes entry/add allocations from its owned executable quote
and the committed strategy action. The limit price is the exact ask at the
instrument scale. Quote eligibility and age use the pinned playback policy.
Missing, stale, future, crossed or unrepresentable quotes are not replaced with
another price. Strategy price caps and complete bracket geometry still apply.

Quantity uses unreserved account cash, the account budget, order cash/risk limits,
fee reserve, lot size and an explicit quantity cap. The proposal does not reserve
cash. Submission revalidates and reserves atomically through Portfolio. A funded
action cannot be resized; its retained allocation must be used for retry.

The caller still supplies approved tick, lot, lifetime and cash policy inputs.
Reference/configuration assembly for the standalone runner is not yet complete.
The allocation API does not certify those inputs merely because they deserialize.

Multi-account lifecycle tests now generate their dispatch allocations through this
API. They check quote-derived prices, cash-limited quantity, lot rejection,
read-only preparation and refusal to resize funded actions. They continue through
fills, exits, settlement and recovery with unchanged expected balances. All 391
offline Rust tests, formatting, Clippy and copied-source checks pass. Source-oracle
parity was not rerun. No service or network test ran.

## Bounded funding reconciliation

The playback controller now reconciles terminal order funding in bounded batches.
Cancelled entries with no fills release their exact reservation. Fully closed
orders settle their journaled cash and costs through the existing portfolio
authority. Open positions and entries that can still fill retain their funding.

Reconciliation requires a dispatched boundary with all fills committed. Each
call returns per-order results and handles at most 4,096 orders in stable
submission order. Missing currency evidence or settlement errors retain funding.
Successful orders are excluded from subsequent batches, including after recovery.
This does not release reserved-but-unsubmitted plans or authorize broker activity.

The multi-account lifecycle fixtures now use this path for cancelled-entry
release and closed-order settlement. They verify missing evidence, fill-journal
gating, one-order batches, nonterminal exclusion, exact retry and unchanged final
cash. All 391 offline Rust tests and static checks pass. Source-oracle parity was
not rerun. No service or network test ran.

Market/evidence input assembly, allocation policy wiring, the complete CLI runner,
multi-instrument orchestration and runtime/UI integration remain unfinished.

## Bounded committed-action dispatch

The playback controller now dispatches all executable action types through one
bounded API. It reads retained committed decisions, not caller-supplied actions.
Entries/adds require explicit allocations and account cash policies. Protection
changes use the same session, LULD and risk checks as individual amendments.
Cancellations and reduce-only exits retain their existing scoped authorities.

Each call returns per-action outcomes and processes at most the requested number
of actions, with a hard limit of 4,096. Unknown allocation keys and undeclared
cash accounts fail preflight. Missing entry inputs fail that action. Failed work
remains pending and blocks boundary acknowledgment. Later actions in the same
decision are deferred; independent decisions in the batch can still succeed.
Successful work is not repeated by a later dispatch call.

Dispatch order is stable by decision ID and action index. Reservations are serial
in this single-instrument controller, avoiding scheduling-dependent cash races.
This is not a multi-instrument concurrency or latency-performance claim.

The multi-account lifecycle fixtures now use bounded dispatch before checking
individual-action idempotency. They cover one missing allocation while the other
account succeeds, exact retry, one-action batches, target replacement, exits,
cancellation and recovery. All 391 offline Rust tests, formatting, Clippy and
copied-source checks pass. Source-oracle parity was not rerun. No service ran.

The standalone runner still needs market/evidence input assembly, allocation
policy wiring and CLI integration. Bounded funding reconciliation is implemented
above and must be called at the appropriate runner boundaries.

## Controller-owned entry submission

The playback controller now derives entry/add bracket plans directly from its
retained committed strategy receipt. Callers supply portfolio allocations and
verified session, risk, band and cash inputs. They no longer need to supply a
second strategy decision or construct the executable plan themselves.

The path checks execution context, reserves account cash, then submits through
the existing simulator authority. Invalid latency and missing decision authority
are rejected before reservation. Successful retries do not reserve twice. Once
funded, the request fingerprint is pinned. A failed submission leaves the exact
reservation in place and blocks boundary acknowledgment. Changing the funded
request is rejected; no automatic release or resizing occurs.

Controller checkpoints are now version 3 and include reserved-request progress.
Earlier controller images are rejected; no migration is implemented. The existing
multi-account entry, target replacement, exit and cancellation fixtures use this
new path and still recover at each boundary. An additional failure fixture checks
retained funding and changed-retry rejection. All 391 offline Rust tests and
static checks pass. Source-oracle parity was not rerun. No service ran.

This closes one execution-wiring gap. A complete standalone backtest command,
multi-instrument orchestration and runtime/UI integration remain unfinished.

## Durable common-cut checkpoint adapter

The single-instrument common-cut bundle now has a binary archive format and a
ClickHouse publication adapter. Binary framing avoids JSON byte-array expansion.
The archive bounds object counts and payload bytes. It stores one-MiB chunks and
a small header that pins their order, archive hash, manifest and boundary cut.

Publication validates the complete recovered graph before writing. It requires
repository-extraction and durability acceptance plus cooperative lease ownership.
Every chunk is read back before the root is published. The publication slot binds
the manifest and boundary sequence. A different checkpoint at that slot is a
conflict, not a replacement. Loading verifies all hashes and performs the same
semantic, journal and portfolio checks before returning paused owners.

Schema 017 adds `backtest_checkpoint_chunks_v1` and `backtest_checkpoints_v1` on
`live_market_ssd`. It is unapplied. This is cooperative publication, not a
ClickHouse compare-and-swap transaction or proof of deployed database durability.

The existing two-account fixtures now archive, publish to an in-memory store,
read back, restore and continue. Tests reject missing or corrupt chunks, wrong
pins, incomplete publication, conflicting roots and lost ownership. Exact retry
after an ambiguous root write does not duplicate the stored graph. All 390 offline
Rust tests, formatting, Clippy and copied-source hash checks pass. Source-oracle
parity was not rerun. No database connection, migration or service test ran.

Multi-instrument recovery, service wiring and deployed acceptance remain open.

## Common-cut backtest recovery

The playback recovery bundle now pins controller, candidate-owner and portfolio
roots under one manifest and boundary cut. Capture takes exclusive references to
the three owners. The combined serialized graph is capped at 64 MiB. Component
capture consumes the remaining budget rather than receiving a fresh full budget.

Restore validates the trusted root, component pins, journal readbacks, effective
policies and funding reconciliation before returning any recovered owner. It
returns paused and does not advance input or publish orders. Missing, extra or
unowned reservations and settlement receipts fail closed. Reserved plans not yet
submitted must be resolved before this checkpoint can be captured.

This API currently supports one instrument across multiple accounts. It rejects
multi-instrument manifests explicitly. Multi-instrument coordination remains
unfinished. The adapter above now provides durable-publication code for this graph.

The two-account retry fixtures now restore all three owners before candidate
evaluation, compare recaptured root hashes and continue through journal retries
and later boundaries. Negative cases cover wrong root pins, corrupt portfolio
bytes, substituted candidate roots and unowned funding. Core tests also check
exact reserved and settled command populations. Offline validation: 389 Rust
tests, formatting, Clippy and copied-source hashes passed. Source-oracle parity
was not rerun. No service or network test ran.

## Candidate-owner checkpoint graph

The candidate owner now captures shared feature state and each declared strategy
transaction in one bounded object graph. The root pins the manifest and pending
boundary. Feature recovery also verifies the exact market state. Restore requires
matching effective policies and independent last-committed journal rows per scope.

Prepared decisions remain uncommitted. Restored pending and committed decisions
must agree with the controller boundary and receipt barrier. Missing journal rows,
changed scope/configuration and receipts beyond the recovery boundary fail closed.
The complete graph has a 64 MiB ceiling and at most 4,096 consumers.

The two-account retry tests now round-trip prepared and committed owners, compare
checkpoint hashes and continue playback using the restored instances. All 388
offline Rust tests, formatting, Clippy and copied-source hash checks pass.
Source-oracle parity was not rerun. No service or network test ran.

The common-cut bundle and adapter above now coordinate these component roots for
one instrument. Multi-instrument recovery and deployed acceptance remain open.

## Recovery with working targets

The execution lifecycle fixtures now restore the controller at every boundary and
continue from the restored instance. They cover pending entries, held positions,
target replacement, exits and unfilled cancellation. Recaptured checkpoint hashes
and reconciled candidate-position hashes must match before and after recovery.
Final cash, quantities and reservation checks still run after continuation.

A targeted corruption test changes a retained target's clock without reordering
the checkpoint fields. Recovery rejects the future timestamp. The shared portfolio
is checked against each restored execution lane; it is not recreated in this test.

All 388 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
Source-oracle parity was not rerun. No service or network test ran. This validates
execution recovery, not candidate-generated entries, whole-run candidate/portfolio
recovery, strategy profitability or live readiness.

## Owned target metadata

Successful committed entry and target-replacement actions now retain target
metadata, decision hash and action time in the playback controller. Entry targets
retain structural selection when present. Adds must match the retained target.
Failed execution actions do not update this state.

The candidate owner can now prepare from controller-owned position and target
evidence without a caller-supplied target. Controller checkpoint version 2 captures
the metadata and validates scope, clocks and prices against held and pending orders.
Older controller images are rejected. No persisted database data was migrated.

Lifecycle fixtures consume owned targets and capture them at each boundary.
Configured completed-bar playback uses the owned preparation path. All 388 offline
Rust tests, formatting, Clippy and copied-source hash checks pass. Source-oracle
parity was not rerun. No service or network test ran. Recovery testing with nonempty
target metadata is recorded above. Full candidate-driven lifecycle acceptance remains open.

## Candidate reconciliation bridge

The playback controller now builds the long candidate's position observation
from journaled strategy positions and owned orders. Quantity and remaining FIFO
cost come from the strategy projection. Pending entries and exits come from
execution state. Prices outside the supported exact-integer conversion range
are rejected. Flat state has no invented average, stop or target.

Target metadata remains an explicit strategy input. Its price must exactly match
the actual working target. Missing or mismatched target evidence blocks evaluation.
The candidate's scalar contract cannot represent heterogeneous held protection;
the bridge rejects that state rather than inventing a common stop or target.
This does not replace the execution controller's independent exit actions.

The candidate owner has a reconciled preparation path. Execution-owned safety
fields are populated from the bridge; external risk flags are retained. Modeled
observation revisions use playback boundary sequences, not fabricated broker
receipts. Use this path before executing the boundary's resulting actions.

Lifecycle fixtures check fill price, target replacement, flat cleanup and safety
field reconciliation. Configured completed-bar playback uses the bridge. All 388
offline Rust tests, formatting, Clippy and copied-source hash checks pass.
Source-oracle parity was not rerun. No service or network test ran. Persistent
target metadata and full candidate-driven entry/fill/exit acceptance remain open.

## Strategy attribution in execution and recovery

Journal-acknowledged fills now update both account and strategy FIFO projections.
The execution owner resolves strategy attribution from command ownership. Reads
remain blocked while any fill batch is pending. Exact retries reuse fill identity.
Only legacy raw-order unit fixtures bypass missing ownership; production rejects
it and has no raw submission API.

The playback controller exposes strategy positions only for exact manifest scopes.
Execution checkpoints are version 3 and include the attributed projections.
Recovery verifies their population, ownership, quantity, direction, cash and clocks
against the owned orders and cash ledger. Older execution images are rejected.
No database data was migrated. Archive byte limits still bound the whole graph.

Tests cover two strategies sharing an account across fill publication and recovery.
Existing entry/protection/exit fixtures also compare strategy and account evidence.
All 388 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
Source-oracle parity was not rerun. No service or network test ran.
Candidate position reconciliation and full-run integration remain unfinished.

## Strategy-owned FIFO projection

The core now provides a strategy-scoped wrapper around the existing FIFO
projection. Command ownership is an explicit input from execution authority.
The wrapper checks owner, account, instrument, origin and trading mode without
rewriting the fill. Strategy positions remain separate from account positions.

Recovery binds the projection to the full strategy scope, origin and parent
checkpoint context. A different strategy cannot restore the same image.
The test covers two strategies in one account, distinct cost bases, duplicate
fills, wrong ownership, recovery and a partial exit.

Execution-journal and parent checkpoint integration is recorded above.
Candidate reconciliation remains required before these projections can drive
the candidate runtime.
All 387 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
Source-oracle parity was not rerun. No service or network test ran.

## Exact per-order fill notionals

The order cash ledger now retains cumulative entry and exit notionals as exact
integer price atoms. Partial exits no longer leave consumers with only net cash
when they need average entry fill cost. This is cumulative entry cost, not the
FIFO cost basis of remaining lots. The account projection remains the FIFO owner.

Each order in the journal-gated account view exposes its cash evidence. The view
checks entry/exit quantities, price scale and clock against execution state.
Cash checkpoints are now version 2 and verify the signed cash equation and
notional bounds. Version 1 is rejected; it must be rebuilt from verified fills,
not upgraded by guessing missing costs. No persisted data was migrated.

Tests cover multiple entry prices, partial exits, both directions, recovery and
corrupted notionals. All 386 offline Rust tests, formatting, Clippy and copied-source
hash checks pass. Source-oracle parity was not rerun. No service or network test
ran. Candidate position reconciliation remains unfinished.

## Journal-gated execution account view

The playback controller now exposes a borrowed account view only at a dispatched
boundary after all fills are journaled. It includes the fill-derived net position
and individual orders with strategy ownership and active protection. It does not
invent a common stop, target or strategy allocation for the net account position.

The view checks aggregate order quantity and direction against the projection.
Unknown ownership, contradictory quantities and future projection clocks fail.
Accounts absent from the run manifest are rejected. The read clock is explicitly
playback evaluation time, not a broker or provider receipt timestamp.

Multi-account execution lifecycle fixtures consume this view during entry,
protection replacement, exit and cancellation. Pending or ambiguously acknowledged
fill journals block it. Mapping this evidence into candidate position state and
full candidate-driven lifecycle acceptance remain unfinished.
All 385 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
Source-oracle parity was not rerun. No service or network test ran.

## Offline policy preflight command

The executable now exposes:

```text
arte check-policies <manifest.json> <expected-manifest-sha256> <policies.json> [--json]
```

The expected hash is the canonical run-manifest hash, not a hash of JSON file
formatting. Inputs use explicit paths and bounded reads. The command validates
manifest identity and policy structure/consumer binding. It does not construct
market features, verify effective strategy hashes, or authorize trading.
Human output states those limits. Machine output is selected explicitly with
`--json`. Failures retain the CLI's nonzero exit behavior.

All 385 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
New in-process tests cover malformed inputs, invalid arguments and report fields.
Report lines are checked against an 80-column budget with no terminal escapes.
An actual terminal launch, valid-file command invocation and narrower-terminal
inspection were not performed. Source-oracle parity was not rerun. No service or
network test ran. Service startup wiring remains unfinished.

## Portable policy loading

Candidate policies now have a versioned JSON document. It names the pinned run
manifest and each account, instrument and strategy instance. Duplicate, missing
and foreign consumers are rejected. Nested settings reject unknown field names.
Required values have no deserialization defaults.

The playback owner accepts an explicit reader. Reads are bounded to 16 MiB plus
one overflow-detection byte. Documents accept at most 4,096 consumers. Larger
inputs fail; they are not truncated. No parent configuration, environment fallback
or filesystem discovery is used. Effective policy hashes are still checked
against the actual market features and quote policy before constructing the owner.

Two-account offline playback now starts from serialized policy input. Tests cover
unknown settings, duplicate/foreign accounts, mismatched manifests, truncated
JSON and an oversized reader. CLI and deployment startup wiring remain unfinished.
All 383 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
Source-oracle parity was not rerun. No service or network test ran.

## Configured boundary routing

The candidate owner now selects its evaluator from the controller's pending
boundary. Completed one-second bars use completed-bar evaluation. Eligible
trades use intrabar evaluation. Other boundaries use observation-only handling.
This entry point requires a bound policy before selecting any path.

The two-account retry tests now continue through completed-bar evaluation and
reach playback completion. Admission uses the causal feature snapshot's MACD
and activity evidence. The runtime still rejects contradictory evidence.
Account journals and action gates remain required before market acknowledgment.

This is a flat-position fixture. It does not prove configured strategy-driven
entry, fill and exit acceptance. That integration remains required.
All 383 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
Source-oracle parity was not rerun. No service or network test ran.

## Manifest-bound candidate policies

The playback candidate owner now accepts one owned policy per declared
account/strategy scope. Construction rejects missing scopes, extra scopes and
effective hashes that differ from the run manifest. Shared feature settings
must agree. Account-specific strategy settings may differ.

Configured completed-bar and intrabar methods use these bound policies. The
completed-bar method obtains recovery state from the owned candidate rather
than accepting a separate recovery snapshot from the caller. External admission,
session and swing evidence remain explicit inputs.

The two-account tests now use distinct manifest-bound policies. They exercise
intrabar preparation, journal publication and boundary acknowledgment after the
quote cancellation/retry path. These fixtures do not prove strategy-driven
entry, fill and exit acceptance. Deployment configuration loading and combined
owner recovery remain unfinished.

All 383 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
Source-oracle parity was not rerun. No service or network test ran.

## Candidate policy bundle and cancelled journal acknowledgment

The shared core now has an owned, serializable candidate policy bundle. It holds
feature, entry, add, protection, acquisition, recovery and position settings.
Fields have no implicit defaults. Its effective hash delegates to the existing
candidate configuration authority and requires matching feature and quote-policy
identities. A JSON file hash is not a substitute for that effective hash.
Algorithm-specific parameter admissibility remains in the existing evaluators.

The policy round-trip test reproduces the existing evaluator's effective hash.
Changed feature settings and unsupported schema versions are rejected. Deployment
loading still needs integration. Per-consumer binding is implemented above.

A virtual-time journal test now cancels the candidate owner's commit future after
the second account stores its row but before readback arrives. Retry verifies the
same row; the already committed account is not written again. Both account and
execution-action acknowledgment gates remain enforced. This proves in-process
retry behavior, not database crash durability or whole-run recovery.

All 383 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
Source-oracle parity was not rerun. No service or network test ran.

## Quote-boundary candidate decisions

The candidate owner now handles quote and other non-entry-price boundaries.
Previously, those boundaries required account receipts but had no candidate
preparation path. That could stop playback at its first quote.

The new observation path reconciles account state and uses the same exit-first
dispatcher as price evaluation. It does not create entries, calculate targets,
invent body-high prices or update price-derived peaks. Eligible trade boundaries
and completed one-second bars cannot use this path; their evaluators remain
required. Feature identity, sequence, availability and evaluation clocks must
match the observed boundary.

The owner test now prepares real candidate transactions for two accounts. One
waits; the other's flatten condition produces cancellation. An injected journal
failure leaves only the failed account eligible for retry. Market acknowledgment
remains blocked until both journals and the cancellation action finish. Full
candidate-driven entry/fill/exit and cancellation-of-await tests remain required.

All 382 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
Source-oracle parity was not rerun. No service or network test ran.

## Owned candidate playback coordination

The playback adapter now has an owner for shared features and the manifest's
account/strategy candidate runtimes. It initializes only against an unused,
matching controller. Completed-bar and intrabar preparation call the existing
shared candidate evaluator; no alternate strategy algorithm is introduced.
Effective-policy validation remains in that evaluator.

Journal publishers must match the complete scope-hash set. Every selected
decision is preflighted before I/O. Writes use bounded concurrency, up to 64.
Successful receipts stay in owned consumer slots across cancellation and retry.
Only receipt registration satisfies the controller's account barrier. Market
acknowledgment and execution actions remain separate controller responsibilities.

All 382 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
The new test covers consumer ownership, duplicate feature observation, late owner
initialization, missing prepared decisions, publisher mismatch and invalid
concurrency without journal I/O. Full candidate-driven entry/exit and cancellation
acceptance for this owner is still required. Its combined recovery/publication
and production orchestration are not implemented. Source-oracle parity was not
rerun. No services or network tests ran.

## Execution and portfolio consistency

The simulated execution lane now exposes a read-only funding consistency check.
Every owned, unreleased order must retain its exact portfolio reservation. A
released unfilled order must be cancelled and have neither reservation nor
settlement receipt. A released filled order must be terminal and have the exact
settlement receipt reconstructed from its journaled cash and pinned currency
evidence. The normal settlement path and this check share request construction.

The check does not repair balances, recreate reservations or settle orders. It
requires the shared portfolio to be quiescent. Other instrument lanes and funded
plans not yet submitted still require accounting by the run coordinator; this
lane check alone cannot prove that the portfolio has no orphan reservations.

Multi-account lifecycle tests now exercise the check before each market
acknowledgment, including filled exits and unfilled cancellations. Negative tests
cover missing or changed active reservations, missing settlement receipts and
missing settlement currency evidence.

All 381 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
Source-oracle parity was not rerun. No service or network test ran.

## Shared feature recovery

The shared feature authority now captures and restores MACD episode state, setup
range history, activity history and the published boundary snapshot. The image
pins the feature configuration, source scope, parent context and exact market
checkpoint hash. It cannot be paired with a different market calculation state.

Capture requires an observed pending boundary and rejects failed feature state.
Restore validates configuration through the normal constructor, preserves the
observed boundary identity and clocks, and checks one-second snapshot alignment.
It does not replay observations or infer freshness. Genesis remains the normal
empty constructor. Recovery images are bounded to at most 64 MiB.

The multi-timeframe continuation test now restores after every boundary and
compares checkpoint hashes against uninterrupted feature processing. It covers
gaps, completed-bar ordering, duplicate observation, configuration changes,
changed market hashes and byte limits. This is component recovery, not the
completed candidate/portfolio/controller run coordinator.

All 380 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
Source-oracle parity was not rerun. No service or network test ran.

## Candidate transaction recovery

Candidate runtime recovery now uses the shared strategy transaction codec. The
image preserves committed state, the last committed decision, and any prepared
decision with its uncommitted next state. Scope, effective configuration, state
budget and parent context are pinned. The image has a caller-selected byte limit
capped at 64 MiB.

Restore verifies the last committed decision against independently read journal
rows. Only then can it return a committed receipt for account-barrier recovery.
Prepared state stays pending and uses the normal journal acknowledgment path.
Dispatch is reconstructed through the existing safety arbitration and identity
rules. Retrying the last committed or prepared input does not rerun calculation.

All 380 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
Tests cover genesis, prepared and committed recovery, subsequent decisions,
committed retries, missing journal rows, account/configuration mismatches and
state-budget pins. Source-oracle parity was not rerun. No services or network
tests ran.

Shared feature-state recovery and its integration with candidate, controller and
portfolio images remain incomplete. These component tests do not establish
whole-run recovery or strategy robustness.

## Controller recovery

Controller restore now rebuilds the manifest-bound playback graph, simulated
execution and action ledger from one captured boundary. It checks total byte
limits, canonical encoding, component hashes, source scope, run identity, clocks,
cost binding and quote-age policy. Playback restores paused without replaying the
already dispatched quote.

Verified decision receipts must reconstruct the exact executable-action set.
Pending actions remain pending. Completed actions retain their request
fingerprints so identical retries do not repeat the operation. The expected root
hash must come from trusted durable publication, not from the supplied image.
Receipt verification does not independently certify a completed execution action;
that state is authenticated by the externally pinned controller root.

Continuation tests cover pending and completed cancellation recovery, checkpoint
equality, missing account receipts and altered decision or quote-policy fields.
Candidate, feature and portfolio coordination, durable root publication and
whole-run recovery remain incomplete. No live-order recovery is implemented by
this backtest controller.

All 378 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
Source-oracle parity was not rerun. No service or network test ran.

## Controller checkpoint capture

The adapter can now capture playback, execution and action progress at one
dispatched boundary. Capture requires committed fill journals. It rejects a cut
whose sequence, identity or clock differs from the pending playback boundary.
The root binds the run manifest, playback graph, execution graph, quote-age limit,
committed decision hashes and completed execution-request fingerprints.

The combined payload has a caller-selected limit capped at 64 MiB. Action progress
is bounded to 4096 consumers with 16 actions each. This path only captures state;
it does not publish a durable root or provide controller restore yet. Candidate,
feature and portfolio state still require coordinated capture and recovery.

All 378 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
The new test verifies pending and completed cancellation-action capture, component
identity binding, invalid cuts and byte limits. Source-oracle parity was not rerun.
No services or network tests ran.

## Composed account playback recovery

The manifest-bound account playback wrapper now composes scheduler, prepared-input
cursor and account-barrier recovery. Restore rechecks the run manifest, source
catalog, declared consumers, capacities and component hashes. Consumer scopes
come from the same shared constructor used for a new run.

A pending playback boundary must have a matching barrier. An idle cursor cannot
carry barrier receipts. Independent transaction receipts must reproduce the saved
barrier exactly. Nonterminal recovery remains paused. Restoring does not commit
strategy decisions, execute orders or acknowledge the market boundary.

The two-account continuation test restores after the first account commits and
again after both commit. Missing receipts block recovery. The second consumer
remains pending after partial recovery. After acknowledgment, the recovered run
continues to the same final checkpoint hash as uninterrupted playback.

The adapter still needs to compose candidate state, feature state, portfolio,
execution and these playback components under one durable run boundary. The
whole-run recovery and production coordinator are not complete.

All 377 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
Source-oracle parity was not rerun. No service or network test ran.

## Account journal barrier recovery

The account barrier now has a bounded recovery image. It pins the parent context,
complete market-input identity and exact consumer set. It stores only scope and
decision hashes, not copied market arrays or decision payloads.

Restore requires independently verified transaction receipts. Those receipts must
reproduce the saved consumer ledger exactly. A saved hash cannot stand in for a
journal acknowledgment. Missing, duplicate, extra or changed receipts fail closed.
Consumers without receipts still require decisions. A fully committed barrier
still requires market acknowledgment. An already acknowledged barrier cannot
produce an operational image.

Tests cover partial and fully committed recovery, continuation after recovery,
changed scopes and clocks, context mismatches, corrupt and noncanonical images,
and insufficient byte budgets. Images are capped at 1 MiB. The playback account
wrapper and production coordinator still need to compose this component with
their other recovery state; this is not whole-run recovery.

All 376 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
Source-oracle parity was not rerun. No service or network test ran.

## Playback cursor recovery

Playback recovery now combines the scheduler graph with its prepared-input hash,
frame position, admission and duplicate counts, acknowledgment count and poll
budget. Prepared event data remains externally pinned and is not copied into the
checkpoint. The combined image is limited to 64 MiB, including a 4 KiB cursor
reservation.

Restore validates the source identity, cursor bounds, counters, pending boundary
clock and final watermark. Nonterminal playback always restores paused. A pending
boundary stays available for its required journal work; recovery does not
acknowledge it. Completed playback remains terminal. Failed playback cannot
produce an operational checkpoint.

The offline continuation test restores after every poll and compares boundary
identities and checkpoint hashes with uninterrupted playback. It also rejects
changed source hashes, frame positions and counters. This does not yet restore
the account fan-out barrier or the adapter's strategy and execution controller.
Whole-run recovery remains incomplete.

All 374 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
Source-oracle parity was not rerun. No services or network tests ran.

## Scheduler recovery

The scheduler now saves one recovery graph for market calculations, both event
queues, the quote book, quote identities, trade eligibility, completed-bar queues
and the pending consumer boundary. Applied events awaiting acknowledgment remain
in their queues. Restore verifies their payloads against the calculation or quote
ledger instead of applying them again.

Recovery pins the run, parent context, calculation configuration, historical seed,
quote policy and queue capacity. It rejects changed component hashes, duplicate
identities, invalid clocks and changed pending-boundary identities. The combined
image has a caller-selected byte limit, capped at 64 MiB.

All 373 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
The new mixed-stream test restores at every boundary, including unacknowledged
trades, quotes and completed bars across multiple timeframes. It compares boundary
identities and recovery images with uninterrupted execution. Source-oracle parity
was not rerun. No service or network test ran.

This completes the scheduler component, not coordinated whole-run recovery.
Restoration does not acknowledge the decision journal or authorize trading.
The playback cursor, strategy, portfolio and execution recovery still need a
shared durable recovery boundary in the production coordinator.

## Partial-release ordering recovery

The shared event-ordering buffer now checkpoints its exact pending observations,
capacity and watermark. Restore reconstructs the already-admitted queue before
installing the saved watermark. This preserves a partially processed batch without
treating its remaining events as newly arriving late data.

Normal admission is unchanged. New events behind the watermark still fail closed.
Pending duplicates retain their original availability timestamps. Recovery rejects
duplicate or reordered image rows, changed context/capacity, corrupt bytes and
faulted buffers. Encoding and decoding enforce a caller budget capped at 64 MiB.

All 371 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
The new interrupted-release test compares the remaining event sequence after
restore with uninterrupted processing, including the half-open watermark boundary.
Source-oracle parity was not rerun. No service or network test ran.

This is a buffer component, not scheduler recovery. It does not decide whether a
consumer already applied the pending head. The scheduler must bind that fact to
its calculation state, pending decision and journal acknowledgment when composing
the recovery graph. The existing ordered-market recovery path is unchanged.

## Quote-book recovery

The shared quote book now has a bounded, versioned recovery image. It binds the
parent context, source scope and quote-eligibility policy. It preserves the raw
latest observation, including receipt, availability, SIP and participant clocks.
Restore does not refresh timestamps or grant permission to trade.

Unusable observations remain visible. A crossed quote is not replaced by an older
valid quote during recovery. Duplicate observations still cannot refresh age.
Faulted or policy-unbound books cannot produce an operational checkpoint. Images
have a 1 MiB ceiling and must also fit the caller's byte budget.

All 369 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
New tests cover timestamp preservation, duplicate replay, stale/crossed quotes,
empty books, changed scope/policy/context, noncanonical images and faulted capture.
Source-oracle parity was not rerun. No service or network test ran.

Scheduler recovery is not implemented by this codec. Its quote identity ledger,
pending buffers, completed-bar queue and pending decision boundary still need
coordinated recovery. The existing ordered-trade restore assumes queued events
are not behind its watermark. A scheduler paused mid-frame can retain releasable
events behind that watermark, so its recovery cannot reuse that assumption
without preserving the partial-release state explicitly.

## ClickHouse execution checkpoint publication

Execution checkpoints now have a chunked archive and a ClickHouse publisher and
loader. The archive uses explicit byte lengths and ordered objects. It preserves
payload bytes without embedding them in JSON arrays. Database chunks are 1 MiB;
the total payload remains bounded at 64 MiB, plus bounded framing overhead.

The publication slot binds run manifest, instrument and boundary sequence. A
different cut at the same slot is rejected. Every chunk requires exact readback
before the header is published. The publisher then reloads and validates the
complete execution runtime. It requires extraction and durability acceptance plus
cooperative lease ownership. Storage policy and active part placement use the
existing verifier. This is not a distributed transaction or whole-run fence.

Schema 016 defines the chunk and publication tables on `live_market_ssd`. It is
unapplied. All 367 offline Rust tests, formatting, Clippy and copied-source hash
checks pass. Tests cover multi-chunk binary round-trip, missing and corrupt chunks,
budget enforcement, incomplete writes, ambiguous retries, ownership loss and a
real execution graph's publish/load/restore path through an in-memory store.
No ClickHouse call or service ran. Source-oracle parity was not rerun.

Database durability still needs connected acceptance after repository extraction.
Whole-run recovery must coordinate execution with portfolio settlement receipts,
strategy state, market/input cursors and pending actions before resuming a run.

## Manifest-bound simulated fill policy

Playback now requires an explicit fill policy. Its hash binds the algorithm,
displayed-size participation, fixed submission delay and maximum quote age.
Startup checks the manifest hash and the simulator's actual participation value.
Binding must precede any order or consumed quote. Funded submission rejects a
different delay; quote processing rejects a different age limit.

Combined execution checkpoints now use version 2 and include this policy.
Restore verifies it against the pinned run before returning the execution lane.
Version 1 images are rejected, not upgraded with invented settings.

All 364 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
Tests cover mismatched policies, changed participation, latency and quote age,
binding after order creation, mutation-free rejection and checkpoint versioning.
The existing funded lifecycle and partial-exit recovery fixtures still pass.
Source-oracle parity was not rerun. No service or network test ran.

The supported policy is a hypothetical quote-touch model with a fixed entry
submission delay. It does not estimate real broker latency or queue priority.
Amendments retain the algorithm's immediate boundary-acknowledgment semantics.
The publication adapter is implemented above. Connected durability acceptance
and whole-run recovery remain unfinished.

## Combined simulated execution recovery

The historical execution lane now captures one content-addressed graph for:

- Simulator orders, protection state and modeled clock.
- Fill-derived positions, FIFO lots and duplicate-fill receipts.
- Strategy ownership and original reservation amounts.
- Per-order cash, cost-model binding and last journaled fills.
- Terminal release markers and the last consumed source quote.

Capture requires no pending fill publication. Restore checks the pinned run and
cut, exact object set, content hashes and canonical root. It checks ownership
against the run's consumers. It reconciles order quantities with per-order cash
and aggregate positions. Source quote prices, sizes and identity must match the
simulator's last quote. Caller budgets bound orders, pending fills and total image
bytes; the graph has a 64 MiB hard ceiling.

All 361 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
New tests restore two accounts before and during partial exits, then compare
continued cash and projection state against uninterrupted execution. They cover
ambiguous journal acknowledgments, duplicate quote replay, release markers,
missing or surplus objects, wrong cuts, inconsistent source identity and budgets.
These fixtures seed orders directly. They do not prove strategy acceptance or
portfolio settlement durability. Source-oracle parity was not rerun.

No graph has been published to ClickHouse during validation. Whole-run recovery must coordinate
it with portfolio receipts, strategy state, input cursors and pending actions.
The fill-policy manifest binding identified during this stage is implemented in
the version 2 checkpoint described above.
No service or network test ran.

## Order cash component recovery

Per-order simulated cash now has a versioned checkpoint codec. Restore requires
the expected object hash, pinned run cost model and exact last durable fill.
It checks scope, sequence, observation time, fill hash, quantities, direction and
fee bounds. The public cash type cannot be deserialized without these checks.
Images are capped at 16 KiB. Noncanonical encoding and unknown fields fail closed.

All 358 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
Long and short partial exits continue identically after restore. Retrying the last
fill does not charge its fee twice. Tests also reject changed fills, changed cost
models, corrupted bytes and inconsistent quantities. Source parity was not rerun.

This does not establish journal durability or whole-run recovery. The coordinator
must obtain the last fill from the committed journal and pin both components in
its recovery manifest. Execution ownership, reservations and release markers still
need coordinated adapter recovery. No service or network test ran.

## Fill-position component recovery

The fill-derived position projection now has a bounded, content-addressed
checkpoint. It preserves FIFO lots, gross P&L, trade cash, causal cursors and
duplicate-fill receipts. Restore requires externally pinned content and context
hashes. It checks lot totals, signed cash accounting, position geometry, capacity
and canonical serialization. Invalid images fail before a projection is returned.

All 356 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
The new tests compare uninterrupted and restored long/short partial-exit paths.
They also check duplicate replay, changed context, byte budgets, altered accounting
and noncanonical payload rejection. Source-oracle parity was not rerun.

This is a component codec, not coordinated execution recovery. The adapter still
needs one checkpoint covering simulator orders, owners, reservations, costs,
cash and release markers. The run coordinator must bind its market and strategy
state to the same cut. No service, database writer or network test ran.

## ClickHouse portfolio checkpoint publication

Portfolio checkpoints now have a chunked content-addressed storage format and a
ClickHouse adapter. Every chunk must pass exact readback before the root is
published. The publication slot binds the run manifest and boundary sequence;
changed data for the same slot is rejected. Restore checks the requested cut,
checkpoint hash, complete chunk set, chunk lengths and content hashes.

Byte chunks use strict hexadecimal encoding in ClickHouse. This preserves Unicode
even when a chunk boundary splits a UTF-8 character. The chunk size is 1 MiB; the
checkpoint payload is bounded at 64 MiB and remains subject to smaller caller
budgets. Cooperative lease ownership, extraction and durability acceptance are
required. Storage policy and active part placement use the existing verifier.

Schema 015 defines the two tables on `live_market_ssd`. It has not been applied.
All 354 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
Tests cover a multi-chunk Unicode image, missing/corrupt/surplus chunks, root-last
publication, failed writes, ambiguous root acknowledgments and ownership loss.

No database call or service ran. Database durability and whole-run recovery remain
unverified. The coordinator must still publish matching market, strategy and
execution state before accepting this portfolio checkpoint as a recoverable run.
Source parity was not rerun.

## Portfolio recovery image

A versioned portfolio checkpoint now includes balances, mandates, pending
reservations and settlement receipt hashes. It binds to the exact run manifest
and an explicit replay boundary. Capture acquires account locks in stable order
and writes through a byte-limited encoder. Account and row budgets are explicit.

Restore requires the expected content hash, canonical encoding, exact declared
account population and consistent reservation/settlement identities. It rejects
broker-owned accounts, undeclared instruments, future balance timestamps and
reservations whose commands are already settled. An ordinary account snapshot
remains insufficient for recovery.

All 350 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
Restoration tests preserve pending funding and prove that settled cash cannot be
applied twice. Corrupt bytes, wrong boundaries, noncanonical data, invalid account
populations and byte-budget overruns are rejected.

This is a portfolio-only codec, not durable or coordinated run recovery.
ClickHouse publication and matching market, strategy and execution checkpoints
remain unfinished. No services ran; source parity was not rerun.

## Atomic simulated portfolio settlement

Accounts now declare currency, currency precision and an optional simulation run.
Simulated submissions reject broker-owned accounts and mismatched run/currency
context. Closed-order settlement requires terminal simulator state, matching
journaled quantities, the pinned cost model and point-in-time currency evidence
referencing the run's reference manifest.

Under one account lock, settlement applies modeled net cash and removes the exact
original reservation. Bounded receipt hashes prevent duplicate application and
reject changed requests. Settled command IDs cannot reserve funds again. Neither
the capital mandate nor the balance timestamp is changed by settlement. Overflow,
negative account cash, missing funding and mismatched currency fail before mutation.

All 348 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
The funded two-account playback scenarios now settle losses/profits after fees
and verify released reservations. Core tests exercise concurrent exact retries,
changed requests, currency/run mismatch, capacity and rejection of broker balances.

This is in-memory simulated settlement, not exchange settlement. Durable receipt
publication, restart recovery and reference-producer currency certification remain
unfinished. Account snapshots alone do not contain settlement receipts and must
not be used as a complete recovery image. No services ran; source parity was not
rerun.

## Journaled per-order cash

The simulated execution lane now tracks trade cash, entry/exit quantities and fees
per command from the same journaled fills as the position projection. A bounded
batch is preflighted before publication. Accounting advances only after journal
readback and position projection succeed. Cash replaces the separate fee-only
accumulator; there is one accounting state per order.

The projection rejects missing entry history, direction changes, over-exits,
changed retries and command/scope mismatches. Net cash is available only when the
order's journaled entry and exit quantities match and no fill batch is pending.
It uses actual fill prices and the pinned cost model. Gross P&L remains separate.

All 347 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
Core tests cover partial fills, shorts, exact retries and rejected mutations.
Funded playback scenarios check net cash after explicit exits and replacement
target fills. Unfilled cancellations have no fabricated cash projection.

This is accounting evidence, not account settlement. Currency certification,
atomic portfolio settlement, filled-order reservation release and durable recovery
remain unfinished. No services ran; source parity was not rerun.

## Manifest-bound simulated costs

Playback now requires a cost model bound to the exact run manifest. The initial
model uses explicit fixed-per-fill, per-share and minimum-per-fill parameters.
It has no implicit zero-fee default and makes no claim to reproduce broker fees.
Integer arithmetic checks overflow and rounds charges conservatively.

The execution lane preflights charges for each bounded fill publication. Fee
totals advance only after journal readback and position projection succeed.
Failed or repeated publications cannot duplicate charges. Unbound lower-level
execution adapters report cost accounting as unavailable, not zero.

All 345 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
Tests cover exact fees, partial-fill minimums, currency rounding, overflow, wrong
run/consumer origins, manifest mismatch and journal-retry idempotency. Both funded
lifecycle scenarios verify final fees; unfilled cancellations produce no charges.

Filled-order cash settlement, currency certification and recovery of accounting
state remain unfinished. Model publication to ClickHouse is also pending. No
services ran; source parity was not rerun.

## Unfilled-order reservation cleanup

Simulation retains each accepted order's exact original cash reservation. A
cancelled or expired entry can release it only when the order has never filled
and no fill publication is pending. Unknown ownership, missing funding and a
changed reservation fail closed. Account-locked comparison prevents removing a
different reservation. Successful release retries are no-ops. Released commands
cannot be resubmitted to the simulation runtime.

The playback controller exposes this transition at a ready decision boundary.
An offline two-account scenario submits funded entries, cancels before the first
fill and releases both reservations. Later quotes create no fills; account cash
is unchanged. Existing filled lifecycle scenarios verify that this cleanup path
cannot release their funding, even after positions become flat.

All 342 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
Filled-order cash settlement and cost-model accounting remain unfinished; their
reservations deliberately remain held. Reservation ownership and release markers
also require durable recovery. No services ran; source parity was not rerun.

## Funded playback lifecycle integration

Two offline integration scenarios now traverse the public playback controller.
Orders are not preloaded. Each account commits its entry decision, derives a
bracket, reserves cash and submits through the protected execution path. Accounts
have different cash budgets and submit different quantities for the same ticker.

Later quotes create fills. An injected ambiguous journal write blocks decisions
until exact retry succeeds. Journaled positions supply the next decision's
quantity. Target replacements and explicit exits are dispatched twice to check
controller retry idempotency. Both scenarios reach playback completion with flat
positions, four distinct fill records and the expected gross realized P&L.

The target scenario holds positions through a quote above the original target
but below its replacement. A later quote reaches the replacement and closes the
positions. This checks that replacement affects fills, not only action status.

All 340 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
These fixtures use synthetic committed strategy proposals. They do not prove
the candidate's entry logic, profitability, latency or historical/live parity.
Reservation release, durable recovery, candidate-driven lifecycle integration
and connected acceptance remain unfinished. No services ran; source parity was
not rerun.

## Strategy-scoped protection dispatch

Playback now dispatches committed stop and target replacements. The retained
decision supplies the proposal and expected position quantity. Prices convert to
integer atoms without rounding. Future proposals and unrepresentable prices fail.
The selected strategy must own exactly the expected open exposure.

Each replacement preserves the other active protection leg. Shared session and
LULD checks run before mutation. The simulator preflights the entire batch,
including revisions and geometry, before changing any order. Triggered stops,
positions already exiting and incompatible remaining entries block replacement.
The single-order amendment path uses the same geometry checks.

All 338 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
Coverage includes atomic invalid-batch rejection, duplicate commands, strategy
isolation, price precision, future proposals, missing regular-hours bands and
the controller's stale-exposure gate. No services ran. Source parity was not rerun.

Successful protection dispatch followed by fill feedback still needs a complete
controller integration test. Broker acknowledgment, recovery and portfolio
reservation release remain separate unfinished work. These modeled amendments
do not prove live broker protection or latency behavior.

## Strategy-scoped reduce-only exit dispatch

Playback can now dispatch a committed reduce-only exit to its owning strategy's
simulated positions. The requested quantity must equal the selected exposure
that is not already pending exit. Quantity mismatches, missing commands, unknown
ownership and invalid clocks fail before any order changes. Other strategies,
including those in the same account, remain untouched.

An accepted exit cancels remaining entries. It creates no immediate fill.
Subsequent quotes supply the modeled liquidity, and fills cannot exceed held
exposure. The controller retains unsuccessful actions and blocks acknowledgment
of the market boundary. Successful action retries use the retained request hash.

All 336 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
New tests cover long and short exits, partial liquidity, atomic quantity mismatch
rejection, strategy ownership and the controller's stale-exposure rejection gate.
The successful controller-to-fill exit lifecycle is not yet tested end to end.

This path exits the selected strategy exposure in full. Arbitrary partial-close
allocation, protection-action dispatch, ownership recovery and reservation release
remain unfinished. No services or network calls ran. Source parity was not rerun.

## Strategy-scoped cancellation dispatch

Successful reserved submissions now retain the complete owning strategy scope.
An existing command cannot be silently adopted when its ownership is unknown or
different. Cancellation selects only that exact scope's remaining entry orders;
other strategies in the same account are excluded. Unknown ownership blocks the
batch before any order changes.

The simulator validates every selected command and revision before applying the
cancellation batch. Lookup is indexed rather than repeatedly scanning all orders.
Already-cancelled entries are idempotent. Filled positions and protection remain
intact. The playback controller resolves a committed cancellation action only
after that batch succeeds, including the valid no-pending-entry case.

All 332 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
Tests cover atomic preflight, retries, same-account strategy isolation, unknown
ownership and release of a committed cancellation's playback gate.

Ownership restoration, reservation release, exits and protection-action dispatch
remain unfinished. No services or network calls ran. Source-oracle parity was not
rerun.

## Explicit simulation clock

The simulator now owns a monotonic modeled clock independently of its last quote.
Playback advances it for every released boundary. This lets a future cancellation
or amendment handler act on trade/bar boundaries without manufacturing a quote.
Advancing the clock alone creates no liquidity or fills.

Submissions and new quotes cannot precede that clock. Amendment acknowledgment
must match it. Checkpoints persist the clock and reject a last quote later than
the stored clock. The execution model is now quote-touch-shared-size-v4; old
snapshots are not silently migrated.

All 330 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
New tests cover cancellation before the first quote, between quotes, rejected
rewinds and clock checkpoint restoration. Cancellation dispatch still requires
strategy/order ownership tracking. No services or network calls ran. Source-oracle
parity was not rerun.

## Committed execution-action tracking

The playback controller now retains every executable action from each committed
decision. Wait/Hold need no execution work. Entry, add, cancellation, exit and
protection changes block cursor acknowledgment until handled. Duplicate receipt
registration does not create duplicate work. Preflight limits each decision to
16 actions; the existing 4096-consumer bound limits retained action count.

Entry/add submission re-derives the complete bracket from the retained decision
and allocation, compares it with the supplied plan, then calls the existing
funding/session/risk-checked simulation path. Submission time must match the
modeled boundary clock. Successful exact retries do not submit again.

All 328 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
The new integration check proves that committed account receipts alone cannot
advance a boundary with an unresolved cancellation. The new entry/add wrapper
still needs a full successful candidate-to-submission acceptance test.

Cancellation, exit and protection dispatch remain unfinished and block rather
than disappear. Action state is not yet restart-persisted. No services or network
calls ran. Source-oracle parity was not rerun.

## Unified playback decision/fill gate

A new in-process controller owns account playback and its simulated execution
lane. It dispatches each released quote once. Decision access and cursor
acknowledgment stay blocked until pending fills are journaled and projected.
Account decision receipts then pass through the existing concurrent journal writer.
The controller exposes no mutable run or execution escape hatch.

The integrated offline test now verifies both gates: a failed fill write blocks
decision access; committed fills alone do not permit cursor advancement; both
account receipts finally allow acknowledgment. The resulting position has one
share. A test lookup was corrected to use the existing position-origin hash domain.

All 326 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
No services or network calls ran. Source-oracle parity was not rerun. This
controller still needs decision-to-order dispatch, submission/amendment lifecycle,
complete position feedback and durable recovery. It is not a complete backtest.

## Playback quote-to-simulation dispatch

The execution adapter can now consume a released quote directly from account
playback. It checks run identity, requires a pending quote boundary and verifies
that the owned quote book matches that observation. Boundary sequence and modeled
evaluation time become the simulation coordinates; raw timestamps are unchanged.

An offline test preloads a bracket, releases a playback quote, obtains one modeled
fill and retries a failed fill-journal publication. Reusing the same boundary does
not create a second fill. Missing boundaries and foreign run IDs are rejected.
The fixture's preloaded order bypasses production submission gates only in the
test; this is not full candidate entry-to-fill acceptance.

All 326 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
No services or network calls ran. Source-oracle parity was not rerun. A unified
controller still needs to gate market acknowledgment on both account decisions and
fill publication, then feed reconciled positions back into strategy evaluation.

## Merged quote and trade playback boundaries

The shared scheduler now accepts quotes with a bounded queue and a session identity
cache. Quotes and trades are selected by SIP time, provider sequence, then event
key. This is an explicit deterministic ordering rule, not proof of cross-channel
arrival order. Completed bars precede events at their closing timestamp. Boundary
identities now use causal-market-boundary-v3.

Only the currently released quote updates the scheduler's quote book. Future
queued quotes are not visible. Quote boundaries require acknowledgment just like
trade/bar boundaries. Repeated source identities are coalesced; changed identities
fail closed. Quote cache capacity uses maximum_market_events and its queue uses
maximum_pending, separately from the trade budgets. Capacity does not evict data.

Prepared playback accepts quotes only with trade eligibility false. Playback's
candidate methods now use the scheduler-owned quote book, not a caller-supplied
snapshot. Quote eligibility policy must be bound before executable quote use.

All 325 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
Tests verify interleaving, bar-close order, hidden future quotes, acknowledgment,
duplicate/conflicting quotes and quote-only playback. One initial test assertion
was corrected to respect the fixture's one-nanosecond timestamp offset.

Scheduler quote checkpoint recovery, live-lane cutover to this shared quote owner,
quote-to-fill dispatch and full candidate acceptance remain unfinished. No services
or network calls ran. Source-oracle parity was not rerun.

## Boundary-derived intrabar preparation

The shared feature authority now builds intrabar acquisition observations from
the exact pending eligible trade. Price, VWAP, MACD and candle body-high come from
the shared market state. Ask and quote-policy identity come from the executable
quote book. Missing quotes, mismatched feature boundaries and trades outside the
developing candle are rejected. Admission/account gates remain explicit inputs.

The playback controller can pass these observations into the existing candidate
intrabar evaluator after validating the declared consumer scope. No independent
historical strategy algorithm was introduced.

The multi-account playback test covers operand derivation, quote-policy binding,
missing quotes and changed boundary rejection. All 323 offline Rust tests,
formatting, Clippy and copied-source hash checks pass. Full candidate entry-to-fill
acceptance, merged quote scheduling and fill feedback remain unfinished. No
services or network calls ran. Source-oracle parity was not rerun.

## Playback candidate preparation

The account playback controller can now advance the shared candidate feature state
and prepare completed-candle decisions through the same evaluator used by live.
It checks the candidate's immutable scope against the declared consumer set before
preparation, then validates the resulting decision against the current boundary.
Quotes, admission, position state and policies remain explicit authority inputs.

The existing multi-account playback test now uses the required five-second MACD
timeframe and verifies that features advance once per boundary. Repeated feature
observation while journals are pending is idempotent. All 323 offline Rust tests,
formatting, Clippy and copied-source hash checks pass.

The new completed-decision wrapper compiles but does not yet have a full candidate
entry-to-fill playback acceptance test. Intrabar orchestration, quote scheduling,
fill feedback and runtime command wiring remain unfinished. No services or network
calls ran. Source-oracle parity was not rerun.

## Concurrent playback journal integration

The concurrent account journal writer now accepts the manifest-bound playback
controller directly. Preflight and committed-receipt registration use that same
controller. Its private barrier cannot be replaced or reduced by the writer.
Existing live/general barrier consumers keep the same commit implementation.

An offline integration test constructs a seeded market scheduler, pinned source
catalog and two-account playback run. One simulated journal write fails. The
boundary stays pending; retry writes only the failed account, then allows one
cursor acknowledgment. This tests journal coordination, not candidate performance
or broker/fill completion. Full strategy-backtest orchestration remains unfinished.

All 323 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
No services or network calls ran. Source-oracle parity was not rerun.

## Recorded-live receipt requirements

Recorded-live playback now requires a receive receipt for every input event.
Capture sequences must be positive. Within each capture run/lane, sequences and
monotonic receive times cannot regress. Identical repeated observations are
allowed; a changed observation cannot reuse the same capture sequence. Sequence
gaps from ticker filtering and shared frame receive times remain valid.

The check retains at most 256 capture-lane cursors and does not rewrite timestamps.
It rejects missing evidence rather than deriving a receive timestamp from SIP.
The underlying capture authority still requires independent certification; receipt
fields alone do not prove authenticity. Full late-event fault replay and captured
evaluation-clock verification remain unfinished.

All 322 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
No services or network calls ran. Source-oracle parity was not rerun.

## Prepared playback source binding

Account-aware playback now requires a source catalog whose hash matches the run
manifest. The catalog pins the underlying source authority, clock kind and sorted
provider/instrument/session shards. Each shard pins prepared event content and its
explicit clock model. A different source catalog, clock, shard or prepared payload
is rejected before playback construction.

This defines the playback meaning of the run's source_manifest_hash: it references
the prepared-source catalog, which in turn references the acquisition authority.
Live source pins retain their configuration meaning. Catalog persistence, source
coverage certification and verification of recorded-live clock evidence remain
unfinished. Hash binding does not replace those checks.

All 321 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
No services or network calls ran. Source-oracle parity was not rerun.

## Manifest-bound multi-account playback

An account-aware playback controller now derives every consumer for its instrument
from the pinned run manifest. It rejects non-Backtest mode, a different scheduler
run ID, missing consumers and insufficient consumer capacity. Callers cannot pass
a reduced account list or access mutable playback through this controller.

Each market boundary creates the shared account journal barrier. Partial receipt
completion retains that boundary. Duplicate receipts are idempotent. Undeclared
consumer receipts are rejected. The cursor advances only after all declared
consumers have committed; pause, step and resume remain available.

This is journal coordination, not execution completion. The controller does not
yet verify prepared data against the manifest's source certificate or execute the
candidate and quote-driven simulator itself. Full backtest orchestration and
durable restart remain unfinished. No service or network call was started.

All 320 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
Source-oracle parity was not rerun.

## ClickHouse run manifest publication

The ClickHouse adapter now publishes and loads chunked run manifests. It reads
back every chunk before publishing the root. Exact retries reuse existing rows.
A stable run-ID slot rejects replacement by a different manifest. Readers require
the expected manifest hash and reject missing or corrupted chunks.

Publication requires extraction and durability acceptance plus a cooperative
single-host lease keyed by run ID. This is not a distributed fencing mechanism.
Both tables use the existing storage-policy and active-part placement verifier.
Schema 014 is authored but unapplied. No database calls or services ran.

Offline tests cover child-write failure, root-last publication, exact retry,
conflicting run reuse, lost root acknowledgment, missing/corrupt children and
missing ownership. Referenced-content certification, power-loss durability and
full restart orchestration remain separate, unfinished obligations.

All 318 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
Source-oracle parity was not rerun. These checks do not establish connected
ClickHouse durability or complete live/backtest run recovery.

## Bounded run manifest storage codec

Run manifests can now be encoded as content-addressed chunks with an ordered root.
Chunks are limited to 1 MiB. The complete payload is limited to 64 MiB; the root is
limited to 16 KiB. Identical chunks are stored once. The full supported limit of
100,000 consumers, including maximum-length names, fits and round-trips in tests.

Hydration requires the expected run ID and manifest hash. It rejects missing or
surplus chunks, corrupted content, wrong chunk sizes and noncanonical JSON. The
codec reuses the existing immutable object hash contract. It does not establish
database durability or verify the content referenced by the manifest.

All 315 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
No services or database calls ran. Source-oracle parity was not rerun. The next
storage step is a ClickHouse adapter that verifies chunks before publishing the
root, with exclusive ownership keyed by run ID rather than manifest content.
Database publication and restart orchestration remain unfinished.

## Aggregate run manifest contract

A shared run manifest now pins code release, source/reference/seed/algorithm
manifests, dependency plan, hardware profile, clock model and execution model.
Per-consumer records contain account, instrument, strategy instance and effective
configuration hash. Run ID, mode and code identity are stored once and used to
derive the existing strategy Scope contract.

Backtest manifests require simulated execution and a historical, recorded-live or
explicitly pinned fault-simulation clock. Live and Paper require a live clock and
broker session-scope pin. These are contract checks, not credential isolation or
broker acceptance evidence. A live source pin identifies configuration; it does not
pretend that future streaming events are already frozen.

Consumers must be sorted and unique. The immutable pinned handle validates once
and derives scopes by binary search. Candidate runtimes can be constructed from
declared manifest consumers. Hash identity alone does not certify referenced data.

All 312 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
Tests cover scope derivation, round trips, identity changes, undeclared consumers,
missing pins, duplicate consumers and mode/clock/execution conflicts. No services
or database calls ran. Source-oracle parity was not rerun. ClickHouse manifest
publication, referenced-content verification and restart orchestration remain
unfinished.

## Run-level quote-policy pinning

The candidate's effective configuration now includes the quote eligibility policy
hash under candidate-configuration-v3. Both completed-candle and intrabar paths
recompute that configuration against the run's pinned scope before journal work.
A different valid policy hash is rejected, not merely logged as changed evidence.
The live market lane derives its configuration hash from its bound quote book.

Existing integration tests verify that policy changes alter configuration identity
and are rejected by both evaluation paths without preparing a journal batch.
All 310 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
No services or database calls ran. Source-oracle parity was not rerun.

This is a configuration-contract version change; older configuration hashes must
not be silently reused. Complete persisted run manifests, migration/restart handling
and the full runtime coordinator remain unfinished.

## Intrabar quote-policy evidence

Intrabar acquisition observations now carry the quote policy hash. A shared binding
method derives the ask and policy identity from one executable quote-book view.
It checks provider, instrument, session, freshness and the integer-to-float range.
Other admission fields still belong to their respective upstream authorities.

The candidate runtime rejects missing or malformed policy hashes before intrabar
journal preparation. The policy identity enters candidate-intrabar-v2 evidence.
Completed-candle and intrabar paths use the same hash-format check.

All 310 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
Tests cover binding, scope/freshness rejection, policy-sensitive evidence hashes and
missing-pin rejection before preparation. No services or database calls ran.
Source-oracle parity was not rerun. Full run-configuration pinning, evidence-block
persistence and complete source-to-strategy orchestration remain unfinished.

## Completed-candle quote-policy evidence

Entry frames built from shared market features now borrow the exact policy hash
from their quote book. The completed-candle candidate runtime rejects missing or
malformed policy hashes before preparing journal work. The policy identity is part
of the frame's serialized evidence and the candidate-completed-v2 evidence hash.
Changing only the policy therefore changes decision input evidence.

Existing offline tests now verify the feature-to-frame policy binding, evidence
hash sensitivity and rejection before journal preparation. All 309 Rust tests,
formatting, Clippy and copied-source hash checks pass. No services or database calls
ran. Source-oracle parity was not rerun.

This binds completed-candle evidence, not the entire run configuration. Intrabar
evidence, run-manifest policy pins and evidence-block persistence still need their
corresponding integration. A syntactically valid hash is not source certification.

## Protection amendment validation

The shared session authority now validates replacement stop/target pairs. It checks
direction, tick alignment, permitted session phase and scoped official buffered LULD
bands during regular hours. Entries and replacements share the same band-price
validator. A trailing stop may pass the original entry price, and an expired entry
deadline does not prevent protection updates on an acquired position.

The historical execution adapter requires this evidence before changing protection.
Missing or invalid evidence leaves the previous protection and amendment revision
unchanged. Cancellation and exposure-reducing exit requests do not require the
replacement-specific LULD evidence. Existing pending-fill journal barriers remain.

All 309 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
Tests exercise missing/stale bands, buffered limits, long/short geometry, closed
sessions, expired entry deadlines and a subsequent fill under the new stop.
An initial test used the inclusive freshness boundary; it was corrected to exceed
that existing bound. No services or broker calls ran. Source-oracle parity was not
rerun. Durable amendment intents, live broker replacement and reconciliation remain
unfinished; these tests do not establish broker-side protection behavior.

## Dependency-planned quote-policy startup

Quote policy startup now resolves the Quotes nodes in the shared dependency plan.
Every requested instrument needs a matching quote implementation hash and a policy
binding that covers all its requested intervals. Missing, duplicate and unused
bindings fail before loader calls. Instruments sharing a provider must agree on
the exact policy hash and target-session window; the provider is then loaded once.

Plans without quote consumers return an explicit not-required result and perform
no policy reads. Conflicting versions or session windows require separate plans.
This planner does not merge them into an implicit latest-policy authority.

All 307 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
Integration tests exercise dependency resolution through the mocked startup loader
and ready cache, including pre-load rejection and provider deduplication. No services
or database calls ran. Source-oracle parity was not rerun. The overall service
coordinator and certified policy producer remain unfinished.

## Startup quote-policy cache

A bounded startup loader now retrieves one pinned policy per provider through the
ClickHouse adapter. It rejects duplicate providers before loading, caps the provider
count at 256 and concurrency at 32, and supports cancellation. Every requested
provider retains an outcome. Callers cannot remove failed outcomes before promoting
the report into a ready cache.

Loaded policies must match their expected hashes and cover the full requested
half-open use interval. Future availability and partial loads cannot become ready.
Cache reads enforce the declared interval and perform no database requests.
Ticker books and the live market lane can bind shared Arc policy handles, avoiding
copies of the condition and indicator sets for every ticker.

All 304 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
Tests cover shared identity, scope and interval checks, duplicate plans, cancellation,
wrong hashes and unavailable policies. No services or database calls ran.
Source-oracle parity was not rerun. The overall service startup coordinator and
certified policy production remain unfinished.

## Persisted quote-policy records

The ClickHouse adapter now publishes immutable quote eligibility policies and loads
them by exact content hash for startup. There is no latest-row fallback. Readback
validates provider, availability, canonical JSON, payload bounds and the full policy
hash. Conflicting or malformed rows fail closed.

Publication requires repository-extraction and durability acceptance plus a matching
ownership lease. It verifies the explicit live_market_ssd policy and actual active
part placement through the shared storage verifier. Existing identical records are
reused. New inserts require verified readback before returning a pinned policy.

Schema 013 defines quote_eligibility_policies_v1 in the configured ARTE database.
The schema has not been applied. No database calls or services ran.

All 302 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
Codec tests cover conflicting rows, modified policies, future availability, invalid
keys, noncanonical payloads, response limits and maximum supported policy size.
Source-oracle parity was not rerun. Connected publication, policy source certification
and startup orchestration remain unverified or unfinished.

## Shared pinned quote eligibility

The shared quote book now requires a pinned provider-scoped eligibility policy for
executable reads. This applies to live quote/admission consumers, candidate feature
reads and normalized historical simulation. Raw observations remain available for
audit even when execution is blocked.

Policies include explicit allowed condition and indicator sets, explicit empty-set
permissions, a half-open effective interval, causal availability and source manifest
identity. Unknown codes, unavailable policies and mismatched providers block reads.
Policy identity cannot change inside an existing book. A disallowed latest quote
does not cause fallback to an earlier eligible quote.

The first validation run exposed a feature fixture without a policy. Synthetic
fixtures now declare test-only policies. Production has no permissive default.
All 300 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
No services or network tests ran. Source-oracle parity was not rerun.

Provider-specific numeric mappings, source certification, policy publication and
run-manifest binding remain required. A hash confirms identity, not correctness of
the chosen allowlist. This conservative all-codes-approved rule may need a versioned
extension if certified provider rules require combinations or venue-specific context.

## Normalized quotes feed historical execution

The historical execution adapter now accepts the shared quote book instead of
public raw integer quotes. Its source is bound once to provider, instrument and
session. Conversion uses the simulator's instrument price scale and the shared
quote freshness and executability checks. Locked, crossed, empty, stale and future
quotes cannot generate fills through this path.

Prices are rescaled exactly. Fractional displayed sizes that the current whole-share
fill model cannot represent are rejected, not rounded. Source timestamps remain
unchanged. Simulation sequence and time are explicitly supplied replay-clock values.
The same source quote cannot acquire a new simulation identity to replenish its
displayed liquidity. Exact retries retain the original pending fills through journal
failure and subsequent publication.

Offline tests cover normalized quote-to-fill-to-position processing, source binding,
exact conversion, freshness failures, duplicate liquidity prevention and publication
retry. All 298 Rust tests, formatting, Clippy and copied-source hash checks pass.
No services, broker calls or database writes ran. Source-oracle parity was not rerun.

The merged trade/quote playback controller and complete source-bound restart state
remain unfinished. Quote-condition eligibility, full-run manifests and end-to-end
strategy scheduling still need integration. The shared book checks are not a claim
that all exchange-specific quote eligibility rules have been implemented.

## Offline playback and debug controls

The shared market scheduler now has an in-process playback controller. It supports
pause, resume and one-boundary stepping. Pending boundaries remain visible and
unchanged until the caller acknowledges them. The caller must first complete its
required account journals and execution work; cursor acknowledgment is not a
durability receipt.

Prepared trade frames are immutable and shared between runs through Arc. Preparation
validates scope, availability, monotonic explicit clocks and resource limits. The
input hash binds frame contents, eligibility and the declared clock model. Playback
does not create receive timestamps, participant timestamps or final watermarks.
Each poll has a frame-work budget. Status reports admitted, coalesced and queued
events, acknowledged boundaries, completed frames and terminal failures.

Tests show identical boundary IDs and market/V7 checkpoints for stepped and
uninterrupted playback. They cover pending-consumer retention, malformed input,
duplicate accounting, bounded yielding and failure when the final watermark leaves
events queued. All 296 offline Rust tests, formatting, Clippy and source hashes pass.
No services or network tests ran. Source-oracle parity was not rerun.

This controller currently drives the trade/bar/V7 lane. Quote scheduling, source
loading, candidate/account consumers, full-run manifests, durable restart and the
CLI/UI control interfaces remain to be connected. Prepared frames are not a source
coverage certificate. This is not yet an end-to-end strategy backtest.

## Historical submission uses the shared session authority

Historical reserved submission no longer accepts a caller-supplied regular-session
boolean. It requires the same pinned TradingSession used by live order validation.
The adapter checks the actual submission clock, permitted session phase, bracket
geometry, risk-policy session and required official LULD evidence before mutating
the simulator. Funding is recomputed using the calendar-derived phase.

Already-reserved cash cannot bypass these checks. Tests exercise regular-session
submission without bands, valid regular submission, permitted and prohibited
extended hours, and the exact closed-session boundaries. Rejected submissions
produce no simulated fills on the next quote.

All 292 offline Rust tests, formatting, Clippy and copied-source hash checks pass.
No services or network tests ran. Source-oracle parity was not rerun. This change
does not complete the historical run controller, calendar producer certification,
run-manifest binding or full strategy-to-simulation orchestration.

## Settled-parent bracket evidence checks

The shared core now checks normalized broker protection snapshots against the exact
authorization, account, instrument, broker session, paper/live mode and parent ID.
Both children must have distinct broker IDs, the correct parent link, exit direction,
price scale, approved prices and order types. The stop must be StopMarket; the target
must be Limit. Both must be working with remaining quantity equal to the command's
attributed open position. Parent fills minus child fills must equal that position.

The check requires fresh observations and explicit evidence of broker residence and
sibling quantity management. These fields are adapter assertions, not proof supplied
by this module. A broker adapter must establish them from authoritative evidence.
The result is an audit hash, not an order permit or session-gate release.

This first check accepts filled parents and cancelled partial entries with remaining
exposure. Still-fillable parents fail closed. Their changing coverage, replacement
orders, closed-position reconciliation, evidence persistence and broker wiring remain
required. The check does not replace session, feed, LULD or exposure admission checks.

All 291 offline Rust tests, formatting, Clippy and frozen-source hashes pass.
New tests cover long/short protection, partial exits, cancelled partial entries,
authorization changes and 24 malformed or insufficient evidence cases. No services,
database writes or broker calls ran. Source-oracle parity was not rerun.

## Classification of committed broker responses

Initial response classification now requires the committed-outcome token and its
matching submission marker. HTTP failures, invalid JSON and malformed acknowledgments
remain Unknown. Broker errors and unapproved warning categories are Blocked. Approved
categories produce ConfirmationRequired, never an automatic confirmation.

Order identifiers retain their supplied documented status and produce ReconcileOrders.
They do not prove working protection. The parser rejects empty or duplicate IDs,
missing or unknown statuses, ambiguous mixed confirmation rows, invalid reply IDs
and oversized response/category collections. The warning-policy hash is retained
alongside the original outcome hash.

The shared HTTP session owner can receive the classification only for its unresolved
request. It stores the classification without clearing the gate. Repeated or foreign
classification cannot overwrite it. This supplies evidence for the next reply or
reconciliation step, not permission for another order.

All 287 offline Rust tests, formatting, Clippy and frozen-source hashes pass.
Tests cover malformed acknowledgments, failed HTTP status, duplicate IDs, approved
and unapproved notices, policy identity and continued session blocking after an
acknowledgment. No services or broker calls ran.

The classifier is not the reply executor or protection reconciler. Those workflows,
their durable transitions, restart recovery and eventual gate release remain required.

## One-send execution and retryable publication workflow

An execution attempt now owns the prepared request, single-use send permit,
authorization and submission marker. Its phases are Ready, Sending, Observed,
Publishing and Complete. It moves to Sending before awaiting transport. No phase
can return to Ready. Database retries therefore cannot resend the broker request.

The response clock is sampled once. Raw outcome evidence remains in the attempt
until it becomes a validated pending publication. Invalid clock or outcome data
does not discard the response or replace its timestamp. Publication failure and
cancellation preserve the exact pending record for retry.

Cancelling a send leaves the attempt in Sending. After the borrowing future has
ended, the owner can record an explicit interruption observation. This becomes an
Unknown outcome for reconciliation, not permission to send again. A successful
publication returns the existing committed-outcome token and closes the attempt.

All 284 offline Rust tests, formatting, Clippy and frozen-source hashes pass.
Mocked integration tests cover one send across failed publication, cancelled
publication, cancelled send, explicit unknown recovery and invalid-clock evidence
retention. They perform no network or database calls.

This connects send and outcome publication in process. The service runner must
still own attempts, persist/discover restart state, classify committed responses,
resolve broker replies and reconcile protection before releasing the session gate.
No services were started.

## Durable initial broker outcomes

The shared order contract now records one immutable initial outcome per submission
marker. It preserves either HTTP status and raw response bytes, or an explicit
unknown-result reason. The original observation time is retained. Binary bytes are
not coerced to UTF-8. Body, reason and timestamp bounds are validated.

The transport result can become a pending outcome. The asynchronous journal commit
verifies marker/authorization identity and exact readback before issuing a committed
outcome token. Failed or ambiguous writes leave the pending observation unchanged.
A second acknowledgment cannot issue another token. This token proves matching
readback, not successful order execution or complete bracket protection.

The account-owned ClickHouse publisher requires the submission marker to exist,
checks storage placement, reuses identical outcomes and rejects conflicting versions.
Schema 012 defines `broker_initial_outcomes_v1` on `live_market_ssd`; it is unapplied.
Reconciliation observations must be separate records, not replacements for initial
unknown outcomes.

All 280 offline Rust tests, formatting, Clippy and frozen-source hashes pass.
Tests cover raw-byte preservation, invalid time/status/size, exact acknowledgment,
ambiguous-write retry, mismatched readback and a maximum-sized binary payload.
No database or broker calls ran.

The full send-and-publish driver, restart outcome discovery, response classification,
reply workflow and session-gate release remain incomplete. Committing an outcome
does not yet alter the shared broker gate. No service was started.

## Shared broker pacing and non-waiting order admission

The HTTP transport now receives a shared session owner containing both its broker
gate and request governor. Account transports must share this owner. Status refresh
and order submission consume the same global pacing budget. The policy rejects
spacing below 100 ms, more than 10 concurrent requests, or a default penalty below
15 minutes. Future endpoints with stricter limits need additional endpoint budgets.

Orders use non-waiting admission. If a slot, pacing deadline or cooldown blocks the
request, the transport does not wait and does not issue HTTP. The current conservative
outcome is Unknown; the consumed order permit is not recreated. Runtime scheduling
still needs to reserve capacity before expensive order preparation where possible.
Control status refresh may wait under the governor.

HTTP 429 applies at least the documented 15-minute penalty. A longer valid Retry-After
extends it. Invalid or out-of-policy retry instructions halt the shared governor and
invalidate readiness while retaining a complete HTTP response for later audit.
No request is automatically retried. Admission permits remain held through response
consumption or cancellation.

All 276 offline Rust tests, formatting, Clippy and frozen-source hashes pass.
Paused-clock tests cover immediate rejection without waiting, admission counts,
inflight limits, lock contention, shared cooldown, the penalty floor and invalid
retry instructions. No network calls or services ran.

Durable response handling, reply resolution and startup ownership are still missing.
The shared pacing implementation does not make the full trading runtime ready.

## Shared broker-session gate and inactive HTTPS transport

One shared gate now covers all account transports for a broker session. Status
must be fresh, authenticated, established, connected and non-competing. Missing
flags cannot grant readiness. Direct and documented success.value response forms
are supported. Malformed data and clock reversal block the gate. Account, mode
and session identity are checked before a request is claimed.

Claiming a request blocks every account in that session until outcome resolution.
Status refresh cannot clear the pending request. There is deliberately no generic
reset. The durable outcome/reply workflow must provide the eventual release path.

The Client Portal HTTPS transport now compiles. Construction requires all existing
live acceptance gates and an approved hardware profile. URLs must use local HTTPS
and the /v1/api base path. Credentials, queries, fragments, remote hosts and insecure
HTTP are rejected. TLS verification remains enabled, with optional explicit trusted
certificate input. Proxy use, redirects and automatic retries are disabled.

The transport consumes opaque authorized requests, claims the shared gate before
I/O, preserves HTTP status and bounded response bytes, and leaves the pending gate
latched on cancellation or failure. Explicit status refresh uses the documented
authentication-status endpoint and cannot log in or resolve a pending order.

All 274 offline Rust tests, formatting, Clippy and frozen-source hashes pass.
Tests cover shared-account blocking, status freshness and parsing, scope mismatch,
invalid hashes and URL restrictions. HTTP methods and certificate connectivity have
compile coverage only. No HTTP requests, gateways or services were started.

This is not a ready live broker service. Shared pacing, authentication supervision,
account discovery, durable response handling, reply confirmation and reconciliation
remain unfinished. Without the outcome-release workflow, a session allows at most
one claimed order request. The entrypoints remain unarmed.

## Permit-consuming broker request boundary

IBKR bracket requests now have an immutable prepared representation. Its hash
includes mode, broker-session identity, account, authorization hash, endpoint path
and exact serialized request body. The supplied contract ID is therefore pinned.
The factory does not certify that contract mapping; reference authority must do so.
Only Live and Paper scopes are accepted. Account path components are validated.

The sender consumes the persisted marker's single-use permit. A closure supplies
current clocks and safety evidence when the future executes. The sender checks
scope, request identity, authorization, market/session safety and transport readiness
before handing an opaque, non-copyable request to the transport. It makes one call.
Errors after that call return Unknown. No retry or reply confirmation is attempted.
Bounded raw responses remain evidence, not proof of working bracket protection.

All 271 offline Rust tests, formatting, Clippy and frozen-source hashes pass.
Mocked tests verify exact path/body delivery, one transport call, scope mismatch,
unready broker, expiration before send, ambiguous failure, oversized responses and
request identity changes. No broker connections or services ran.

The current IBKR order reference was checked for the account-specific endpoint and
object containing the orders array. See the linked reference in document 12.
Authenticated HTTP transport, pacing, reply serialization, protection verification
and outcome persistence remain unfinished. The transport trait is not itself a
working gateway or proof of broker readiness. Backtest process isolation still
requires deployment and runtime integration beyond the scope-type rejection.

## Known-order restart discovery

The account-owned ClickHouse publisher can now recover a known command. It requires
the exact authorization from the pinned decision path, verifies its stored row,
then discovers the submission marker by the stable order slot. Missing authorization,
query failure, malformed data or conflicting marker versions fail recovery.

A found marker restores the order as `Unknown`, without send permission. Confirmed
marker absence restores `Durable`, subject to all current submission checks. This
absence interpretation requires the approved durable-storage and exclusive-owner
protocol. It is not valid against a stale replica or a legacy writer that bypassed
submission markers.

Recovery validates the complete candidate record before insertion. It refuses to
overwrite an existing order, so a later marker-absent result cannot downgrade an
unknown order to durable. Invalid recovery leaves the destination ledger unchanged.

All 269 offline Rust tests, formatting, Clippy and frozen-source hashes pass.
Tests cover marker discovery, foreign authorizations, duplicate versions, invalid
data, unknown-state restoration, denied permission and overwrite prevention.
The database query path has compile coverage only; no database calls ran.

This handles known commands, not complete account restart discovery. Enumeration
of authoritative pending commands, fill replay, broker protection reconciliation,
and startup integration remain required before live execution is ready.

## Persisted submission markers and single-use send permission

The submission preparation path now creates an immutable marker containing the
order slot, authorization hash, broker-request hash and original preparation time.
Retries preserve the marker. Changing the request hash or reversing its clock
fails. Exact marker readback issues one non-copyable, non-serializable send permit.
The ledger itself is no longer cloneable. Repeated acknowledgments cannot issue
another permit. Recovery retains marker evidence but never recreates permission.

The permit validates the actual request hash, current time, pinned authorization
context, current feed health and session/bracket policy after persistence. It is
consumed even when those checks fail. Such orders require reconciliation or an
explicit future resolution workflow; they are not automatically retried.

The asynchronous journal commit and ClickHouse publisher support this marker.
Publication requires the authorization to be present first. The account-owned
publisher verifies placement, rejects conflicting marker versions and reads back
the synchronous insert. Schema 011 adds `order_submissions_v1` with the explicit
`live_market_ssd` policy. It has not been applied.

All 268 offline Rust tests, formatting, Clippy and frozen-source hashes pass.
Tests cover exact retry after an ambiguous marker write, single permission issue,
changed request rejection, expiry, disconnected feed, policy changes, recovered
permission denial and canonical marker readback. No database or broker calls ran.

The actual broker transport must still require and consume this permit. Restart
discovery must query submission markers before deciding whether an authorization
can be sent. Neither integration is complete. This gate is not yet an end-to-end
exactly-once execution guarantee or verified power-loss durability.

## Validated ledger recovery and interrupted submissions

Ledger deserialization now validates records before exposing execution methods.
It rejects duplicate keys, command/key disagreement, invalid bracket geometry,
malformed context hashes, mismatched receipts and inconsistent fill/broker states.
Live insertion and recovery share a 100,000-record safety ceiling. Capacity failure
does not evict orders. Runtime ownership must bound each ledger's lifetime.

A recovered `Submitting` order becomes `Unknown`. It cannot be submitted again
through the ledger. Broker reconciliation is required. Consumers can enumerate
records through a read-only iterator for recovery work.

All 264 offline Rust tests, formatting, Clippy and frozen-source hashes pass.
Tests cover interrupted submission, blocked resend, partial-fill reconciliation,
duplicate JSON keys, inconsistent states and corrupted stored authorization data.
No services or database calls ran.

This validates supplied snapshots, not their freshness or external durability.
Persisting submission state before a broker request, discovering the authoritative
restart head and reconciling against the broker remain unfinished. Loading an old
authorization-only snapshot is not sufficient evidence that no request was sent.

## Authorization publication and verified readback

The order journal now publishes a complete immutable authorization before marking
the ledger durable. Its asynchronous commit keeps the order authorized on failure,
conflicting readback or cancellation. Retry uses the original account/command slot
and exact envelope. The public hash-only durability transition has been removed.

The ClickHouse publisher requires repository-extraction and durability acceptance,
plus cooperative account ownership. It checks the required storage policy and
actual part placement. It reads the stable slot before insertion, reuses identical
content, rejects conflicting versions and verifies synchronous insert readback.
Payload and response bounds apply. Source schema 010 defines
`order_authorizations_v2` on `live_market_ssd`; it has not been applied.

All 262 offline Rust tests, formatting, Clippy and frozen-source hashes pass.
New tests cover ambiguous writes, mismatched readback, cancellation after a simulated
write, exact retry, canonical payloads, conflicting slots and response limits.
No database calls, migrations or services ran.

This does not prove power-loss durability. The lease is single-host cooperative
ownership, not distributed fencing. Restart discovery, persisted submission states,
broker reconciliation and connection to the full execution driver remain required.

## Durable order identity includes authorization context

Each order record now stores the pinned calendar hash, extended-hours permission
and risk-policy hash. The versioned `arte.order-authorization.v2` envelope hash
covers the bracket and this context. It no longer hashes only the bracket.
The runtime must retain the referenced calendar and configuration records.

Authorization retries preserve the existing context. Reusing the command ID with
a different bracket, calendar, permission or risk policy fails. Submission checks
the supplied context and durable receipt before changing state. Ledger records
are private; consumers receive read-only record access. Legacy serialized records
without the required authorization context fail deserialization.

All 259 offline Rust tests, formatting, Clippy and frozen-source hashes pass.
New tests cover context changes, identity-preserving retries, serialized recovery,
missing legacy context, modified bracket quantity, missing receipts and incorrect
receipts. Failed submission leaves the durable order state unchanged.

This is an in-process identity and serialization contract. A hash is not proof
of a database commit. Durable publication/readback, crash-state reconciliation and
broker submission integration remain required. No services or database calls ran.

## Mandatory session policy in the order ledger

Order authorization and submission now require a pinned `TradingSession`.
Neither ledger operation accepts a caller-supplied regular-hours boolean.
Both re-evaluate the actual phase and check the risk policy's session identity.
The adapter constructs this reusable policy from its timezone-validated calendar.
The adapter's bracket helper delegates to the same core validation authority.

All 257 offline Rust tests, formatting, Clippy and frozen-source hashes pass.
A new regression authorizes a bracket in premarket, records durability, then
attempts submission at the regular open without LULD evidence. Submission fails
and the order remains durable. Closed-session submission also fails. Fresh valid
bands permit the regular-hours transition. Existing ambiguity, expiry and feed
health tests use the new mandatory session contract.

This proves the in-process ledger gate, not a complete broker workflow. Calendar
source certification, immutable authorization-context persistence, broker transport
and full account reconciliation remain incomplete. No services were started.

## Pinned runtime session and bracket phase checks

The adapter now provides an immutable pinned session handle. Construction checks
the full record hash, availability, exchange identity and New York date mapping.
Cached regular-session admission requires this handle. It checks the actual phase
and previous-close session before using the cached reference.

The handle also validates bracket prices against the actual session phase.
Regular hours always require official buffered LULD evidence. Extended-hours
permission cannot bypass that check. Premarket and postmarket require explicit
permission. Closed sessions reject exposure increases. Complete bracket geometry
is required in every permitted phase.

All 256 offline Rust tests, formatting, Clippy and frozen-source hashes pass.
Tests cover exchange/session mismatch, future availability, half-open regular
boundaries, missing regular bands, extended-hours permission, closed-session
rejection and incomplete brackets. No service or network tests ran.

These checks do not certify the external calendar source or authorize broker
submission. The full OMS driver still needs to require them immediately before
submission, alongside funding, feed health and protection checks. The cached
admission wrapper has compile coverage but no full runtime integration test.

## New York calendar conversion and UTC-date validation

The calendar adapter converts explicit local session hours into UTC using the
bundled New York timezone rules. It does not use a fixed UTC offset. Ambiguous or
nonexistent local times, invalid dates and timestamp overflow are rejected.
Supplied early closes remain explicit; no holiday or close time is inferred.

UTC session validation checks that the first and final included instants of both
extended and regular intervals belong to the declared New York date. The policy
covers same-day US equity sessions, not overnight exchange sessions. Calendar-bound
reference construction now applies this validation after checking the record pin.

All 254 offline Rust tests, formatting, Clippy and frozen-source hashes pass.
New tests cover winter/summer UTC offsets, supplied early-close duration, DST gaps
and ambiguities, invalid dates and wrong declared UTC session dates. The reference
factory test uses real timezone conversion. No services or calendar APIs ran.

Calendar source acquisition/certification, holiday validation, persistence and
session-driven lifecycle integration remain incomplete. Timezone conversion alone
does not establish that a supplied exchange schedule is correct.

## Pinned trading-session geometry

The shared core now represents a session with an exchange identity, valid calendar
dates, previous trading session, explicit UTC extended/regular intervals, original
availability and a source-manifest hash. Consumers require the pinned full-record
hash. Phase calculation uses half-open boundaries and the supplied regular close,
including an early close. It does not assume every session ends at 16:00.

Reference requests can be constructed from this session contract. The target
session and previous-close source session must agree with the calendar record.
The request inherits the extended-session use interval. Changed calendar geometry
cannot pass an unchanged pin.

All 252 offline Rust tests, formatting, Clippy and frozen-source hashes pass.
Tests cover phase boundaries, early-close geometry, future availability, invalid
dates, leap years, invalid containment and calendar/reference pin disagreement.
An initial Clippy diagnostic was corrected and the complete validation rerun.
No services or calendar API calls ran.

The calendar producer must still verify exchange holidays, previous-session links
and timezone-to-UTC conversion, then persist its certified records. The contract
does not prove that UTC intervals correspond to the supplied dates. Session-driven
startup/shutdown, risk-policy selection and maintenance scheduling remain unfinished.

## Reference-use interval retained through cache loading

The calendar-supplied use interval now travels with each reference request into
the immutable startup cache. It is no longer discarded after planning. Cached
lookups require `start <= evaluation_time < end` as well as the original record's
availability check. A valid record cannot be reused after its declared session
window. Loading before a window opens is allowed; use before it opens is rejected.

The interval has one owner in the request contract, not duplicate fields in the
binding and cache request. Invalid intervals are rejected before loader I/O.
All 249 offline Rust tests, formatting, Clippy and frozen-source hashes pass.
Expanded cache tests exercise the final permitted instant, exact end boundary and
later times. No services ran.

Calendar certification and full-session runtime orchestration remain incomplete.
This check enforces the supplied interval; it does not establish that the calendar
authority supplied the correct interval.

## Explicit previous-close dependency planning

The shared dependency contract now has a distinct `PreviousClose` variant. Generic
`Reference` dependencies are not implicitly interpreted as previous close.
The reference planner resolves declared previous-close nodes into pinned load
requests. It checks the implementation hash, instrument, source-record requirement,
and coverage of the declared use intervals by the calendar-supplied target interval.
Missing, duplicate, unused and conflicting bindings fail before loading.

`load_planned` composes this planner with bounded reference loading. Only requested
previous-close records are loaded. Other reference and derived requirements remain
unresolved by this branch. Each plan covers one target session per instrument;
multi-session backtests must resolve separate per-session reference plans rather
than reuse one close across sessions.

All 249 offline Rust tests, formatting, Clippy and frozen-source hashes pass.
Two new planner tests cover exact bindings, missing/conflicting inputs, interval
overflow and separation from generic references. No services ran. The composed
loader compiles but has not been exercised against ClickHouse.

Calendar/reference source certification, automatic binding production and the full
startup readiness coordinator remain incomplete. Reference plan success does not
certify upstream inputs or event/derived readiness.

## Bounded startup reference loading and cache

Startup reference loading now supports up to 4096 pinned requests with at most 32
concurrent reads. Requests are validated before I/O. Duplicate target scopes are
rejected. Every returned record is revalidated against its original request and
startup as-of time. The ClickHouse adapter implements the loader interface.

Each request has a reported outcome. Stop requests cancel pending reads and prevent
new reads from starting. Failed, invalid or stopped requests cannot produce a ready
reference cache. The report's outcome collection is read-only to callers. A fully
loaded cache owns the records in memory and serves exact scoped lookups without
database access. The live lane has a cached regular-admission entry point.

All 247 offline Rust tests, formatting, Clippy and frozen-source hashes pass.
Three new tests cover bounded concurrency, cache lookup, invalid loaded records,
pre-stopped requests and duplicate-request rejection before I/O. The live cached
wrapper compiles; it has not been exercised end-to-end with ClickHouse. No services
or database connections ran.

The complete startup driver must still derive the request set from enabled strategy
requirements, certify the reference source, and combine reference readiness with
event/derived-data readiness. This component alone does not make startup complete.

## Previous-close ClickHouse storage adapter

Schema 009 adds `previous_close_records_v1` on `live_market_ssd`. It stores only a
record hash and canonical payload. There is no mutable latest pointer and no
duplicated price/session projection. Readers request the exact startup-pinned hash.

The adapter validates identity, scope, source session and as-of availability after
loading. Readback is bounded and rejects multiple distinct rows, wrong hashes,
noncanonical payloads and corruption. Publication requires extraction/durability
acceptances and a matching ownership lease. Existing identical content is reused;
new inserts use synchronous insertion and verified readback. This does not claim
power-loss durability or distributed writer fencing.

All 244 offline Rust tests, formatting, Clippy and frozen-source hashes pass.
The new decoder test covers missing rows, correct records, future availability,
conflicting responses, altered keys, noncanonical JSON and byte limits. Database
methods compile but have not been exercised against ClickHouse. Schema 009 has not
been applied. No service, database connection or migration ran.

Source acquisition/certification, reference manifest publication, startup planner
integration and automatic session selection remain unfinished. Persisting a record
does not certify its source manifest or make the full runtime ready.

## Pinned previous-close evidence

Live-lane regular admission no longer accepts an unqualified previous-close decimal.
It requires a typed record and a pinned requirement. The record contains provider,
instrument, source session, exact price, original availability and source-manifest
hash. The requirement pins the full record hash and expected preceding session.

Future availability, changed prices or source hashes, wrong instruments/providers,
wrong source sessions and missing pins are rejected. The current session cannot be
used as previous close. The startup/reference authority must choose the actual
preceding trading session; no calendar-day subtraction is performed. Missing data
remains explicitly unavailable even when the expected identity is known.

All 243 offline Rust tests, formatting, Clippy and frozen-source hashes pass.
The new reference test covers a Friday-to-Monday session pin and changes to every
record field. The live-lane test now verifies that changed records and precision
loss cannot enter through the regular-admission path. No services ran.

Hash agreement proves identity, not that a source manifest is trustworthy. The
reference producer, manifest certification, ClickHouse storage/loading, startup
dependency integration and full runtime configuration binding remain incomplete.

## Live-lane regular admission integration

The live lane now calculates regular-session admission using its own fresh quote
and current LULD state. The caller supplies previous close and the pinned policy,
not bid/ask prices or a caller-assembled band. Feed permission, quote age and band
age are checked at the requested evaluation time. Missing or invalid quote/band
state returns an error; it is never replaced with stale evidence.

Decimal conversion to instrument atoms is shared with order-plan price conversion.
Both scale increases and exact scale decreases are supported. Precision loss,
invalid scales and overflow are rejected. No float conversion occurs on this path.

All 242 offline Rust tests, formatting, Clippy and frozen-source hashes pass.
The expanded live-lane test calculates a buffered target from mixed-scale quote
prices, checks missing previous close, and rejects stale quotes, denied feed
permission and inexact previous-close conversion. No services ran.

Previous-close provenance and applicable-session determination remain upstream
requirements. Candidate-frame wiring, effective configuration binding for this
policy and end-to-end orchestration are still incomplete. This method calculates
admission evidence; it does not authorize or submit an order.

## Shared in-memory LULD state

The live lane now owns one bounded LULD projection for its provider, instrument and
session. Executable reads validate the current feed gate, scope, scale and original
band age. Duplicate delivery preserves the first availability timestamp. Older
effective-time updates are reported without replacing the latest band.

Conflicting same-time geometry, malformed evidence and wrong-scope updates latch
the book closed. Disconnection invalidates it. The last observation remains an
audit view only; consumers cannot fall back to it for execution. Recovery requires
explicit replacement of the invalidated owner. No implicit reset is provided.

All 241 offline Rust tests, formatting, Clippy and frozen-source hashes pass.
Two new state-owner tests cover duplicate, older, conflict, invalidation and expiry
behavior. The expanded live-lane test checks current feed permission, expiry,
disconnects and that band updates do not advance trade/quote watermarks.
No services or network tests ran.

The provider adapter must still certify original effective times and supply bands.
Same-time corrections need an explicit provider ordering contract; this version
rejects ambiguity rather than inventing that order. Band persistence and recovery,
candidate admission wiring and the continuous runtime driver remain incomplete.

## Shared LULD evidence at order validation

Order validation now uses the same scoped LULD evidence type as admission. The old
thin `Bands` record is removed; its name aliases the shared contract. The risk
policy must pin the expected provider and session. The bracket supplies instrument
and price scale. Effective time, availability time, official status, geometry and
scope are checked by one shared validator.

Regular-session bracket planning, cash reservation, order authorization and
pre-submission validation therefore reject expired or wrong-scope bands. Refreshing
availability cannot extend the original effective-time age. Both long and short
brackets retain their existing mandatory protection and tick-buffer rules.

This is an incompatible input-contract change: serialized band records missing
scope, scale or effective time are rejected. Risk policies missing provider/session
are rejected. There is no default or migration that invents missing provenance.

All 239 offline Rust tests, formatting, Clippy and frozen-source hashes pass.
Expanded order tests verify both directions, reject altered scopes and clocks,
recheck bands after durability, and preserve the durable state on failed submission
validation. No service, database or broker test ran.

Provider certification, session-policy production, continuous band delivery and
full broker orchestration remain incomplete. This does not establish live readiness.
Order risk still applies its configured tick buffer; the strategy admission's
additional basis-point/spread buffers are not silently substituted for that policy.
Simulation geometry checks use a separate non-authorizing method and do not
invent a provider/session policy. Shared OMS validation still owns authorization.

## Exact regular-session LULD admission

The shared core now calculates regular-session admission from explicit previous
close, quote prices and official band evidence. The calculation follows the frozen
candidate's previous-close gates and maximum of basis-point, tick and optional
spread buffers. It uses exact integer arithmetic and inward tick rounding instead
of float epsilon. At least three buffer ticks are required by ARTE's safety policy.
This intentionally excludes the legacy research-estimate fallback.

Band evidence carries provider, instrument, session, scale, effective time and
availability time. Mismatches, future availability, inverted clocks and expired
effective time block admission. Updating availability cannot rejuvenate old bands.
Quotes on either buffered boundary are blocked. Missing previous close, low previous
close, missing official evidence and infeasible buffers have explicit reasons.

All 239 offline Rust tests, formatting, Clippy and frozen-source hashes pass.
Three new tests cover rounding, independent buffers, identity, timestamps and
missing dependencies. No service ran. Python source-parity comparisons were not run.

This is a pure admission calculation, not a provider-evidence certificate. The
caller must still supply certified previous-close provenance, a fresh scoped quote,
and the applicable session. Feed ingestion, candidate-frame integration, and the
execution validator's adoption of the effective-time contract remain unfinished.
The existing order validator is not claimed to enforce this new contract yet.

## Entry operand expiry at the actual decision clock

Inspection found that completed-entry admission and MACD age used candle close,
even when the account evaluated later. The entry evaluator now accepts an explicit
evaluation timestamp. Candidate evaluation passes its actual account clock.
Admission and MACD expiry use that clock. Structural geometry, detector identity,
setup history and evidence coordinates remain tied to the completed candle.
Evaluation before candle close is rejected. The immediate-close helper remains
available for deterministic algorithm tests; runtime callers use `evaluate_at`.

All 236 offline Rust tests, formatting, Clippy and frozen-source hashes pass.
New checks cover expiry at and just after the configured age boundary. The composed
candidate test also covers an unexpired candle with independently expired admission
or MACD evidence, producing a journaled wait instead of an entry. No services ran.

This fixes operand age validation. It does not implement admission producers,
certify external permission evidence, or complete live/backtest orchestration.

## Concurrent account journal commit

The journal adapter now commits independent prepared account decisions with bounded
async concurrency. It validates all supplied scopes and market boundaries before
the first write. Duplicate slots and foreign decisions fail before I/O. Limits are
explicit: at most 4096 supplied slots and 64 concurrent writes.

Each slot retains its verified receipt. Cancellation preserves completed slots and
leaves unfinished account transactions pending. Retrying the same slots skips their
successful writes. An ambiguous failed write retries its original transaction.
Results report each supplied account separately. One failure does not cancel other
accounts or undo their committed decisions. Missing consumers keep the barrier shut.

Offline tests cover bounded overlap, partial failure, exact retry counts,
cancellation after one account completes, and preflight rejection before any write.
All 235 Rust tests, formatting, Clippy and frozen-source hashes pass. No service or
network test ran. Source-oracle comparisons were not rerun.

This completes a journal coordination component, not the full account actor. The
runner must retain slots across cancellation and route verified decisions onward.
Crash recovery, concurrent strategy calculation, broker command submission and
continuous live/backtest orchestration remain incomplete. No broker protection or
trading readiness is inferred from a successful journal commit.

## Multi-account market acknowledgment

The shared core now has a bounded account journal barrier. It freezes the consumer
scopes for one market boundary. Every account/strategy consumer must supply a
verified transaction receipt before the market boundary can advance. Consumers
share a run, mode and instrument but retain independent configuration and state.

The barrier binds event identity, source sequence, event time and availability time.
Accounts may evaluate later without changing those source clocks. Receipts must
match the exact configured consumer scope. Duplicate receipts are idempotent.
Conflicting receipts are rejected. A failed market acknowledgment keeps progress.
Workers can query which consumers still need a decision after a partial commit.

The live lane exposes barrier creation and account-aware market acknowledgment.
The existing market-only acknowledgment remains a low-level path; a strategy
runner must use the account-aware path. The complete consumer set still has to be
provided by the future configuration/runtime coordinator. The barrier does not
implement concurrent account workers, broker authorization, cross-account atomic
execution, durable recovery, or emergency-exit processing.

All 232 offline Rust tests pass, including three new barrier tests and an expanded
live-lane test. The latter prepares two account transactions, rejects incomplete
readback, preserves the pending market boundary after the first account commits,
and advances only after both receipts. Formatting, Clippy and frozen-source hashes
also pass. No service, network, database, broker or browser test ran.

## Effective candidate configuration and evaluation clock

Effective candidate configuration version 2 includes the shared feature configuration
hash. Completed-bar and intrabar evaluation both check it before preparing a decision.
Candidate and feature completed-bar age limits must agree. The candidate instrument
must match the feature owner's instrument. Changed calculation windows, conflicting
age limits and wrong-instrument owners cannot produce a pending journal batch.

The live lane can calculate the combined configuration identity and pass its feature
owner, built frame and current feed check into candidate preparation. The result is
an intent with a pending journal batch, not an order or a market acknowledgment.
The caller still owns durable commit and account coordination.

Entry-frame construction now accepts the actual account evaluation time separately
from feature preparation time. Quote and bar age use that later clock. Newer quotes
may be used without restamping the prepared market snapshot. Evaluation cannot
precede preparation. The candidate decision records the actual evaluation time.

All 229 offline Rust tests, formatting, Clippy and copied-source hashes pass. Expanded
tests reject configuration/instrument mismatches before journal preparation and
check delayed evaluations against expired and newly received quotes. The live-lane
preparation wrapper compiles but has not been exercised end-to-end. Source-oracle
comparisons were not rerun. No service or network test ran.

Still incomplete: admission and swing producers, journal/account coordination,
broker execution, full orchestration and whole-engine recovery. These checks bind
configuration; they do not prove arbitrary caller-supplied frame provenance or
authorize trading.

## Candidate entry-frame construction

The shared feature owner now constructs the existing candidate entry-frame contract.
It borrows market bars, cached current levels, prior levels, setup range and external
account/swing evidence. Current level projection runs once per completed one-second
feature snapshot, not separately for each account. The live lane exposes this builder.

Admission remains explicit. Its MACD and activity fields must agree with calculated
features. Missing quote data, stale quotes, other-instrument quotes, future swings,
invalid geometry, future admission evidence and mismatched boundaries are rejected.
An old completed bar produces `fresh = false`; processing time never refreshes it.
Permission and tradability flags are preserved, including denied values.

Feature configuration version 2 pins quote/bar age limits and evidence budgets.
Current/prior levels and swing arrays are bounded. The entry-frame builder is not
an emergency-exit path or order authorization. Emergency exits must not depend on
entry-frame readiness.

All 229 offline Rust tests, formatting, Clippy and copied-source hashes pass. Existing
scheduler tests now construct a real entry frame and exercise missing/stale/wrong-scope
quotes, contradictory admission fields, future swings and delayed-bar freshness.
Source-oracle comparisons were not rerun. No service or network test ran.

Still incomplete: authoritative admission and local-swing producers, effective
candidate/feature configuration binding, journal-bound account evaluation, broker
execution and full live/backtest orchestration. Frame construction alone does not
establish strategy acceptance or end-to-end parity.

## Shared candidate market features

One feature owner now consumes every scheduler boundary. It combines candidate
MACD/episode state, preceding setup range, 300-second range evidence, 60-second
progress evidence, session VWAP and high-of-day facts. Completed one-second snapshots
do not include the current candle in the preceding setup range. Sparse gaps do not
invent a contiguous previous candle. Missing progress history remains a failed
evidence record, not an assumed pass.

The owner pins its configuration and market configuration. It requires the declared
five-second 12/26/9 series, rejects skipped boundaries and handles exact retries
without recalculation. Calculation failures hide the state until recovery. Rolling
histories are not cloned for each event. Setup history is bounded by a maximum
one-hour window; activity history uses its existing bounded retention.

The live lane now runs this shared feature owner before exposing a pending boundary.
Processing a new source candle does not assert feed freshness or grant account
admission. One-second feature snapshots are absent on trade and five-second events.
MACD transition flags are not reissued merely because a later trade reads the state.

All 229 offline Rust tests, formatting, Clippy and copied-source hashes pass. Tests
cover feature alignment, preceding-range exclusion, VWAP/highs, missing progress
history, duplicate handling, dependency rejection, skipped input and failure latching.
Source-oracle comparisons were not rerun for this increment. No services ran.

Still incomplete: local swing/admission authorities, complete entry-frame construction,
effective strategy configuration binding to the feature hash, account journal
coordination, full runtime orchestration and coherent feature/scheduler recovery.

## Candidate forming MACD and episodes

The shared candidate MACD state now consumes completed five-second samples and
one-second preview boundaries. Two contiguous completed samples recover the source's
hidden slow EMA. Forming previews do not compound that base. Missing or old base
evidence stays unavailable. Disabling previews retains the completed sample's age.

A bullish sample can start an episode. A forming reversal blocks bullish readiness
but does not end the episode or signal a completed reversal. A valid non-bullish
five-second completion ends the episode. Episode body highs and prior highs are
retained. Duplicate samples are idempotent; changed identities and invalid clocks
are rejected before state changes.

The scheduler binding requires the declared five-second 12/26/9 series and pins the
market configuration. It rejects skipped five-second boundaries and foreign current
bar views. Unit tests cover this binding, EMA previews, episode transitions, gaps,
unknown samples, disabled previews, duplicate handling and state round trips.

All 228 Rust tests, formatting, Clippy and copied-source hashes pass. A separate
offline comparison compiled the exact hash-verified frozen `forming_macd` function.
It matched 4,042 observations across 32 histories with zero numerical difference;
availability, base clocks and bullish classifications also matched. The comparison
does not cover the full Python episode observer or full strategy decisions.
The full offline validator also reran the existing 70 extraction cases and 87 fit
cases successfully, with their previously declared numerical tolerances unchanged.

Historical warmup certification, admission/detector production, account journaling
and full strategy orchestration remain incomplete. No service or network test ran.

## Declared timeframes and close ordering

The frozen candidate requires one-second bars and a forming five-second MACD.
One-second MACD values cannot substitute for that dependency.

The market owner now accepts up to 16 additional timeframes at construction. Each
declares its interval, MACD periods and retained-bar budget. Intervals must be whole
seconds, longer than one second, no longer than a day, unique and aligned with both
session boundaries. The combined declared budget cannot exceed one million bars.
Undeclared timeframe access fails. All series consume the same qualified events.

The scheduler exposes closes chronologically. Larger timeframes precede smaller
ones at a shared close. It stops at the earliest developing-bar end before applying
a later trade. A five-second close cannot appear in an earlier one-second view.
The live lane exposes the same borrowed timeframe state. No HTTP boundary exists.

Simultaneously completed bars retain their original computation-availability time
even if the consumer delays a later acknowledgment. The later evaluation clock is
recorded separately. An offline assertion covers this delay without restamping data.

Market recovery is version 5 and includes the additional series. Scheduler boundary
identity is version 2 and includes the timeframe. Tests cover simultaneous closes,
large sparse gaps, configuration rejection, and identical recovery continuation.
All 224 Rust tests, formatting, Clippy and copied-source hashes pass. Source-oracle
parity was not rerun. No service or network integration test ran.

The series currently initialize their indicators from admitted input. They do not
certify historical indicator warmup. Candidate MACD episode handling, warmup
readiness, complete admission evidence and full strategy-frame wiring remain
unfinished. This increment is not evidence of full candidate parity or readiness.

## Causal scheduler increment

The shared market scheduler prepares one boundary at a time. It exposes a completed
bar before applying the next trade. It then exposes each trade, including its
eligibility and unchanged source/availability clocks. Empty intervals create no bars.
The strategy input envelope uses a shared bar/trade boundary sequence, separate from
the provider event sequence.

A pending boundary stays in memory until its exact ID is acknowledged. Dropping a
borrowed view, attempting another step, or sending a wrong acknowledgment cannot
dequeue it or recalculate market state. Calculation failures retain queued input and
block subsequent views. This pause supports asynchronous consumer work. It does not
prove durable journal completion or provide crash recovery for the whole scheduler.

The live lane now uses this scheduler instead of releasing a batch directly into
the final market state. It rechecks the current feed gate before each new boundary.
The existing market-only batch API remains available for non-strategy consumers.

Offline validation passed: 222 Rust tests, formatting, Clippy with warnings denied,
and copied-source hashes. Four new tests cover causal boundary order, absent empty
bars, pending acknowledgment behavior, failure retention, and live-gate binding.
Source-oracle parity was not rerun. No service or network integration test ran.

Still incomplete: full candidate-frame production, journal-bound multi-account
consumption, quote-triggered strategy evaluation, coherent scheduler recovery, and
end-to-end live/backtest orchestration. The local boundary acknowledgment is not a
trading authorization. Performance and representative-session parity are unproven.

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
- Integration of the receiver with the complete in-process live path.
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

- 196 in-process Rust tests passed (136 core and 60 adapter tests).
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

Event persistence now has a bounded lossless staging contract. Payload objects are
content-addressed; thin observations retain source clocks, optional live receipt
and knowledge time. A pinned versioned manifest preserves batch application order.
Restoration verifies complete readback and tolerates identical physical retry rows
before database merges. Reused live receipt slots with different contents fail.
Payload corrections remain separate observations, not destructive replacements.
Four offline tests cover live/REST sharing, missing/corrupt readback, receipt
collisions and ordered restoration. The batch uses the existing EventStore retry
rules; cross-batch deduplication and receipt-slot enforcement remain writer duties.
This is a staging contract, not the final compact ClickHouse layout. No event DDL
or writer is enabled. Provider identity overlap, physical codec/storage acceptance,
batch publication and the persistence worker remain incomplete.

The ClickHouse adapter now stages event payloads and observation references with
bounded bulk lookups, inserts only missing content objects, verifies readback and
publishes the manifest last. Loading verifies the pinned manifest and restores the
batch in application order. Identical retry rows are tolerated before merges;
conflicting or unrequested values fail. Publication requires explicit repository
extraction, source-identity, event-storage and durability acceptance. The supervisor
must validate the evidence behind those flags; flags alone are not certificates.
Event-storage acceptance is also required by the Live arming checklist.

Candidate migration 003 defines staging tables only, using live_market_ssd.
No migration, database read or write ran. Two offline tests cover the acceptance
gate and bulk readback conflict handling. Network methods only compiled. The final
compact event codec, range-query index, coverage catalog, cross-batch receipt-slot
validation, writer ownership and ingestion persistence worker remain incomplete.
Staging storage must not be presented as the complete canonical event authority.

A bounded serial event-publication worker now connects prepared batches to the
gated ClickHouse publisher. It retains an Arc-owned pending batch through failed
or cancelled publication futures. Exact pending work is retried before dequeuing
another batch. Wrong batch acknowledgments fail. Progress exposes pending identity,
attempt count and acknowledged batch identity before a slow write completes.
Shutdown retains pending/queued work; closing input supports draining. Four offline
fake-publisher tests cover retry order, incorrect acknowledgments, cancellation and
in-flight progress. No database function ran. This is not process-crash durability
or certified source coverage. Durable acquisition catalog, ingestion fan-out,
supervisor recovery and real storage acceptance remain incomplete.

Acquisition coverage now has a versioned certificate and bounded in-memory catalog.
Certificates require a complete non-cyclic pagination chain, checked page identity,
ordering and interval membership, zero rejected rows, consistent counts and
acknowledged batch references. Empty coverage still requires a verified successful
page. Gap queries match provider, instrument, channel, source revision, contract,
capabilities and publication cutoff. Three offline tests cover unfinished pagination,
empty coverage and revision/channel/knowledge-time isolation. The acquisition owner
must supply real evidence behind page flags and batch acknowledgments. These
metadata checks do not prove provider completeness or replace response validation.
Persistent catalog storage, REST orchestration and derived coverage remain unwired.

Coverage catalog publication now requires a VerifiedCertificate from a streaming
batch verifier. Every referenced batch must match its hash, provider, instrument,
channel, half-open SIP interval and REST acquisition clock. Live receipts are
rejected from REST coverage. Persisted row counts must reconcile with explicitly
recorded deduplication. Failure latches and incomplete verification cannot publish.
Two new offline tests cover actual batch scope and count failures. Raw response
counts, deduplication reasons and ordering evidence still require the acquisition
owner; metadata flags are not independent proof of those facts.

ClickHouse coverage publication and loading now invoke that verifier against actual
batch readback, one batch at a time. Publication writes the immutable certificate
last and rejects future publication times. Candidate migration 004 provides hash-keyed
coverage staging, not range discovery. These methods compiled but were not called.
REST orchestration, persisted interval discovery, derived certification and runtime
handover remain incomplete. No migration or network call ran.

REST acquisition now has a bounded one-page fetch API and single-interval state
machine. Requests use explicit ascending timestamp order and half-open bounds.
Each response retains request/response hashes and acquisition time. Cursors are
origin/path checked and API-key parameters removed. Normalization rejects timestamp
rewinds and out-of-range rows before publication; equal-timestamp boundaries remain
valid. The state machine retains prepared pages and batch progress through ambiguous
publication, advances only after acknowledgments, and produces a certificate only
after pagination ends. Three offline tests cover duplicate counts, timestamp bounds
and a two-page retry-to-coverage workflow with fake adapters. No REST call ran.

The caller must resolve symbol-to-instrument identity for the whole request interval.
Durable restart checkpoints, rejected-response audit storage, bounded worker scheduling,
rate-limit retry policy, derived repair and executable maintenance routing remain open.
The implemented query/pagination fields were checked against the official
[Massive trades](https://www.massive.com/docs/rest/stocks/trades-quotes/trades) and
[quotes](https://www.massive.com/docs/rest/stocks/trades-quotes/quotes) documentation.

REST progress now uses linked immutable one-page records. A page's progress must
be acknowledged before fetching another page or producing a final certificate.
Restoration checks the pinned plan/head, complete predecessor chain, request cursors,
page numbers, source frontier and metadata prefix. Prefix validation cannot construct
a coverage certificate. One new offline test covers blocked advancement, restart at
the next cursor, completed-chain recovery and corrupted-head rejection.

ClickHouse checkpoint/recovery methods and candidate migration 005 are implemented
but unexecuted. Recovery bounds both record count and bytes. The job controller must
durably store the acknowledged head and own the publication lane. That job record,
cross-process crash acceptance and pending-page recovery policy remain incomplete.
Restoring progress does not replace final data-backed coverage verification.
No service, migration, database call or network integration test ran.

Acquisition jobs now have a durable head index keyed by job name and pinned plan.
Checkpoint acknowledgment follows progress-object readback, head append and head
readback. Exact retries reuse the same revision. Missing predecessors, skipped
pages and conflicting latest heads fail before acknowledgement. Recovery resolves
the indexed head and then validates its linked progress chain. Candidate migration
006 stores append-only heads on live_market_ssd. Two offline tests cover retry/fork
rules and conflicting pre-merge readback. No database operations ran.
One externally fenced owner per job remains mandatory; this is not a distributed
lock or compare-and-swap service. Ownership fencing, job scheduling and actual
storage crash/restart acceptance remain incomplete.

A maintenance-job runner now composes durable-head recovery, REST acquisition,
batch publication, progress checkpoints and final coverage publication. It exposes
recovering/acquiring/checkpointing/verifying/complete phases. Failed checkpoints do
not refetch already prepared data. Ambiguous coverage publication retains the same
certificate identity for retry. Completion requires the expected coverage ID.
One integrated offline test uses fake adapters through checkpoint and coverage
failures, retries and completion. The real database adapter binding only compiled.
Multi-job scheduling, ownership fencing, retry policy and executable service routing
remain incomplete. Publication-clock semantics and storage durability still need
real integration acceptance. No service or network/database call ran.

Maintenance's database backend now requires a matching, exclusively borrowed local
ownership lease. The backend checks job/plan identity, batch scope and certificate
scope before database work. Job hashes share one implementation with durable heads.
The lease uses Rust's nonblocking file lock on a stable file in a caller-approved
external runtime directory. It never truncates or deletes the lock file. Two offline
tests verify second-handle exclusion, release on drop and invalid-input rejection.
Empty test lock files remain under the external Cargo output directory.

This is cooperative single-host ownership, not cross-host fencing. All cooperating
copies must use the same approved lock directory. Host pinning, failover fencing,
filesystem/ACL acceptance and executable lifecycle integration remain open. The
implementation follows the [Rust file-lock contract](https://doc.rust-lang.org/std/fs/struct.File.html#method.try_lock).
No service or database/network operation ran.

The maintenance scheduler now bounds admitted jobs and concurrent Tokio workers.
It validates duplicate job identities and the configured estimated-memory plan
before dispatch. Results distinguish complete, stopped, failed and not-started jobs.
Fail-fast stops new admission, signals existing workers and joins them. Worker
panics are accounted for without hiding other job results. Five offline tests cover
concurrency, duplicate rejection, fail-fast, panic accounting and pre-start shutdown.
No network worker ran. Memory admission uses estimates, not an enforced OS RSS cap.
The scheduler's worker factory still needs the real lease/runner binding, measured
resource profiles, rate-limit coordination and executable service wiring. Callers
must signal shutdown and await draining; dropping the whole scheduling future
requires recovery from durable heads.

The maintenance pool now has a real worker binding. Each admitted worker acquires
its local job lease, constructs REST/database adapters and drives the resumable
runner through coverage publication. A stop request finishes a pending page progress
checkpoint before releasing ownership. Per-job watch channels retain only the latest
phase and terminal state; slow observers do not create an unbounded queue. Interrupted
workers mark their observer state on drop. Credentials are neither serialized nor
Debug-formatted. Extraction, identity, event-storage, durability and resource-budget
acceptance are mandatory before constructing the runtime context.
Two offline tests cover acceptance gating and interrupted-observer state. The real
campaign compiled but did not run. Host assignment/failover fencing, coordinated
cross-host provider rate limits, measured memory/CPU isolation and executable service wiring
remain incomplete. No network or database operation ran.

One runtime context now shares a REST request governor across every maintenance
worker. An explicit policy bounds concurrent responses and spaces request admission.
Permits remain held through response consumption. HTTP 429 and 503 responses apply
a shared cooldown. No automatic retry occurs. Retry-After accepts decimal seconds
or IMF-fixdate; malformed, obsolete-format, past-date or excessive values halt new
admissions instead of guessing a delay. Missing headers use the configured cooldown.
The policy has no production defaults. Status exposes admission count, remaining
cooldown and the halted state, without credentials. Three deterministic offline tests
cover spacing, cooldown, response concurrency, cancelled waiters and unsafe delays.
See [HTTP Retry-After semantics](https://www.rfc-editor.org/rfc/rfc9110.html#section-10.2.3).
This is process-local coordination, not a provider-account-wide distributed quota.
Already admitted requests cannot be recalled at the provider. Maintenance shutdown
now cancels local read-only REST futures, including semaphore waits, cooldown waits
and response reads. Cancellation drops the request permit and is reported as stopped,
not a provider failure. Closed control channels also stop acquisition. Cancellation
is latched; clearing a stop flag cannot restart that fetcher. Database publication,
recovery and checkpoint futures are not cancelled by this boundary. A completed page
still finishes its pending progress checkpoint before stopping. Four offline tests
cover pending-read cancellation, closed control channels, ordinary errors and shared
request-slot release. Full runtime wiring remains open.

Next: full strategy entry/position/exit lifecycle and its effective configuration,
alongside streaming/partition source parity and batched seed persistence.
Do not substitute the current fixed-noise
extractor for the full historical MLE seed pipeline.

The selected candidate now has a typed shared runtime binding for completed bars
and intrabar acquisition updates. It calls the same exit-first dispatcher and
prepare/journal/commit transaction in Live, Paper and Backtest. Scope pins a hash
of entry, add, protection, phase/failure, intrabar and recovery policies. The binding
hashes supplied causal features and reconciled position evidence itself. It rejects
configuration drift and disagreement between safety and position quantities or
pending-entry state. Reconciliation occurs before exit arbitration. No broker
capability exists in this component; outputs remain intents awaiting journal
readback and downstream OMS authorization.

The existing composed-candidate test now uses this binding for entry and intrabar
cancellation. It checks unchanged committed state before acknowledgment, exact retry
identity, policy drift and conflicting account evidence. All 171 offline tests pass.
This is not full source parity or complete intrabar position management. Production
admission, global safety producers, complete effective configuration export, event-loop
routing, crash recovery and OMS consumption remain open. Full borrowed feature arrays
are currently serialized for hashing; measured incremental fingerprinting is still
needed for the low-latency path. No network or service test ran.

The asynchronous strategy journal adapter now accepts both the generic transaction
runtime and the typed candidate runtime. It appends the pending batch, verifies
readback and only then acknowledges state. Errors or cancelled futures leave the
pending transaction available for an exact retry. Its database publisher requires
extraction and durability acceptance plus an exclusively borrowed local scope lease.
Every record must match that scope. Existing ClickHouse append logic validates the
table policy, actual part placement, predecessor and immutable retry slots.
Two offline tests cover ambiguous writes, incomplete readback, cancellation and
retry without recalculation. The real database path compiled but did not run.
This scope lock is cooperative and host-local; it is not account-level ownership
across different runs or cross-host fencing. Crash recovery, durable OMS handoff
and executable live/backtest consumers remain incomplete.

Committed entry/add decisions now translate into complete long bracket plans.
The translator binds account and instrument to the committed scope and derives a
stable command ID from decision ID plus action index. Stop and target come only
from the selected strategy action. Portfolio supplies quantity; the execution
quote authority must supply the limit price, scale and tick. Price conversion
rejects precision loss and overflow. Maximum-buy bounds are compared at a common
decimal scale without rounding them into an order price. Standard bracket checks
enforce direction, expiry, tick alignment and required buffered official LULD bands.

The composed candidate test now reaches this translator after journal acknowledgment
and checks account mismatch and missing regular-session LULD rejection. A separate
test covers exact price conversion failures. All 174 offline tests pass. This is a
plan, not broker authorization: cash sizing/reservation, reference-certified price
rules, latency revalidation, durable OMS persistence and submission remain required.
The selected candidate is long-only; this translator does not invent short strategies
or reinterpret exits/replacements as exposure increases. No service or broker ran.

Long bracket plans now connect to the shared portfolio cash-reservation authority.
Checked integer arithmetic converts total entry notional and nominal entry-to-stop
risk into currency minor units, rounding required amounts upward. Configured fee
reserves count against both order limits. Bracket validation runs before reservation.
The account mutex protects aggregate cash across ticker requests. Identical reservation
retries do not consume cash twice; rejected order limits do not mutate reservations.
The returned funding evidence includes the plan hash. It is not broker permission.
Two offline tests cover conversion, overflow, cash competition, exact retry and risk
limits; all 176 tests pass. Same settlement currency must be certified by the caller.
This does not implement FX, margin, automatic quantity sizing, durable reservations,
fill-to-balance reconciliation or OMS submission. Nominal stop risk is not a maximum
realized-loss guarantee. No service, database operation or broker request ran.

Whole-share quantity sizing now uses the same exact money arithmetic. It floors the
quantity against cash, nominal stop-risk, caller maximum quantity and lot-size limits.
Fees are removed from both budgets before sizing. The size-and-reserve path reads
the account snapshot, sizes a copy of the maximum plan, then uses the account-locked
reservation check. A concurrent cash change can reject the request; it cannot cause
an overspend. Successful plans must be retained for exact reservation retries.
Already reserved commands cannot be silently resized. One new test checks cash/risk
and lot limits over a range of budgets; the reservation test now covers actual sizing
and retained-plan retry. All 177 offline tests pass. Account mandate provenance,
currency certification, durable reservations and execution integration remain open.

The bracket now owns its required price scale. Its durable hash therefore binds
the interpretation of integer entry, stop, target and tick values. The duplicate
plan-level scale and the separate broker-adapter scale argument were removed.
Funding and broker serialization read the bracket's scale. Old bracket JSON without
that field is rejected; there is no inferred-scale migration. No operational data
exists from this implementation and no database migration ran. Broker prices now
serialize directly as exact decimal JSON numbers without a floating-point round trip.
Two offline tests verify scale-sensitive identity, rejection of missing scale and
decimal serialization beyond binary floating-point integer precision. All 179 tests
and static checks passed. Instrument tick/scale certification and actual broker
compatibility still require the outstanding integration acceptance.

A deterministic quote-touch execution model now consumes the shared bracket contract.
It owns a bounded single-instrument order lane, shares modeled displayed liquidity
across accounts in submission order and applies an explicit participation fraction.
Submission latency is simulated. Duplicate quote identities cannot create new fills.
Entries use marketable quote prices within their limit. Partial protective fills
cannot exceed held quantity. A triggered stop remains triggered after a partial fill
and cancels the remaining entry. Entry deadlines do not remove existing protection.
The model supports long and short bracket geometry without broker credentials or I/O.

Four offline tests cover shared liquidity, duplicate/conflicting quotes, partial stop
fills, price gaps, short targets, participation, latency, expiry and capacity. All 183
tests pass. These are explicit model assumptions, not broker behavior or realistic
queue evidence. Protective fills start no earlier than the quote after an entry.
Each new quote replenishes the modeled size budget; no trade-volume queue model is
claimed. Commission/slippage models, corporate actions, session/auction/condition
eligibility, cancel/replace simulation, journaled common execution-event integration,
checkpoint recovery and the full strategy backtest loop remain incomplete. Shared
OMS risk authorization must precede submission; this model only checks geometry.

Simulated acknowledged amendments now cancel remaining entry quantity or replace
complete protection. The immutable original bracket remains unchanged; active stop
and target prices are separate position state. Revisions must be contiguous and
exact retries must retain identical contents. Acknowledgments apply after the current
quote, so they cannot change earlier fills. Replacements require an open position
and cannot undo a triggered stop. A profit-lock replacement incompatible with an
unfilled entry requires entry cancellation first. Two offline tests cover these
transitions, invalid clocks/revisions and unchanged state after rejection. All 185
tests pass. The caller must still schedule modeled acknowledgment latency and apply
shared OMS authorization; actual broker cancel/replace behavior is unverified.

The quote-touch model is now version 2. An acknowledged strategy exit cancels the
remaining entry and closes only held quantity using subsequent shared quote liquidity.
Partial exits remain pending; a triggered protective stop retains priority. Component
checkpoints include model identity, ordered positions, active protection, amendment
revisions and quote frontier. Restore checks the pinned content hash, byte/order
bounds, identities, scales, quantities and protection geometry. It does not infer
missing fields or load another model version. Checkpoints are returned as bytes;
this component performs no filesystem or database operation.
Two offline tests prove partial-exit continuation matches checkpoint/restore for the
fixture, and reject hash/model/quantity corruption. All 187 tests pass. This is not
whole-engine recovery: market, strategy, portfolio, journal and simulator checkpoints
still need a single coherent run frontier, durable publication and replay integration.

Execution fills now have one versioned shared schema for broker-reported and
simulated origins. It carries account, command, instrument, exact scaled price,
quantity, buy/sell direction, leg, report availability and optional execution time.
Missing execution time is not fabricated. Broker identity uses session, account,
paper/live marker and execution ID; simulation identity uses explicit run/model,
command, account, sequence and leg. A bounded fill book accepts exact retries once
and rejects conflicting contents without overwriting prior evidence.
The simulator now emits this schema directly and requires an explicit production
run ID. Its model version is 3; checkpoints bind the run ID. One offline test covers
origin separation and duplicate/conflicting execution reports. All 188 tests pass.
Broker report normalization, correction/reversal handling, commission events, durable
fill publication and position/account projection remain incomplete. Shared schema
does not yet establish end-to-end live/backtest execution parity.

A shared fill-derived FIFO position projection now calculates open quantity, exact
cost numerator, gross realized P&L and signed trade cash in integer price atoms.
Origin/run or broker-session identity, account and instrument isolate each projection.
The price scale cannot change mid-projection. Exact fill retries do not count twice.
Conflicting reports, excess exits, opposing entries, clock rewinds, overflow and
capacity failures leave state unchanged. Position, fill-identity and per-position
lot budgets are explicit. Adjacent entry lots at the same price are coalesced.
Two offline tests cover FIFO partial exits, short accounting, duplicate identity,
scope/capacity limits and rejection atomicity. All 190 tests pass. This is a derived
projection, not broker balance or portfolio reservation authority. It excludes fees,
FX, settlement, margin, initial-position seeding, corporate actions and corrections.
Durable fill acknowledgment and reconciliation must precede live use. Bounded lots
are cloned for transactional validation; performance and durable recovery remain open.

Fill publication now has a bounded batch/commit path. Batches contain at most 256
reports and 4 MiB, share one position scope and preserve causal report order. The
ClickHouse publisher requires extraction/durability acceptance and an exclusively
borrowed local scope lease. It validates storage policy and part placement, inserts
missing immutable execution identities and verifies exact readback before projection.
Schema 007 defines the new execution-fill table; it has not been executed.

The committer retains its batch across ambiguous writes. Projection starts only after
verified readback. If a fill cannot be projected, the completed prefix count remains
visible and the remaining reports are retained; later batches must not overtake it.
Three offline tests cover publication failure, retry, partial projection failure,
mixed scope and incomplete readback. All 193 tests pass. The storage currently supports
identity lookup, not a complete ordered run catalog. Cross-host fencing, correction
events, crash recovery and engine-loop integration remain open. No database call ran.

The historical execution lane now owns the simulator, pending fills and shared
position projection. A quote generates fills once. Contiguous account-scope batches
then pass through verified journal publication before projection. Pending publication
blocks later quotes, submissions and amendments; an exact quote retry reuses pending
work. The caller selects a scope-owned publisher through the exposed next-scope hash.
Status reports retained fills and the applied prefix. Constructor limits must cover
the simulator's maximum possible fill count per quote.
Two offline tests cover the real simulator-to-journal-to-projection flow for two
accounts, ambiguous-write retry, blocked overtaking, strategy exit and capacity.
All 195 tests pass. Tests use an in-memory publisher, not ClickHouse. Market/V7 and
candidate scheduling, portfolio cash updates, coherent restart, byte-level memory
budgets and the executable backtest driver remain incomplete. No service ran.

The historical execution lane's production submission entry point now requires a
funded plan. It recomputes exact cash/risk requirements, matches funding evidence
and requires the matching reservation to remain held. Balance freshness and total
reserved cash are checked while holding the account mutex through the bounded
simulator transition. The direct raw-bracket shortcut exists only in unit tests.
One offline test covers missing/released reservations, changed funding, stale
balances and exact submission retry. All 196 tests pass. This adds no broker or
network capability. Durable account recovery, run/mandate provenance, fill-driven
cash updates and full strategy-to-execution scheduling remain incomplete.

Bracket plans now carry the originating decision scope: run, mode, account,
instrument, strategy instance and code/configuration identities. Funding verifies
that account and instrument agree with the bracket. The historical execution lane
accepts only Backtest plans whose run matches its simulator. Existing funding hashes
therefore bind this scope too. The reserved-submission test now rejects another run,
Live/Paper modes and account mismatch. All 196 offline tests pass. This contract
change has not been deployed or migrated. Full release provenance validation and
the shared strategy scheduling loop remain incomplete; no service ran.

Frozen-source validation was rerun after the execution integration changes. With
verified NumPy 2.4.4 and SciPy 1.18.1, all 70 extraction cases passed (44 selected
and 330 rejected levels). All 87 fit cases passed matching statuses and the existing
tolerance: max(0.001 ticks, 1e-8 of expected value). Maximum observed difference was
0.00140556 ticks. The loader now checks installed dependency versions before source
execution; an offline injected-version test confirms drift is rejected. No dependency
was installed. The 196 Rust tests, formatting, static checks and source hashes also
passed. These fixtures do not certify all-session fitting, consolidation, streaming,
full strategy decisions or trading robustness. No services or network tests ran.

The shared startup dependency planner now keeps requirements per instrument. It
propagates declared lookbacks, merges shared work, preserves disjoint intervals,
and records consumers and pinned implementation hashes. Missing definitions,
cycles, invalid scopes, epoch underflow and configured node limits fail closed.
Three new offline tests cover those cases and deterministic dependency-first plans.
All 199 Rust tests, formatting, static checks and copied-source hashes pass.
The plan does not certify readiness or start workers. Effective strategy declaration
export, coverage binding and startup repair/warming orchestration remain incomplete.
No service or network test ran. Source oracle parity was not rerun for this planner.

Startup source repair now connects the dependency graph to the verified acquisition
catalog and the existing maintenance Job contract. It subtracts only matching
authority coverage available at the check time. Missing intervals receive stable
resumable job identities. Channel/instrument/implementation mismatches and job
budget overflow reject the plan. Non-event dependencies remain explicitly unresolved.
Three offline tests cover missing intervals, stable retries, source revisions,
publication clocks, future ranges and invalid bindings. All 202 Rust tests,
formatting, static checks and copied-source hashes pass. No service ran. This does
not yet dispatch the startup jobs, certify derived requirements, warm the strategy
or grant trading readiness. Full startup orchestration remains incomplete.

The source startup coordinator now dispatches planned jobs through the bounded
maintenance pool. After each completed worker, it requires a verified certificate
from the supplied durable loader. Certificate identity, authority, interval and
publication clock must match before the catalog advances. It then recalculates
remaining source gaps. Worker and verification failures remain visible; neither a
completion label nor a coverage ID alone grants source completion. An offline test
covers matching publication and wrong-instrument publication through the real pool.
All 203 Rust tests, formatting, static checks and copied-source hashes pass.
Production worker/loader wiring, persisted startup recovery, derived materialization
and warming remain incomplete. Source completion is not trading readiness. No
service or network test ran.

The production source-startup binding now supplies the existing maintenance worker
and ClickHouse acquisition loader to the coordinator. Ordinary maintenance and
startup share one worker implementation, including lease acquisition, REST governor,
restart checkpoints, cancellation and status reporting. Startup checks extraction
acceptance, observer identities and per-worker recovery memory before dispatch.
One new offline test confirms a pre-cancelled worker returns before lease or
external I/O. All 204 Rust tests, formatting, static checks and source hashes pass.
The production binding was compiled but not executed. CLI/service startup wiring,
initial coverage discovery, derived materialization and warming remain incomplete.

Coverage now has a thin interval discovery index in schema 008. Publication writes
the verified certificate first, then the index, and checks the index readback.
Discovery filters by authority hash, overlapping interval and publication clock.
It rejects result overflow instead of truncating, reloads each certificate, checks
the full authority and interval, and verifies its event batches before returning
a catalog. The index is not coverage authority and stores no event payloads.
All 205 Rust tests, formatting, static checks and source hashes pass. The new decoder
test covers malformed identities, duplicate rows and capacity overflow. SQL and
database behavior remain untested; schema 008 was not applied. Existing unindexed
certificates need explicit index reconstruction before discovery can find them.
Multi-instrument startup discovery wiring, derived warming and service entry points
remain incomplete. No services were started.

Startup preparation now discovers coverage for every declared source interval and
instrument, merges verified catalogs under one certificate-count limit, replans
remaining repairs and creates their observers. Discovery supports cancellation
without dispatching writers. Catalog merge coalesces identical certificates and
rejects capacity overflow before changing the destination. One offline test covers
duplicate merge and unchanged state after overflow. All 206 Rust tests, formatting,
static checks and source hashes pass. Discovery queries remain unexecuted. Catalog
byte accounting, efficient range indexing at scale, derived materialization/warming,
CLI startup and full live/backtest execution remain incomplete. No service ran.

Shared bar construction now records causal watermarks even when no bar closes.
Previously an empty-interval watermark could admit an older event later. The
watermark is now monotonic, and bar volume/notional/count overflow fails before
state changes. A retained in-memory Series combines the same bar builder and MACD
for historical and live use. Completed bars advance indicators; developing previews
do not. Capacity exhaustion preserves prior state and never discards session bars.
Three new tests cover watermark boundaries, aggregate overflow and series parity
with the shared indicator implementation. All 209 Rust tests, formatting, static
checks and source hashes pass. The first static-check run found an unnecessary
binding; it was removed and full validation passed. V7 warming, certified handover,
series persistence and strategy scheduling remain incomplete. No service ran.

The market/structure bridge now initializes streaming V7 from its historical seed
and feeds completed one-second bars from the retained market series. Its clock
contract explicitly uses bar-end seconds. Session and observation clocks are checked.
Late events and calculation failures latch a recovery requirement and hide both
market and level projections. One offline test covers completed-bar advancement
and projection blocking after capacity failure. All 210 Rust tests, formatting,
static checks and source hashes pass. Full historical/live clock parity, certified
handover, coherent restart and strategy scheduling remain incomplete. No service ran.

Combined market/V7 recovery now captures bars, indicators, developing state,
watermark, V7 state and observation clock under one content hash. It binds the
seed and configuration, including split factor and evidence. Restore checks the
trusted expected hash before decoding and verifies component clocks and bar counts.
Failed runtime state cannot issue a checkpoint. An offline continuation test matches
subsequent hashes after restore and rejects wrong content, seed or configuration.
All 211 Rust tests, formatting, static checks and source hashes pass after correcting
the split-configuration serialization. Snapshots have a 64 MiB serialized limit;
temporary allocation accounting and durable publication remain incomplete. These
are streaming recovery snapshots, not historical seeds. Full-engine recovery must
also bind event cursors, strategy/portfolio state and pending execution. No service ran.

Normalized trade observations now feed the market/V7 bridge through a provider,
instrument and session boundary. A bounded identity map prevents retransmissions
from advancing calculations. Changed payload, SIP clock or eligibility for the same
key blocks the runtime. Duplicate receipts retain their separate ingestion/audit
path and do not rewrite calculation state. Recovery v2 includes the identity map.
One new test verifies deduplication after restore and failure on changed eligibility.
All 212 Rust tests, formatting, static checks and source hashes pass. Upstream
ordering, condition qualification, latency gates, quote processing and live actor
wiring remain separate integration requirements. Full strategy execution is not
complete. No service or network test ran.

The bounded event-order buffer now releases source-time order strictly before an
explicit watermark. Equal timestamps use provider sequence then event identity.
Pending retransmissions coalesce; changed identities, late arrivals and capacity
exhaustion block release. Rejected input remains owned by the caller. Consumer
failure retains the unacknowledged prefix for exact retry. Two offline tests cover
ordering, partial application, half-open boundaries and capacity/late failures.
The market bridge also accepts a separate processing clock without rewriting source
availability or receipts. All 214 Rust tests, formatting, static checks and source
hashes pass. Watermark production, already-released duplicate routing, queue recovery
and live actor wiring remain incomplete. No service ran.

The ordered market owner now joins the input buffer, eligibility records, retained
market series and seeded V7 runtime. It recognizes already-applied retransmissions
before watermark checks, and preserves original receipt clocks while using a shared
processing clock for a released prefix. Errors block all strategy-facing projections.
The normalized-input test now covers reverse arrival order through this combined
path, completed-bar counts, duplicate release and changed-eligibility rejection.
All 214 Rust tests, formatting, static checks and source hashes pass. The owner does
not yet restore its pending queue or establish live watermarks. Condition-policy
production, latency-gated actor wiring and full strategy execution remain incomplete.
No service ran.

The market/V7 owner now retains the qualified level projection from immediately
before its latest completed-bar update. The live lane exposes that snapshot with
its original boundary timestamp. Strategy consumers can distinguish prior levels
from levels confirmed by the newly completed bar. This retains one prior snapshot,
not an arbitrary historical query index.

Combined market recovery is version 4. It includes the prior projection and rejects
invalid geometry, duplicate IDs, future confirmations and invalid bounds. Offline
tests check prior-boundary timestamps before and after recovery and identical
subsequent recovery hashes. All 218 Rust tests, formatting, static checks and copied
source hashes pass. Source-oracle parity was not rerun for this change.

Per-event strategy scheduling remains incomplete. Releasing several events must
eventually evaluate each causal boundary, rather than evaluating only the final
state of the released batch. No service or network integration test ran.

Completed-candidate policy now pins a maximum completed-bar age at actual evaluation
time. At or beyond the limit, the journal receives a stale-bar wait or pending-entry
cancellation instead of calling the strategy calculation. Exit-priority arbitration
remains ahead of that callback. The completed input's event clock must match the bar
boundary, and dispatch rejects future event times. The composed candidate test now
covers both stale-bar outcomes; another test rejects future input before calculation.
All 217 Rust tests, formatting, static checks and source hashes pass. Full per-operand
freshness and handling fresh account updates after a bar close remain incomplete.
No service or broker integration test ran.

Candidate reconciliation now uses actual evaluation time while completed market
geometry retains bar-close coordinates. A broker snapshot known after bar close
but before evaluation is accepted; a snapshot from after evaluation is rejected.
Recovery observation clocks no longer rewind to the bar boundary. Duplicate-bar
handling retains valid account reconciliation. Existing candidate tests now verify
the delayed-account case and both recorded clocks. All 217 Rust tests, formatting,
static checks and source hashes pass. This does not certify all fill-timing scenarios,
full source parity or per-operand freshness. Full strategy integration remains
incomplete. No service ran.

Qualified V7 state now maps to the strategy TargetLevel contract. The projection
uses fitted centers and bounds, preserves segment confirmation clocks and historical
origin, and derives transition ancestry from prior roles. It rejects future state,
inconsistent segment geometry, missing qualified evidence and capacity overflow.
It never stamps levels with query time or marks them synthetic. Market and live
owners expose this shared mapping. One offline test verifies geometry, unchanged
confirmation times and failed/future-state rejection. All 218 Rust tests, formatting,
static checks and source hashes pass. Prior-boundary snapshots, local swings and
complete frame construction remain unfinished. No service ran.

Ordered-market recovery now includes the pending event queue, eligibility records,
release watermark and newest input receipt alongside the market/V7 snapshot. Restore
requires the expected content, seed, configuration and queue capacity. It rejects
duplicate/already-applied pending identities and inconsistent clocks. The combined
input test now checkpoints two out-of-order pending trades and verifies identical
post-release hashes after restore. All 214 Rust tests, formatting, static checks and
source hashes pass. Feed freshness and trading permission are never restored by
this snapshot. Durable publication, memory-allocation budgets and full-engine
recovery remain incomplete. No service or network test ran.

The audited live-market lane now connects decoder output to ordered market/V7
calculations. It requires matching live receipts and source scope, separates trade
eligibility from feed health, and rechecks the current shared exposure gate before
release. The watermark uses the lesser trade/quote source frontier minus a configured
lateness allowance; silence cannot advance it. Disconnect latches recovery. This is
a conservative operating assumption, not proof of provider completeness, and may
delay quiet instruments. One offline test covers both-channel progression and
monotonicity. All 215 Rust tests, formatting, static checks and source hashes pass.
The real live lane was compiled but not exercised against a feed. Representative
latency tuning, full actor dispatch, quote state and strategy scheduling remain
incomplete. No services or network integration tests ran.

The shared quote book now retains the latest source quote and compares decimal
prices without floating-point conversion. Executable access rejects stale/future
source or availability clocks, zero prices/sizes, locked quotes and crossed quotes.
Duplicate deliveries do not refresh age. Older updates cannot replace newer quotes;
conflicting latest identities block the book. The live lane retains quote state and
requires the current feed gate when exposing an executable quote. An offline test
covers mixed decimal scales, retransmission age, crossed updates and old/conflicting
input. All 216 Rust tests, formatting, static checks and source hashes pass. Raw
quote persistence, quote recovery, strategy frame wiring and account execution remain
incomplete. No service ran.

The retained market series now calculates completed-session volume/notional,
VWAP, high-of-day and prior high-of-day incrementally. Each completed bar keeps its
causal facts frozen; developing-session VWAP is a separate projection. Aggregate
overflow rejects the transition before commit. Existing series tests now verify
these values and that developing trades do not rewrite completed facts. Market
recovery is version 3 because its required state changed. All 216 Rust tests,
formatting, static checks and source hashes pass. Full entry-frame production still
needs admission, local swings, level mapping and explicit evaluation-clock handling.
No service ran.

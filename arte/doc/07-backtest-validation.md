# Backtest, debugging, and validation

## Strategy 350 and bar-first backtest design

Strategy 350 is the replacement candidate for the earlier starting strategy.
Its current source is a revision of Strategy 349. The stated intent is to
remove a duplicate historical Watchlist prior-close condition while retaining
the causal executor's fail-closed gate. The inspected `build()` body changes
descriptions but does not itself remove that rule. Verify the effective
configuration before porting or claiming parity. Freeze the
exact copied source, dependencies, effective configuration, and hashes before
porting. Later changes to Strategy 350 require a new pinned version and parity
run. Candidate number 350 alone is not an approval for live orders.

This section supersedes any event-by-event historical preparation plan where a
certified bar product can supply the same causal operands. The scanner, data
catalogue, and rule-set definitions select the smallest required universe,
sessions, columns, and timeframes before computation. The catalogue records the
source generation, calculation contract, coverage, precision, and knowledge
cutoff for each product. A missing required product blocks that ticker or run;
it never silently substitutes another source.

The first backtest execution grid is completed 100 ms bars. Read bounded,
columnar batches for many tickers and sessions from ARTE ClickHouse. Vectorize
stateless transforms, rolling indicators, scanner predicates, Watchlist rules,
market signals, and feature preparation across bars and independent tickers.
Derive larger timeframes from the same verified base only when their aggregation
contract is identical. Keep bar arrays contiguous and resident within the run's
resource budget. Query ClickHouse by range and required columns, not per bar or
per decision. The same plan must support several days without rebuilding
unchanged products.

Stateful levels, strategy decisions, account reservations, fills, and OMS actions
still advance in causal boundary order. Batch and parallelize independent ticker
work, then merge candidate actions through one deterministic market-time and
account ordering. Vectorization must preserve prior-only operands, equal-time
rules, order timing, and position-dependent behavior. A vectorized lookahead is
not an acceptable speedup.

Strategy 350 still contains trade-native rules, including an exact previous-trade
crossing for some reentries and quote-dependent purchase checks. A 100 ms OHLCV
bar cannot reconstruct the sequence of trades and quotes inside that bucket.
The port must list each rule's minimum input capability. Either a measured,
bounded event/quote refinement supplies those decisions, or the bar-only run
uses an explicitly versioned approximation and reports which decisions are
unavailable or changed. It may not claim event-level parity from bars alone.

The Strategy 350 eligible-trade session builder can consume a verified full
REST session in bounded batch order for after-close maintenance. Its acquisition
timestamps remain historical source-provenance clocks. They cannot be reused
as intraday receive times in backtest decisions. A separate causal bar/session
projection and narrow event refinement must supply backtest context.

Strategy 350 adaptive initial-stop distance now has one Rust state machine for
live completed one-second bars and bounded historical batches. It retains the
last five completed observations and a bounded rolling range distribution. The
90th percentile uses ordered sets with logarithmic insertion and eviction; it
does not sort the full session on each bar. Integer scaled comparisons preserve
the 10-cent minimum, 1.5 times two-bar range, 1.25 times session p90, and the
larger of 10 cents or 5% of entry as the cap. Legitimate empty seconds do not
create synthetic bars. This produces distance evidence only; structural stop
selection, tick rounding, bracket submission and effective-config parity are
not complete. Source provenance for this port is the SHA-256
`99150e27212ed0f15a0c28c739ccb9b6286be65f2a76ca1755f387d44423d1c1`
of the then-inspected strategy executor and
`0541a69a375bd77901ef6ad92725a47abd9d49488b4cc4312e9ed1ea0399d80f`
of the Strategy 350 candidate builder. These are audit references, not runtime
imports or an assumption that the effective configuration is frozen.
The rolling noise state now has a bounded, immutable recovery object. It pins
the configuration and session, saves only the recent bars and bounded range
history, and rebuilds its percentile index on restore. Restore verifies bar
ordering, sample counts, range IDs, the latest five-bar range and canonical
bytes. This object is not yet included in the live lane common-cut root.

The first typed Strategy 350 catalogue plan requests completed 100 ms bars,
the early squeeze signal and reference data for screening. Watchlist membership
is requested only when the pinned activation policy requires it. Inspected
Strategy 350 source sets `watchlist_policy=not_required`, and its activation and
signal-dispatch paths bypass membership under that policy. The plan requests raw
trades and quotes only for selected candidates,
alongside forming MACD timeframes, VWAP, prior close, LULD and historical V7
dependencies. This is a requirement plan, not a scanner evaluator or trading
loop. Each requested product needs a pinned catalogue definition and coverage.

The first Strategy 350 bar screen reads verified contiguous OHLC columns. It
emits a compact per-bucket refinement mask, not entry decisions. It can reject
an empty bucket, a ticker with prior close outside the configured range, or a
bucket wholly outside the late-mode prior-HOD zone when that mode was already
active and the bucket made no new high. A bucket that makes a new high remains
eligible for event refinement: bar OHLC cannot show whether a pullback followed
that high. The same uncertainty applies when late mode first triggers inside a
bucket. This is a bounded columnar prefix pass, not yet measured SIMD or a
complete vectorized scanner/signal/Watchlist implementation.
The first shared Boolean product readback now records explicit evaluation
cadence, known/unknown state, value, source-bar identity and complete bucket
coverage. Strategy 350 intersects the verified signal with the bar mask. It
also intersects Watchlist membership only when the pinned policy requires it.
Products must match generation, ticker, session and grid.
Unknown on a bucket that otherwise needs refinement blocks the run; false is
not treated as missing. A shared fixed-cadence producer now accepts one
explicitly timed evaluation result per due boundary, binds it to a complete
100 ms bar source, carries state across non-evaluation buckets, and hands the
result to the sparse ClickHouse publication contract. An omitted or shifted
evaluation fails. Unknown remains distinct from false. This is calculation
output plumbing. The first Strategy 350 historical signal formula is now ported:
a non-empty completed 100 ms bar must rise at least the pinned basis-point
threshold from the prior non-empty bar while trade count and share volume both
increase. The first occurrence activates the session-watch state through the
remainder of the certified session. The rule is computed with exact integer
comparisons, and its source-algorithm hash is part of the calculation identity.
This does not implement all live episode roles, Watchlist formulas, or connected
ClickHouse publication. The new signal's historical occurrence time is the
completed bar end; it is not represented as a live receipt timestamp. Source
parity remains open because the existing live gateway stamps its occurrence
from the last trade event inside the bar.

The initial formula was inspected from the parent repository at ARTE port time.
This is provenance only, not a runtime dependency: `services/qmd-gateway/src/signal_stream.rs`
SHA-256 `bc268dd23900cd3ac255d92c5fd342ac6b630fb09344fe2ba34a79eabac838b8`
(last source commit `e9943e6580c4b2aacc6b40ef74cdf497dfe46341`), and
`src/backend/trading_configuration_service.py` SHA-256
`aba08317de64470f3eafb17c8c9776a427078d6807433440fbc6fa76159289a9`
(last source commit `e5dc302163d026df85093911b048193c4232d4ae`).
ARTE runs from its own Rust source and a pinned formula configuration after
extraction; it does not load either origin file.

The signal state now has explicit historical and live modes. Historical
projection records the completed-bar end and leaves availability absent. Live
evaluation requires an actual availability time no earlier than that bar end
and keeps it distinct from the signal event time. A small immutable recovery
object pins the source scope and formula hash at state creation, then verifies
both before restore. The live recovery image joins the scheduler, candidate
features, exact developing 100 ms bar, and signal state at one pending boundary.
Restore requires that exact root and boundary; an independent latest signal
snapshot is not a valid live recovery cut. The ClickHouse adapter writes
content-addressed children before one root slot. It rejects conflicting slots
and requires the approved SSD policy and actual part placement. Migration 023
is authored but has not been applied; no connected read/write has been tested.
Live dense-empty-bucket advancement and feed-continuity proof still need
the MDE-to-signal actor; this state object alone does not authorize trading.
The current ARTE market scheduler exposes floating-point bars. The signal
formula uses exact integer compact-bar atoms; converting those floating-point
bars back into price/size atoms would not prove equality at the 0.05% boundary.
An exact compact 100 ms bar builder now consumes pinned-scale eligible ordered
trades and an externally certified event-time watermark. It computes integer
OHLC, share volume and notional, retains the real live receipt separately, and
reports covered empty intervals without creating false bars. A unit test feeds
those advances to the same Early Squeeze state across an empty bucket. No
floating-point reconstruction is approved for parity. The live lane binds the
builder to scheduler boundaries and can capture its in-progress state in a
common recovery cut. The ClickHouse publication adapter is authored but remains
unapplied and untested against a database. Proof that the external watermark
certifies empty intervals remains open.

The first market-time tape borrows values from complete 100 ms columnar batches.
It dispatches only nonempty buckets, sorts equal-time ticker events by instrument,
and rejects overlapping products for one instrument. Empty buckets remain in
the verified batch arrays for rolling calculations and clock progression. The
tape alone does not evaluate Strategy 350 or simulate orders.
The tape now has three explicit traversals: sparse nonempty candidate buckets,
every completed 100 ms boundary, and completed aligned fixed-interval
boundaries. Empty boundaries carry clock progress but no fabricated trade or
price. An event-cadence computation cannot run from bars alone; it requires
verified event input. The fixed-boundary traversal does not yet invoke scanner,
signal, Watchlist, or strategy evaluators.

Historical V7 construction may consume certified completed bars and publish
versioned level books and next-session seeds. Live V7 consumes the streaming
market path. Both implementations must share level identity, state transitions,
causality rules, and tested decision semantics. A historical full-session fit
cannot be substituted for an intraday streaming state. Historical bar-based
level books, signal products, bars, and indicators are read from ARTE ClickHouse
when their pinned contracts and coverage are ready. Missing dependencies are
built through maintenance and published before the backtest uses them. Streaming
state continues in memory and persists the required audit and recovery products.

Measure full-session preparation time, bars per second, peak memory, ClickHouse
bytes read, and end-to-end backtest throughput on representative multi-day runs.
Raise the base timeframe only after measurements show 100 ms is insufficient and
parity tests show what events, signals, entries, exits, and P&L change. The chosen
timeframe is part of run identity. Coarser bars are an explicit fidelity tradeoff,
never a silent fallback.

Historical bars lack live receive-time evidence. Rules requiring measured feed
latency or original arrival order are unavailable in Historical mode unless a
separate recorded-live source supplies them. The run reports that limitation.

All backtest journals, compact diagnostic logs, source and calculation manifests,
checkpoints, decisions, orders, fills, and metrics persist in the dedicated ARTE
ClickHouse database. Use typed codes, run and boundary IDs, shared dictionaries,
bounded payloads, partitioning, and tested ClickHouse compression codecs instead
of repeated verbose JSON. Preserve enough causal evidence to debug and reproduce
each decision. SQLite is forbidden for both the run spool and durable logs.

## Shared core

Use the same event normalization, bars, indicators, V7 streaming algorithm, detectors,
strategy, portfolio rules, and OMS contracts in Live and Backtest.
Inject source, clock, execution adapter, and persistence interfaces.
Do not build a second strategy implementation for historical runs.

Historical V7 extraction is a shared historical module. It prepares seeds for both
future Live sessions and historical runs. It is not the intraday streaming updater.

## Shared completed-bar admission

The feature owner can now assemble the market-derived portion of entry admission
for its exact pending one-second boundary. It uses its retained MACD reading,
activity evidence and causal V7 level snapshot. The detector fingerprint binds
the feature/market configuration, source scope, completed timestamp and qualified
level geometry. An empty qualified book does not create synthetic levels.

Permissions, session-open state, tradability, additional encounter blocking and regular-hours
restrictions remain explicit inputs from their own authorities. The assembler
preserves them; it does not infer permission from indicator readiness. Future
authority timestamps, non-one-second boundaries and snapshot mismatches fail.
Existing entry-evaluator age limits still apply at decision time.

Live and replay expose this same assembler. External-authority integration and
swing evidence remain required coordinator work.

### Boundary-owned encounters

The shared encounter stream consumes scheduler boundaries in sequence. One-second
completed bars use the market owner's prior level view and previous completed bar.
Quotes and excluded trades do not consume next-opening-trade evidence. Exit reasons
apply to one boundary; warning and failure state survive subsequent boundaries.

An identical retry is a no-op. Changed retries, skipped boundaries, clock rewinds,
scope mismatches and future prior-level views fail closed. Failed owners expose no
state. The restriction adapter requires the exact observed boundary and only adds
entry blocking or encounter exits. It never grants account or session permission.

Offline tests cover real scheduler progression and synthetic encounter warnings.
They do not prove live readiness or strategy profitability.

Encounter recovery produces a bounded immutable object. It pins the exact market
image, encounter configuration, run context and observed boundary. Restore checks
the content hash, canonical encoding, geometry, thresholds, causal timestamps and
snapshot consistency. It retains warning opening-consumption state and failures.
Genesis is recreated from configuration; a failed owner cannot publish an image.
These are streaming recovery objects, never historical V7 seeds.

Tests restore at every scheduler boundary and compare exact checkpoint bytes.
Synthetic warning recovery also checks identical next-opening-trade behavior.
The shared feature owner now owns encounter state in live and replay. Required
encounter and swing settings participate in feature configuration identity version 4 and
therefore effective strategy identity. Entry and encounter tick sizes must agree.
Missing settings fail loading; no implicit defaults replace them.

Feature recovery version 3 embeds encounter and local-swing objects. Existing candidate and
whole-run recovery graphs carry it through their feature child. Old feature images
are not silently upgraded. New runs must pin the new configuration and manifests.

Completed admission and intrabar acquisition include internal encounter blocking.
Entry-frame validation rejects attempts to omit an active internal restriction.
Replay boundary wrappers and the live completed-candidate wrapper merge encounter
exits into external safety before dispatch. The lower-level candidate evaluator
remains an injected-evidence interface for isolated algorithm tests; production
orchestration must use boundary-owned wrappers.

Executable loops and complete live orchestration remain unfinished. Connected
checkpoint publication and operational recovery have not been run.

## Local swing evidence

Strategy-local swings are not V7 fitted levels. The Rust local directional-change
component ports the local-scale behavior of the frozen `swing_structure.py` at
the same commit as the strategy reference. Its pivot and confirmation clocks
remain distinct and map to completed-candle end times. Lifetime uses candle
count, matching the source detector's sequence clock, not elapsed wall time.

The component retains anchored geometry, prior-only volatility, frozen reversal
thresholds, accepted breaks and later retest-based role reversals. Its strategy
snapshot contains pre-candle active swings plus newly confirmed swings in source
order. It resets on gaps without inventing a continuity certificate. Errors hide
partial state; configured capacity is never enforced by truncating levels.

Major-scale visual swings, touch scores and visual segments are not consumed by
this strategy projection and are omitted. IDs use an ARTE local namespace rather
than mixed local/major source numbering. This is a separately versioned local
projection, not a port of the entire structural detector.

The offline comparison checks geometry, clocks, roles, activity and ordering on
3,000 candles across six deterministic paths, including gaps and sub-dollar data.
It does not prove trading parity or profitability.

The shared feature owner computes local swings on completed one-second boundaries.
Required swing settings are part of effective configuration identity. Completed
snapshots expose a boundary-checked borrowed swing array. Configured candidate
entry and live completed-candidate preparation now use `OwnedEntryContext`, which
has no swing input. Replay resolves it through `prepare_owned_completed`; live
uses the shared owned-frame assembler. Neither path copies the swing array per
account or permits a caller to substitute it.

Explicit-evidence entry APIs remain for isolated algorithm and execution tests.
They are not the configured production path. Owned assembly retains external
permissions, session evidence and recovery state; having a swing grants no
permission. Tests check borrowed-array identity and reject mismatched boundaries.

Bounded recovery preserves extremes, thresholds, rolling volatility, anchored
levels, pending retests and projections. It pins scope, configuration, context and
the independently selected last consumed candle. Higher-timeframe closes precede
one-second closes with the same end time; restore excludes that unconsumed candle.
Genesis requires an empty state. Hash, canonical-byte, clock and state checks fail
closed. These recovery objects are not historical V7 seeds.

Certified-empty-gap handling and executable orchestration remain unfinished.

### Verified empty-trade evidence

Explicit indexed source loading verifies each trade batch once and builds an
exact per-second occupancy array. Its configured capacity is at most 172,800
seconds, separate from the retained observation byte budget. Quote sources cannot
create this evidence. Unaligned or over-budget requests fail without fallback.

Only complete successful verification returns an index. An empty-span proof binds
source certificate, provider, instrument, session, half-open interval and source
publication time. Every recorded trade occupies its second, including trades a
calculation policy might exclude. Silence, sequence gaps and eligibility decisions
cannot manufacture empty coverage. Proofs are not deserializable authority; rebuild
them from verified source batches after recovery.

This certifies recorded checks for one source revision, not absolute provider
completeness. Source knowledge time must not be confused with simulated session
time. The plain loader grants no gap authority. Fresh backtest bootstrap now uses
indexed startup with a 172,800-second maximum; alignment and capacity failures
reject startup instead of falling back.

Historical projection can bind an empty-span proof to the pinned backtest run,
source catalog, certificate and explicit delay model. Its provenance retains both
actual source publication time and modeled availability at interval end plus the
pinned delay. This does not rewrite source facts or create a live receipt.
Creation rejects unindexed sources, changed pins and unavailable source evidence.
Consumption checks run identity, scope, interval and modeled clock. The token is
historical-backtest-only and cannot authorize live or recorded-live continuity.
Wiring it into swing continuity remains required.

## Replay modes

| Mode | Input | Claim |
|---|---|---|
| Historical | Pinned reconciled REST generation | Behavior under available historical fields |
| Recorded-live | Original observation sequence and field availability | Reproduction of observed decisions |
| Fault simulation | Pinned input plus explicit fault schedule | Behavior under modeled failures |

Historical data cannot prove original local receive latency. An unavailable check is
reported as unavailable, not passed. Synthetic delays are labeled as simulated.
Corrections and REST enrichment must not appear before their declared availability.

## Run identity

Certified REST loading preserves acquisition-time availability. That timestamp is
not automatically the historical simulation clock. Replay preparation must pin an
explicit projection, preserve source provenance and label modeled availability.
It must not invent a historical receive or execution timestamp. The certified
source loader and a retrospective clock projection are implemented. The
executable backtest loop is not yet connected to them.

### Historical source startup

The read-only startup assembler accepts exact trade and quote certificate IDs,
the trade-policy hash, scope, session interval and source knowledge cutoff. It
checks both certificate manifests before reading event batches. Policy loading
uses session start as its knowledge cutoff, not the later REST acquisition time.

The ClickHouse metadata-only certificate read does not return verified coverage.
The existing source loader verifies every referenced batch while retaining its
observations. This avoids downloading batches once for verification and again
for replay. Both fully verified sources feed the shared policy and timing
projection. An error returns no partial input and performs no writes.

Metadata reads run concurrently. Channels load sequentially, with bounded batch
concurrency inside each channel. Source byte/event limits apply separately to
each retained channel. Prepared-input limits apply to their combined projection.
Peak memory includes both source channels, prepared input and bounded in-flight
batches; the serialized-byte limits are not total process-memory guarantees.

### Retrospective projection contract

`arte.historical-projection.v1` pins both acquisition certificate IDs, the session,
the fixed modeled delay, the trade-eligibility policy hash and every eligibility
decision. Its hash becomes the historical catalog's authority identity. The
prepared input also pins the model identity and projected observations.

Preparation requires one trade certificate and one quote certificate for the same
provider, instrument and exact interval. It does not choose revisions or merge
overlapping acquisitions. Every trade needs an explicit eligibility decision.
The low-level adapter checks complete event-key coverage, not the correctness or
approval of the caller's eligibility policy. Production preparation can instead
use `prepare_with_policy`, which evaluates the shared pinned trade-condition
policy and includes its identity in the projection. The policy must cover the
source interval and be available at its start. REST acquisition time cannot make
a future policy available earlier.

`arte.trade-eligibility.v1` has disjoint allowed and excluded condition sets plus
an explicit empty-condition rule. Any unknown condition fails evaluation, even
when another condition would exclude the trade. All marked corrections remain
unsupported by this projection. The live market lane uses the same evaluator;
its ingestion API no longer accepts an arbitrary eligibility boolean.

This implements the existing all-or-none calculation eligibility contract. It
does not implement independent provider-specific OHLC and volume update rules.
Provider rule certification and executable startup wiring remain required.
Hash identity alone does not approve the condition mapping.

Trade and quote policies share one ClickHouse persistence protocol. Reads require
provider, exact policy hash and knowledge cutoff. Canonical payload checks reject
conflicting versions, wrong providers and future policies. There is no implicit
latest-policy lookup. Publication requires extraction and durability acceptance,
cooperative ownership and exact readback. Table policy and actual part placement
are checked through the shared storage verifier. This is not a distributed
compare-and-swap protocol or proof of power-loss durability.

Migration 020 defines `trade_eligibility_policies_v1` with `live_market_ssd`.
It remains unapplied. Connected policy publication has not been tested.

Only a simulation copy receives `available_at_ns = sip_ns + delay_ns`. Source
objects are unchanged. Receive and participant timestamps remain absent when
absent in the source. The delay must be positive and no greater than one second.
This path creates `Historical` input, never `RecordedLive` input. Do not persist
these modeled observations as acquired market data.

Ordering is SIP time, provider sequence, then full event key. Equal-SIP groups
are admitted in chunks of at most 256. Their watermark advances only after the
last chunk. The scheduler must have capacity for the whole tied group. The final
empty frame advances to the certified interval end. Empty certificates create
no invented trades or quotes. Event, frame and serialized-byte limits fail
preparation rather than truncate input. These are not total process-RAM limits.

Duplicate event identities and all explicitly marked trade corrections are
rejected. No numeric correction code is assumed to mean an ordinary trade.
Supporting those records requires a separate causal correction contract. A
provider's final REST revision still cannot prove the original as-known tape.
Historical results must disclose that limitation; modeled timing is not evidence
of actual websocket latency or executable historical fills.

Pin code release, strategy/configuration, algorithm versions, source generation,
reference generation, historical seed, clock model, fill model, and cost model.
Persist input capabilities and declared approximations.

The run contract includes decision, intent, rejection, order, fill, position, and
account-state schemas. Live and historical use the same schemas. Mode and execution
origin differ explicitly. Compare semantic hashes without wall-clock log timestamps
or broker-generated IDs.

## Fresh-session startup document

### Market-owner assembly

`arte.backtest-market-startup.v1` pins the run identity, market configuration,
split adjustment, quote policy and bounded scheduler settings. Its reader accepts
at most 1 MiB and requires an independently supplied expected hash. It never
discovers configuration or seeds from the environment or parent application.

The assembly path verifies the source catalog and prepared input. Input timestamps
must belong to the configured session interval. Its terminal watermark must equal
the interval end. This domain check is not a source-completeness certificate.
The quote policy must cover the interval and be available at session start.

For this single-instrument assembly path, the run's `seed_manifest_hash` is the
canonical content hash of the supplied historical seed manifest. It is not the
seed's own ID. The seed object graph is hydrated and verified before constructing
the shared market/V7 runtime. Existing seed causality and split checks still apply.
The resulting account run starts paused with no admitted events.

Pass that run to the fresh-session publication path below. Connected loaders must
still prove seed publication and acquisition durability. This constructor neither
publishes data nor authorizes strategy activation. It does not yet assemble a
multi-instrument portfolio run. The executable coordinator remains unfinished.

### Strategy and account inputs

`initialize_backtest` connects these startup stages for a fresh historical run.
It checks extraction/durability acceptance, ownership and startup-document identity
before loading data. The read-only bootstrap verifies market/source scope, loads
the exact historical seed, checks its manifest binding, loads certified sources
and assembles the paused market run. It never edits the pinned run manifest to
accept changed inputs.

The existing session constructor then validates strategy configurations, simulated
accounts and model bindings before startup publication. Exact publication readback
precedes returning the paused session. Initialization does not resume playback,
restore a checkpoint or contact a broker. Source input and the seed remain
available for recovery assembly. The market-startup document still needs its own
durable run-plan binding; the executable loop remains unfinished.

`arte.backtest-startup.v1` binds the run manifest hash, effective configuration
map, initial simulated accounts, price precision, fill model, cost model and
resource limits. Startup requires its independently supplied expected hash.
An accepted hash proves content identity, not strategy approval or broker cash.

The JSON document is limited to 16 MiB. Account and configuration maps contain
at most 4096 unique entries each. Duplicate keys and unknown top-level fields
are rejected. Field ordering and whitespace do not change the semantic hash.
The session constructor subsequently checks effective market/policy bindings
and account compatibility. It returns a paused session.

The startup adapter persists content-addressed 1 MiB chunks and publishes a
root only after chunk readback. One immutable slot per manifest prevents changed
startup inputs from replacing the original. Exact retries are idempotent.
Publication requires extraction and durability acceptance plus cooperative
ownership. The adapter is not a distributed compare-and-swap authority.

Migration 019 is unapplied. Run orchestration must publish this document before
advancing the session and retain its expected identity in recovery metadata.
`create_backtest_session` validates a fresh session before publishing and returns
it paused after verified startup readback. On failure, rebuild the unused prepared
run and retry the same document. Do not use fresh creation as checkpoint recovery.
Common-cut root version 2 pins the startup identity for sessions. Restore requires
the matching document and configuration/cost bindings. It restores current
portfolio state; it does not reset balances from the initial startup document.
Version 1 common-cut roots are rejected without implicit migration. Component-only
graphs may omit startup identity, but cannot be restored as complete sessions.
`load_backtest_session` independently reads the persisted startup document before
restoring the checkpoint. It also reads sizing-rejection journal records in
batches of 256. Each must match the retained decision/action evidence exactly.
Missing or conflicting records fail loading; this read-only path never inserts
missing records. Verification does not release funds or re-run strategy logic.
The session returns paused. The executable loop remains
unfinished. Storage readback does not prove strategy acceptance or power-loss
durability.

## Explicit simulated costs

The initial cost contract is `arte.per-fill-costs.v1`. Its hash must match the
run manifest. Playback rejects a cost binding from another manifest, even when
the run name is the same. No model is selected implicitly.

The model declares currency, currency precision, fixed charge per fill, per-share
rate and minimum charge per fill. Variable charges round upward to currency minor
units. Add the fixed charge, then apply the minimum. Each partial fill is charged
separately. Cancelled orders with no fills have no modeled fill charges.

These are hypothetical execution costs, not an IBKR commission schedule. The
contract does not model order-level minimums, rebates, taxes, FX or settlement
delays. A zero-cost experiment must explicitly pin zero rates. It is not evidence
of realistic trading performance. Account/instrument currency compatibility must
be certified before modeled cash settlement.

Charges accumulate only after fill-journal readback and position projection
succeed. Exact publication retries must not add fees twice. Gross P&L remains
distinct from costs. Net cash conversion rounds positive fractional minor units
downward and negative fractions toward minus infinity before subtracting fees.

## Fast repeated runs

- Prepare source arrays once and reuse immutable data by dependency fingerprint.
- Cache indicators and structure only when all their dependencies match.
- Parallelize independent instruments and parameter runs.
- Preserve ordered account reservations across ticker candidates.
- Support seeded deterministic randomness where the fill model requires it.
- Keep preparation, playback, journaling, and UI publication timings separate.
- Do not skip required pre-admission history to improve apparent speed.
- Do not capture full engine snapshots on ordinary status requests.

Historical replay should use explicit merged-event ordering and bounded lookahead
buffers. A display interval must not change decision semantics.

## Debug evidence

Every decision references its causal input boundary, feature values, V7 seed/state,
account state, rule results, and chosen action or rejection reason.
Deduplicate immutable evidence blocks. Do not dump the entire book per decision.

Debug supports pause, step, inspect, and resume for Backtest. Live inspection is
read-only unless an explicit authorized control command is submitted.
Saved Review reads published evidence. It does not restore the execution engine.

## Required tests

| Gate | Required evidence |
|---|---|
| Event codec | Precision, fractional sizes, conditions, source capabilities and round trips |
| Cross-transport identity | REST/WS overlap, duplicates, resets and corrections |
| Causality | Prefix invariance; no future seed, metadata or quote leakage |
| V7 port | Fit tolerance, level identity, transitions and discrete decision parity |
| State recovery | Continuous run equals checkpoint restore plus replay |
| Gap repair | Restart-safe pagination, overlap, derived rebuild and no stale orders |
| Portfolio | Concurrent tickers/accounts cannot overspend or share fill state |
| OMS | Duplicate submits, unknown replies, partial fills and bracket recovery |
| Isolation | Backtest cannot access live control/credentials or exceed resource budgets |
| Performance | Full-session and burst latency; cold and warm backtest throughput |
| UI independence | Closed/disconnected UI does not change execution results |
| Packaging | Build and test after copying only this project root |

Numerical tolerances do not permit unexplained changes to a trading decision.
V7 optimizer changes require both numerical and discrete-state comparison.

## Strategy acceptance

Use diverse sessions, realistic costs, unseen holdouts, and retained failed experiments.
Do not promote from a familiar ticker or a short profitable interval.
Correct engine behavior does not establish a profitable or robust strategy.
Live activation needs a separate explicit approval after engineering gates pass.

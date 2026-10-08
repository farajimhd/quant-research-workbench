# Native causal channel producer contract

`native_causal_channel_contract.py` declares the versioned normalized tables
`arte.native_causal_channels_v1` and `arte.native_causal_channel_coverage_v1`.
The producer reads certified, pinned `arte.bars_v1` attempts only. Its public
function produces typed Arrow packets and content witnesses; it performs no
installation, publication or execution admission.

Declare native source resolutions, a native decision interval, freshness for
each resolution, and prior-bar participation and volatility lookbacks through
`NativeChannelPolicy`. All parameters contribute to the policy digest. Rows
retain absolute native integer OHLC and participation alongside v4 returns,
relative volume/trade counts, volatility-normalized shapes and source timing.
Source clocks are milliseconds since New York source midnight. Current candles
are excluded from their own trailing baselines. Gaps, invalid prices and zero
baselines yield null relative channels; no future labels are accepted. Earlier
regular-session source remains available for AH normalization. This product
does not replace AH's separate prior-day V7 checkpoint and complete RTH warming.

Requests are bounded to eight tickers and two million possible source rows.
The source SELECT has an overflow sentinel and honors the reader's configured
resource policy. Normalization uses Polars expressions. Small loops seal
ticker/resolution partitions; they do not implement sequential market decisions.
Coverage includes each requested ticker/resolution, including zero source rows,
and pins the bar source/output hashes, market token, producer-source identity,
typed content hashes, row counts and completed boundary. Missing market source
certification is fatal before the source read.
The bar source hash is SHA-256 text; the existing certified bar output hash is
canonical decimal UInt64 text for `sum(cityHash64(tuple(*)))`. These distinct
domains are preserved exactly. Feature and coverage content seals use SHA-256
over declared schemas, null positions and canonical Arrow streams of valid
values. Arbitrary hidden bytes beneath nulls and unused bitmap padding are
excluded; every valid Float64 bit, including signed zero, remains significant.

`native_channel_campaign.install` is an explicit producer-only API. Publication
uses `NativeChannelInsertAuthority` under a separate Keeper namespace, reusing
the shared durable dispatch protocol. Unknown INSERTs retain a closed gate;
an explicit trusted original-terminal resolution is required before exact
missing-row resumption. Existing unregistered rows cannot be adopted. Child
readback and actual SSD-part checks precede coverage, and both typed tables
must match before the producer marks its completion fence.
The publisher requires separate read-only and producer transports. The dedicated
`native_causal_channel_reader` has SELECT on three market-source tables, both
feature tables and four storage catalogs. `native_causal_channel_producer` has
INSERT on the two feature tables only. Neither modifies existing Backtest role
grants. The operator command is `python -B scripts/clickhouse/provision_native_channel_principals.py
--apply`, executed on the managed workstation after source synchronization; it
installs only the new product layout and reconciles those two narrow principals.
Private credentials stay in the workstation secret root and are never rotated
or replaced implicitly. The command is a dry run without `--apply`.
`open_native_channel_keeper` authenticates the two feature-specific principals
with digest ACLs. The owner has all namespace permissions; the reader has READ
only. Installation verifies exact private ACLs, never replaces existing ACLs,
and preserves common Keeper paths and other products. READ-only ACL inspection
accepts masked digest hashes by exact principal names and permission bits,
without granting the reader ADMIN.

Installation remains a separate campaign operation: verify schema and
`live_market_ssd` policy, insert immutable child rows, verify actual SSD parts,
publish coverage last, and read back exact typed hashes. A native consumer must
then declare this dependency, obtain read-only grants, and fail preflight on
missing or mismatched coverage before a new numbered strategy can consume it.
`backtest_native_channel_store.read_installed_native_channels` performs SELECTs
only and requires the typed source plan, certified market, SSD placement, exact
rows/coverage hashes and completed producer fence. Its feature read uses the
whole bounded attempt inventory, exposing extra rows rather than filtering
away mismatched identities. The declared table DDL does not itself verify
installation. A bounded August 4, 2026 development pilot for AMIX and ATPC
through 09:30 ET is installed: 309 feature rows and four coverage rows passed
exact native readback, active SSD placement, private Keeper completion and
negative reader/producer permission checks. Its immutable runtime receipt is
`D:\TradingML\runtimes\strategy-optimization-20261005\native_channel_installed_actual_v1\receipt.json`
on the workstation, with a laptop copy named `native-channel-installed-actual-v1.json`.
This is not full-session, full-universe coverage or a financial strategy run.

`load_declared_native_channels` is the SELECT-only dependency loader for an
explicit typed request, feature attempt and declared producer-source hash.
It reconstructs content hashes from installed child/coverage rows, verifies
certified source and SSD placement, and requires that exact reconstructed
projection token to match the completed producer fence. The caller supplies
no feature-content witness. This does not register a native release dependency
or certify a trading population. A future financial strategy must declare
coverage for its causal native candidate population; the hindsight-selected
research cohort below cannot define that population.

`native_channel_qualification.py` supplies the generic declared
`native-completed-channel-qualification@1` rule. Typed bands select relative
channels, native resolutions and finite inclusive bounds; the complete input
policy and bounds are included in the rule payload. The columnar qualifier
intersects with the original mandatory Boolean mask, preserving decision-row
order. Missing, stale, invalid and nonfinite channels cannot qualify; future
or inconsistent availability is rejected. It changes no cash, reservations,
fills or protection and performs no source reads. It must be selected and
bound to installed feature inputs by a future immutable strategy declaration;
adding this helper does not change any existing strategy or publish one.
`parse_native_channel_qualification_policy` and `parse_event_qualification_policy`
reconstruct complete JSON declarations for a future installed configuration.
They require explicit lookbacks, resolutions, freshness, bounds and event
semantics, and verify the input-policy digest. Missing parameters receive no
defaults; altered source/clock semantics, numeric type aliases and undeclared
fields and coercible non-JSON objects fail. Parsing establishes rule contents only, not source certification
or financial admission. The parser, entry qualifier, event qualifier and
installed-input adapter suites passed 56 tests in 3.45 seconds; their log is
`native-policy-parsing-tests-v2.log` under the development runtime root.

The saved post-acquisition development audit uses hash-verified installed
feature artifacts and actual acquisition microseconds. It examines completed
bars strictly after acquisition and before each saved exit. On 126 Strategy57
positions across 12 fully covered development dates, negative 30-second close
returns occurred before 36/71 losing exits and 20/55 winning exits, including
three of the ten largest winners. Requiring relative volume below one reduced
these to 20/71 and 13/55, including two of the ten largest winners. Negative
one-minute returns occurred before 16/71 losses and 8/55 wins. These findings
do not establish a new exit rule or saved-loss avoidance: no new quote fills,
cash release, replacement admission or candidate P&L were calculated.
August19's seven saved positions were explicitly excluded because that date's
feature publication still has one unresolved INSERT; no coverage was invented.
The declaration, script, packet proofs, position/summary Parquets and receipt
are under `native_post_acquisition_development_audit_v1` in the runtime root.

The separate paired native integration experiment on workstation source
`11a565dfc78cab3d47c388d4234e113cfb28e25d` passed both controls. Pure packet
validation reuse took 1012.40 seconds versus 1418.11 seconds without reuse,
a 28.6% elapsed reduction for that fixture. The source, workstation, negative
fixture and AST-restoration cache were held constant. This runtime-only
prototype is not installed and is not full-session financial speed acceptance.
The terminal proofs and comparison are `whole-packet-validation-probe-v4.json`,
`whole-packet-validation-baseline-v1.json` and
`whole-packet-validation-paired-result-v1.json` in the runtime root.
`exact_scalar_packet_validation_cache.py` packages the pure-content reuse
mechanism with explicit entry, row and retained-byte limits. Every field of
the exact frozen packet type participates; row order and Float64 bits are
preserved. Failed checks or changed backing mappings cannot publish success.
Unsupported shapes call the original validator. Fifteen packet/row reuse tests
passed in 1.69 seconds (`packet-reuse-tests-v2.log`). This utility has not been
selected by a release or installed in the execution path; its separate native
integration and any consuming version's full-session financial acceptance
remain required. Source, admission, ownership and recovery checks are outside
the reusable validator and must continue to execute.
`packet_validation_reuse_policy.py` defines the paired
`declared-pure-packet-validation-bounds@1` input and
`exact-scalar-packet-validation-reuse@1` rule. All three cache bounds must be
explicit in the immutable payload; an unselected claim, duplicate companion,
missing bound or altered admission scope fails. The selection-policy and packet
reuse suites passed 21 tests in 0.86 seconds (`packet-reuse-policy-tests-v2.log`).
This declaration is a prerequisite for a future consuming release, not an
installed source capability or a published strategy. The bounded utility's
native integration was launched separately against the same frozen fixture,
with native source `11a565dfc78cab3d47c388d4234e113cfb28e25d` and utility source
`8f157c945d0583313f69ce181c551b0be307f958`. That native integration passed
in 972.71 seconds with 34724 hits, one miss, no bypasses and 4438413 retained
key bytes. The result is 31.4% below the no-reuse control's 1418.11 seconds
on the same native fixture. Terminal proof is
`whole-packet-validation-utility-v1.json`; reconciled comparison is
`bounded-packet-validation-native-result-v1.json`. This is still not an
installed release or full-session financial speed result.
The prepared Strategy93 declaration pins these bounds in its typed factory
and complete parent-derived configuration. Its v14 compiler and native source
factory reject changed economic fields, accounts, identity, source references
or reuse scope; reconstruction compares bounds against the factory's expected
policy rather than trusting the payload to choose them. Forty-four preparation,
inheritance and policy tests passed in 2.65 seconds
(`reuse-release-tests-v4.log`). The synthetic certificates in these tests issue
no runtime authority. Unknown strategy numbers are rejected before database
access. Installed admission, cold integration,
full-session timing and financial runs remain outstanding.
The new `declared_packet_validation_reuse` scope checks the genuinely issued
source, exact registered release and typed factory before activating reuse.
Its hook wraps only pure row-content validation inside source-equivalence
verification. Unselected nested operations explicitly disable the active
scope; the source-equivalence request and financial/recovery admission remain
outside the cache. Sixty-three hook, original packet, prepared release and
cache tests passed in 6.35 seconds (`reuse-runtime-hook-tests-v1.log`). Those
fixtures do not prove installed admission. The changed shared hook requires the
successor's complete reviewed source closure before publishing Strategy93;
older versions retain their original pinned execution source. No deployed app
or running native financial checkout was changed.
The v14 execution and empty-horizon routes now select the successor only through
its paired reuse declarations, and delegate unselected releases to v13. The
configuration and numbered capability readers require the exact legacy or
reuse factory type and compare persisted reuse bounds with the registered
factory. Both initial and resumed launch sites call this semantic router.
Eighty-seven routing, configuration preparation, original-packet, hook and
policy tests passed in 6.79 seconds (`reuse-routing-tests-v2.log`). The policy
parser checks its flat typed payload directly, avoiding an unnecessary feature
module import in the execution dependency graph.
The v14 source certificate now seals 403 ordered source files, including the
complete inherited inventory, all eleven new execution/policy modules and an
exact parent-compatibility restoration. Each restoration requires the complete
current module AST to match its reviewed pin and reconstructs the complete
retained parent AST. Unreviewed edits receive no restoration. The real,
unmodified Strategy42 source preflight passed with this restoration; its proof
is saved in `reuse-parent42-source-preflight-v3.log`. The complete v14 source
certificate also passed without caller overrides. Sixty-nine source-seal,
compatibility, routing, prepared-release, runtime-hook and cache tests passed
in 31.37 seconds (`reuse-source-seal-tests-v2.log`), including semantic
tampering of every new source leaf, altered certifier code, changed loaded
metadata and exact parent reconstruction. `reuse-source-seal-v14-v1.json`
records the pre-registration source inventory and certificate anchors.
Strategy93 is now registered in source with its exact typed executor and
Strategy42 parent. The registry addition is included in the complete source
seal and exact parent restoration. The actual complete numbered source
preflight for Strategy93 passed without source/parent overrides
(`reuse-registered93-complete-source-preflight-v1.log`); 72 source, routing,
release and cache tests passed in 32.34 seconds
(`reuse-registered-source-tests-v3.log`). The checkpoint fixture now includes
the v14 path and asserts that both original and fresh recovery sources use
the declared bounded cache. The selected native fixture passed from clean
commit `3a188f4fc30730923c6614617b799273d3cbba0b` in 1092.14 seconds. It reached
earned-profit arming, partial exit, a fresh recovery actor, final exit and the
completed terminal prefix at sequence 223. Original and fresh sources recorded
9,752 and 22,952 cache hits respectively, one miss each, zero bypasses and
4,577,283 retained key bytes each. The installed loader, complete source checks
and selected validators were not replaced. Complete transport fixtures remain
controlled, so this establishes native integration rather than a real market
financial result. Receipts are `strategy93-selected-native-integration-v2.log`
and `strategy93-selected-native-integration-result-v2.json` in the optimization
runtime root. A passive ten-second sample during the run observed fresh cold
source preflight; it is diagnostic and does not establish full-session speed.
The parent-derived 26-session financial declaration is
`root-strategy93-development-financial-declaration-v2.json`, with independent
$10,000 cash, unchanged Strategy92 trading parameters apart from declared
validation reuse, original dates and LGHL exclusion. Its review receipt keeps
the first unlaunched declaration and explicitly corrects inherited ancestry
links. Full-session timing and financial comparisons remain outstanding.
The Backtest-only publication command ended with a broken socket
connection after entering the publisher. Its process is terminal. Independent
read-only database counts found one release and 1,188 nodes. Subsequent complete
read-only reconciliation passed, without repeating the write, and certified
revision `strategy-one-93:4ece9f7e-2be0-4949-84f1-614f3d5125d7` against the exact
source, payload and node identities. The retained receipts are
`strategy93-publication-terminal-readback-v1.json` and
`worker-strategy93-certified-published-identity-v1.json`. The declaration was
bound through `root-strategy93-financial-envelope-binding-v1.json`; the first
real development unit, August 4 PM 04:00–09:30 ET with $10,000, has launched
through the native app route. Completion, financial results and full-session
speed acceptance remain outstanding. No validation dates have been selected
or inspected.
The qualifier, multi-resolution alignment and campaign suites passed 35 tests.
On the same six-ticker full PM feature grid, three declared benchmark bands
added a median 0.350 seconds of qualification work across 1,188,000 decisions.
Alignment plus qualification took 3.990 seconds median with five-minute
batches and 257 MB observed peak process RSS. All three repeats produced
identical 116,400 qualifying grid rows and zero future selections. These are
benchmark rows with an all-true synthetic mandatory mask, not native admitted
proposals, acquisitions or P&L. The saved receipt is
`full-pm-feature-qualification-benchmark-5min-v1.json` in the campaign root;
its explicit policy and timing fields describe the added qualification stage.
Cash, fills, OCA, journal and per-clock strategy state remain excluded.

`backtest_native_channel_qualification.qualify_installed_native_channel_decisions`
connects that rule to the installed SELECT-only dependency loader during bounded
entry preparation. It rejects foreign build/day/ticker/bars-attempt identities,
duplicate or uncovered clocks, mismatched input policies and unresolved producer
fences. Alignment and eligibility remain columnar. The original mandatory mask
is detached before source reads, and the result uses immutable byte-backed
storage in the original decision order. Source coverage is required even for an
empty decision packet. This is a preparation adapter, not candidate-population
certification or order admission; no existing numbered strategy selects it.
The caller must retain the complete causal parent population and native PM/AH
financial clocks, then bind the dependency and rule in a new immutable release.

`native_channel_event_qualification` adds a separate declared post-acquisition
predicate. Its policy explicitly selects which qualified resolutions must have
a completed boundary strictly after the actual acquisition, using microsecond
event clocks. Slower contextual bands can retain causally available pre-entry
history. Missing or future acquisition clocks on held rows fail closed; a fill
on a bar boundary cannot reuse that bar as new evidence. The output remains
columnar, ordered and immutable. Portfolio/OMS must establish actual acquired
ownership and supply fill clocks. This helper does not issue exits, establish
ownership, replace quote-confirmed risk checks or change independent protection;
no current strategy selects it.

Saved Strategy57 holding periods provide a timing limit for exit experiments:
63 of 75 losses crossed a 30-second boundary, 45 crossed a one-minute boundary,
and 14 crossed a five-minute boundary. The ten largest winners had a median
holding period of 77.2 seconds. These counts establish clock opportunities,
not valid new source bars or profitable exit signals. The saved receipt is
`native-feature-exit-clock-feasibility-v1.json` in the runtime root. A faster
trigger with slower context still needs actual source availability and a new
cash-sequential financial comparison.

A declared 128-variant diagnostic on all 133 saved Strategy57 development
positions compared one-minute wick, return and relative-volume bounds, with an
optional five-minute return bound. No nontrivial tested filter preserved at
least 90% of the saved winning net. A two-percent upper-wick cap excluded
$7,151.48 of saved winning net while excluding $4,262.78 of saved losses and
retained only four of the ten largest winners. A nonnegative one-minute return
condition retained eight of those ten winners and 86.89% of saved winning net.
These are position-net attributions: replacement opportunities, released cash,
changed exits and fills were not simulated. They do not establish candidate
P&L. The declared policy, exact input hashes, per-position and per-session
attributions are under `native_saved_entry_band_attribution_v1` in the runtime
root. The result argues against transferring the big-move wick comparison
directly into a broad late-entry veto; earlier admission and failure exits still
need native financial comparisons.

A development campaign declares 581 packets for 11,547 saved big-move and
negative-control anchors across the 26 PM/AH sessions. Native resolutions are
30 seconds, one minute and five minutes. Completed-anchor clocks use source
midnight milliseconds, `(backtest_anchor_minute + 241) * 60000`. Full source-day
context is retained, but backward as-of alignment selects completed features
only. Research labels remain separate; this cohort is not an executable
opportunity universe or a financial backtest. Immutable packet receipts and
the campaign declaration live under `native_big_move_feature_campaign_v1` in
the existing optimization runtime root. All 581 research packets completed.
The saved matched comparison covers 26 sessions and 11,547 anchors, with zero
future feature selections. Within source-time and prior-return strata, the
positive episode cohort had a smaller one-minute upper-wick return than
failed-momentum controls in all 26 sessions; volatility-normalized upper wicks
were smaller in 22 sessions. These are descriptive development comparisons,
with control-selection and repeated-ticker dependence still present. They
do not establish a profitable threshold or a financial result. Evidence lives
under `native_big_move_matched_channels_v1/1791488371492091600`.

A separate financial-input campaign certifies the complete native market and
candidate population before excluding LGHL. Its feature dependency comprises
tickers with certified PM/AH MACD candidate keys, retaining full source-day
context. This dependency is suitable only for an additional filter within
that parent candidate gate; it cannot define a broader breakout universe or
act as a future candidate-presence signal. Each bounded feature request is
projected from the hash-pinned full market parent. Research label-selected
packets cannot substitute for these financial dependencies.

The financial-input campaign has 216 of 217 packets complete, with 2,343,259
feature rows and 3,813 coverage rows. Twelve development dates have all their
declared packets; August 19, 2026 has one held packet. Its original feature
INSERT remains persistently pending after a transport disconnect. Neither
elapsed time nor observed child rows authorizes clearing that gate: the shared
protocol requires a trusted original-terminal resolution, which has no
production implementation yet. The other 140 independent packets completed
under a separate declaration preserving the original failed receipt. The
hash-checked reconciliation is `native_financial_feature_campaign_v1/
reconciled-publication-v1.json` under the optimization runtime root. It records
the held packet explicitly and does not claim complete campaign coverage,
native strategy admission, financial backtesting or validation exposure.

On the workstation, three repeats of the full 330-minute PM decision grid
for six tickers (1,188,000 decisions at 100 ms) used 13,537 actual certified
feature rows. Median feature-alignment times were 4.884, 3.824 and 3.832 seconds
for two-, five- and twenty-minute batches respectively. Observed process RSS
peaks were 143, 212 and 504 MB; maximum output batches were 5.94, 14.86 and
59.44 MB. Every repeat had identical per-resolution availability counts and
zero future selected rows. Five-minute batches are the measured starting
choice for a future consumer, with smaller declared batches available when
memory requires them. Keep batch size separate from decision interval and
source resolution. These measurements include alignment and causal checks,
but exclude strategy state, cash, fills, OCA and journal work: full native
Backtest speed acceptance remains outstanding. Receipts are named
`full-pm-feature-clock-benchmark[-<minutes>min]-v1.json` in the campaign root.

Splits and fundamentals await
verified point-in-time availability; news is omitted. Profitability and complete
session performance require native financial runs, not projection benchmarks.

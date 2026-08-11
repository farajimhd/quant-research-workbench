# Implementation decision and delivery log

[Top](README.md) · [Previous](14-implementation-backlog.md) · [First](01-product-and-principles.md)

This log captures the final implementation instruction that followed the
architecture review and prevents scope decisions from being lost in chat.

## 2026-08-10 implementation goal

Implement the accepted application architecture across QMD Gateway, QMD
History, the application backend and frontend, Portfolio, and OMS. Add the
conversation decisions to this documentation, validate durable phases, and
leave explicitly deferred producer-service work unchanged.

### Authorized implementation boundaries

- QMD Gateway, shared `qmd_core`, and QMD History;
- backend APIs, registries, run controllers, and projections;
- frontend Market Discovery, Canvas, charts, Backtest, configuration, and
  operational surfaces;
- Portfolio and OMS runtime contracts;
- focused tests, operations contracts, and this architecture package.

### Deferred or separately authorized boundaries

- Market AI intelligence work, including its QMD History client;
- News Gateway, SEC Gateway, Reference Gateway, Text Intelligence, Text Embed,
  and Model Gateway producer changes;
- broker orders, deployment changes, and IBKR Gateway/Supervisor changes.

Existing contracts from deferred services may be consumed, but this work must
not modify those producers. Market AI's intended live QMD, QMD History, and
bounded direct ClickHouse paths remain documented in
[system context](02-system-context-and-services.md).

## Delivered phases

1. Defined computation scopes and centralized the QMD capability catalog.
2. Added QMD History live/recent/archive source routing and retention handoff
   verification.
3. Registered application enrichment fields and query plans.
4. Enforced the Universal/Core/Watchlist computation funnel and exposed the
   causal Watchlist runtime in Market Discovery.
5. Added durable cross-run Portfolio fencing and enforced Portfolio authority
   at the OMS boundary.
6. Streamed chart bars before request-scoped indicators and progressively
   hydrated Canvas charts.
7. Added Backtest creation, monitoring, and stop controls over one continuous
   shared historical Strategy/Portfolio/OMS runtime.
8. Made the QMD Gateway runtime catalog authoritative for backend Market
   Discovery projection, with a short-lived and explicitly unavailable legacy
   review fallback when QMD cannot be reached.
9. Added Replay chart-originated manual and semi-automatic proposals with
   captured market/identity/protection evidence and the normal Portfolio-to-OMS
   authority path. Live/Paper execution remains disabled pending their shared
   controller migration and separate broker authorization.
10. Added Backtest result projection and UI summaries directly from the run's
    canonical Portfolio/OMS journal state; strategy attribution remains open.
11. Added a versioned backend readiness envelope and a central Services UI that
    keeps liveness, dependency, data and execution readiness separate. Missing
    producer evidence remains unknown, and non-broker services report execution
    as not applicable.
12. Versioned the broker-neutral Strategy execution-intent contract, included
    the version in durable payloads, rejected unsupported future versions, and
    retained explicit version-1 recovery for pre-versioned OMS journal rows.
13. Added a Canvas-only approved-release API and separated runtime workspace
    overlays from editable Configuration storage. Standalone Canvas and Replay now
    start from the approved/pinned profile, persist revision-scoped changes, and
    can reset to approved without rewriting application defaults.
14. Added historical chart-indicator provenance from QMD History through the
    backend to Canvas: engine/schema versions, effective parameters, warm-up,
    source-plan hash and tiers, revision evidence, completeness, and stale reason.
15. Standardized QMD History operational status and added a versioned backend
    QMD operations envelope. The Services UI now shows declared archive
    watermark, live lag, writer/drop state, cache efficiency/footprint, and
    active historical build capacity without converting missing evidence into
    healthy zeroes.
16. Extended the existing application registry with market-source
    coverage/watermark contracts, QMD product dependencies, all runnable Canvas
    containers and typed links, and versioned trading configuration schemas.
    The shared QMD live/history capability catalog now also declares version,
    cadence, timeframe, warm-up, state/persistence class and mode support. The
    backend exposes each registry family for runtime consumers.
17. Made the backend container registry authoritative for every shared trading
    workspace. Frontend renderer adapters are admitted only when the backend
    record is implemented and mode-compatible; failed verification is visible
    and blocks new unverified container choices without destroying an existing
    saved layout.
18. Consolidated QMD live/history base-URL resolution, HTTP JSON transport,
    WebSocket URL construction and error handling in the backend QMD client.
    Existing trading/replay imports remain compatible; typed product/window
    routing is the next migration step and is not claimed complete.
19. Added and adopted typed QMD product/window routing for chart,
    compact-event and Scanner reads. Windowless requests resolve to QMD live;
    timezone-aware causal windows resolve to the consumer-neutral QMD History
    contract, whose internal source planner alone chooses archive/recent/gap
    segments. Replay WebSockets use the same client URL authority.
20. Replaced Core Scan's per-symbol full-trade rate buffers with bounded
    per-second counters and gave Scanner snapshot plus row-delta delivery one
    monotonic sequence. WebSocket clients subscribe before snapshot capture,
    discard already-covered deltas, and receive an explicit resnapshot action
    if their delta subscription lags.
21. Enforced timeframe-aware focused QMD routing. A lease computes its declared
    finalized timeframes plus the canonical 100 ms dependency stream, while
    unrelated timeframe rows are rejected before entering indicator shards.
22. Added a fail-closed backend runtime-capability registry. The endpoint
    exposes QMD's hashed capability, indicator, and signal catalogs and returns
    a typed retryable 503 instead of presenting the Python review fallback as
    live runtime authority.
23. Extended Strategy taxonomy inputs with producer/capability identity,
    compiled the grouped observation dependency manifest into immutable Run
    Plans, and made exact Paper/Live Watchlist membership publish and retire
    separate `strategy_run` QMD leases. Deferred intelligence dependencies stay
    declared but are never misrouted to QMD.
24. Upgraded the application registry contract to schema v3 and made validation
    reject duplicate IDs, broken references or dependency cycles, unsupported
    clocks/scopes/modes/statuses, missing local implementations, incomplete
    source/query paths, and ungoverned compatibility aliases. The legacy QMD
    scanner-primitives stream is registered as a deprecated alias of the
    canonical signal stream with an explicit removal condition.
25. Carried QMD capability owner, implementation version, compute cadence,
    persistence policy, and intended consumer scopes through the backend
    configuration contract. Market Discovery now presents that evidence with
    reason, cost, operational state, and coverage for locked Universal Ingest
    primitives and the Core Scan inventory.
26. Added bounded Canvas chart adjacent-window prefetch. The client preloads
    only the exact next earlier cursor after the visible page completes,
    consumes it without another request when the user loads earlier history,
    and aborts/discards it whenever symbol, timeframe, projection, or workspace
    navigation supersedes the request.
27. Added the Market Discovery historical Scanner view. A backend-owned route
    converts the chosen New York market clock, calls QMD History's typed
    full-market Scanner replay contract, and returns source revision, engine,
    event, ticker, and indicator evidence. The UI presents that snapshot beside
    the existing append-only Watchlist membership history.
28. Added a registry-driven Market Discovery Enrichment Fields view. It exposes
    owner/source, point-in-time query plan and `available_at`, freshness,
    coverage, null reasons, provenance, historical support, cadence, and status
    without embedding database paths or producer formulas in browser code.
    Deferred intelligence producer fields remain visible as integration pending
    and are not mislabeled runnable.
29. Removed the complete handwritten Python QMD family fallback. Current/default
    choices now come only from the QMD runtime catalog, and an unavailable QMD
    authority yields no invented capability rows. Saved configuration rows stay
    embedded and reviewable through migration without being promoted to current
    runtime evidence. The remaining reference and deferred-intelligence
    projections are explicitly still open for registry generation.
30. Version-fenced QMD History's paged event snapshot contract. The first page
    returns its source-plan hash and revision token; Replay/Backtest echo both
    on every continuation. QMD History returns a typed HTTP 409 with
    `restart_snapshot` if the plan or revision drifts, and the Python source
    independently verifies every response. Storage-level old-revision reads
    remain open, so this detects drift rather than claiming immutable snapshots.
31. Added a stable QMD Live per-ticker compact-event page and migrated the typed
    backend client. The versioned envelope delivers ascending arrival-sequence
    pages with exact per-ticker eviction evidence, buffer start/end,
    `has_more`, and a next cursor. The legacy raw-array endpoint remains for
    compatibility; latest ticker-state versioning and stream gap repair remain
    open and are not implied by this snapshot contract.
32. Added and adopted a versioned QMD Live latest ticker-state envelope. It
    captures the row and global Scanner sequence under one read lock, declares
    authority and schema version, reports `as_of` and event age, and represents
    a missing symbol explicitly. The older nullable-row endpoint remains only
    for measured compatibility. Stream recovery was handled separately in the
    following phase.
33. Made the QMD Scanner stream self-repairing. Each connection subscribes
    before capturing its bounded initial snapshot; receiver lag or an observed
    non-contiguous sequence now emits explicit gap evidence and immediately
    sends a replacement snapshot from the same sequence authority. Queued
    deltas through the replacement boundary are discarded. Other raw event and
    product streams still require equivalent recovery contracts.
34. Closed the compact-event current-tail routing gap at the typed QMD client
    boundary. QMD History remains the archive/recent source planner; when its
    plan declares a current-live segment, the client filters QMD Gateway's
    bounded live page to that exact interval, deduplicates and orders the union,
    and preserves head/tail limits. An evicted forward cursor fails closed.
    Equivalent live continuation for current-window charts and historical
    Scanner products remains open.
35. Removed the remaining Market Discovery availability authority drift.
    Reference and deferred-intelligence capability rows now inherit owner,
    source, query plan, availability clock, and status from registered fields.
    The backend Scanner reference query also implements causal IPO and split
    event-distance joins from the existing Reference Gateway tables, so the IPO
    template is no longer falsely integration-pending. Saved system templates
    migrate that status without overwriting ordinary user enablement choices.
36. Added bounded Portfolio and OMS operational metrics to the canonical
    Portfolio projection and Canvas. The envelope counts disposition and
    reservation transitions from the newest 5,000 journal records, reports
    truncation, and combines them with current active reservations, managed OMS
    states, unknown outcomes, reconciliation failures, and protection deficits.
37. Added QMD focused-computation demand metrics. Active leases now expose
    per-scope target, unique-symbol and unique-capability counts plus weighted
    demand units derived from registered cost class, ticker count, and effective
    timeframe dependencies. QMD Service Core and Market Discovery consume the
    same snapshot; the UI labels absence rather than inventing zero cost.
38. Closed the Portfolio fencing test gate with an explicit concurrent
    cross-run admission case using separate journal connections to the shared
    account authority. Together with the existing epoch-expiry, stale-owner,
    state-recovery, and shared-account tests, the suite proves reservations
    cannot overallocate the available account capacity.
39. Exposed canonical Backtest attribution and bounded comparative analysis.
    Each result now includes the existing flat-to-flat performance journal,
    preserving run, strategy, and strategy-revision identity. A backend-owned
    comparison projection presents up to twenty terminal runs and their
    strategy rows, while the Backtest UI shows current-run attribution and the
    latest ten-run comparison without duplicating performance formulas.
40. Closed the Market Discovery Core Scan catalog and approval UX gap. Core
    choices were confirmed to already come from registry rows whose execution
    scope is `core_scan`, while QMD and backend validation reject unregistered
    scope broadening. Optional all-market activation now requires a focused
    confirmation showing population, cost class, cadence, owner/version, and
    allowed scopes before it changes the draft; configuration publication
    remains the immutable approval record.
41. Reconciled two stale Canvas backlog entries with the already shipped
    schema-v3 application registry. The backend publishes a versioned container
    catalog and typed link contracts with mode, identity, and clock policies;
    validation cross-checks every container, product, link direction, mode, and
    state schema. Shared trading workspaces consume the catalog and block new
    unverified container choices when registry or renderer evidence fails.
42. Added bounded request correlation and causation propagation across the
    active application path. Browser API calls originate correlation IDs; the
    backend validates or creates both identities, returns them in exposed
    response headers, and carries them through QMD HTTP/WebSocket transport.
    QMD Live and History validate and echo the headers, while broker-event
    envelopes and authoritative Portfolio/OMS journal payloads inherit the
    active context. Autonomous source-event and computation-lease lineage is
    still open and is not mislabeled complete.
43. Extended causal lineage across autonomous trading work. Strategy
    evaluations now derive bounded correlation from assignment identity and
    causation from the newest QMD/source signal or observation clock. Portfolio
    decisions cite the Strategy intent, reservations cite the durable decision,
    and the approved intent carries that decision into OMS lifecycle records.
    The implementation also reconciles two stale backlog entries: publication
    already compiles and content-hashes immutable Run Plans that mode-specific
    runtime resolvers consume. Autonomous market-source, computation-lease, and
    generic continuation lineage remains open.
44. Removed the remaining handwritten Market Discovery field/column/filter
    authority. Application registry schema v4 declares presentation metadata
    against existing app fields or QMD capabilities; configuration schema v17
    resolves the full catalog and generates Watchlist columns from it. Backend
    validation rejects unknown Watchlist sources, unsupported comparators, and
    non-registry columns. The frontend custom-filter dialog consumes only
    eligible registry rows and their operator policies. Forty-seven focused
    backend/configuration tests, the managed frontend build, and the focused
    1600x1000 Market Discovery browser interaction passed with zero objective
    UI issues. The application-wide theme, scale, responsive, and accessibility
    matrix remains open.
45. Closed the QMD recent-coverage evidence gap and reconciled the stale bar
    authority backlog. Compact-event confirmations are now cumulative per run,
    UTC event partition, and New York session; canonical base-bar confirmations
    are cumulative per run and `local_date` partition. A UTC rollover during
    after-hours therefore cannot blur the market-session or physical-partition
    boundary. Existing code was confirmed to own singular recent event/family
    persistence, three-family algebraic intraday rollups, daily-derived macro
    bars, explicit partial periods, revision-keyed bounded caches, and the
    retired legacy macro read path. QMD History now requires overlapping
    compact-event and base-bar confirmations from the same run before planning
    an interval as recent. All 85 QMD Gateway tests, 26 QMD History tests, and
    both all-target compile gates passed. Archive watermark promotion
    still needs an explicit retained QMD equivalence certificate and remains
    open; no Market SIP producer or schema changed.
46. Reconciled the computational-funnel checklist with the enforced runtime
    contract and added an exact regression fence. QMD already represented all
    six scopes, rejected leased Universal/Core work, locked the six Universal
    primitives, and limited Core Scan to five low-cost families. The new catalog
    test fixes those exact sets and costs so a new indicator cannot silently
    become all-market work. Existing bounded Scanner materializations and the
    append-only Watchlist membership journal close the compact-history item.
    Generic Structure still executes in the broad bar store, measured Core Scan
    profiling is still required, offline requirements are not yet part of the
    live lease identity, and warm state is not reclaimed; those gaps remain
    explicitly open. All 86 QMD Gateway tests and its all-target compile gate
    passed.
47. Closed the compiled warm-up gap without creating a second formula
    authority. QMD's capability catalog now projects its stable capability key
    and recommended warm-up bars into configuration schema v18. Published Run
    Plans pin those bars and the QMD implementation revision on each declared
    observation dependency; migrated plans are enriched from their saved
    catalog rows. If matching catalog evidence is absent, the dependency says
    `catalog_unavailable` rather than silently assuming no warm-up. The same
    Run Plan already carries allowed modes, account mandates, runtime
    assignments, and validated action permissions, so the corresponding stale
    backend backlog item is now closed. Sixty-seven focused registry,
    configuration, and QMD-client tests passed. Four Watchlist runtime tests still fail when run
    without live QMD catalog evidence; that pre-existing fail-closed behavior
    was not weakened or counted as validation for this change.
48. Reconciled the point-in-time enrichment backlog against the runnable
    Scanner/Watchlist services: reference, float, short-interest,
    corporate-event, SEC fundamental, previous-close, and daily-volume inputs
    are already loaded in causal full-universe queries and joined without
    per-symbol remote calls, so that stale item is closed. Began the broader
    response-contract migration at the shared QMD boundary. Product response
    schema v2 now carries completeness, warnings, coverage and source revision;
    GET transport failures carry stable typed error details through QMD-facing
    backend routes, and the frontend displays their message. Other transports
    and services remain explicitly open. Sixty-nine focused backend tests and
    the managed TypeScript/Vite production build passed.
49. Completed the remaining QMD transport portion of the typed response
    boundary. Computation-lease PUT and DELETE calls now raise the same stable
    service/code/path/retryability/upstream-status error as GET requests, and
    invalid mutation responses fail as typed invalid JSON. Proxied QMD Live and
    History WebSockets now emit schema-v1 terminal error frames with structured
    detail while preserving the existing `error` string for current consumers.
    The application-wide response item remains open for non-QMD clients and
    uniform success envelopes. Seventy-one focused backend tests, Python compile,
    and a direct typed-stream boundary smoke check passed.
50. Closed the remaining callable direct-broker bypass in the backend. The
    dormant real-live submit path now permits only non-executing IBKR what-if
    preview; direct submit, reply confirmation, modify, and cancel calls fail
    before account resolution or broker I/O. The low-level unused DELETE helper
    was removed. Runnable execution remains `TradingRuntime` to Portfolio to
    OMS to the broker adapter, and OMS independently rejects an intent without the exact
    durable Portfolio decision and reservation. Reconciled stable account/group
    ownership and removed generic run-priority fields as already-shipped
    Portfolio authority. Seven exact bypass/Portfolio/OMS causal-authority tests
    and Python compile passed. Broader async suites exceeded their bounded test
    timeouts and are not counted as passing; restart reconciliation remains an
    open backlog acceptance item.
51. Added explicit causal lineage to focused computation demand. Autonomous
    Watchlist and Strategy Run leases derive correlation from the target and
    causation from the membership clock; chart leases derive both from the
    request when present or a stable chart identity otherwise. QMD validates,
    stores, and exposes the identities in computation-target snapshot schema
    v2, with bounded target-derived fallbacks for older callers. This closes the
    computation-lease portion of the operations lineage backlog; autonomous
    source events and generic background continuations remain open. Thirty-four
    focused Python tests, five Rust computation-target tests, Rust formatting,
    Python compile, and the QMD all-target compile gate passed.
52. Bounded the backend service-monitor projections. Service-table and News/SEC
    histogram caches now share a thread-safe TTL/LRU implementation with hard
    entry limits, explicit contract revisions, optional source-revision keys,
    and eviction metrics instead of process-lifetime dictionaries. The broader
    cache backlog remains open for chart and mode-specific source-revision
    invalidation. Four focused tests, including the real service-table helper,
    Python compile, and diff validation passed.
53. Made the bounded processed-artifact chart LRU revision-aware. Its key now
    carries a compact hash of every relevant date/timeframe artifact build,
    path, size, schema/feature/supervision contract, provider catalog version,
    and presentation override revision. Updated artifacts therefore miss the
    old entry without a backend restart. Four focused revision/cache tests,
    Python compile, and diff validation passed.
54. Replaced the process-wide canonical live account-state dictionary with the
    shared bounded TTL/LRU cache. Account selectors remain part of the key, the
    projection contract is explicitly versioned, refresh still bypasses reads,
    and at most 32 account-selector projections are retained. Five focused
    cache tests, Python compile, and diff validation passed.
55. Bounded Replay and Backtest controller residency. Existing per-subscriber
    queues already held four coalesced snapshots; the service now retains at
    most 32 controllers by default, evicts only the oldest terminal controller,
    preserves durable run directories, and returns HTTP 429 instead of growing
    without bound when every slot is active. Three focused service/API tests,
    Python compile, and diff validation passed.
56. Closed backend cache governance after the remaining causal audit. The
    Watchlist full-universe reference cache no longer shares one value across
    different `as_of` clocks; it is a four-entry bounded TTL/LRU projection
    keyed by contract and causal source revision. Replay caches are run-local
    and pinned by the immutable approved configuration, while other discovered
    process caches are bounded and revision/signature keyed. Eighteen focused
    Watchlist/cache tests, Python compile, and diff validation passed.
57. Corrected Replay/Backtest historical warm-up fan-out. The controller now
    fetches the full Scanner signal product once per run window, groups it by
    assigned ticker, and limits ticker/timeframe derived WebSocket fetches with
    a configurable semaphore (default eight, accepted range one through 32).
    This removes per-ticker duplicate Scanner computation and unbounded request
    concurrency. Five focused Replay capacity/fetch tests, Python compile, and
    diff validation passed.
58. Moved Paper/Live broker identity fully behind the backend boundary.
    Configuration schema v19 accepts only stable account keys and named server
    environment bindings, rejects a stored broker account ID, and migrates older
    releases to the standard Paper/Cash keys. Internal runtime compilation may
    resolve the value; public effective and revision responses scrub it, and the
    configuration UI no longer offers a direct-ID fallback. Thirty-six focused
    configuration tests, Python compile, the managed frontend production build,
    and four Account Configuration browser scenarios passed with zero objective
    issues.
59. Added honest current-live continuation for historical chart and Scanner
    product requests. The typed backend QMD client now reads the QMD History
    source plan, filters QMD Gateway bars, indicators, active signals, and
    signal events to the exact current-live interval, and deterministically
    replaces older derived rows. Because these bounded product snapshots do not
    provide a replay cursor or eviction proof, composite responses explicitly
    report `complete=false` and `live_snapshot_continuation`; pinned Replay is
    not allowed to infer completeness. Thirty-one focused QMD client tests,
    Python compile, and downstream Scanner/Replay/trading-runtime tests passed.
60. Moved current-live product continuity into QMD authority. QMD Gateway now
    exposes a bounded cross-market compact-event page with exact SIP-time and
    ticker filters, global arrival continuation, and conservative eviction
    detection. QMD History consumes and canonically orders every page for both
    bounded reads and Scanner streams, fails closed on eviction, page overflow,
    or a stalled cursor, pins every page to the revision's live arrival
    watermark, and includes that sequence in its source revision and cache key.
    Intraday charts and Scanner now run once through the shared
    QMD computation library; the backend trusts QMD request-completeness
    evidence instead of repeating product snapshots. All 88 QMD Gateway tests,
    all 26 QMD History tests, 74 backend/downstream tests, Rust formatting,
    Python compile, and diff validation passed.
61. Isolated backend workload admission. Commands, discovery, charts/Canvas,
    Replay/Backtest simulation, offline/build work, and general reads now have
    independent environment-configurable concurrency limits at the browser API
    boundary. Saturation waits for a bounded interval and returns a retryable,
    correlated typed 429 without consuming another lane. A read-only system
    endpoint reports active, available, completed, rejected, and wait-time
    evidence. Two focused classification/concurrency tests, Python compile, and
    diff validation passed.
62. Closed Portfolio restart reconciliation evidence. The existing shared
    runtime already refreshed canonical cash/positions/orders before admission
    and then recovered OMS groups by stable broker/client IDs, whose callbacks
    resize or release durable reservations. The missing seam was Portfolio's
    broker-versus-managed attribution differences: they are now saved with
    account state, restored after journal reopen, and journaled only when the
    difference set changes. Dedicated external-runtime test directories avoid
    costly retries at the crowded runtime root. All 37 Portfolio/OMS tests,
    Python compile, and diff validation passed.
63. Replaced Backtest's permanently frozen first-clock Watchlist population
    with a bounded causal session timeline. The controller resolves the first
    requested clock and every later 04:00 New York weekday boundary, builds the
    historical stream from the safe union, journals additions and removals,
    admits flat synthetic assignments only while active, and keeps evaluating
    an open position after removal so risk management is not abandoned. A
    ticker whose point-in-time conid changes across the run fails closed because
    ticker-only assignment authority would be ambiguous. All 26 Replay/Backtest
    service tests and Python compile passed. Intraday Watchlist-event replay is
    still explicitly open and this phase does not claim full mode parity.
64. Closed a server-side QMD History event-page watermark seam discovered
    during the revision audit. The endpoint already computed and returned a
    pinned source revision, but its actual source fetch used the unpinned helper;
    it now passes the revision's live continuation sequence into the merge, so
    newer live arrivals cannot enter a page labeled with the older revision.
    Rust formatting and all 26 QMD History tests passed using the external Cargo
    runtime.
65. Corrected focused-computation union evidence and tick routing. QMD demand
    schema v3 now expands leases into versioned ticker/capability/timeframe
    requirements, reference-counts overlaps, and distinguishes requested cost
    from effective deduplicated cost. Bar-only leases no longer route every raw
    trade and quote through the indicator event engine; a lease must contain a
    catalog capability with event-driven cadence. Market Discovery displays the
    effective requirement count and duplicate cost removed. All 89 QMD Gateway
    tests and the managed external-runtime frontend production build passed.
66. Added low-overhead evidence for the Core Scan performance gate. QMD samples
    one of every 1,024 events around the compact all-market Scanner-state update
    and, independently, the all-market bar/structure update. The existing
    metrics/status payload now reports each stage's sampling rate, sample count,
    last, mean, and maximum microseconds. Two focused metric tests passed. This
    instruments the real runnable path but does not claim the profiling gate is
    complete until an active-session run is captured against explicit budgets.
67. Removed an avoidable chart-loading barrier from the legacy Paper and Live
    manual-trading workspaces. The primary chart now loads and renders
    independently; daily and five-minute secondary charts request data only
    while visible and expose separate loading and error state. The managed
    external-runtime frontend production build passed. The managed UI review
    passed all 12 Real Live route scenarios with no objective shell issues, but
    the environment remained at the broker/market Session Gate, so this phase
    does not claim browser exercise of the chart panels or completion of their
    migration to the shared Canvas/QMD resolver.
68. Completed the live focused-demand semantic identity. QMD demand schema v4
    now keys effective requirements by ticker, capability, timeframe,
    implementation version, parameter hash, anchor, and source revision;
    ambiguous delimiter/control input is rejected and semantically different
    requests cannot be deduplicated. Current Watchlist, Strategy Run, and chart
    publishers send deterministic parameter hashes, the New York session
    anchor, and the advancing-live revision class. All 92 QMD Gateway tests, 44
    focused backend QMD/Watchlist tests, Python compile, Rust formatting, and
    diff validation passed. Offline QMD History demand remains a separate open
    planner-integration item.
69. Made offline QMD History computation demand explicit. Every revisioned
    derived-cache entry now publishes a stable requirement ID, product/profile,
    ticker, timeframe, deterministic engine-parameter hash, event-time anchor,
    exact source revision/plan, runtime state, processed-event count, and memory
    footprint. The backend operations envelope preserves that producer evidence
    instead of reducing historical work to an active-build count. Identical
    historical requests continue to single-flight on the same complete cache
    identity. All 27 QMD History tests, six backend readiness tests, Rust
    formatting, Python compile, and diff validation passed. A single aggregated
    live-plus-history planner projection remains open.
70. Added the cross-service computation planner projection. QMD demand schema
    v5 now returns structured live requirements and reference counts rather
    than requiring consumers to parse identity strings. The backend composes
    those rows with QMD History's offline requirements at
    `/api/system/computation-requirements`, preserves distinct authority and
    revision, and returns partial evidence with per-authority errors. Market
    Discovery shows live/offline counts even before its first Scanner
    resolution. All 92 QMD Gateway tests, 46 focused backend tests, Python
    compile, Rust formatting, and the managed external-runtime frontend build
    passed. The strict Market Discovery browser matrix captured all 12
    light/dark, scale, and viewport scenarios with zero objective issues; a
    focused loaded-state capture verified the partial-authority planner text.
71. Closed QMD stream lag ambiguity. Scanner already repaired gaps in-band; all
    remaining broadcast-delta streams now emit a schema-v1 terminal
    `stream_gap` frame with `resnapshot_required`, skipped count, and the
    authoritative recovery endpoint. Compact events also carry the last
    delivered arrival sequence for exact bounded page continuation. QMD History
    bar, indicator, and derived streams now require reconnect with the original
    causal window after lag instead of silently rescanning and continuing.
    Periodically sampled ticker/bar/indicator/product endpoints remain complete
    current snapshots. All 93 QMD Gateway tests, all 28 QMD History tests, Rust
    formatting, and diff validation passed.
72. Made the QMD archive handoff restart-safe across separate event and bar
    retention mutations. QMD now records a per-session handoff certificate only
    after quote and trade publication are confirmed and the live/archive event
    fingerprints are identical. If recent events have already been removed, a
    retry accepts the certificate only when the current remote-object identities
    and archive fingerprint still exactly match the recorded evidence; otherwise
    retention remains blocked. Empty live partitions produce explicit zero
    fingerprints instead of an unparsable aggregate row. Rust formatting and all
    95 QMD Gateway tests passed using the external Cargo runtime.
73. Closed the active Canvas tape/quote resnapshot seam. Its backend WebSocket
    now establishes the QMD subscription first, sends a versioned per-ticker
    compact-event snapshot with snapshot ID and last sequence, and forwards
    tickerless terminal control frames instead of filtering them out. The
    browser replaces its local event window on each connection and reconnects
    after `stream_gap`, preventing false continuity. Three focused backend
    contract tests, Python compile, and the managed external-runtime frontend
    production build passed. The historical-to-live chart-bar merge remains a
    separate open item.
74. Added explicit QMD History event revision policies. Replay and Backtest keep
    the default pinned contract and reject either source-plan or token drift.
    Live consumers can request an advancing contract that accepts a newer live
    arrival/revision watermark while holding the physical source plan fixed; a
    tier or plan change still returns the typed restart conflict. The shared
    Python historical source exposes the same policy and remains pinned by
    default. All 30 QMD History tests, four focused Python consumer tests,
    Python compile, and Rust formatting passed.
75. Added runtime Canvas save-as-workspace without creating a second layout
    authority. Approved Canvas and Replay users can clone the current normalized
    overlay into a new workspace stored under the same mode/revision scope; the
    clone carries its workspace state and current link/container settings but
    never mutates the published Configuration default. Reset-to-approved remains
    unchanged. Three-way rebase/conflict handling is still open. The managed
    external-runtime frontend production build passed.
76. Added explicit Canvas overlay rebase authority. Runtime overlays now retain
    the approved base, user overlay, revision, and normalized workspace state.
    When the approved revision changes, a deterministic three-way merge keeps
    non-conflicting changes from both sides, lists conflicting leaf paths, and
    waits for the user to apply the overlay-preferred result or keep the new
    approved default. Records are isolated by mode/run scope and Canvas ID.
    Legacy overlays without a base record remain safely revision-isolated and
    are not guessed forward. The managed external-runtime frontend production
    build passed; real browser conflict-state validation remains part of the
    broader UI acceptance gate.
77. Migrated the active post-gate Live/Paper workspace to the published Canvas
    resolver without changing broker services or the account/gateway preflight.
    The published profile supplies layout, links, and container settings;
    mode-and-account-set scopes isolate runtime overlays; QMD Live supplies the
    scanner, tape, current bars, and indicators; and canonical trading endpoints
    supply broker, Portfolio, and OMS projections. Charts render their causal
    QMD History base first, merge bounded QMD Live snapshots by bar timestamp,
    replace corrected current bars, and label transition, partial, reconnecting,
    and stale-tail states. The legacy renderer remains compiled only as a
    rollback seam, and Live/Paper trade proposals remain review-only pending the
    separately authorized shared execution-controller migration. The managed
    external-runtime frontend production build and nine focused account-gate,
    retired-order-authority, and QMD Canvas stream tests passed. Authenticated
    post-gate browser validation remains in the broader UI acceptance gate.
78. Migrated Backtest inspection to the same published Canvas resolver. Run
    snapshots now declare their mode and retain the pinned Canvas revision and
    profile; a bounded Backtest Canvas endpoint projects the active controller's
    canonical Strategy, Portfolio, OMS, and journal state at the causal run
    clock. The Backtest page mounts that run-scoped workspace alongside its
    definition, progress, results, attribution, and comparison views. Backtest
    assignment commands and chart proposals are explicitly disabled because
    the run is immutable analysis evidence, not Replay execution authority.
    All 30 focused historical-runtime/Canvas tests, Python compile, diff
    validation, and the managed external-runtime frontend production build
    passed. Real-browser validation remains in the broader UI acceptance gate.
79. Added the compact application feature projection to historical Canvas and
    Live/Paper Scanner responses. Each browser-friendly flat column now has one
    registry-derived companion record carrying field/source/query-plan identity,
    schema and source revision, event/availability clocks, latest observed
    availability, freshness policy, implementation status, coverage, and
    aggregated explicit null reasons. QMD native Scanner columns use the newly
    registered `qmd.scanner.snapshot.v1` plan; enrichment fields retain their
    existing Reference/SEC query-plan authority. No producer service changed.
    All 16 focused projection, application-registry, Canvas, and preflight tests
    plus Python compile and diff validation passed.
80. Began the approved backend SQL migration with the complete Canvas context
    family. Company-News, SEC filings, cross-sectional News/SEC summaries, and
    bounded CIK-to-ticker identity queries now live in the versioned
    `canvas_context_v1` module. The application registry exposes five exact plan
    IDs with physical source paths, identity joins, causal clocks, and coverage
    authorities; the Canvas composition service no longer embeds those query
    strings. This is a backend consumer migration only and changes no News, SEC,
    Reference, or Text Intelligence producer. All 15 focused query-plan,
    Canvas, and registry tests plus Python compile and diff validation passed.
81. Added deterministic Backtest Debug source injection to the shared
    historical controller. A bounded fixture can carry canonical quote/trade
    events and normalized derived frames with explicit causal clocks. The run
    uses the same approved configuration, Strategy, Portfolio, OMS simulator,
    journal, lifecycle, and Canvas projection as Backtest while making no QMD
    History request. The fixture is content hashed and its exact records are
    persisted beside the run manifest under the external runtime root. Dedicated
    create/list/status/stop/Canvas APIs expose the mode without conflating it
    with production Backtest evidence. The application now has a dedicated
    Backtest Debug page with a bounded JSON editor, browser-local saved-case
    library, preflight, run controls, fixture-hash evidence, and the shared
    read-only Canvas. Python compile, 39 focused historical-controller/API tests
    including an end-to-end no-QMD fixture run,
    and the managed frontend production build passed. A targeted Playwright
    review captured the page at 1600x1000 and 1280x720 with zero objective
    issues; because no backend was running, the screenshots correctly showed
    the typed preflight failure state rather than ready/run-state evidence.
82. Moved the causal daily-session-bar SQL into the registered
    `market.daily_session_bars.v1` plan. Historical Scanner, ticker facts, and
    Watchlist consumers now import the versioned plan directly; the old module
    is only an identity-preserving compatibility re-export. The plan keeps its
    three-session completeness gate, identity ambiguity rejection,
    canonical/source-ticker fallback rule, and `available_at_us` cutoff.
    Python compile and 33 focused plan, registry, Watchlist, and historical
    Scanner tests passed (pytest ran in the existing `ml4t` environment because
    the default repository Python does not contain pytest).
83. Standardized the exposed automatic-historical lifecycle boundary. Backtest
    and Backtest Debug now accept only typed `pause`, `play` (resume), and
    `stop` commands through matching run endpoints, and both pages expose pause,
    resume, and stop controls against the authoritative controller status.
    Replay-only stepping, speed, and fast-forward operations fail closed on
    these automatic modes. The older stop endpoints remain compatibility aliases
    while the UI uses the shared command contract. Python compile, 41 focused
    historical runtime/API tests, and the managed frontend production build
    passed.
84. Made historical-run checkpoint state operationally visible. Replay,
    Backtest, and Backtest Debug snapshots now expose pending/available status,
    durable cursor, event and write clocks, processed-event count, and interval;
    the pages repeat that restart resume is not yet supported. Journal checkpoint
    reads now use the journal lock because status HTTP calls and run publication
    can occur on different threads. This intentionally does not mark durable
    resume complete: Strategy, simulator, Portfolio, OMS, source cursor, and
    clock restoration remain required. The five stale chart tests discovered
    during validation now select the intended chart call rather than assuming it
    is last after the planner's `/source-plan` request; no QMD behavior changed.
    Python compile, all 65 historical, journal, checkpoint, and trading-runtime
    tests, and the managed frontend production build passed.
85. Reconciled implementation-backlog status drift against already shipped
    code. Full live-demand identity deduplication, live Canvas gap resnapshot,
    partial/stale/corrected/transition presentation, and cross-mode workspace
    isolation now have checked parent rows matching their completed subitems.
    Stale text claiming Backtest still lacked the shared resolver/overlay was
    corrected. Research workspace adoption, Live/Paper executable proposal
    migration, and durable historical resume remain explicitly open.
86. Added the separate Research workspace without inventing a second Canvas
    configuration authority. The route resolves the approved Canvas profile,
    instantiates the shared request-mode container surface, and persists layout,
    link, symbol, and display changes under an isolated
    `research.<workspace>` revision scope. It neither creates a Replay run nor
    acquires executable account authority; reset, rebase, and save-as cannot
    rewrite Configuration defaults. Research is registered in navigation and
    the managed visual-review page matrix. The managed TypeScript/Vite
    production build passed. Targeted normal and compact route captures
    reported zero objective issues; with the backend intentionally absent they
    also verified the explicit `Research unavailable` fail-closed state. The
    shared successful Canvas surface had already been validated separately.
87. Moved the bounded ticker-presentation ClickHouse query into the registered
    `market.ticker_presentation.v1` plan. The plan is now the single SQL
    authority for latest reference snapshots, issuer naming, linked-logo
    precedence, deterministic legacy-asset fallback, and one-row-per-ticker
    reduction. The service keeps its 200-symbol syntax bound, graceful optional
    branding degradation, response projection, and a compatibility re-export
    for existing callers. Python compile and all 15 focused application-registry
    and ticker-presentation tests passed.
88. Unified current Live universe selection and command-time tradability lookup
    under the registered `market.tradable_universe.v1` query plan. The full
    projection preserves latest universe/scanner selection and joins issuer and
    presentation facts; the bounded lookup preserves the universe date,
    tradability decision, exclusion reason, and IBKR conid used by Live/Paper
    preflight. The market-data loader and trading service now resolve only the
    server-owned database and call the shared builders. Python compile and all
    22 focused registry, plan, compatibility-caller, market-data configuration,
    Live preflight, and order-authority tests passed.
89. Closed the focused-computation state lifecycle in QMD Live. Removing or
    narrowing a lease now immediately reclaims unreferenced indicator
    calculators, history series, base rows, microstructure aggregates, tick
    windows, and microstructure windows; a 30-second sweep handles silent TTL
    expiry. Cleanup checks the current target set while holding each indicator
    shard, so concurrent or overlapping activation wins safely; workers also
    recheck the lease after dequeue so pre-removal queue entries cannot recreate
    state. Every lease
    scope now warms a missing ticker/timeframe once from authoritative core bars
    while repeated refreshes avoid copying the same 500-bar window. DELETE
    responses expose exact reclaimed counts. Cargo formatting passed and all 96
    QMD Gateway library tests passed using the external runtime target.
90. Replaced the application registry's composition-service pointer for
    `reference.identity_for_symbol.v1` with a real versioned query builder. The
    extracted anchor preserves the causal universe-day and recording-time
    cutoffs, point-in-time symbol/listing/security/issuer joins, and deterministic
    tradable USD stock preference. Ticker Facts keeps the former
    `identity_anchor_sql` name as a compatibility import, so route behavior did
    not change. Python compile and all 27 focused query-plan, registry, and
    ticker-facts service tests passed.
91. Completed the non-fundamental Ticker Facts query-plan split. The registered
    `reference.ticker_facts.v1` bundle now owns causal market snapshot, float,
    borrow, short-interest/volume, FTD, Reg SHO, identifier, classification,
    split/dividend, and daily-volume builders. Ticker Facts retains independent
    source degradation and compatibility imports but no longer owns that SQL.
    The registry now routes IPO fields to `reference.scanner_asof.v1`, which is
    the path that actually loads IPO data, and keeps SEC/XBRL fundamentals in
    their distinct plan. Python compile and all 28 focused query-plan, registry,
    and Ticker Facts tests passed.
92. Made the advertised SEC fundamentals plan executable without changing the
    deferred SEC Gateway. The versioned backend builder now owns bounded current
    XBRL facts, one-tag comparison history, and multi-tag causal history with
    explicit filing and recording cutoffs. Ticker Facts retains its existing
    function names as compatibility wrappers, while the application registry
    points `sec.fundamentals_asof.v1` at the real query bundle instead of the
    historical Scanner composition service. Python compile and all 28 focused
    fundamentals-plan, registry, and Ticker Facts tests passed.
93. Moved Historical Scanner's all-universe XBRL read into the same versioned
    fundamentals authority. The set-based builder causally resolves the latest
    recorded universe, selects CIK identity from that publication, applies both
    filing and recording cutoffs, and bounds comparison history per ticker and
    tag. Historical Scanner now owns only fact analysis/card projection after
    the query. Python compile and 49 focused fundamentals, historical Scanner,
    Ticker Facts, registry, and Watchlist tests passed. One broader Canvas test
    remains stale against the pre-existing News query (`provider_tags` versus
    `channels`) and is unrelated to this change.
94. Closed fallback lineage for the durable shared trading journal. Records
    created outside an HTTP context now receive a bounded run-derived
    correlation ID and an event/category/entity/time-derived causation ID.
    Explicit domain lineage from Strategy, Portfolio, OMS, or a request still
    wins, so this does not overwrite more precise causal predecessors. This
    covers background Replay, Backtest, Strategy, Portfolio, and OMS journal
    continuations; autonomous QMD source-event lineage remains a separate open
    item. Python compile and the focused journal/runtime tests passed.
95. Made `reference.scanner_asof.v1` a real versioned query plan and removed the
    SQL from Historical Scanner composition. The single set-based query covers
    tradable identity, issuer/security labels, country, market cap, shares,
    float, short interest, IPO/split proximity, and presentation assets. During
    extraction, three point-in-time leaks were corrected: Scanner-static,
    current issuer branding, and active logo assets now all require publication
    no later than the workspace clock. The registry now lists the plan's actual
    universe, identity, presentation, and reference sources. Python compile and
    all 31 focused plan, registry, Historical Scanner, and Watchlist tests
    passed.
96. Standardized backend HTTP failures without breaking route success payloads.
    HTTP exceptions and request-validation errors now emit one schema-v1
    envelope with compatibility `detail`, `complete=false`, warnings, stable
    code, message, retryability, status, and correlation/causation IDs. The
    shared frontend API client projects those typed fields onto `ApiError` and
    still produces the same readable message for existing callers. Python
    compile, 43 focused response/request/QMD/workload tests, a real ASGI 404
    contract check, and the managed TypeScript/Vite production build passed.
97. Completed the application response-envelope migration without breaking
    external callers. JSON success responses now negotiate schema v1 through
    `X-Response-Envelope: 1`, carrying `data`, completeness, warnings, and
    correlation/causation metadata. Requests without the header receive their
    byte-compatible route shape. The shared frontend client—the only direct
    browser `fetch` authority—requests the envelope for all 127 API call sites
    and unwraps the inner payload before returning it to pages. Existing QMD
    partial-coverage warnings are promoted to the outer contract. Python
    compile, all 45 focused response/request/QMD/workload tests, negotiated and
    legacy ASGI checks, and the managed TypeScript/Vite build passed.
98. Made historical decision-source authority durable. Replay and Backtest now
    persist the approved configuration identity plus QMD event,
    per-ticker/timeframe derived, and Scanner-signal plan hashes and revision
    tokens in the run snapshot/manifest and journal. A second observation of
    the same source key must match exactly or the run fails and requires a new
    run. Backtest Debug records its fixture content hash as both source plan and
    revision. Missing QMD revision evidence fails closed. Storage-level reads
    of superseded revisions and complete Watchlist-enrichment revision evidence
    remain explicit open work. Python compile and all 44 focused Replay,
    Backtest, Debug, source-continuation, and Canvas-contract tests passed.
99. Completed causal historical enrichment authority for Watchlist decisions.
    Every session-boundary membership resolution now returns and persists its
    full-market Scanner archive revision, technical archive revision and exact
    windows/schema, reference query-plan version and clock, and fundamentals
    plan version/clock when a rule requests it. The fundamentals authority is
    retained even when no facts exist, distinguishing causal absence from an
    unrecorded dependency. Replay/Backtest journal this bundle before Strategy
    evaluation and include it in the run manifest. Python compile and all 64
    focused Replay, Watchlist, Scanner, reference-plan, and fundamentals-plan
    tests passed.
100. Bounded Historical Scanner background materialization. Scanner snapshots
     and QMD-derived cross-sectional snapshots now admit at most four concurrent
     builds per family; excess requests return a retryable
     `capacity_limited` state rather than spawning another thread. Coordination
     registries retain at most 256 entries, expire terminal state after one
     hour, and evict only oldest ready/error entries--never active builds. The
     durable ClickHouse snapshots remain authoritative and are unaffected by
     coordination eviction. Python compile and all 60 focused Scanner,
     Watchlist, Replay, and capacity tests passed.
101. Completed revision-safe, bounded historical chart caching. QMD History's
     existing entry, byte, row/update, concurrency, and broadcast bounds remain
     the enforced cache limits. Cache keys and offline computation requirements
     now explicitly include event-source, calculation, and corporate-action
     revisions; the current price authority is declared as
     `raw-unadjusted-v1`, so a future adjusted-price policy cannot reuse stale
     entries. Chart provenance exposes both revisions and Canvas presents them
     to manual and semi-automatic users. All 12 focused Rust cache tests and the
     managed TypeScript/Vite production build passed.
102. Enabled fail-closed restart recovery for Replay, Backtest, and Backtest
     Debug. Checkpoint schema v2 atomically captures raw-event and derived-frame
     cursors, event and calculation clocks, controller/Strategy caches,
     assignment state, the complete deterministic simulator ledger and order
     state, Watchlist membership, runtime counters, and pinned data authority.
     Portfolio and OMS recover from the same durable SQLite-WAL journal before
     source continuation. Services discover persisted manifests after backend
     restart; mode-specific resume APIs and UI controls reject completed,
     incomplete, identity-drifted, and legacy cursor-only runs. Validation
     included a real stop-after-first-event/new-service/continue-to-completion
     cycle, derived-only cursor coverage, exact simulator order-state round trip,
     98 focused runtime/OMS tests, and the managed frontend production build.
103. Added a shared lifecycle status contract for historical trading runs and
     market-data background builds. Both now expose canonical state, bounded
     progress, terminal state, timestamps, failure/retry semantics, checkpoint
     capability, command availability, resource identity, and explicit control
     authority without removing their existing compatibility fields. Paused
     jobs advertise in-place resume, failed/cancelled jobs advertise stateful
     retry, and historical resume remains gated by checkpoint schema v2. All 42
     focused lifecycle and Replay controller tests passed. Live/Paper execution
     lifecycle and intelligence research-job adoption remain open because their
     controllers are outside this approved service-change phase.
104. Bounded market-data build-job collection reads. The backend now validates
     a caller limit of 1-500 (default 100), selects only the newest durable job
     files before loading payloads and event summaries, and returns row-count
     and limit evidence. Three focused lifecycle/job-list tests and Python
     compilation passed. A managed localhost frontend was also started and
     reached HTTP 200 for visual validation, but the in-app browser policy
     blocked localhost reload; the exact processes were stopped and browser
     acceptance remains open rather than being inferred from the build.
105. Completed the shared frontend accessibility and appearance foundation.
     The existing persisted theme catalog, five-level global UI scale,
     zoom-aware viewport sizing, and responsive page rules remain authoritative.
     The application shell now adds a keyboard skip link and unique main
     landmark in both standard and chromeless layouts, a consistent visible
     focus fallback, reduced-motion behavior, and explicit expanded/selected
     semantics for appearance controls. The production build passed; the
     separate real-browser acceptance gate remains open because localhost
     navigation is policy-blocked in the available in-app browser.
106. Added the application HTTP authority boundary. A system-configured policy
     now binds user, workspace, environment, mode, stable account key, and
     command permissions before route dispatch. Local mode is loopback-only;
     proxy mode requires a secret-backed trusted token and injected identity.
     Browser mutations validate origin and cross-site fetch context. Authorized
     and denied mutations emit structured correlation/causation-aware audit
     events. All application WebSockets apply the same authority before opening
     an upstream connection or run subscription, while
     `/api/system/authority` exposes non-secret policy and request authority for
     review without creating a user-editable deployment screen. Fifteen
     authority and response-contract tests passed, including actual HTTP and
     WebSocket origin rejection and secret-redaction coverage; Python
     compilation passed. Broker and deferred producer services were unchanged.
107. Standardized operational evidence across every registered service. The
     backend schema-v2 projection now always carries authority, freshness,
     coverage, queue, cache, transition, checkpoint, and degradation fields,
     while preserving missing producer evidence as `null`, empty, and unknown.
     The Service Health detail page renders Coverage, Freshness, Queue,
     Checkpoint, Degradation, and Authority uniformly with responsive layouts;
     it never labels absent evidence clear. Three focused projection tests plus
     the fifteen authority/response tests passed, Python compilation passed,
     and the managed frontend production build passed. This is application-side
     composition only; no deferred producer or broker service was changed.
108. Closed a hidden correlation-lineage break in concurrent backend
     composition. Ordinary Python thread pools do not propagate request context,
     so downstream QMD and other fan-out calls could lose the request IDs even
     though direct calls preserved them. A shared context-preserving executor
     now copies the submitting context separately for every backend worker
     across QMD, Canvas, account, facts/reference, SEC composition, and live
     market-data helpers. Forty request-context and QMD client tests passed,
     including actual three-worker QMD catalog lineage and sequential
     no-leakage tests; all migrated modules compiled. Deferred producer and
     broker services were unchanged.
109. Added durable causal lineage to generic market-data background jobs. Each
     submission stores a correlation root and parent cause, workers receive
     both identities across the subprocess boundary, and every append-only job
     event carries correlation, event causation, and parent causation. An
     autonomous event derives a stable identity from its job and event fields;
     pause/cancel/retry actions preserve the active command cause. Stateful
     retry keeps the original build correlation and records both the previous
     failure cause and retry command. Forty-four job, request-context, and QMD
     client tests passed and the changed modules compiled. Remaining raw market
     source-event lineage stays open at QMD's source schema boundary.
110. Completed autonomous QMD source-event lineage in the shared Live/History
     decoder. Each canonical event now derives a bounded ticker/session
     correlation root and an event causation hash from ticker, SIP clock,
     source sequence, type, prices, sizes, and exchanges before downstream
     computation. Live and recent storage preserve the vendor sequence. The
     deployed legacy archive schema does not, so QMD History must use its
     deterministic ordinal fallback; ordering and lineage presence remain
     exact, but cross-tier causation equality is not claimed for those rows.
     Decoded raw metadata carries both IDs. Seven focused QMD compact-event
     tests and eight QMD History source tests passed using the external Cargo
     target.
111. Added a read-only QMD authority acceptance runner. It validates both QMD
     service identities/readiness, exact archive/recent/current-live/gap source
     tiling, source-plan and pinned page-revision agreement, deterministic
     event ordering, and correlation/causation presence. Evidence is written
     atomically only under `D:\TradingML\runtimes\qmd_validation`. Three focused
     tests and Python compilation passed. Representative active-service runs
     remain an explicit acceptance gate; the harness does not manufacture a
     parity verdict when QMD is unavailable or the requested population is
     truncated.
112. Corrected QMD fan-out priority under backpressure. After the minimal Core
     Scan state update, compact and optional raw persistence are now admitted
     before live-state, bar, and indicator consumers can block the producer.
     Replaceable broadcasts stay non-blocking and require resnapshot; canonical
     Scanner and bar events remain lossless rather than being dropped. A new
     bounded-channel regression test proved compact admission while the raw
     lane was saturated; all 98 QMD tests and formatting validation passed.
113. Closed the streaming reconnect/resnapshot protocol test gap. A backend
     WebSocket route test now proves the upstream QMD subscription is active
     before the bounded ticker snapshot is captured, then verifies a typed
     `resnapshot_required` gap frame bypasses ticker filtering and reaches the
     client. The existing Canvas consumer closes, reconnects with bounded
     backoff, and replaces state from the new snapshot. Forty-seven focused
     QMD client, stream, and application-authority tests passed. Real-browser
     visual acceptance remains separately open because localhost navigation is
     policy-blocked.
114. Extended the QMD authority runner with approved direct-ClickHouse Scanner
     primitive parity. For a complete durable plan, the probe expands only
     QMD-declared archive years and the registered recent event table, performs
     bounded explicit-ticker reads, and compares first/last/five-minute price,
     volume, trade count, quote count, and population with QMD History's decoded
     ordered stream. It refuses gaps, current-live continuations, unapproved
     sources, and truncated QMD reads. Six focused tests and Python compilation
     passed. Runtime parity evidence remains open because port 8800 currently
     identifies the separately authorized IBKR Gateway Supervisor and QMD
     History is unavailable on 8801.
115. Completed the in-scope trading authority regression gate in the required
     external runtime. Two hundred one Portfolio, OMS, canonical projection,
     Replay/Backtest, Strategy campaign, configuration, adaptive-risk, and
     runtime service tests passed. Coverage includes cross-process fenced
     admission, lease expiry, reservation recovery, uncertain broker outcomes,
     partial fills/protection, restart, deterministic clocks and simulator
     state, and fail-closed direct-order authority. No broker gateway or
     supervisor code was changed or used as execution authority.

     The active point-in-time contract audit also passed 55 Reference,
     fundamentals, Watchlist, ticker-facts, Scanner, and feature-projection
     tests. One pre-existing News Synthesis assertion still expects
     `provider_tags` although the current v48 query no longer selects it. That
     intelligence test drift remains deferred rather than being silently
     rewritten during this phase.
116. Real durable QMD acceptance exposed and fixed an archive schema regression.
     `market_sip_compact.events_2026` has the documented legacy compact schema
     and no vendor `source_sequence`, but QMD History had begun selecting that
     nonexistent column for archive rows. The adapter again uses deterministic
     archive `ordinal` as the wire-contract fallback while recent `q_live` rows
     retain vendor sequence. Documentation no longer claims fabricated
     cross-tier causation equality for legacy rows.

     The acceptance runner now has a fail-closed `--allow-history-only` mode
     available only for fully durable plans and reports bounded ClickHouse HTTP
     diagnostics. Against real QMD History and ClickHouse, the pinned
     2026-08-07 13:30-13:31 UTC AAPL/MSFT window passed with 48,071 events over
     two pages, 48,071 lineage-bearing decoded rows, exact plan hash
     `fnv1a64:24bdd17a110cb65f`, two matching Scanner rows, and zero failures.
     The report is
     `D:\TradingML\runtimes\qmd_validation\qmd_authority_validation_20260811T143323Z.json`.
     All 30 QMD History tests and eight acceptance-runner tests passed; QMD
     History was stopped after validation. The separately authorized IBKR
     Supervisor still occupies 8800, so Live/recent/archive transition proof
     remains open.
117. Reconciled every remaining unchecked implementation item against the
     approved service boundary and current runtime evidence. The remaining
     work is not one undifferentiated coding queue:

     - QMD immutable revision continuation needs a version-retaining storage
       authority; the current pin detects drift but cannot read an overwritten
       old revision.
     - The nullable ticker snapshot and compact-event array compatibility
       endpoints still have production-source callers in Market AI and Text
       Intelligence, so zero-caller retirement cannot be claimed while those
       producer migrations are deferred.
     - Generic Structure still executes in the all-market bar store. Safe
       focused activation requires an atomic source-sequence barrier plus exact
       event replay since the restored checkpoint; gating it without that
       handoff would silently corrupt state.
     - Enrichment-triggered recomputation depends on deferred News, SEC, Text,
       and Reference producers publishing registered FeatureUpdate events.
     - Paper/Live chart proposals, the shared mode controller, unified proposal
       control, and Live/Paper lifecycle migration cross the separately
       authorized broker/gateway/supervisor boundary. Replay and Backtest remain
       the implemented broker-neutral proof path.
     - Intraday causal Backtest Watchlist refresh needs a durable historical
       membership-delta product; repeated all-market snapshots are not an
       acceptable substitute.
     - Representative active-session Scanner/load budgets and the complete
       archive/recent/live QMD acceptance run require a correctly assigned QMD
       Live endpoint. Port 8800 currently identifies IBKR Gateway Supervisor.
     - Real visual acceptance remains environment-blocked because the available
       in-app browser policy refuses localhost navigation; a successful build
       and HTTP readiness are not recorded as visual proof.

     No deferred producer, broker, gateway-supervisor, deployment, or unrelated
     user file was modified to bypass these constraints. The unchecked items in
     the backlog therefore remain honest gates rather than being administratively
     marked complete.
118. Removed the remaining Watchlist-local daily-market projection SQL. The
     registered `market.daily_session_bars.v1` module now owns the causal
     previous-close and average-volume projection as well as its underlying
     completed-session relation; Watchlist composition only executes the
     versioned plan and projects its rows. Thirteen Watchlist runtime tests,
     focused causal-plan assertions, and Python compilation passed. The
     Miniconda runtime does not provide pytest, so the existing pytest wrapper
     file was not claimed as executed.
119. Added snapshot-before-delta semantics to the Canvas market-signal stream.
     The backend establishes the QMD subscription first, captures one bounded
     ticker signal snapshot with a versioned snapshot identity, then forwards
     ticker-scoped events and terminal resnapshot controls. QMD's signal store
     now assigns one monotonic publication sequence shared by its snapshots and
     flattened delta envelopes without changing the event field shape; lag
     controls expose the last delivered continuation sequence. All 99 QMD
     library tests and five backend route tests passed, proving sequence
     agreement, subscription ordering, initial snapshot delivery, and
     lag-control forwarding; the changed Python modules also compiled.

     This closes the backend snapshot/delta and reconnect parent contracts:
     Scanner rows, compact events, and signals have monotonic watermark-aligned
     deltas, while periodic chart products remain explicit complete replacement
     snapshots and every lagging raw/product stream terminates with a resnapshot
     requirement.
120. Registered the Live market-data and Services schema-preview queries as
     `market.schema_inventory.v1`. The plan owns the bounded `system.tables`
     and `system.columns` reads for the current ClickHouse database, configured
     service-table statistics, column discovery, at-most-100-row previews, and
     configured time-bucket counts. The Live gateway and application Services
     projection now execute those builders rather than carrying local SQL.
     Three focused plan/gateway tests, five bounded-cache/Services tests, and
     all nine application-registry tests passed; the changed Python modules
     compiled.
121. Added backend admission-budget evidence to the Services dashboard. The UI
     polls the existing system-owned workload contract independently of fleet
     health and shows active/limit usage, remaining capacity, completed work,
     rejected admissions, cumulative wait, and the configured admission wait
     for command, discovery, chart, simulation, offline, and general lanes.
     Unavailable evidence is explicit and limits are review-only. The managed
     TypeScript/Vite production build passed in the external frontend runtime;
     real-browser inspection remains under the existing localhost policy gate.
122. Defined the missing historical Watchlist cadence product at the QMD
     History/backend boundary. One pinned market window is replayed once through
     shared `qmd_core`; an approved compiled predicate/rank plan consumes QMD
     fields plus bounded causal external-feature value intervals and emits only
     explainable membership/rank transitions. Fixed event-window chunks carry
     state and revision evidence forward, avoiding both repeated all-market
     snapshots and an unbounded cadence-by-symbol matrix. Backtest wiring stays
     open until this product is executable and validated; the session-boundary
     fallback is not relabeled as cadence parity.
123. Implemented the backend compiler for the historical Watchlist timeline.
     It accepts only an approved configuration model, validates rule references,
     operators, comparators, time bounds and registered sources, separates
     QMD-owned fields from point-in-time external feature plans, rejects deferred
     intelligence/live-only evidence, bounds each state-carrying chunk to 1,800
     evaluations, and produces a deterministic SHA-256 plan identity. Backtest
     preflight exposes these plans and each controller journals their schema,
     cadence, QMD fields, external feature contracts, and hash as data authority.
     Four compiler/wiring tests and all 40 Replay/Backtest controller tests
     passed; the changed modules compiled.
124. Added the fail-closed QMD History admission boundary for compiled
     historical Watchlist plans. A typed Rust contract now rejects unsupported
     schemas, invalid clocks, widened chunk or membership bounds, unsupported
     QMD fields, incomplete or duplicate external-feature contracts, non-delta
     output, missing state carry, and altered content hashes before replay.
     The backend submits the exact plan through a typed client and rejects a
     mismatched returned identity. A fixed real compiler plan proves Python and
     Rust canonical JSON produce the same SHA-256 identity. All 32 QMD History
     tests and 39 focused backend compiler/client tests passed; the changed
     Python modules compiled.
125. Corrected a state-loss defect in full-market QMD History Scanner replay.
     The source contract is globally event-time ordered, but the worker had
     treated each ticker change as end-of-symbol and finalized/dropped that
     ticker's state; any later interleaved event restarted its bars and
     indicators. Each shard now owns one multi-symbol shared-computation engine
     for the entire window, preserving independent bar, indicator,
     microstructure, and signal state until terminal finalization. A regression
     test interleaves AAPL, MSFT, then AAPL and proves both symbol projections
     survive. All 33 QMD History tests passed.
126. Implemented the bounded transition reducer for historical Watchlists.
     QMD History now independently validates the exact compiled rule graph and
     source union, including unique referenced rules/conditions, comparators,
     score thresholds, right-hand dependencies, ranking input, expiry,
     overrides, and a two-million membership-slot chunk budget. Its reducer
     accepts exact cadence frames, fails closed on undeclared evidence, applies
     inclusion/exclusion/manual-override semantics, ranks deterministically,
     emits only add/remove/rank-change events, and carries a plan-bound state
     cursor into the next fixed chunk. A two-chunk test proves causal additions,
     reranking, removal, and continuation. All 35 QMD History tests passed.

     This is the internal product reducer, not yet the completed public
     materialization path: the single-pass event replay must still feed these
     frames, merge certified external-feature intervals, persist revisioned
     output, and replace Backtest's session-boundary fallback.
127. Added the executable single-pass QMD History Watchlist materializer. A
     bounded POST contract accepts the admitted plan plus exact external-feature
     intervals and complete per-field revision evidence. It pins a complete
     event-source revision, replays that stream once through shared QMD market,
     bar, indicator, microstructure, and signal state, refreshes the completed
     previous-session close at each New York session, and feeds only changed
     symbols into the persistent rank index. Trade-rate decay and indicator
     finalization also schedule causal updates without rescanning unchanged
     rows. Output chunks carry plan, market, calculation, external-feature, and
     materialization identities.

     Admission now limits external request bodies to a configurable 64 MiB,
     permits one materialization by default, caps event replay and transition
     slots, and returns retryable HTTP 429 when busy. The backend has a typed
     five-minute client that rejects a changed plan hash or missing
     materialization identity. All 35 QMD History tests, all 99 shared QMD
     library tests, and 36 backend client tests passed; Python compilation
     passed.

     This phase intentionally remains fail closed for aligned relative volume;
     substituting prorated daily volume would violate the configured field
     contract. Backend production of causal Reference/fundamental intervals,
     ticker-sharded load acceptance, durable reuse, and Backtest cutover remain
     explicit open items.
128. Corrected the historical Watchlist plan schedule before consumer cutover.
     Schema v1 treated the wall-clock span from the first session start through
     the last session close as continuous cadence time, which would evaluate
     nights and weekends and inflate a 20-session one-second plan to roughly
     1.7 million clocks. Schema v2 now content-hashes explicit New York 04:00 to
     20:00 evaluation windows, clamps partial first/last sessions, and maps the
     reducer cursor across closed gaps without weakening in-session cadence.
     Python and Rust share the new fixed plan hash. Five Rust plan/reducer tests
     and 14 backend compiler/registry tests passed; the changed Python modules
     compiled.
129. Implemented the registered external-feature interval provider and the
     identity-safe first Backtest cutover. The backend now queries registered
     Reference and SEC projections only at bounded source-change and session
     clocks, including effective/observed/publication availability rather than
     insertion time alone; diffs those projections into nonoverlapping causal
     intervals; and supplies complete content-hashed, query-plan-versioned
     evidence to QMD History. Point-in-time symbol/security/issuer/listing IDs
     and positive IBKR conids are materialized separately as control metadata,
     so they cannot broaden the compiled rule graph. QMD transitions are
     enriched from those intervals, fail closed on missing identity, and the
     application materialization identity binds the QMD replay to its identity
     revision.

     Backtest and preflight now consume the transition product for one
     Watchlist rather than the session-boundary snapshot fallback, preserving
     identity and complete authority in each causal membership snapshot. The
     active path still rejects multiple configured Watchlists: they require a
     shared multi-plan QMD event pass, not repeated full-market replays. All 48
     focused backend tests and all 36 QMD History Rust tests passed; the changed
     Python modules compiled.
130. Replaced the remaining one-Watchlist admission limit with a bounded shared
     batch product. QMD History now accepts one to 64 unique plans sharing exact
     replay bounds, pins and reads the market stream once, advances one shared
     computation engine at the union of their evaluation clocks, and preserves
     independent dirty sets, external boundaries, requested QMD sources,
     cadence cursors, rank indexes, chunk state, and revision evidence for each
     Watchlist. Aggregate evaluation and membership-slot ceilings prevent a
     valid collection of individually bounded plans from broadening service
     work without limit.

     The backend submits one typed batch, binds QMD and identity revisions into
     application materialization identities, and unions membership causally.
     A ticker removed from one Watchlist remains active while another owns it;
     conflicting conids fail closed. Eighty-two focused backend tests and all
     36 QMD History Rust tests passed. Durable revision-keyed persistence,
     aligned 20-session relative volume, ticker-sharded load acceptance, and
     real service/database acceptance remain separate open gates.
131. Made historical Watchlist reuse durable and correction-safe. The prior
     process-local cache identity included plans and external-feature revisions
     but omitted the QMD market-source revision, so a corrected source could
     incorrectly reuse an old timeline. QMD History now exposes the exact
     complete source-plan hash and revision token for a bounded window. Backend
     memory and durable cache keys bind that evidence with every plan,
     external-feature revision, and point-in-time identity revision; the
     materializer rejects a source revision that changes between lookup and
     replay rather than caching mixed authority.

     Durable envelopes are written atomically under
     `D:\TradingML\runtimes\qmd_history\watchlist_timelines`, verify their own
     content hash before reuse, reject mismatched revisions, cap each file at
     256 MiB, and retain at most 64 exact materializations. Eighty-four focused
     backend tests and all 36 QMD History Rust tests passed; the changed Python
     modules compiled.
132. Implemented causal historical aligned relative volume and restored the
     intended focused-computation funnel. Historical Watchlist plan schema v3
     content-hashes the live runtime's five-times-maximum-size liquidity seed.
     QMD History maintains an incremental all-market liquidity rank using only
     compact Core state, evaluates VWAP/relative-volume Watchlists over that
     bounded seed plus retained/manual members, removes tickers that leave the
     seed, and prewarms a two-seed-width buffer capped at 2,000 tickers to avoid
     per-clock churn queries. Aggregate focused evaluation slots are separately
     bounded.

     For each needed ticker/session, QMD selects the exact prior 20 completed
     three-segment sessions from daily authority, reads their pinned archive or
     recent event plan, applies the shared Massive trade-condition
     `update_volume` rule, constructs cumulative 10-second elapsed-session
     profiles, and divides current causal session volume by their mean. Zero or
     incomplete baselines remain missing evidence; daily-volume proration is
     never substituted. Per-session ticker hashes and source revisions enter
     each Watchlist materialization identity, and the backend durable cache
     conservatively binds the entire 45-day dependency window and rechecks it
     after replay.

     Eighty-nine focused backend tests, all 37 QMD History tests, and all 99
     shared QMD tests passed. Representative database/load acceptance and
     multi-session baseline build optimization remain under the explicit
     sharding/performance gate rather than being inferred from unit coverage.
133. Partitioned historical Watchlist materialization by stable ticker
     ownership. Each configured History Scanner shard now owns its daily
     reference state, compact-event updates, indicator finalization,
     relative-volume baseline profiles, liquidity lookups, and focused
     candidate projection. Evaluation clocks remain globally coordinated so
     independently owned ticker state is finalized at the same causal boundary
     before cross-sectional rank and membership reduction. The shard-ownership
     test covers interleaved events and stable routing; all 38 QMD History Rust
     tests passed.

     The representative real-data load gate did not pass and remains open. An
     isolated release service against `market_sip_compact` returned HTTP 200
     for a 2026-08-07 13:30:00Z to 13:30:02Z full-market request, consuming
     683,497 events into two evaluation clocks and 25 transitions in 25.779
     seconds. Resident memory was 1.268 GB afterward. A preceding one-minute
     request completed after roughly 212 seconds and the process recorded an
     8.084 GB peak working set. Evidence is outside the repository at
     `D:\TradingML\runtimes\qmd_goal_load\acceptance_2s.json`; the isolated
     service was stopped. Ticker sharding currently bounds state ownership but
     does not yet parallelize shard finalization or eliminate full-stream
     memory pressure, so no throughput or memory approval is claimed.
134. Added the non-executing Live/Paper Canvas proposal handoff without
     crossing the broker-deployment boundary. The Canvas order-entry panel now
     uses the stable application account key rather than treating a masked
     broker account ID as authority, enables manual and semi-automatic
     proposals in Paper/Live workspaces, and carries the chart's last causal
     event clock as its client price sequence.

     The backend resolves the real account only server-side, verifies the
     approved Run Plan binds that account and mode, resolves current registered
     tradable-universe identity, requires an exact conid, replaces client price
     claims with the current QMD ticker-state snapshot and Scanner sequence,
     enforces both client and authoritative freshness, validates quantity,
     action, and directional stop/target placement, and journals an idempotent
     semantic proposal. Its response explicitly states that broker submission
     is false and that the shared Live/Paper runtime must repeat Portfolio and
     OMS validation before execution. This avoids creating a stranded capital
     reservation while broker deployment remains separately unauthorized.

     Eighty-three focused backend/runtime tests passed, including six new
     proposal-validation cases and Live/Paper authority-mode routing. The
     managed external-runtime frontend TypeScript and production Vite build
     passed. The in-app browser still blocked navigation away from its prior
     localhost connection-error document despite both managed services being
     HTTP-ready; the exact validation processes were stopped, and visual
     acceptance remains open rather than being inferred from the build.
135. Replaced full-population Watchlist rule re-evaluation with a targeted,
     configuration-aware eligibility index. The shared pure resolver now
     separates one-symbol rule evaluation from cross-sectional rank and size
     reduction without changing fail-closed missing-evidence, manual override,
     or deterministic ordering semantics. For each Watchlist, the backend
     derives the exact row fields used by its active inclusion/exclusion rules
     and ranking field, fingerprints only those values, and re-evaluates new,
     changed, or removed symbols. A change to float, short interest,
     fundamentals, labels, price, or another configured dependency therefore
     affects only the corresponding symbols; advancing provenance timestamps
     alone cause no rule work. Changing the Watchlist or any selected rule
     invalidates and safely rebuilds the affected index.

     Twenty focused Watchlist resolver/runtime tests passed, including a new
     regression proving that a provenance-only revision recomputes zero of two
     symbols and one changed rule input recomputes exactly one. The attempted
     wider command named a nonexistent test module after those twenty tests
     passed; no result is claimed for that nonexistent module.
136. Removed an unintended expensive computation from QMD History's
     all-market Watchlist materializer. `CrossSectionEngine` previously used
     the normal live bar-store constructor, which allocated and updated a
     `GenericStructureEngine` for every ticker even though historical
     Watchlist schema v3 does not admit Generic Structure as a rule source.
     The shared bar authority now has an explicit structure-disabled
     constructor. It preserves the same bars and indicators with default-empty
     structure payloads and allocates no structure engines; normal QMD Live and
     chart/history structure paths retain the existing structure-enabled
     constructor.

     All 38 QMD History tests and all 100 shared QMD tests passed. The exact
     isolated release acceptance request used for entry 133 returned the same
     683,497 events, two evaluation clocks, and 25 transitions in 21.197
     seconds versus 25.779 seconds, a 17.8 percent reduction. The fresh process
     peaked at 1.743 GB and released to about 22 MB after the response. This
     does not close the performance gate: two market seconds still require
     about 21 seconds, and live Generic Structure still needs its separate
     sequence-safe focused-activation design. Evidence is outside the
     repository under `D:\TradingML\runtimes\qmd_goal_load` and the isolated
     PID 40184 was stopped with port 18801 verified free.
137. Made the historical Watchlist engine compile its top-level computation
     path from the admitted batch dependency union. A batch that needs only
     Core market fields now instantiates the shared market-state authority and
     skips bar stores, indicator calculators, microstructure intervals, and
     market-signal state entirely. A batch declaring VWAP retains the
     structure-disabled derived path, so missing evidence is never replaced by
     a cheaper formula. Relative volume continues to use its separately pinned
     20-session aligned baseline and current causal Core volume; it does not
     activate bars.

     All 39 QMD History tests passed, including a regression proving that the
     market-only profile produces last price, volume, and liquidity rank while
     allocating no bar, indicator, microstructure, or signal state. The exact
     isolated 683,497-event release request returned the same two evaluation
     clocks and 25 transitions in 13.551 seconds, 47.4 percent faster than the
     original 25.779-second baseline. The fresh process peaked at 99.6 MB and
     released to 15.7 MB. This remains only about 50,000 events per second and
     therefore does not pass representative active-session throughput. Evidence
     is under `D:\TradingML\runtimes\qmd_goal_load`; isolated PID 39452 was
     stopped and port 18801 was verified free.
138. Reduced the internal QMD History full-market stream wire overhead without
     changing public APIs. The ordered ClickHouse stream no longer repeats 17
     JSON field names for every event. It uses one fixed tab-separated compact
     row contract and a strict parser that rejects the wrong column count,
     invalid UTF-8, or any incorrectly typed value before canonical compact
     decoding. Public event pages, source revisions, materialization requests,
     and responses remain unchanged JSON contracts.

     Rust formatting and all 40 QMD History tests passed, including exact TSV
     column preservation and malformed-row rejection. Two isolated real-data
     runs returned the identical 683,497 events, two evaluation clocks, and 25
     transitions. They consumed 2.609 and 2.406 QMD CPU seconds versus 3.453
     seconds in the preceding JSON run, but wall times were 18.095 and 14.205
     seconds versus 13.551 seconds. The result therefore proves lower service
     decode cost but not lower end-to-end latency; ClickHouse/query variance and
     transport remain the bottleneck. Evidence is under
     `D:\TradingML\runtimes\qmd_goal_load`; PID 42184 was stopped and port
     18801 was verified free.
139. Corrected QMD History materialization error semantics. The single and
     batch Watchlist endpoints previously mapped every replay failure to HTTP
     400, including ClickHouse outages, corrupt upstream rows, pinned source
     drift, resource ceilings, and internal worker failures. They now emit
     stable codes and actions: invalid requests remain 400, bounded work that
     exceeds an admitted ceiling is 413, source revision changes are retryable
     409 restart conflicts, upstream data failures are retryable 502 responses,
     internal failures are 500, and capacity contention is a typed retryable
     429. Every response carries `error_code`, `retryable`, `retry_action`, and
     `source` while retaining the existing human-readable `error` field.

     Rust formatting and all 41 QMD History tests passed. The new regression
     covers invalid request, resource-limit, source-conflict, and upstream
     classifications so callers can distinguish request correction from retry
     and full materialization restart.
140. Registered the backend's bounded reads of already-published intelligence
     outputs without changing any deferred producer. The new
     `intelligence.published_consumer.v1` plan owns query construction for
     current-version News Synthesis documents and document-scoped SEC labels.
     Both reads require an explicit bounded source-ID set, preserve the
     producer-issued document identity and source clock, and retain the exact
     latest-version selection and presentation payload behavior. Dynamic table
     identifiers fail closed before query construction.

     The backend News and scoped-label loaders now contain only request
     normalization, result grouping, and presentation mapping. Text
     Intelligence engines, schemas, and publication code were not changed.
     `news_prior_context` was deliberately not migrated in this phase because
     research labeling imports that shared utility directly; moving it would
     cross the deferred producer/research boundary. Nineteen focused registry,
     query-plan, News presentation, scoped-label, and SEC Canvas tests passed,
     and every changed Python module compiled with bytecode writes disabled.
141. Moved the five canonical News detail reads out of the backend route and
     into registered `news.detail_asof.v1` builders. Service detail binds the
     canonical News ID to the exact event, rendered-text revision, and ticker
     link. Trading detail first resolves the event identity, then uses its
     published date, provider article ID, and source-revision key for bounded
     rendered-text and ticker reads. Empty or incomplete identities fail
     closed. The route still owns ISO timestamp validation, query-session
     hints, optional Synthesis enrichment, transport error mapping, and the
     public presentation contract; News Gateway and Text Intelligence code and
     publications were not changed.

     The wider route test also caught and corrected a compatibility regression
     from entry 140: `app.py` still imports `LIVE_SEMANTIC_TABLE` through the
     News presentation module, so that re-export is retained until its callers
     migrate. Thirty-one focused query-plan, route, application-registry, News
     presentation, and scoped-label tests passed, and all changed Python
     modules compiled with bytecode writes disabled.
142. Moved the Services News operational reads into the registered
     `news.operations_intraday.v1` plan. The plan owns the bounded New York
     market-day summary, exact event/rendered-revision rows, and complete
     fixed-width histogram bucket query. It enforces the half-open UTC window,
     deterministic ordering, and a defensive 1,000-row ceiling independently
     of the stricter route limit. `app.py` retains market-calendar window
     selection, bounded caching, JSON decoding, and the operational response
     projection; News Gateway schemas and publication behavior were unchanged.

     Twenty-nine focused operational-plan, News detail, trading-News route,
     and application-registry tests passed. All changed Python modules compiled
     with bytecode writes disabled.
144. Extended `sec.operations_intraday.v1` to own the Services SEC market-day
     summary, bounded parent-filing page, and document, rendered-text,
     company-fact, and frame aggregates. Related reads are generated only for
     the exact unique CIK/accession keys returned by the bounded parent page;
     an empty page executes no aggregate query. The plan independently caps
     parent rows at 1,000 while the route retains its stricter product limit.
     The backend still owns SEC Gateway recent-feed composition, identity
     presentation, classification, and optional degradation.

     Fourteen focused SEC operations, SEC Canvas, and application-registry
     tests passed. All changed Python modules compiled with bytecode writes
     disabled.
146. Completed the Services SEC operational query bundle by moving exact
     filing-detail SQL into `sec.operations_intraday.v1`. One validated
     CIK/accession identity now produces the parent, documents, rendered text,
     company facts, and frame queries. The parent stays mandatory and bounded
     to one latest row; company facts and frames retain their 300-row ceilings;
     child reads remain independently degradable in the service composition.
     SEC and Reference producers were not changed.

     Sixteen focused SEC operations, SEC Canvas, and application-registry tests
     passed. All changed Python modules compiled with bytecode writes disabled.
145. Moved the Services SEC operational identity join into
     `sec.operations_intraday.v1`. The builder accepts a nonempty, normalized,
     deduplicated CIK set and joins the published SEC bridge to issuer,
     security, listing, and symbol identities in one bounded query. Ordering
     preserves the existing primary-symbol, primary-listing, confidence, and
     ticker precedence used by backend presentation. The consumer rejects a
     database other than the registered `q_live` authority instead of silently
     issuing a query against an unregistered source. Reference and SEC Gateway
     publications were not changed.

     Fifteen focused SEC operations, SEC Canvas, and application-registry
     tests passed. All changed Python modules compiled with bytecode writes
     disabled.
143. Moved the Services SEC market-day classification histogram into the
     registered `sec.operations_intraday.v1` plan. The query uses the bounded
     half-open `accepted_at_utc` window and the canonical CIK/accession identity
     to classify each filing by the strongest published downstream state:
     XBRL, rendered text, document only, or filing only. It reads only existing
     SEC Gateway publications and does not change their schemas or production.
     The backend route retains caching, optional degradation, JSON decoding,
     and operational presentation.

     Fifteen focused SEC operational-plan, SEC Canvas, News operational-plan,
     and application-registry tests passed. All changed Python modules compiled
     with bytecode writes disabled.

The authoritative remaining work and acceptance gates are maintained in the
[complete implementation backlog](14-implementation-backlog.md). A checked
item means its real runnable path was implemented and focused validation passed;
it does not imply all application work is complete.

147. Closed the real-browser frontend acceptance gate and repaired the
     standalone backend launcher discovered by that gate. `run_backend.ps1`
     no longer assumes a bare `python` command exists: it accepts an explicit
     interpreter, then resolves the active Conda interpreter, PATH, or the
     standard per-user Miniconda/Anaconda locations and fails before startup
     with an actionable message if none exists. The workspace starter passes
     its already-resolved interpreter into the backend launcher, keeping all
     three managed processes on one explicit Python authority.

     The repository-managed frontend and backend were then exercised together
     in the in-app browser. Market Discovery rendered Universal Ingest first,
     locked it as system authority, and showed all six registered primitives.
     Canvas, Portfolio, OMS, Live, Replay, Backtest, and Service Health reached
     their configured or truthful dependency/approval gate states. The Market
     Discovery viewport was visually inspected, all route DOM contracts were
     inspected, and browser warning/error logs were empty. A first failed
     configuration load was traced to an older backend process and was not
     accepted as a UI pass; the current backend returned the complete base
     configuration successfully before the repeated browser checks.

148. Moved the complete SEC Canvas read domain out of its presentation and
     composition service into the registered `sec.canvas_asof.v1` backend query
     plan. The plan now owns filing discovery and cursor filtering, approved
     disclosure taxonomy, scoped-label accession filtering, document/text/XBRL
     coverage, related filing entities, point-in-time identity, filing detail,
     document metadata, rendered/original text pagination, and XBRL fact pages.
     Its registry record declares every existing SEC, intelligence-publication,
     and identity source it reads. SEC Gateway and Reference Gateway producer
     code and schemas were not changed.

     `sec_canvas_service` now owns validation, bounded fan-out, optional
     degradation, session cursors, and response presentation only. Existing
     builder imports remain compatible by resolving directly to the registered
     plan implementations. Twenty-four focused query-plan, SEC Canvas filter,
     service, and application-registry tests passed, and all changed Python
     modules compiled with bytecode writes disabled.

149. Moved the Canvas News page and ticker-facet SQL out of `app.py` into the
     registered `news.canvas_asof.v1` backend query plan. The plan owns the
     bounded publication window, stable timestamp/source-ID cursor, canonical
     event-to-rendered-revision join, ticker/search/content filters, and reads
     of the existing News Synthesis publication used for optional semantic
     filters. Its registry entry declares all three published source tables.

     The route retains parameter validation, query-session and facet caching,
     timeout/error mapping, optional synthesis projection, and its existing
     user-facing response contract. News Gateway and Text Intelligence producer
     code, schemas, and publication behavior were not changed. Thirty-two
     focused Canvas News, trading-News, News operations/detail, and application-
     registry tests passed, and all changed Python modules compiled with
     bytecode writes disabled.

150. Reconciled the Backtest backlog and preflight evidence with the implemented
     causal Watchlist runtime. Backtest already consumes the revisioned QMD
     History transition timeline, applies all due membership states before a
     same-clock market event or Strategy frame, journals add/remove transitions,
     and checkpoints/restores its timeline cursor and active membership. The
     unchecked session-boundary wording was stale; no duplicate runtime path
     was added.

     Backtest preflight now reports transition-state count and explicitly says
     the pinned timeline is applied at every configured intraday refresh clock.
     The separate representative active-session performance gate remains open
     and is not represented as passed by this contract correction.

151. Registered Historical Scanner's all-universe calculations as
     `market.historical_scanner_materialization.v1`. The plan now owns the
     compact yearly-event union, canonical continuity revision read, bounded
     core Scanner aggregation, minute evidence reduction, VWAP variants, and
     causal 20-session average-volume baseline. Its source declaration covers
     compact events, ordinal continuity, and completed daily session bars.

     `historical_scanner_service` retains bounded background scheduling,
     revisioned materialized-cache storage, enrichment composition, fallback
     status, and response projection; it no longer embeds the calculation SQL.
     Thirty focused query-plan, Historical Scanner technical/service, and
     application-registry tests passed, and all changed Python modules compiled
     with bytecode writes disabled.

152. Registered Historical Scanner's revisioned materialized storage contract
     as `market.historical_scanner_cache.v1`. The plan owns all five cache-table
     schemas, exact snapshot/calculation/schema/source-revision reads, bounded
     row/event projections, the complete-meta plus exact-indicator-count proof,
     and a fail-closed whitelist for JSONEachRow insert targets. The orchestration
     service now contains no SQL.

     This closes the approved backend SQL migration inventory. SQL intentionally
     left outside query plans is confined to authoritative persistence adapters
     and shared `news_prior_context`; the latter has direct research/intelligence
     callers and remains inside the explicitly deferred intelligence boundary.
     Thirty-five focused cache/materialization-plan, Historical Scanner
     technical/service, and application-registry tests passed, and all changed
     Python modules compiled with bytecode writes disabled.

153. Corrected the frontend's causal-universe authority and its Run Plan
     navigation. New Run Plan universes now offer configured symbols and the
     implemented versioned Watchlist resolver; the obsolete Scanner-view value
     is shown only for an existing legacy draft and remains explicitly fail
     closed until converted. The `assignment-configuration` route now opens the
     existing Run Plan editor instead of being silently remapped to Strategy
     Studio.

     Canvas Watch Universe now consumes the canonical Market Discovery
     Watchlist runtime, merges each versioned member with the current Scanner
     projection, uses the runtime clock and actual member count, and presents
     distinct loading, dependency-error, awaiting-first-resolution,
     missing-snapshot, empty-membership, and legacy-source states. Its five-
     second polling is single-flight, abortable, and bounded by the shared API
     timeout. The managed TypeScript/Vite production build passed. In the
     running app, the corrected hash route rendered `Strategy Run Plans` and
     its guided configuration directly; the live runtime endpoint returned its
     truthful `awaiting_first_resolution` state with both unavailable QMD
     dependencies rather than inventing membership. The current standalone
     Canvas registry was unverified and therefore correctly blocked adding a
     new Watch Universe container during browser review; existing Canvas state
     was preserved.

154. Repaired QMD History source planning against the production ClickHouse
     version. The recent-coverage query previously reused
     `coverage_start_utc` and `coverage_end_utc` as formatted String aliases;
     ClickHouse 26.3 resolved those aliases in `WHERE` and rejected the
     resulting String-to-DateTime64 comparison. The projection now uses
     distinct `coverage_start_text` and `coverage_end_text` names, leaving the
     predicate bound to the typed source columns. A focused regression test
     locks that non-shadowing contract, and all 42 QMD History Rust tests
     passed with Cargo output under the external runtime target.

     The same acceptance run confirmed and corrected the stale live-port
     default. QMD History and `validate_qmd_authority.py` now use QMD Live at
     `127.0.0.1:8795`; `127.0.0.1:8800` remains the IBKR Supervisor and is not
     a QMD fallback. Eight validator tests passed. With QMD Live and History
     running on 8795/8801, the read-only recent-to-live report
     `qmd_authority_validation_20260811T182103Z.json` passed for IBM and F.
     The earlier durable archive report remains valid. A multi-day plan still
     truthfully exposes an archive-to-recent coverage gap while QMD startup
     repair is incomplete, so the combined production boundary gate remains
     open rather than being inferred from the two passing scopes.

155. Removed wide chart-state and duplicated planner payloads from the
     interactive Scanner/Watchlist path. QMD's focused Scanner indicator
     snapshot now preserves scalar Generic Structure evidence but omits the
     active-level and per-timeframe state arrays; detailed per-ticker chart
     requests retain their complete state. The backend defensively enforces
     the same projection. Current Watchlist members now store only fields that
     can affect their declared rules or rank, stable identity, and membership
     lifecycle evidence instead of retaining the complete wide Scanner row.

     The Live Scanner no longer serializes an identical `market_rows` array;
     the existing frontend already treats that field as optional and derives
     its market view from `rows`. Market Discovery Watchlist runtime now returns
     bounded computation summaries rather than duplicating 17,000-plus
     per-symbol requirements in both `computation_requirements` and
     `computation_demand`; full requirement detail remains available from
     `/api/system/computation-requirements`.

     Under the same active QMD process, the 5,000-row focused-indicator payload
     dropped from roughly 3.21 MB to 334 KB and from 1.54-1.65 seconds to
     0.206-0.210 seconds. The resolved Watchlist endpoint dropped from roughly
     29.8 MB/3.25 seconds to 312 KB/0.79 seconds, and the 250-row Live Scanner
     response dropped from roughly 6.7 MB to 2.2 MB. Warm end-to-end Scanner
     latency remained 1.4-2.6 seconds while QMD was catching up, so the wider
     steady-state load gate remains open. All 100 QMD library tests and 63
     backend Watchlist, QMD-client, authority, and summary-contract tests
     passed; changed Python modules compiled with bytecode writes disabled.

156. Removed the once-per-minute cold-query stall from Live Scanner reference
     enrichment. The previous cache bound each entry to the current minute, so
     the first Scanner request after a minute boundary synchronously repeated
     the full point-in-time identity, supply, short, market-reference, and
     daily-bar reads. A 60-second UI-cadence soak captured the defect with zero
     request failures but alternating Scanner latency: 1.93/2.16 seconds warm
     versus 6.45/7.95 seconds on reference refresh. The generated report is
     `D:\TradingML\runtimes\qmd_validation\scanner_watchlist_soak_20260811T183953Z.json`.

     Live reference enrichment now uses a single bounded stale-while-refresh
     authority. The first process load is still synchronous and fail closed;
     after expiry, concurrent requests retain the prior projection and its
     original `reference_available_at`, while exactly one daemon refresh loads
     the next point-in-time projection. A refresh failure preserves visibly
     stale evidence rather than fabricating a new timestamp. Real expiry
     validation measured 8.307 seconds for the initial cold read, 1.664 seconds
     for the expired request returning the prior timestamp, and 1.327 seconds
     after the background refresh published a new timestamp. All 64 focused
     Watchlist, QMD-client, application-authority, and cache tests passed, and
     the changed Python modules compiled with bytecode writes disabled.

157. Separated live admission priority from lossless QMD REST repair at their
     shared persistence and computation fan-in. Startup repair previously used
     the exact websocket fanout and eight repair workers could fill its bounded
     compact, bar, indicator, or live-market-state queues. Current events were
     not dropped, but they could wait behind historical catch-up. Repair now
     pauses when free capacity in any applicable queue reaches a 10% reserve,
     capped at 25,000 slots, while the websocket continues to use the reserved
     capacity. Repair resumes automatically as consumers drain; cumulative wait
     count and wait milliseconds are visible in QMD metrics.

     The review also found that repair events could overwrite freshness and
     same-session current state with an older timestamp. The service freshness
     watermark is now monotonic. Symbol state still applies missing historical
     trades to session totals, but only the newest trade or quote may replace
     current price/quote state and `last_event_ts`. This preserves the existing
     shared live/recent computation path without allowing concurrent repair to
     make the Scanner appear older than an already-seen websocket event.
     All 103 QMD tests passed using the required external Cargo target,
     including explicit live-reserve, monotonic-freshness, and late-repair
     state regression coverage. The production catch-up soak remains a
     separate open acceptance gate until the rebuilt service has progressed
     through representative active traffic. An initial compact-only runtime
     sample correctly fixed freshness reporting but proved that a later router
     could still stall live intake; that partial result was rejected and the
     reserve was extended to the complete lossless fanout before this entry was
     accepted. A second sample showed that channel capacity alone did not see
     the compact writer's internal pending batch or contention before queue
     admission. The final gate therefore uses total compact lane backlog and
     explicit decoded-websocket demand as well as all sender capacities. The
     mixed compact FIFO was the final source of head-of-line blocking: live and
     repair now have separate bounded inputs, a one-row merge handoff, and a
     live-first writer selection. Per-lane pending counts are exposed so this
     priority can be verified without inferring it from the combined backlog.

     The universal compact hot path was also corrected in four places. Normal
     event admission drains only that event's ticker reorder buffer instead of
     scanning every active ticker; timed/forced flush still drains the complete
     map. Market-product cache updates now use the existing ticker shards
     through bounded per-shard workers instead of serializing them inside the
     persistence writer. During upstream backlog, persistence accumulates the
     configured full ClickHouse batch instead of issuing a small partial insert
     every five seconds; low-traffic flush latency remains five seconds. Every
     condition-overflow audit row remains durable, while stderr reports only
     the first and each 10,000th summary rather than synchronously printing one
     line per event.

     The managed QMD launcher now uses an optimized release binary by default,
     stores Cargo output under the external runtime authority, and retains an
     explicit `-DebugBuild` diagnostic switch. A first launch test found and
     rejected a PowerShell string-splat bug that passed `--release` as program
     characters; the final process path proved the actual release binary.
     In `qmd_live_priority_acceptance_20260811T191900Z.json`, four active
     catch-up samples held event lag at 1.071-1.083 seconds, the live queue
     returned to zero, persistence advanced from 1,234,339 to 1,498,728 rows,
     repair advanced from 164,874 to 203,551 rows, and failures remained zero.
     All 105 QMD tests passed. The longer CPU/memory/Scanner steady-state soak
     remains open and is not implied by this active catch-up acceptance.

158. Updated the two operational consumers of QMD's split live/repair lanes.
     The Rich terminal now reads the versioned compact-event page envelope
     instead of the legacy raw-array endpoint and shows live pending, repair
     pending, repair wait count/duration, persistence failures, and throughput
     on the q_live stage. The Services dashboard exposes the same live/repair
     pending and wait evidence beside authoritative queue-drop totals, so a
     healthy aggregate lane cannot hide repair throttling or live backlog.
     Deferred Market AI and Text Intelligence callers still use the nullable
     legacy ticker endpoint; therefore endpoint retirement remains correctly
     open until those deferred migrations resume.

159. Removed chart-scale bar retention from QMD's all-market memory path. A
     longer release run invalidated the short throughput acceptance when its
     resident set reached 16.1 GB and event freshness later regressed. The
     bounded market-product cache reported only 335.6 MB estimated over 1.34
     million rows; the larger drift was the bar store retaining up to 1,000
     rows per ticker and timeframe, with each row cloning full Generic
     Structure vectors. The system-owned default is now four current-tail rows
     for replacement and downstream close processing. Full chart windows stay
     on QMD History/ClickHouse and do not expand universal memory. Generic
     Structure activation itself remains a separate open barrier/replay task;
     this change does not claim that broader migration is complete.

160. Moved QMD's live chart-product cache behind the existing focused
     computation lease union. The four-row bar-tail run remained fresh but
     still grew from 1.5 GB to 6.3 GB while the product engine allocated more
     than 1.3 million family/condition rows over the complete market. The
     product router now checks the same reference-counted Watchlist, Strategy,
     chart-request, and offline leases as focused indicators before admitting
     a ticker. Full chart windows remain QMD History authority; QMD Live owns
     only the demanded incremental tail. This removes chart presentation state
     from universal ingest without weakening chart continuity or silently
     dropping an authoritative source event.

161. Corrected the remaining QMD all-market memory and scheduling boundaries.
     Unfocused symbols now retain only the one-second safety bar used for LULD
     and locked/crossed market-state evaluation; active computation leases
     receive the complete enriched timeframe set. Retained bars share one
     reference-counted Structure snapshot instead of cloning its vectors into
     every timeframe. This reduced the final post-close soak from the earlier
     multi-gigabyte trajectory to 543 MB resident memory at 235 seconds.

162. Removed three independent freshness stalls found by progressively longer
     release runs. Intraday ready-bar selection now uses ticker/session-scoped
     ordered ranges rather than scanning every active series for every event.
     Structure persistence takes at most 256 dirty checkpoints per flush and
     requeues a failed batch without losing a newer dirty update. Canonical bar
     inserts use four bounded writers with aggregate pending metrics. Compact
     events and their warning audit use two bounded persistence workers, a
     separate 50,000-row batch, and a five-second maximum batch age; a closed
     worker falls back to inline lossless retry. Late intraday repairs are
     keyed by ticker/session/bucket and limited to one per writer tick so
     current bar inserts cannot be starved by redundant historical rebuilds.

163. Validated the combined QMD build with 108 passing Rust tests and an
     optimized managed release soak against the real post-close feed plus
     recent repair. At 235 seconds it reported 1,116 ms event lag, 543 MB
     resident memory, 254,127 ingested and 252,967 persisted compact events,
     303,096 persisted canonical bar rows, zero live/repair input backlog,
     zero source/bar drops, zero repair failures, an empty unfocused product
     cache, 15/107 microseconds average/maximum all-market bar time, and 5/17
     microseconds Core Scan time. The evidence report is
     `D:\TradingML\runtimes\qmd_validation\qmd_funnel_postclose_acceptance_20260811T202225Z.json`.
     Because the final build completed after 16:00 ET, representative
     active-session budget approval remains explicitly open. Generic Structure
     also remains exact but all-market until its atomic activation barrier and
     checkpoint-to-barrier replay are implemented.

164. Completed the non-executing Live/Paper proposal control plane requested in
     the final design review. The endpoint now accepts manual,
     semi-automatic, and automatic proposal origins; preserves the existing
     approved release, account/mode, point-in-time conid, QMD freshness,
     Scanner sequence, chart sequence, and protection checks; refreshes one
     canonical broker snapshot for every configured account in the selected
     mode; synchronizes the existing Portfolio authority; and obtains a fenced
     admission decision. Accepted intent is compiled by the shared IBKR OMS
     planner into a broker-neutral order/protection summary. The validation-only
     reservation is always released before response because broker submission
     is false and no runtime owns it. Portfolio rejection and OMS rejection are
     distinct terminal proposal dispositions. Broker-dependent risk validation
     and command submission remain pending the separately authorized
     Live/Paper runtime deployment. Validation covered 41 focused proposal,
     canonical-state, and OMS tests; the existing full Portfolio test module
     was separately observed to hang in its admission-lease path and is not
     represented as passing.

165. Published lifecycle contract schema v2 across the permitted application
     paths. Every projection now declares its mode and exact clock,
     observation-source, latency, execution, and fill adapters. Replay,
     Backtest, Debug, and offline market-data builds continue to use their
     existing bounded command/checkpoint authorities. Live/Paper canonical
     state now exposes the same envelope as a continuous session projection,
     but deliberately advertises no start/stop/broker commands. Unknown modes
     and incomplete adapter contracts fail closed. This makes parity and
     intentional mode differences reviewable without changing the IBKR
     Gateway, Supervisor, or broker submission runtime. Seventy-six focused
     lifecycle, cache, historical-controller, Canvas, and canonical-state tests
     passed. Remaining research-job producer migrations stay deferred with
     their owning intelligence services.

166. Enabled compressed high-volume ClickHouse transport in QMD History. Both
     streaming event queries and bounded scalar/JSON queries request ClickHouse
     HTTP compression, and reqwest transparently decompresses before the
     existing strict incremental TSV parser. The exact full-market
     2026-08-07 13:30:00Z-to-13:30:02Z request returned the unchanged 683,497
     events, two evaluation clocks, and 25 transitions. The already-running
     uncompressed release required 47.917 seconds immediately before restart;
     the compressed release required 27.030 and 39.402 seconds on two runs.
     Service CPU was about 3.1 seconds per run, final working set stayed near
     17.5 MB, and peak working set was 142.1 MB. This is useful transport
     reduction but not active-session acceptance: ClickHouse latency remains
     variable and the request still cannot keep pace with its two-second input
     span. Evidence is
     `D:\TradingML\runtimes\qmd_goal_load\acceptance_gzip_summary.json`.
     Cargo formatting/checks and all 42 QMD History tests passed.

167. Added and ran a database-backed point-in-time enrichment acceptance
     command. It requests one ticker at two explicit UTC cutoffs, requires exact
     response clocks and ready state, traverses identity, market, float, borrow,
     short-interest, fundamental, freshness, and identifier availability
     fields, and fails if any evidence is later than its cutoff. A configurable
     change path must also advance so two superficially identical cached
     responses cannot pass. The real AAPL run checked 56 fields at each cutoff:
     14:00 UTC returned the 2026-08-05 borrow authority and 15:00 UTC returned
     the borrow snapshot observed at 14:32:39, with no future evidence. The
     command writes atomically outside the repository, has compact human and
     explicit JSON output, and returns nonzero on failure. Two focused tests,
     Python compilation, help rendering, and the real acceptance passed. The
     deferred News Synthesis assertion remains unchanged.

168. Recorded and implemented the final scope clarification: QMD Live, QMD
     History, backend, frontend, Portfolio, and OMS are in scope; intelligence
     producer changes remain deferred, and broker/deployment execution remains
     separately authorized. The whole-path read review found that the system
     computation endpoint duplicated QMD's full per-symbol requirement graph,
     producing a roughly 62-78 MB ordinary management response. QMD now exposes
     a bounded summary contract, the backend shares that summary for Watchlist
     and management consumers, and explicit `include_details=true` preserves
     intentional diagnostic access to the full graph.

     Scanner composition now uses a bounded five-second single-flight cache:
     one caller composes the complete QMD, Reference, indicator, and Watchlist
     population, while each request independently applies its UI row limit and
     feature projection. Concurrent misses are coalesced without fabricating a
     newer source revision. The reusable cache records loads/coalescing and has
     bounded entries, TTL, and wait time. A new acceptance command enforces
     response contracts and hard wall-clock reads for Scanner, Watchlist,
     Canvas chart, and computation planning, with compact terminal output and
     atomic external evidence.

     The first real profile failed honestly: Scanner p95 was 8.75 seconds. A
     later diagnostic run exposed the wide planner payload and showed Scanner
     and Watchlist p95 at 14.94 and 12.24 seconds. After loading the tested QMD
     and backend processes, the identical 30-second/eight-client profile passed
     1,808 requests with zero errors: Scanner p95 254.806 ms, Watchlist p95
     506.596 ms, Canvas p95 756.429 ms, and planner p95 281.524 ms. Evidence is
     `D:\TradingML\runtimes\qmd_validation\application_read_load_20260811T212024Z.json`.
     All 108 QMD tests and 62 focused backend/cache/client/acceptance tests
     passed. This closes post-close whole-path read acceptance only; the
     representative active-session soak and every deferred producer/broker
     item remain open.

169. Removed repeated internal expansion from QMD's bounded computation-summary
     route. The summary projection now has a one-second QMD-owned cache and is
     invalidated synchronously whenever a computation target is replaced or
     removed; expiry still rebuilds from the authoritative lease set so lease
     expiration is not hidden indefinitely. The detailed target endpoint is
     unchanged. With 81,816 active requirements, the rebuilt release measured
     252.52 ms cold, 2.64-3.54 ms for repeated reads, and 245.7 ms after cache
     expiry. All 109 QMD tests passed, including mutation-invalidation coverage.

     The accompanying Generic Structure review confirmed that it cannot simply
     be gated by the current focus predicate. Its state is event-native, so an
     unfocused interval makes the persisted checkpoint stale. The safe target
     remains a staged lease activation: checkpoint-to-ClickHouse ordered replay,
     an atomic live-event barrier/handoff, and only then visible focus. No
     gap-prone approximation was introduced; that larger non-deferred task
     remains open.

170. Proved the remaining QMD History cold-materialization wall time is a
     physical archive-layout constraint rather than shared-engine CPU. The
     authoritative `market_sip_compact.events_2026` table is partitioned by
     month and ordered by `(ticker, ordinal)`, whereas causal cross-sectional
     replay filters and orders by `sip_timestamp_us`. A read-only
     `EXPLAIN indexes=1` for the exact 2026-08-07 two-second pattern selected
     ten parts and all 240,923 date-matched granules; the primary-key condition
     was `true`, followed by an explicit global sort. This explains why the
     service uses roughly three CPU seconds but waits 27-48 seconds on the
     shared ClickHouse path.

     QMD History already uses projection pushdown, the exact date/timestamp
     predicate, incremental TSV decoding, gzip transport, and bounded ticker
     shards. No further query-only change can make the existing primary key
     skip on time. The correct options are a Market-SIP-owned time-order
     projection/skipping index or a separately approved QMD History-owned
     archive cache with backfill, equivalence, revision, and retention rules.
     Because the user prohibited Market SIP changes and did not authorize a
     second archive copy, neither mutation was made. The performance gate stays
     open with the precise authority decision now recorded.

171. Aligned live QMD computation with the shared compact event authority.
     Previously the raw Massive/repair `MarketEvent` updated Scanner, live
     market state, bars, indicators, and event streams in parallel with compact
     conversion, while QMD History replayed the decoded compact representation.
     Live and historical calculations could therefore observe different size
     precision, condition encoding, raw metadata, and event identity.

     Raw events now enter the authoritative persistence queues first. The
     compact writer assigns canonical arrival identity, applies the registered
     condition/tape encoding, decodes one canonical `MarketEvent`, and sends it
     through a new bounded computation handoff. Only that event updates the
     Core Scanner, market-state, bar, indicator, and public event paths.
     Market-product and canonical intraday-bar consumers retain the same compact
     authority. The handoff is registered as a required operational lane,
     reports pending/success/failure state, and is included in repair admission
     so repair cannot consume its reserved live capacity. Compact-disabled
     diagnostics keep an explicit raw fallback.

     All 109 QMD tests passed on the optimized release. Real startup-repair
     validation then processed 4,660 received events through 4,660 successful
     canonical fanouts with zero failures and zero pending rows while Scanner
     sequence advanced. An AAPL authority check matched Scanner last trade
     `304.94` to compact arrival `28228314` price integer `30494`. This removes
     the representation drift required before an exact Generic Structure
     checkpoint cursor and activation barrier can be implemented.

172. Added the exact Generic Structure checkpoint cursor made possible by the
     canonical-event cutover. Each newly applied compact-decoded event advances
     `(updated_at, last_arrival_sequence)`. The engine rejects a replayed event
     when both its timestamp and canonical arrival identity are at or behind
     the checkpoint, while still accepting a later arrival at the same SIP
     timestamp. Structure persistence now compares the same composite cursor;
     timestamp-equal updates can no longer disappear behind an `updated_at`
     watermark.

     The JSON extension is backward compatible: existing checkpoints restore
     with arrival cursor zero and preserve their prior state, but zero is
     explicitly insufficient evidence for exact focus activation. All 110 QMD
     tests passed, including duplicate same-time replay and legacy checkpoint
     coverage. The rebuilt real service processed 6,230 canonical events with
     zero lane failures, and `q_live.qmd_structure_state_v2` persisted positive
     cursors such as `28297895`. Staged lease replay and atomic barrier handoff
     remain the next step; Generic Structure is not yet removed from the
     all-market path.

173. Added the bounded QMD History Generic Structure checkpoint-advancement
     product required before focused-only live computation can be safe. The
     endpoint accepts one schema-versioned checkpoint, a causal as-of cutoff,
     optional plan pin, and a bounded event limit. It replays the single ticker
     through QMD History's existing three-tier planner and the shared
     `qmd_core::GenericStructureEngine`, then returns the proposed checkpoint,
     exact source plan, pre/post source revisions, and decoded/applied counts.

     Exact live cursor identity is enforced rather than inferred: Recent and
     Current-Live segments are accepted, while Archive ordinal identity and
     gaps return a typed conflict. Windows, event counts, and concurrency are
     independently bounded by configuration. All 43 QMD History tests passed.
     A real PLAG replay advanced arrival `28435075` to `28435824`; 15 boundary
     and new events were decoded, 14 advanced state, and the checkpoint-boundary
     duplicate was ignored. The source tier was `recent` and the returned plan
     hash was `fnv1a64:09579ec01698b4a3`. Scheduled pre-retention advancement,
     staged focus activation, and removal from the all-market path remain the
     next live-QMD phase.

---

[Top](README.md) · [Previous](14-implementation-backlog.md) · [First](01-product-and-principles.md)

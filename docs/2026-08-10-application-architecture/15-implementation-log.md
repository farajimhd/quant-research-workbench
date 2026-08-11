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

The authoritative remaining work and acceptance gates are maintained in the
[complete implementation backlog](14-implementation-backlog.md). A checked
item means its real runnable path was implemented and focused validation passed;
it does not imply all application work is complete.

---

[Top](README.md) · [Previous](14-implementation-backlog.md) · [First](01-product-and-principles.md)

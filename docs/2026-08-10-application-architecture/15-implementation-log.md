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

The authoritative remaining work and acceptance gates are maintained in the
[complete implementation backlog](14-implementation-backlog.md). A checked
item means its real runnable path was implemented and focused validation passed;
it does not imply all application work is complete.

---

[Top](README.md) · [Previous](14-implementation-backlog.md) · [First](01-product-and-principles.md)

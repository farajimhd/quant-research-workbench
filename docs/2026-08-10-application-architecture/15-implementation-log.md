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

The authoritative remaining work and acceptance gates are maintained in the
[complete implementation backlog](14-implementation-backlog.md). A checked
item means its real runnable path was implemented and focused validation passed;
it does not imply all application work is complete.

---

[Top](README.md) · [Previous](14-implementation-backlog.md) · [First](01-product-and-principles.md)

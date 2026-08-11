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
    backend exposes each registry family; frontend catalog generation remains
    open.

The authoritative remaining work and acceptance gates are maintained in the
[complete implementation backlog](14-implementation-backlog.md). A checked
item means its real runnable path was implemented and focused validation passed;
it does not imply all application work is complete.

---

[Top](README.md) · [Previous](14-implementation-backlog.md) · [First](01-product-and-principles.md)

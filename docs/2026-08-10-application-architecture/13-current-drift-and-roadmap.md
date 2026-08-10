# Current drift and implementation roadmap

[Top](README.md) · [Previous](12-operations-reliability-and-security.md) · [First](01-product-and-principles.md)

## 1. Verdict

The repository contains most necessary domain pieces, but not yet one compiled application architecture. The largest drift is duplicated planning and authority: market-source selection, enrichment SQL, feature availability, Canvas defaults, mode controllers and trading bindings are expressed in several code/UI locations instead of generated from shared registries and plans.

The target in this package preserves strong existing authorities—QMD shared computation, ClickHouse market archives, Reference publications, domain gateways, Replay controller direction, Portfolio/OMS separation—and connects them through explicit contracts. It does not propose one monolithic service.

## 2. Confirmed current-to-target gaps

| Area | Confirmed current shape | Target | Main consequence today |
|---|---|---|---|
| QMD source routing | Live retains recent data; History primarily queries yearly compact archive | One coverage-driven live/recent/archive planner shared by charts, scanner, Replay and Backtest | Boundary logic and behavior differ by caller |
| Retention | Recent/archive verification concepts and retention jobs exist | A queryable coverage ledger gates every deletion and route | Handoff is not yet the universal read contract |
| Bars | Durable intraday family bars and historical daily session bars exist; macro support differs by path | Daily authority plus partial current day derives 1w/1mo/1y consistently | Chart/history intervals can diverge |
| Enrichment | Reference table groups/publications exist; scanner code knows many direct fields/queries | Complete versioned field registry and feature store | UI availability and query ownership drift |
| Computation funnel | Shared QMD computations and scanner/watchlist concepts exist | Registry-planned universal → core → watchlist → strategy/request scopes | Expensive features can be mislabeled or misplaced |
| Market Discovery UI | Draft configuration and unavailable labels exist | Universal locked primitives, core scan, Watchlists and capability status from backend registry | UI labels are not reliable runnable truth |
| Canvas | Container/default concepts exist | Published container registry + exact runtime default + per-user overlay | Pages duplicate layouts and behavior |
| Charts | Multiple chart/data paths exist | One progressive QMD source plan with request-scoped indicator DAG | Smoothness, parity and provenance vary |
| Trading modes | Replay is most developed; Live has legacy paths; Backtest is incomplete | Shared controller/state machines with injected clock/source/fill adapters | Results and safety differ by mode |
| Portfolio | Per-run engine/locks and policies exist | Cross-run/account fenced allocation and reservation authority | Shared-capital races remain possible |
| Deployments | UI/config drafts imply bindings | Deterministically compiled immutable Run Plan, optionally reviewable | Runtime authority is not fully inspectable |
| Intelligence/models | Several services and research systems exist | Standard domain events/features, causal contracts and promotion registry | Integration is service-specific |
| Backend | Broad route composition exists | Shared typed clients/planners and standard snapshot/delta envelope | Route logic and error semantics vary |
| Operations | Per-service health/audits exist | Central readiness/coverage/authority view | Open-port health can be confused with usable state |

## 3. Implementation sequence

### Current implementation constraint

The permitted implementation scope is **QMD Gateway, QMD History, application
backend, frontend, Portfolio, and OMS**. News, SEC, Reference, Text Intelligence,
Text Embed, Model Gateway, Market AI, and other working producer services remain
unchanged. Permitted components may integrate with their existing contracts;
tasks requiring changes to a deferred service remain design dependencies unless
a later request explicitly reopens that service.

Permitted task groups:

| Component | Tasks that may proceed |
| --- | --- |
| QMD Gateway | Stable live event/snapshot contracts, recent retention, coverage/watermarks, capability metadata |
| QMD History | Unified source planning, historical products, macro bars, causal replay, direct-read parity |
| Backend | Typed QMD clients, source/query planning, capability and field registry APIs, configuration compiler, chart/scanner composition, snapshot/delta recovery |
| Frontend | Registry-driven Market Discovery status, Universal/Core/Watchlist configuration, progressive charts and indicators, shared Canvas defaults/overlays, mode and readiness presentation |
| Portfolio | Cross-run/account arbitration, fenced ownership, capital/risk reservations, proposal dispositions, restart reconciliation |
| OMS | Idempotent order lifecycle, execution/protection policy, broker/simulator adapter parity, recovery and journal projections |

This permission does not authorize broker orders, deployment changes, or
modifications to deferred services without a separate implementation request.

### Phase 0 — freeze contracts and registries

In the current scope, implement QMD market-source/product/capability registry
fragments, backend compilation/API contracts, and generated frontend catalogs.
Registry population owned by deferred producer services remains deferred.

1. Establish machine-readable schemas for services, market sources, fields/enrichments, capabilities, containers and trading configuration objects.
2. Assign stable IDs, owners, versions and current implementation status.
3. Generate frontend catalogs/statuses from backend registry endpoints.
4. Mark old documentation as current, superseded, proposal or historical where needed.

**Gate:** every visible Market Discovery/Canvas option resolves to one registry record and implementation state.

### Phase 1 — unify QMD distribution

1. Implement coverage/watermark ledger and `MarketSourcePlan`.
2. Put live memory/tail, `q_live`, and `market_sip_compact` behind one client contract.
3. Make QMD History use the same normalizer/computation/bar contracts.
4. Gate retention on archive coverage verification.
5. Make daily-session bars the historical base for weekly/monthly/yearly aggregation; append partial current daily state explicitly.
6. Stabilize the QMD live compact-event/ticker-snapshot consumer contracts used
   by Market AI without changing Market AI.
7. Add a bounded QMD History market-product/replay contract suitable for future
   Market AI consumption, including source-plan hash, coverage, continuation,
   product/calculation versions, and causal cutoff.
8. Define parity tests showing that an approved direct ClickHouse event read
   using QMD-owned decoding matches the equivalent QMD History result.

**Gate:** boundary-spanning queries are duplicate-free, gap-explicit and parity-tested across restarts and market days.

### Phase 2 — centralize enrichment

Backend field-registry, query-planner, caching, provenance, and frontend status
work may proceed against existing producer contracts. Changes to Reference,
News, SEC, Text Intelligence, Text Embed, Model Gateway, or Market AI remain
deferred.

1. Populate the complete field registry from Reference table groups, publications, SEC, News, market statistics and approved models.
2. Move SQL/table knowledge into versioned query plans.
3. Build batch/PIT loaders and a compact enrichment feature store with invalidation events.
4. Expose freshness, causal availability, source and null reasons to all consumers.

**Gate:** scanner/watchlist/chart/strategy request the same field ID and receive the same as-of value/version.

### Phase 3 — enforce the computation funnel

QMD, backend, and frontend work is permitted. Deferred producer services remain
read-only dependencies.

1. Register universal/core/watchlist/strategy/request/offline capabilities.
2. Add dependency DAG, warm-up, state, cost and scope validation.
3. Keep scanner projection state compact and change-driven.
4. Implement dynamic Watchlist lifecycle and planner.
5. Remove direct expensive calculations from all-market paths unless explicitly approved as core.

**Gate:** representative all-market load meets latency/memory budgets; scope violations fail configuration validation.

### Phase 4 — unify Canvas and charts

QMD/QMD History, backend, and frontend implementation is permitted. Existing
intelligence/reference contracts must be consumed without producer changes.

1. Publish versioned container schemas/defaults from Canvas Configuration.
2. Make Live, Replay, Backtest and research workspaces instantiate those defaults plus user overlays.
3. Implement progressive chart base-series/indicator loading through unified QMD routing.
4. Add typed container linking, request cancellation, single-flight caching, prefetch and resnapshot recovery.
5. Route chart/manual/semi-auto proposals through Portfolio and OMS.

**Gate:** the same saved workspace operates in each compatible mode, with explicit mode/account isolation and smooth boundary-spanning charts.

### Phase 5 — converge trading runtimes

Backend, frontend, Portfolio, and OMS implementation is permitted. Broker,
intelligence, reference, and model-service authority boundaries remain intact.

1. Complete the configuration compiler and immutable Run Plan.
2. Make Replay the parity benchmark; migrate Live and implement Backtest using shared state machines.
3. Establish fenced cross-run/account Portfolio ownership and durable reservations.
4. Standardize OMS/broker/simulator adapters, journal events and restart reconciliation.
5. Add manual, semi-auto and automatic proposal authorities without bypass paths.

**Gate:** identical Run Plan and recorded inputs produce deterministic Replay/Backtest decisions; Live/Paper exercise the same controls.

### Phase 6 — standardize intelligence and model integration

**Deferred until the undergoing intelligence work stabilizes.**

1. Emit validated domain events/FeatureUpdates from Reference, News, SEC, Text Intelligence and model services.
2. Enforce causal `available_at`, identity and revision lineage in Replay/Backtest.
3. Create application-wide artifact/promotion records and capability bindings.
4. Add evidence-linked, expiring Market AI hypotheses with deterministic trading boundaries.

**Gate:** no model or intelligence feature enters a strategy without a registered version, causal input contract, expiry and failure policy.

### Phase 7 — operations, migration, and retirement

QMD/QMD History, backend/frontend, Portfolio, and OMS validation and operations
work are in scope. Migrations or retirements requiring changes to deferred
producer services remain deferred.

1. Standardize readiness/coverage endpoints and central operations view.
2. Add representative load, boundary, restart, race and browser validation suites.
3. Migrate callers by authority domain; retain measured compatibility adapters only temporarily.
4. Remove duplicate source routers, UI catalogs, SQL paths, controllers and stale documentation after equivalence proof.

**Gate:** each concern has one declared authority and legacy paths have zero production callers.

## 4. Cross-cutting acceptance criteria

- No historical decision sees data whose `available_at` is later than decision time.
- No ticker join ignores point-in-time identity/effective intervals.
- No recent QMD partition is deleted before verified archive coverage.
- No visible capability status is maintained only in frontend code.
- No scanner-wide expensive feature is added without cost/scope approval.
- No manual, semi-auto, automatic or recovery order bypasses Portfolio and OMS.
- No model artifact runs without immutable identity and input contract.
- No service is called ready based solely on an open port.
- No authoritative data is silently skipped, truncated, overwritten or represented as complete when partial.

## 5. Documentation reviewed

This design reconciles the current repository structure and the principal documents for repository organization, service gateways, QMD signal/history/live behavior, event-market storage, Reference table groups, Market Discovery, Canvas, Replay, trading runtime/configuration, Portfolio/OMS, AI services, SEC/news pipelines and research systems.

Where those sources conflict, this package uses the following priority: verified current code/data authority, newer explicit design decisions, then older proposals. A statement here marked **Target** is still a design requirement, not a claim that code has shipped.

Principal navigation points include:

- [Documentation index](../README.md), [repository map](../codex/REPO_MAP.md), and [repository organization](../architecture/repository_organization.md);
- [service gateway standard](../architecture/service_gateway_standard.md) and [event-based market engine](../architecture/event_based_market_engine.md);
- [QMD signal architecture](../architecture/QMD_SIGNAL_ARCHITECTURE.md), [QMD design guide](../../services/qmd-gateway/docs/DESIGN_GUIDE.md), and [QMD History guide](../../services/qmd_history_gateway/README.md);
- [Market SIP pipeline](../../pipelines/market_sip/README.md) and [Reference table groups](../../services/reference_gateway/TABLE_GROUPS.md);
- [trading runtime](../architecture/TRADING_RUNTIME.md), [trading configuration authority](../architecture/TRADING_CONFIGURATION_AUTHORITY.md), [Portfolio Management](../architecture/PORTFOLIO_MANAGEMENT.md), and [OMS](../architecture/IBKR_ORDER_MANAGEMENT.md);
- [canonical IBKR trading](../trading/CANONICAL_IBKR_TRADING_ARCHITECTURE.md), [Canvas market screeners](../trading/CANVAS_MARKET_SCREENERS.md), [Canvas XBRL evidence](../trading/CANVAS_XBRL_FINANCIAL_EVIDENCE.md), [performance journal](../trading/TRADING_PERFORMANCE_JOURNAL.md), and [AI inference services](../services/AI_INFERENCE_SERVICES.md).

## 6. Decisions that should remain explicit

- Whether the enrichment feature store is a dedicated table family or a projection service over existing publications.
- The exact cross-process Portfolio fencing technology and recovery quorum.
- Which universal/core computations are truly mandatory after representative profiling.
- Data licensing and retention limits for browser caches and derived products.
- Initial strategy/model capabilities approved for automatic trading.

These choices affect implementation, but they do not change the authority boundaries in this design.

---

[Top](README.md) · [Previous](12-operations-reliability-and-security.md) · [First](01-product-and-principles.md)

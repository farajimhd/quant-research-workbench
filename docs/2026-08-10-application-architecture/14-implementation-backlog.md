# Complete implementation backlog

[Top](README.md) · [Previous](13-current-drift-and-roadmap.md) · [Next](15-implementation-log.md) · [First](01-product-and-principles.md)

This checklist translates the accepted application direction into executable
work. Items remain unchecked until the real runnable path has been implemented
and validated. Existing partial implementations are inputs to the work, not
automatic completion evidence.

## Scope boundary

Permitted implementation areas:

- QMD Gateway and shared `qmd_core`;
- QMD History;
- application backend;
- frontend;
- Portfolio Management runtime;
- OMS runtime;
- tests, operational contracts, and documentation for those areas.

Deferred producer services must remain unchanged: Market AI, News Gateway, SEC
Gateway, Reference Gateway, Text Intelligence, Text Embed Gateway, and Model
Gateway. Permitted components may consume their existing contracts. Broker
orders, deployment changes, and changes to IBKR Gateway/Supervisor require a
separate explicit authorization.

## 0. Registries and compiled contracts

- [ ] Register market sources and their coverage/watermark contracts.
- [ ] Register QMD events, bars, indicators, signals, scanner, and chart products.
- [ ] Register every capability with inputs, outputs, implementation version,
      allowed/default scope, cadence, timeframe, warm-up, state/cost class,
      persistence policy, mode support, and implementation status.
- [ ] Register enrichment fields with semantic grain, owner, physical source,
      query-plan ID, point-in-time join, availability clock, freshness, null
      reasons, coverage, security class, and historical support.
- [ ] Register Canvas container schemas and typed link contracts.
- [ ] Register Strategy, Watchlist, Canvas, Portfolio, OMS, execution,
      protection, account-binding, and mode schemas.
- [ ] Validate unique IDs, references, dependency cycles, clocks, scopes, modes,
      source paths, and retired compatibility aliases.
- [ ] Expose the registries through backend catalog APIs.
- [ ] Generate frontend choices and statuses from the backend catalogs.
- [ ] Remove competing handwritten frontend/backend/QMD availability catalogs.
- [ ] Version approved configuration releases and compute deterministic hashes.
- [ ] Compile immutable Run Plans and preserve migration for saved configurations.

Acceptance gate: every visible Market Discovery and Canvas option resolves to
one backend registry record and one executable implementation status.

## 1. Unified QMD distribution

- [x] Define the shared source-plan, coverage, provenance, continuation, and
      explicit-gap contracts used by QMD History and its consumers.
- [ ] Route current requests to QMD memory/live tail.
- [x] Route recent historical requests to `q_live.events` under verified
      coverage intervals.
- [x] Route older requests to `market_sip_compact.events_YYYY` and completed bars.
- [x] Select archive/recent boundaries from verified watermarks and the New
      York extended-session close rather than assumed UTC dates.
- [x] Split multi-year requests across archive tables.
- [x] Prevent overlaps through ordered non-overlapping source segments and
      stable ordinal/arrival cursor semantics.
- [x] Preserve event-time, source-sequence, event-identity ordering.
- [x] Return explicit missing and live-continuation segments.
- [ ] Pin source plans/revisions for Replay and Backtest.
- [ ] Permit advancing tail watermarks for Live consumers.
- [ ] Put routing behind one QMD client contract.
- [x] Make QMD History read recent and archive tiers.
- [x] Keep live tail ownership in QMD Gateway through explicit continuation.
- [x] Reuse the same `qmd_core` decoder, bars, indicators, structure, and signals.
- [ ] Stabilize live compact-event and bounded ticker-snapshot contracts.
- [ ] Add reconnect continuation and sequence-gap repair.
- [x] Expose bounded historical events, bars, indicators, signals, and scanner products.
- [x] Return source-plan hash, event schema, coverage, `as_of`, and continuation cursor.
- [ ] Provide a consumer-neutral historical contract suitable for future Market AI use.
- [ ] Prove approved direct ClickHouse reads match QMD History decoding/results.
- [ ] Prevent clients from choosing physical QMD databases/tables.

Acceptance gate: a boundary-spanning request returns one ordered,
duplicate-free, gap-explicit stream.

## 2. Retention and bar authority

- [ ] Persist recent live events and family bars in QMD-owned `q_live` tables.
- [ ] Record recent coverage by session and partition.
- [ ] Read existing archive publication coverage from Market SIP outputs.
- [x] Compare counts, bounds, stable identities, and schema versions before
      retention. Ordering remains preserved by the source contracts rather
      than accepted through an order-insensitive comparison.
- [ ] Advance the archive handoff watermark only after equivalence passes.
- [x] Block retention when archive coverage is incomplete or inconsistent.
- [x] Delete only QMD-owned event and intraday-bar partitions beyond the
      configured recent market-session window.
- [ ] Preserve distinct `trade`, `quote_bid`, and `quote_ask` bar families.
- [ ] Derive higher intraday resolutions algebraically.
- [ ] Use completed daily-session bars as historical daily authority.
- [ ] Produce recent/current daily-session state with the same schema.
- [ ] Derive weekly, monthly, and yearly bars from daily authority.
- [ ] Mark current macro periods explicitly partial.
- [ ] Version and invalidate derived caches after correction.
- [ ] Retire legacy macro authority only after parity and caller migration.

Acceptance gate: no recent row is deleted before verified archive coverage and
macro bars agree across Live, History, Replay, Backtest, and charts.

## 3. Computational funnel and Market Discovery runtime

- [ ] Enforce `universal_ingest`, `core_scan`, `watchlist`, `strategy_run`,
      `request`, and `offline` execution scopes.
- [ ] Lock integrity-critical Universal Ingest primitives.
- [ ] Limit Universal Ingest to normalization, identity preservation,
      sequencing, NBBO/trade state, freshness, quality, and required persistence.
- [ ] Profile all-market computations before Core Scan approval.
- [ ] Limit default Core Scan to last/change, volume/dollar volume, activity,
      spread, halt/stale, basic liquidity, and rank inputs.
- [ ] Maintain one compact row per eligible security.
- [ ] Publish scanner snapshots plus row deltas.
- [x] Remove broad expensive indicators from the default all-market path.
- [x] Implement dynamic Watchlist inclusion, exclusion, ranking, maximum size,
      TTL, manual override, and promotion/demotion reasons.
- [x] Persist membership add/remove/expire events.
- [x] Subscribe focused calculations only for current members.
- [ ] Union Watchlist, Strategy, chart, and offline computation requests.
      QMD now owns the validated leased union and chart requests are wired;
      Watchlist, Strategy, and offline producers still need to publish targets.
- [ ] Deduplicate by capability, identity, parameters, timeframe, anchor, and revision.
- [x] Reference-count subscriptions and release unused state.
- [x] Reject unapproved moves to broader populations.
- [ ] Trigger targeted recomputation from relevant enrichment changes.
- [ ] Preserve compact scanner/Watchlist history and explicitly approved materializations.

Acceptance gate: representative all-market load satisfies latency/memory
budgets and invalid scope broadening fails configuration validation.

## 4. Backend integration

- [ ] Build one typed QMD client for live and historical products.
- [ ] Remove physical source selection from route handlers.
- [ ] Implement shared source and query planner boundaries.
- [ ] Expose capability, field, container, and configuration catalogs.
- [ ] Move approved SQL into versioned query plans.
- [ ] Remove duplicated route-level SQL as callers migrate.
- [ ] Consume deferred producer services through unchanged bounded contracts.
- [ ] Bulk-load point-in-time enrichment and avoid per-row remote queries.
- [ ] Maintain a compact feature projection with source, version,
      `available_at`, freshness, and null reasons.
- [ ] Compile configuration dependencies, warm-up, modes, permissions, and accounts.
- [ ] Emit immutable Run Plans.
- [ ] Standardize response envelopes, warnings, partial coverage, and typed errors.
- [ ] Implement HTTP snapshot plus sequenced delta streams.
- [ ] Fill reconnect gaps or require resnapshot.
- [ ] Isolate budgets for commands, discovery, charts, simulation, and offline work.
- [ ] Bound and version caches.
- [ ] Enforce user, workspace, environment, mode, account, and command authority.
- [ ] Resolve secrets and broker identifiers server-side.
- [ ] Aggregate service readiness separately from data readiness.

Acceptance gate: browser code knows no database table, service credential,
internal topology, or producer formula.

## 5. Market Discovery frontend

- [x] Make Universal Ingest the first, normally locked configuration page.
- [ ] Show primitive reason, owner, version, cost, coverage, and consumers.
- [ ] Generate Core Scan choices from eligible registry capabilities.
- [ ] Show cost and broadening approval for all-market changes.
- [x] Configure Watchlist rules, rank, size, TTL, overrides, and focused calculations.
- [x] Expose Strategy/request/offline availability without calling it unavailable.
- [x] Separate implementation, execution scope, configuration authority,
      operational readiness, and coverage status.
- [ ] Show enrichment provenance, freshness, gaps, and null reasons.
- [ ] Add scanner and membership history views. Runtime membership history is
      connected; compact Scanner history remains.
- [ ] Generate fields, columns, and filters from registry metadata.
- [ ] Preserve theme, global scale, responsive, and accessibility authorities.
- [ ] Validate all affected states in real browsers.

Acceptance gate: UI availability equals backend registry and runnable truth.

## 6. Canvas and smooth charts

- [ ] Publish a versioned Canvas container catalog.
- [ ] Define typed input/output links and mode compatibility.
- [ ] Implement draft, validate, preview, publish, reset, and rebase.
- [ ] Instantiate published defaults in Live, Replay, Backtest, and research workspaces.
- [ ] Persist user/workspace overlays separately from defaults.
- [x] Route intraday historical charts through QMD History source planning and
      its shared derived cache.
- [x] Return base bars first and progressively add requested indicators,
      signals, and structure from the same cache entry.
- [ ] Merge archive, recent, and live tail by watermark.
- [x] Deduplicate identical historical derived requests with QMD History
      single-flight execution.
- [ ] Cancel superseded navigation requests and prefetch adjacent windows.
      Superseded requests are cancelled; adjacent-window prefetch remains.
- [ ] Bound caches and invalidate by source/corporate-action/calculation revision.
- [ ] Recover live sequence gaps or resnapshot.
- [ ] Expose partial, stale, corrected, and source-transition states.
- [ ] Return indicator provenance and warm-up.
- [x] Keep chart indicators request-scoped rather than expanding Core Scan.
- [ ] Create chart-originated manual and semi-automatic proposals.
  - [x] Create and confirm both proposal authorities in Replay Canvas.
  - [ ] Add the same proposal lifecycle to Paper/Live after shared-controller
        migration; broker execution still requires separate authorization.
- [ ] Attach snapshot, identity, price sequence, freshness, and requested protection.
  - [x] Attach and validate those fields for Replay chart proposals.
- [ ] Revalidate every proposal through Portfolio and OMS.
  - [x] Route confirmed Replay proposals through durable Portfolio admission
        and OMS planning; rejected/deferred proposals never reach the simulator.
- [ ] Keep Live, Paper, Replay, and Backtest visually and authoritatively isolated.

Acceptance gate: the same workspace operates in compatible modes and charts
load progressively without false continuity.

## 7. Shared mode controller

- [ ] Compile approved configuration into an immutable Run Plan.
- [ ] Implement one lifecycle for Live, Paper, Replay, Backtest, and Debug.
- [ ] Inject mode-specific clock, observation source, latency, and fill/broker adapter.
- [ ] Share Strategy, Portfolio, OMS, and journal state machines.
- [ ] Use Replay as the parity benchmark.
- [ ] Migrate remaining Live legacy paths.
- [ ] Complete Backtest through shared runtime contracts.
  - [x] Run a pinned approved revision through one continuous shared
        Strategy/Portfolio/OMS/simulator runtime across multiple sessions.
  - [x] Create, monitor, and stop Backtest runs from the Backtest UI.
  - [ ] Apply causal Watchlist membership changes during the run rather than
        holding first-clock membership static.
  - [x] Build canonical Backtest performance, Portfolio, position, order,
        execution, and closed-trade result projections.
  - [ ] Add strategy/run attribution and comparative analysis projections.
  - [ ] Add durable resume/restart from checkpoints.
- [ ] Add deterministic Debug fixtures.
- [ ] Pin causal market/enrichment versions for historical decisions.
- [ ] Standardize progress, pause, resume, cancel, failure, and completion.
- [ ] Add restart-safe checkpoints.
- [ ] Route manual, semi-automatic, and automatic proposals through one control plane.

Acceptance gate: identical Run Plan and recorded inputs produce deterministic
Replay and Backtest decisions.

## 8. Portfolio authority

- [ ] Make Portfolio the exclusive account allocation, capital, and risk authority.
- [ ] Define account/account-group ownership.
- [x] Add cross-run and cross-strategy admission arbitration for processes
      sharing the authoritative trading journal.
- [x] Replace process-local admission safety with SQLite-WAL-backed account and
      account-group fencing plus durable reservations.
- [x] Add lease epochs, bounded expiry, and stale-owner rejection.
- [x] Reserve capital for accepted unfilled intent.
- [x] Release/resize reservations on fill, cancel, reject, or timeout.
- [x] Enforce buying power, leverage, concentration, exposure, loss, and drawdown limits.
- [x] Implement portfolio-level entry kill and emergency-flatten controls.
- [x] Return approve, resize, defer, or reject dispositions. Queueing remains a
      scheduler concern and is not represented as Portfolio approval.
- [ ] Treat run priority as an arbitration input, never authority.
- [ ] Reconcile reservations, positions, cash, and broker truth after restart.
- [x] Journal every Portfolio decision and reservation transition.
- [x] Expose Portfolio configuration and operational UI/API, including distinct
      Run Plan allocations and aggregated Strategy allocation labels.
- [ ] Test races, lease expiry, restart, and shared-account conflicts.

Acceptance gate: two processes cannot allocate the same account capital without
one fenced decision.

## 9. OMS authority

- [x] Accept only an execution intent whose durable Portfolio decision and
      reservation match its account, intent, ticker, quantity, and active state.
- [ ] Version the execution-intent schema.
- [x] Generate stable client/order identifiers and reject persisted duplicate intent IDs.
- [x] Implement submit, acknowledge, replace, cancel, reject, and fill transitions.
- [x] Handle partial fills and remaining quantity.
- [x] Implement parent/child, bracket, stop, target, trailing, flatten, and disconnect protection.
- [x] Separate execution and protection policies.
- [x] Preserve existing IBKR Supervisor/broker boundaries.
- [x] Normalize broker and deterministic simulator events to one schema.
- [x] Recover and reconcile uncertain orders after reconnect.
- [x] Fail closed when broker, Portfolio reservation, or journal truth is uncertain.
- [x] Journal commands and every lifecycle/reconciliation result.
- [ ] Build order, execution, protection, and reconciliation UI projections.
- [x] Test Portfolio authority, idempotency, restart, disconnect, partial fills, and protections.

Acceptance gate: no chart, strategy, model output, or backend route can issue a
broker command outside OMS.

## 10. Operations, migration, and validation

- [ ] Separate liveness from dependency/data/execution readiness.
- [ ] Report coverage, freshness, queues, checkpoints, degradation, and authority.
- [ ] Build a central backend/frontend readiness view.
- [ ] Propagate end-to-end correlation and causation IDs.
- [ ] Add QMD transition/gap/lag/cache/queue metrics.
- [ ] Add discovery computation-cost metrics.
- [ ] Add Portfolio disposition/reservation and OMS reconciliation metrics.
- [ ] Bound queues, caches, subscriptions, retries, concurrency, and result sets.
- [ ] Shed replaceable projections before authoritative events or journal writes.
- [ ] Test QMD boundaries, retention, and live/history parity.
- [ ] Test point-in-time identity and enrichment behavior.
- [ ] Test scanner population, cost, and performance.
- [ ] Test streaming reconnect and resnapshot.
- [ ] Run real browser/visual validation for frontend changes.
- [ ] Test trading races, restart, protection, and deterministic mode parity.
- [ ] Run representative end-to-end load tests.
- [ ] Migrate one authority domain at a time with compatibility measurement.
- [ ] Remove duplicate paths only after zero production callers are proven.
- [ ] Document release, rollback, and recovery for every migration.

Acceptance gate: every concern has one declared authority and retired paths
have no production callers.

## Deferred backlog

- [ ] Wire Market AI to QMD History.
- [ ] Change Market AI direct ClickHouse contextual queries.
- [ ] Add News/SEC/Text Intelligence FeatureUpdate publication.
- [ ] Change Reference Gateway schemas or publications.
- [ ] Change Text Embed or Model Gateway.
- [ ] Implement application-wide model artifact promotion integration.
- [ ] Add Market AI features to Strategy observations.
- [ ] Perform cross-service intelligence migrations or retirements.

Deferred items are recorded for architectural completeness and are not part of
the active implementation goal.

---

[Top](README.md) · [Previous](13-current-drift-and-roadmap.md) · [First](01-product-and-principles.md)

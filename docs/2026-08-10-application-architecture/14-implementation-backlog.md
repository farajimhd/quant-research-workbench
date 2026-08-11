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

- [x] Register market sources and their coverage/watermark contracts.
- [x] Register QMD events, bars, indicators, signals, scanner, and chart products.
- [x] Register every capability with inputs, outputs, implementation version,
      allowed/default scope, cadence, timeframe, warm-up, state/cost class,
      persistence policy, mode support, and implementation status.
- [x] Register enrichment fields with semantic grain, owner, physical source,
      query-plan ID, point-in-time join, availability clock, freshness, null
      reasons, coverage, security class, and historical support.
- [x] Register Canvas container schemas and typed link contracts.
- [x] Register Strategy, Watchlist, Canvas, Portfolio, OMS, execution,
      protection, account-binding, and mode schemas.
- [x] Validate unique IDs, references, dependency cycles, clocks, scopes, modes,
      source paths, and retired compatibility aliases.
- [x] Expose the registries through backend catalog APIs.
- [x] Generate frontend choices and statuses from the backend catalogs.
- [x] Remove competing handwritten frontend/backend/QMD availability catalogs.
  - [x] Remove the backend-authored QMD family fallback; an outage now yields no
        invented current rows, while saved releases retain embedded review data.
  - [x] Generate remaining reference and deferred-intelligence projections from
        registered fields rather than handwritten Market Discovery lists.
- [x] Version approved configuration releases and compute deterministic hashes.
- [x] Compile immutable Run Plans and preserve migration for saved configurations.

Acceptance gate: every visible Market Discovery and Canvas option resolves to
one backend registry record and one executable implementation status.

## 1. Unified QMD distribution

- [x] Define the shared source-plan, coverage, provenance, continuation, and
      explicit-gap contracts used by QMD History and its consumers.
- [ ] Route current requests to QMD memory/live tail.
  - [x] Compose compact-event windows across QMD History archive/recent rows and
        the exact QMD Gateway current-live source-plan segment.
  - [ ] Compose current-window chart and historical Scanner products with their
        QMD Gateway live continuation.
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
  - [x] Pin the first event page's source-plan hash and revision token across
        every continuation; reject drift with a typed restart conflict.
  - [ ] Add storage-level immutable revision reads so a changed source can
        continue from the pinned old revision instead of restarting.
- [ ] Permit advancing tail watermarks for Live consumers.
- [x] Put routing behind one QMD client contract.
- [x] Make QMD History read recent and archive tiers.
- [x] Keep live tail ownership in QMD Gateway through explicit continuation.
- [x] Reuse the same `qmd_core` decoder, bars, indicators, structure, and signals.
- [ ] Stabilize live compact-event and bounded ticker-snapshot contracts.
  - [x] Publish and consume a versioned per-ticker compact-event page with an
        arrival cursor, exact eviction evidence, bounds, and forward pagination.
  - [x] Publish and consume a versioned latest ticker-state envelope with
        authority, Scanner sequence, freshness, and explicit missing state.
  - [ ] Retire the nullable ticker-row and compact-event raw-array compatibility
        endpoints after zero production callers are proven.
- [ ] Add reconnect continuation and sequence-gap repair.
  - [x] Make Scanner reconnect start from an authoritative snapshot and
        automatically replace client state after lag or a sequence gap.
  - [ ] Add equivalent bounded continuation or resnapshot contracts to the
        remaining raw event and product streams.
- [x] Expose bounded historical events, bars, indicators, signals, and scanner products.
- [x] Return source-plan hash, event schema, coverage, `as_of`, and continuation cursor.
- [x] Provide a consumer-neutral historical contract suitable for future Market AI use.
- [ ] Prove approved direct ClickHouse reads match QMD History decoding/results.
- [x] Prevent clients from choosing physical QMD databases/tables.

Acceptance gate: a boundary-spanning request returns one ordered,
duplicate-free, gap-explicit stream.

## 2. Retention and bar authority

- [x] Persist recent live events and family bars in QMD-owned `q_live` tables.
- [x] Record recent coverage by session and partition. Compact-event writer
      confirmations are cumulative by run, UTC event partition, and New York
      session; canonical 100 ms bar confirmations are cumulative by run and
      `local_date` partition.
- [x] Read existing archive publication coverage from Market SIP outputs. QMD
      consumes the authoritative ordinal-continuity publication and per-source
      flatfile handoff state without writing Market SIP tables.
- [x] Compare counts, bounds, stable identities, and schema versions before
      retention. Ordering remains preserved by the source contracts rather
      than accepted through an order-insensitive comparison.
- [ ] Advance the archive handoff watermark only after equivalence passes.
- [x] Block retention when archive coverage is incomplete or inconsistent.
- [x] Delete only QMD-owned event and intraday-bar partitions beyond the
      configured recent market-session window.
- [x] Preserve distinct `trade`, `quote_bid`, and `quote_ask` bar families.
- [x] Derive higher intraday resolutions algebraically from closed 100 ms bases.
- [x] Use completed daily-session bars as historical daily authority.
- [x] Produce recent/current daily-session state through the shared product schema.
- [x] Derive weekly, monthly, and yearly bars from daily authority.
- [x] Mark current macro periods explicitly partial.
- [x] Version and invalidate derived caches after correction. Historical cache
      identity includes source revision and engine/product contracts; live
      corrected rows increase revision.
- [x] Retire legacy macro authority after caller migration. The old macro table
      remains only a migration/training artifact and is not read as QMD candle
      authority.

Acceptance gate: no recent row is deleted before verified archive coverage and
macro bars agree across Live, History, Replay, Backtest, and charts.

## 3. Computational funnel and Market Discovery runtime

- [x] Enforce `universal_ingest`, `core_scan`, `watchlist`, `strategy_run`,
      `request`, and `offline` execution scopes. QMD validates every capability
      against its allowed scopes and rejects leased Universal/Core broadening;
      backend configuration applies the same fail-closed contract.
- [x] Lock integrity-critical Universal Ingest primitives.
- [x] Limit Universal Ingest to normalization/encoding, point-in-time identity,
      sequencing, NBBO/trade state, freshness/quality, and compact persistence
      fanout. The exact six-family set is catalog-tested.
- [ ] Profile all-market computations before Core Scan approval.
- [x] Limit default Core Scan to last/change, volume/dollar volume, activity,
      spread, halt/stale, basic liquidity, reference context, and rank inputs.
      Its exact five low-cost runtime families are catalog-tested; optional
      narrower families cannot enter this set without changing the authority.
- [x] Maintain one compact row per eligible security.
- [x] Publish scanner snapshots plus row deltas.
- [ ] Remove broad expensive indicators from the default all-market path.
      Indicator shards are focused, but `GenericStructureEngine` still runs
      inside the all-market bar store. Safe lease activation needs an atomic
      event-sequence barrier and exact replay since the restored checkpoint.
- [x] Implement dynamic Watchlist inclusion, exclusion, ranking, maximum size,
      TTL, manual override, and promotion/demotion reasons.
- [x] Persist membership add/remove/expire events.
- [x] Subscribe focused calculations only for current members.
- [ ] Union Watchlist, Strategy, chart, and offline computation requests.
      QMD now owns the validated live lease union and Watchlist, Paper/Live
      Strategy Run, and chart producers are wired. Offline demand still needs
      the same requirement identity inside QMD History planning.
- [ ] Deduplicate by capability, identity, parameters, timeframe, anchor, and revision.
- [ ] Reference-count subscriptions and release unused state.
      Overlapping leases are reference-counted for routing, but retained warm
      indicator/structure state is not reclaimed after the final lease ends.
- [x] Reject unapproved moves to broader populations.
- [ ] Trigger targeted recomputation from relevant enrichment changes.
- [x] Preserve compact Scanner/Watchlist history and explicitly approved
      materializations. Scanner history is bounded/versioned and Watchlist
      membership transitions are append-only journal evidence; indicator rows
      remain memory-first unless their persistence policy is explicitly enabled.

Acceptance gate: representative all-market load satisfies latency/memory
budgets and invalid scope broadening fails configuration validation.

## 4. Backend integration

- [x] Build one typed QMD client for live and historical products.
- [x] Remove physical QMD source selection from route handlers.
- [x] Implement shared QMD source and query planner boundaries.
- [x] Expose capability, field, container, and configuration catalogs.
- [ ] Move approved SQL into versioned query plans.
- [ ] Remove duplicated route-level SQL as callers migrate.
- [ ] Consume deferred producer services through unchanged bounded contracts.
- [x] Bulk-load point-in-time enrichment and avoid per-row remote queries.
      Scanner reference, corporate-event, float, short-interest, fundamental,
      previous-close, and daily-volume inputs are resolved through causal
      full-universe queries and joined in memory; no per-ticker remote query is
      present on the interactive Scanner/Watchlist path.
- [ ] Maintain a compact feature projection with source, version,
      `available_at`, freshness, and null reasons.
- [x] Compile configuration dependencies, warm-up, modes, permissions, and
      accounts. Configuration schema v18 carries QMD catalog capability keys,
      warm-up bars, and implementation revisions into compiled observation
      dependencies. Missing matching catalog evidence is preserved as
      `catalog_unavailable`; environment/account bindings and action authority
      remain backend-validated parts of the immutable Run Plan.
- [x] Emit immutable Run Plans. Configuration publication compiles the selected
      Strategy, Watchlist universe, account mandates, action authority, OMS
      profile, runtime assignments, and observation dependencies into the
      content-hashed approved release consumed by mode-specific resolvers.
- [ ] Standardize response envelopes, warnings, partial coverage, and typed errors.
      The shared QMD product response is now schema v2 with completeness,
      warnings, coverage status and source revision, and QMD GET transport
      failures are typed through backend HTTP routes. QMD lease mutations use
      the same typed boundary and proxied QMD streams emit schema-v1 terminal
      error frames while retaining the compatible `error` string. Non-QMD
      clients and application-wide success envelopes still need the same
      contract before this broad item is closed.
- [ ] Implement HTTP snapshot plus sequenced delta streams.
- [ ] Fill reconnect gaps or require resnapshot.
- [ ] Isolate budgets for commands, discovery, charts, simulation, and offline work.
- [ ] Bound and version caches.
  - [x] Replace backend service-table and News/SEC histogram dictionaries with
        bounded thread-safe TTL/LRU caches carrying explicit contract revisions.
  - [x] Key the bounded processed-artifact chart LRU by the relevant artifact
        build identities, schema/calculation versions, and presentation revision.
  - [x] Bound the canonical live account-state projection cache by account
        selector and an explicit projection contract revision.
  - [ ] Audit remaining run-local caches and add source/calculation revision
        invalidation where their immutable Run Plan does not already provide it.
- [ ] Enforce user, workspace, environment, mode, account, and command authority.
- [ ] Resolve secrets and broker identifiers server-side.
- [x] Aggregate service readiness separately from data readiness.

Acceptance gate: browser code knows no database table, service credential,
internal topology, or producer formula.

## 5. Market Discovery frontend

- [x] Make Universal Ingest the first, normally locked configuration page.
- [x] Show primitive reason, owner, version, cost, coverage, and consumers.
- [x] Generate Core Scan choices from eligible registry capabilities. The UI
      filters the backend/QMD capability contract by `core_scan`; capabilities
      registered only for narrower scopes cannot enter the chooser.
- [x] Show cost and broadening approval for all-market changes. Activating an
      optional Core Scan capability now requires an explicit population, cost,
      cadence, authority, and allowed-scope confirmation before the change can
      enter the draft; publishing the versioned configuration remains the
      durable approval event.
- [x] Configure Watchlist rules, rank, size, TTL, overrides, and focused calculations.
- [x] Expose Strategy/request/offline availability without calling it unavailable.
- [x] Separate implementation, execution scope, configuration authority,
      operational readiness, and coverage status.
- [x] Show enrichment provenance, freshness, gaps, and null reasons. Market
      Discovery reads the backend application-field registry and presents each
      field's owner/source, point-in-time query plan, `available_at`, freshness,
      coverage path, null reasons, provenance, historical support, cadence, and
      implementation status. Deferred producer fields remain visibly pending.
- [x] Add scanner and membership history views. Market Discovery rebuilds a
      bounded full-market historical Scanner snapshot through QMD History for
      a chosen New York clock and shows the append-only Watchlist event journal.
- [x] Generate fields, columns, and filters from registry metadata. Application
      registry schema v4 supplies Market Discovery presentation policy;
      configuration schema v18 resolves the complete application/QMD field
      catalog, projects Watchlist columns, and validates filter sources and
      operators. The Watchlist editor creates filters only from eligible
      registry rows and supported comparators.
- [ ] Preserve theme, global scale, responsive, and accessibility authorities.
- [ ] Validate all affected states in real browsers.

Acceptance gate: UI availability equals backend registry and runnable truth.

## 6. Canvas and smooth charts

- [x] Publish a versioned Canvas container catalog. The schema-v3 application
      registry exposes container IDs, implementations, products, modes, state
      schema versions, and status through `/api/registries/containers`; shared
      trading workspaces fail closed when the registry or renderer adapter is
      invalid.
- [x] Define typed input/output links and mode compatibility. Link contracts
      declare value type, producers, consumers, clock and identity policies,
      modes, and schema version; registry validation rejects broken container,
      product, link, mode, or direction references.
- [ ] Implement draft, validate, preview, publish, reset, and rebase.
  - [x] Draft, validate, preview, publish, and reset-to-approved are wired.
  - [ ] Add explicit overlay rebase/conflict presentation and save-as-workspace.
- [ ] Instantiate published defaults in Live, Replay, Backtest, and research workspaces.
  - [x] Standalone Canvas resolves the approved profile; Replay uses its pinned
        release profile.
  - [ ] Migrate Live/Paper and Backtest/research workspaces to the same resolver.
- [ ] Persist user/workspace overlays separately from defaults.
  - [x] Standalone Canvas and Replay overlays are isolated by workspace/run and approved revision
        and can be reset without changing Configuration defaults.
  - [ ] Apply the same overlay contract to remaining mode workspaces.
- [x] Route intraday historical charts through QMD History source planning and
      its shared derived cache.
- [x] Return base bars first and progressively add requested indicators,
      signals, and structure from the same cache entry.
- [ ] Merge archive, recent, and live tail by watermark.
- [x] Deduplicate identical historical derived requests with QMD History
      single-flight execution.
- [x] Cancel superseded navigation requests and prefetch adjacent windows.
      Canvas keeps one bounded exact-cursor earlier-page prefetch, consumes it
      only for the matching chart request, and aborts/discards it on navigation.
- [ ] Bound caches and invalidate by source/corporate-action/calculation revision.
- [ ] Recover live sequence gaps or resnapshot.
- [ ] Expose partial, stale, corrected, and source-transition states.
  - [x] Show source tiers, source-plan completeness, engine/schema revision and
        warm-up state for historical chart indicators.
  - [ ] Add live-tail transition, correction, and stale-state presentation.
- [x] Return indicator provenance and warm-up.
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

- [x] Compile approved configuration into an immutable Run Plan. Published
      releases are content hashed, idempotent, append-only revisions; Replay,
      Backtest, Live, and Paper resolve runtime projections from that pinned
      approved payload rather than mutable UI session state.
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
  - [x] Add strategy/run attribution and comparative analysis projections.
        Backtest results now expose the canonical flat-to-flat performance
        journal, including strategy-revision attribution, and a bounded
        comparison endpoint projects the latest terminal runs without
        recalculating statistics in the browser.
  - [ ] Add durable resume/restart from checkpoints.
- [ ] Add deterministic Debug fixtures.
- [ ] Pin causal market/enrichment versions for historical decisions.
- [ ] Standardize progress, pause, resume, cancel, failure, and completion.
- [ ] Add restart-safe checkpoints.
- [ ] Route manual, semi-automatic, and automatic proposals through one control plane.

Acceptance gate: identical Run Plan and recorded inputs produce deterministic
Replay and Backtest decisions.

## 8. Portfolio authority

- [x] Make Portfolio the exclusive account allocation, capital, and risk
      authority. Strategy and confirmed external proposals enter
      `PortfolioManagementEngine.approve`; OMS requires the matching durable
      decision/reservation before submission. Dormant backend direct
      submit/reply/modify/cancel helpers now fail closed, while broker what-if
      preview remains non-executing.
- [x] Define account/account-group ownership. Stable application account keys
      bind one exact runtime broker account and policy; Run Plan mandates bind
      Strategy allocation to those keys; validated Portfolio groups declare
      their member keys and group exposure limits.
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
- [x] Remove generic run priority as an authority. Configuration migration
      deletes legacy Run Plan, mandate, and capital-request priority fields;
      allocation and concurrent admission are decided only by Portfolio policy,
      current broker/canonical state, durable reservations, and fenced account
      or group capacity.
- [ ] Reconcile reservations, positions, cash, and broker truth after restart.
- [x] Journal every Portfolio decision and reservation transition.
- [x] Expose Portfolio configuration and operational UI/API, including distinct
      Run Plan allocations and aggregated Strategy allocation labels.
- [x] Test races, lease expiry, restart, and shared-account conflicts.

Acceptance gate: two processes cannot allocate the same account capital without
one fenced decision.

## 9. OMS authority

- [x] Accept only an execution intent whose durable Portfolio decision and
      reservation match its account, intent, ticker, quantity, and active state.
- [x] Version the execution-intent schema and fail closed on unsupported
      versions while retaining explicit version-1 recovery for legacy journals.
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
- [x] Build order, execution, protection, and reconciliation UI projections in
      the canonical Canvas trading containers.
- [x] Test Portfolio authority, idempotency, restart, disconnect, partial fills, and protections.

Acceptance gate: no chart, strategy, model output, or backend route can issue a
broker command outside OMS.

## 10. Operations, migration, and validation

- [x] Separate liveness from dependency/data/execution readiness.
- [ ] Report coverage, freshness, queues, checkpoints, degradation, and authority.
      QMD live/history coverage, freshness, queue/cache, transition and source
      authority evidence is delivered; uniform checkpoint evidence across the
      remaining existing service contracts is still open.
- [x] Build a central backend/frontend readiness view.
- [ ] Propagate end-to-end correlation and causation IDs.
  - [x] Generate/preserve bounded IDs from browser requests through backend
        context, QMD HTTP headers and WebSocket query identity, QMD Live/History
        response evidence, broker-event envelopes, and authoritative
        Portfolio/OMS journal payloads.
  - [x] Give autonomous Strategy evaluations, Portfolio decisions/reservations,
        and OMS lifecycle records explicit causal predecessors when no HTTP
        request context exists.
  - [x] Give autonomous computation leases explicit causation lineage when no
        HTTP request exists. Backend Watchlist/Strategy/chart publishers derive
        bounded IDs from membership, Run Plan target, or chart request; QMD
        target snapshot schema v2 stores and exposes both identities.
  - [ ] Give autonomous source events and generic background continuations
        explicit causation lineage when no HTTP request context exists.
- [x] Add QMD transition/gap/lag/cache/queue metrics.
- [x] Add discovery computation-cost metrics.
- [x] Add Portfolio disposition/reservation and OMS reconciliation metrics.
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

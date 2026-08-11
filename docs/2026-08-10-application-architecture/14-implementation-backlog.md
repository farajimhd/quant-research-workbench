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
- [x] Route current requests to QMD memory/live tail.
  - [x] Compose compact-event windows across QMD History archive/recent rows and
        the exact QMD Gateway current-live source-plan segment.
  - [x] Compose current-window chart bars/indicators and historical Scanner
        derived snapshots with segment-filtered QMD Gateway live snapshots.
        These bounded snapshot continuations are explicitly marked incomplete
        for pinned Replay rather than being misrepresented as replayable input.
  - [x] Replace bounded product-snapshot continuation with a paged,
        eviction-evidenced cross-market QMD event continuation. QMD History
        now replays that event input through shared computation and certifies
        request completeness separately from immutable Replay eligibility.
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
  - [x] Persist approved-configuration, event, derived-frame, and Scanner-signal
        source-plan/revision evidence in each run manifest and durable journal;
        reject same-product drift during the active run.
  - [ ] Add storage-level immutable revision reads so a changed source can
        continue from the pinned old revision instead of restarting.
- [x] Permit advancing tail watermarks for Live consumers. QMD History event
      pages expose explicit `pinned` and `advancing` policies; advancing reads
      accept newer live revision tokens while keeping the source-plan hash
      fail-closed.
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
- [x] Add reconnect continuation and sequence-gap repair.
  - [x] Make Scanner reconnect start from an authoritative snapshot and
        automatically replace client state after lag or a sequence gap.
  - [x] Add equivalent bounded continuation or resnapshot contracts to the
        remaining raw event and product streams. Lagged raw/compact event,
        intraday-bar, live-state, signal, and historical derived streams emit a
        typed terminal resnapshot frame; periodic product streams are complete
        current snapshots.
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
- [x] Advance the archive handoff watermark only after equivalence passes. QMD
      appends a per-session certificate before deleting either recent table and
      reuses it only when current remote identities and the archive fingerprint
      still match the recorded proof.
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
  - [x] Instrument sampled Core Scan and all-market bar/structure stage latency
        in the QMD operational snapshot without adding per-event histogram cost.
  - [ ] Capture representative active-session throughput, latency, queue, CPU,
        and memory evidence and approve explicit budgets.
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
- [x] Union Watchlist, Strategy, chart, and offline computation requests.
      QMD now owns the validated live lease union and Watchlist, Paper/Live
      Strategy Run, and chart producers are wired.
  - [x] Register QMD History cache work with product/profile, ticker, timeframe,
        engine-parameter hash, event-time anchor, exact source revision, state,
        event count, and footprint; preserve it through backend operations.
  - [x] Aggregate live and historical requirements into one planner projection.
        The backend preserves distinct live/history authority and revision,
        returns partial evidence when one service is unavailable, and exposes
        the result through System and Market Discovery APIs.
- [x] Deduplicate by capability, identity, parameters, timeframe, anchor, and revision.
  - [x] Deduplicate live demand by ticker, capability, timeframe, and capability
        implementation version; expose exact requirement reference counts and
        requested-versus-effective weighted cost.
  - [x] Add parameter hash, anchor, and source revision to the effective identity.
        QMD demand schema v4 includes all three in reference-count keys; current
        Watchlist, Strategy, and chart publishers provide deterministic values.
- [x] Reference-count subscriptions and release unused state. Explicit removal
      or narrowing reclaims focused indicator/tick state immediately, and TTL
      expiry is reclaimed within 30 seconds. Cleanup rechecks current leases
      under each state shard so an overlapping or concurrent lease remains
      resident; workers recheck demand after dequeue so stale queued rows cannot
      recreate released state. Generic Structure is tracked by its separate all-market-path
      migration item rather than being mislabeled as focused retained state.
  - [x] Prevent bar-only leases from enabling per-tick indicator processing.
  - [x] Warm missing ticker/timeframe state once for every newly active scope
        and skip repeated core-bar copies for already-warm lease refreshes.
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
  - [x] Move Canvas context company-News, SEC filing, Scanner summary, and
        bounded SEC identity SQL into registered `canvas_context_v1` builders.
  - [x] Move the causal daily-session-bar aggregation used by historical
        Scanner, ticker facts, and Watchlists into the registered
        `market.daily_session_bars.v1` plan.
  - [x] Move bounded ticker branding and issuer-name lookup into the registered
        `market.ticker_presentation.v1` plan while retaining the existing
        service import as a compatibility re-export.
  - [x] Move the Live market-data universe and bounded Live/Paper tradability
        lookup into the shared registered `market.tradable_universe.v1` plan.
  - [x] Make `reference.identity_for_symbol.v1` resolve to a versioned causal
        identity-anchor builder instead of the ticker-facts composition service.
  - [x] Move the non-fundamental Ticker Facts fanout into the registered
        `reference.ticker_facts.v1` query bundle; keep SEC/XBRL facts under their
        distinct fundamentals plan.
  - [x] Move the Ticker Facts SEC/XBRL current and comparison-history SQL into
        the registered `sec.fundamentals_asof.v1` backend plan without changing
        SEC Gateway publication behavior.
  - [x] Move Historical Scanner's set-based, all-universe XBRL query into the
        same fundamentals plan with causal universe, filing, and recording
        clocks.
  - [x] Move Historical Scanner's set-based identity, market, supply, short,
        corporate-event, and presentation enrichment read into the registered
        `reference.scanner_asof.v1` plan.
  - [ ] Move remaining approved backend SQL domains without modifying deferred
        producer services.
- [ ] Remove duplicated route-level SQL as callers migrate.
  - [x] Remove the migrated Canvas context SQL from its composition service.
  - [x] Replace daily-session-bar service imports with the registered plan;
        retain only a compatibility re-export for external callers.
  - [x] Remove ticker-presentation SQL from its service; the service now owns
        only request normalization, optional-data degradation, and projection.
  - [x] Remove duplicate Live universe SQL from the market-data loader and
        trading command preflight while preserving their distinct consumers.
  - [x] Remove point-in-time identity-anchor SQL from ticker-facts composition;
        retain its existing function name as a compatibility re-export.
  - [x] Remove market, float, borrow, short, identifier, classification,
        corporate-action, and daily-volume SQL from Ticker Facts composition;
        retain builder names as compatibility imports.
  - [x] Remove SEC/XBRL SQL from Ticker Facts composition while retaining its
        existing builder names as compatibility wrappers over the versioned
        fundamentals plan.
  - [x] Remove all-universe XBRL SQL from Historical Scanner composition; it now
        consumes the registered fundamentals builder before deriving cards.
  - [x] Remove point-in-time reference SQL from Historical Scanner composition;
        keep only optional logo URL and row projection behavior in the service.
  - [ ] Remove remaining route/service-local SQL only after its registered plan
        and focused parity tests exist.
- [ ] Consume deferred producer services through unchanged bounded contracts.
- [x] Bulk-load point-in-time enrichment and avoid per-row remote queries.
      Scanner reference, corporate-event, float, short-interest, fundamental,
      previous-close, and daily-volume inputs are resolved through causal
      full-universe queries and joined in memory; no per-ticker remote query is
      present on the interactive Scanner/Watchlist path.
- [x] Maintain a compact feature projection with source, version,
      `available_at`, freshness, and null reasons. Historical Canvas and
      Live/Paper Scanner responses attach one registry-derived column envelope
      with coverage and aggregated null evidence instead of repeating metadata
      on every ticker row.
- [x] Compile configuration dependencies, warm-up, modes, permissions, and
      accounts. Configuration schema v19 carries QMD catalog capability keys,
      warm-up bars, and implementation revisions into compiled observation
      dependencies. Missing matching catalog evidence is preserved as
      `catalog_unavailable`; environment/account bindings and action authority
      remain backend-validated parts of the immutable Run Plan.
- [x] Emit immutable Run Plans. Configuration publication compiles the selected
      Strategy, Watchlist universe, account mandates, action authority, OMS
      profile, runtime assignments, and observation dependencies into the
      content-hashed approved release consumed by mode-specific resolvers.
- [x] Standardize response envelopes, warnings, partial coverage, and typed errors.
      QMD product responses carry completeness, warnings, coverage, and source
      revision. All backend failures use the typed compatibility envelope. JSON
      success envelopes are content-negotiated so existing external callers are
      unchanged; the shared browser client requests them application-wide,
      promotes existing partial-coverage evidence, and unwraps `data` for page
      compatibility.
  - [x] Standardize every backend HTTP and request-validation failure behind a
        typed compatibility envelope and expose code, retryability, correlation,
        and causation through the shared frontend `ApiError` client.
  - [x] Negotiate versioned JSON success envelopes with completeness, warnings,
        data, and request lineage; make the sole browser fetch authority request
        and transparently unwrap the contract for every application API call.
- [ ] Implement HTTP snapshot plus sequenced delta streams.
  - [x] Give the live Canvas compact-event path a versioned ticker snapshot
        with snapshot ID/last sequence and establish its QMD delta subscription
        before the snapshot is captured.
- [ ] Fill reconnect gaps or require resnapshot.
  - [x] Forward tickerless QMD terminal gap frames through the backend and make
        the live tape/quote Canvas replace state from a new snapshot on reconnect.
- [x] Isolate budgets for commands, discovery, charts, simulation, and offline work.
  - [x] Enforce independent HTTP admission limits with typed retryable 429
        rejection and expose per-lane active/completed/rejected/wait evidence.
  - [x] Give Replay/Backtest historical warm-up a configurable bounded fetch
        semaphore and deduplicate the cross-sectional Scanner signal request.
- [x] Bound and version caches.
  - [x] Replace backend service-table and News/SEC histogram dictionaries with
        bounded thread-safe TTL/LRU caches carrying explicit contract revisions.
  - [x] Key the bounded processed-artifact chart LRU by the relevant artifact
        build identities, schema/calculation versions, and presentation revision.
  - [x] Bound the canonical live account-state projection cache by account
        selector and an explicit projection contract revision.
  - [x] Audit remaining run-local caches and add source/calculation revision
        invalidation where their immutable Run Plan does not already provide it.
        Replay caches are controller-local and pinned by the immutable approved
        configuration; Watchlist reference projections are bounded and keyed by
        explicit causal `as_of` revision.
- [ ] Enforce user, workspace, environment, mode, account, and command authority.
- [x] Resolve secrets and broker identifiers server-side. Schema v19 requires
      Paper/Live bindings to name a backend environment key, rejects stored
      broker IDs, migrates older releases, and keeps resolved values out of
      effective/revision browser responses.
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
      configuration schema v19 resolves the complete application/QMD field
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
- [x] Implement draft, validate, preview, publish, reset, and rebase.
  - [x] Draft, validate, preview, publish, and reset-to-approved are wired.
  - [x] Save a customized approved Canvas or Replay overlay as a separate
        revision-scoped workspace without changing Configuration defaults.
  - [x] Add explicit three-way overlay rebase/conflict presentation for
        overlays recorded by the current schema. Conflicting leaf paths are
        shown before the user applies the overlay-preferred merge or keeps the
        new approved default.
- [x] Instantiate published defaults in Live, Replay, Backtest, and research workspaces.
  - [x] Standalone Canvas resolves the approved profile; Replay uses its pinned
        release profile.
  - [x] Migrate Live/Paper to the same resolver after the existing account and
        service preflight. Backtest and Research now also use the resolver.
  - [x] Migrate Backtest to the same resolver using its pinned run profile and
        causal run clock.
  - [x] Add a dedicated Research workspace that resolves the approved profile
        without acquiring a Replay run or executable account authority.
- [x] Persist user/workspace overlays separately from defaults.
  - [x] Standalone Canvas and Replay overlays are isolated by workspace/run and approved revision
        and can be reset without changing Configuration defaults.
  - [x] Apply the same overlay contract to Live/Paper using separate
        mode-and-account-set scopes. Backtest and Research use the same
        revision-safe contract with their own scopes.
  - [x] Apply the same run-and-revision overlay contract to Backtest.
  - [x] Apply the same overlay contract to Research through an isolated
        `research.<workspace>` scope.
- [x] Route intraday historical charts through QMD History source planning and
      its shared derived cache.
- [x] Return base bars first and progressively add requested indicators,
      signals, and structure from the same cache entry.
- [x] Let legacy Paper/Live primary charts render independently and fetch the
      visible daily and five-minute secondary charts on demand, with isolated
      loading and failure state. This is an interim smooth-loading correction,
      not completion of the shared Canvas/QMD resolver migration.
- [x] Merge the QMD History archive/recent base with QMD Live bar snapshots by
      canonical bar timestamp; full live snapshots replace corrections rather
      than appending duplicate candles.
- [x] Deduplicate identical historical derived requests with QMD History
      single-flight execution.
- [x] Cancel superseded navigation requests and prefetch adjacent windows.
      Canvas keeps one bounded exact-cursor earlier-page prefetch, consumes it
      only for the matching chart request, and aborts/discards it on navigation.
- [x] Bound caches and invalidate by source/corporate-action/calculation revision.
  - [x] QMD History bounds entries, total bytes, rows/updates per entry, build and
        fetch concurrency, and broadcast capacity. Cache identity and returned
        provenance include the event-source revision, calculation revision, and
        explicit `raw-unadjusted-v1` corporate-action/price basis.
- [x] Recover live sequence gaps or resnapshot.
  - [x] Resnapshot the active live compact-event Canvas after a typed QMD gap.
  - [x] Resnapshot the merged chart-bar tail from complete bounded QMD Live
        snapshots; a failed refresh retains the last snapshot and reconnects.
- [x] Expose partial, stale, corrected, and source-transition states.
  - [x] Show source tiers, source-plan completeness, engine/schema revision and
        warm-up state for historical chart indicators.
  - [x] Add live-tail transition, current-bar replacement, partial-indicator,
        reconnecting, and stale-snapshot presentation.
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
- [x] Keep Live, Paper, Replay, and Backtest visually and authoritatively isolated.
  - [x] Live and Paper now render explicit mode badges and use distinct
        account-scoped overlays while retaining canonical trading-state and
        QMD Live authorities. Replay and Backtest use run-and-revision scopes,
        pinned clocks, and explicit mode badges; Backtest commands remain
        read-only except for its lifecycle controller.

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
  - [x] Replace the active post-gate Live/Paper workspace presentation with the
        published Canvas resolver. The preflight and broker/gateway boundaries
        remain unchanged; the legacy renderer remains compiled only as a
        temporary rollback path.
- [ ] Complete Backtest through shared runtime contracts.
  - [x] Run a pinned approved revision through one continuous shared
        Strategy/Portfolio/OMS/simulator runtime across multiple sessions.
  - [x] Create, monitor, and stop Backtest runs from the Backtest UI.
  - [ ] Apply causal Watchlist membership changes during the run rather than
        holding first-clock membership static.
    - [x] Apply and journal causal activation/deactivation at every exchange-session boundary.
    - [ ] Replay intraday membership events at the configured Watchlist refresh cadence.
  - [x] Build canonical Backtest performance, Portfolio, position, order,
        execution, and closed-trade result projections.
  - [x] Add strategy/run attribution and comparative analysis projections.
        Backtest results now expose the canonical flat-to-flat performance
        journal, including strategy-revision attribution, and a bounded
        comparison endpoint projects the latest terminal runs without
        recalculating statistics in the browser.
  - [x] Project the pinned Backtest run through the shared Canvas resolver and
        canonical Strategy/Portfolio/OMS state while keeping all interactive
        assignment/proposal commands read-only.
- [x] Add durable resume/restart from checkpoints. Replay, Backtest, and
      Backtest Debug persist an atomic versioned checkpoint spanning exact raw
      and derived cursors, controller clocks/caches, Strategy assignment state,
      simulator cash/positions/orders/executions/market state, runtime counters,
      Watchlist state, and pinned data authority. Portfolio and OMS restore from
      their durable journal before continuation. Resume fails closed for older
      cursor-only checkpoints, changed account/configuration/fixture identity,
      incomplete state, or completed runs.
- [x] Add deterministic Debug fixtures.
  - [x] Inject bounded, causally ordered, content-hashed market-event and
        derived-frame fixtures through the shared historical controller,
        persist exact fixture evidence, and expose mode-specific backend run
        and Canvas APIs without calling QMD History.
  - [x] Add the end-user fixture library/editor and Backtest Debug run page.
- [x] Pin causal market/enrichment versions for historical decisions.
  - [x] Pin QMD event, per-ticker/timeframe derived, and Scanner-signal revisions
        that feed historical Strategy decisions; use the fixture content hash
        as Backtest Debug authority.
  - [x] Persist equivalent revision evidence for every historical Watchlist
        reference/technical/fundamental membership input, including the plan
        and clock when a causal query returns no facts.
- [ ] Standardize progress, pause, resume, cancel, failure, and completion.
  - [x] Give Backtest and Backtest Debug a shared typed `pause`/`play`/`stop`
        command contract and user controls while rejecting Replay-only commands.
  - [x] Publish one versioned lifecycle projection for Replay/Backtest and
        background market-data builds, including canonical state, progress,
        terminal state, timestamps, failure retryability, checkpoint evidence,
        enabled commands, resource identity, and controlling authority.
  - [ ] Unify lifecycle command/status envelopes across Live/Paper controllers
        and background research jobs.
- [x] Add restart-safe checkpoints.
  - [x] Expose durable historical-run checkpoint cursor, event/write clocks,
        processed count, interval, and honest resume-support status in backend
        and UI snapshots.
  - [x] Restore clock, raw and derived source cursors, Strategy, simulator,
        Portfolio, OMS, Watchlist membership, pinned authority, and journal
        state before enabling historical restart resume. Persisted candidates
        remain discoverable after backend restart and mode-specific resume
        endpoints/UI controls never advertise legacy cursor-only checkpoints.
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
- [x] Reconcile reservations, positions, cash, and broker truth after restart.
      Runtime startup refreshes canonical broker state before entry authority,
      OMS recovers nonterminal groups and updates Portfolio reservations, and
      broker/allocation differences are now journaled and restored durably.
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
  - [x] Report historical-run checkpoint status and clocks without claiming
        that an existing checkpoint is resumable.
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
  - [x] Give every durable trading-journal continuation fallback lineage when
        no request or domain-specific predecessor exists; derive correlation
        from the run and causation from the event identity and clock while
        preserving explicit Strategy/Portfolio/OMS lineage.
  - [ ] Give autonomous source events and generic background continuations
        explicit causation lineage when no HTTP request context exists.
- [x] Add QMD transition/gap/lag/cache/queue metrics.
- [x] Add discovery computation-cost metrics.
- [x] Add Portfolio disposition/reservation and OMS reconciliation metrics.
- [ ] Bound queues, caches, subscriptions, retries, concurrency, and result sets.
  - [x] Bound Replay/Backtest subscriber queues and resident controllers;
        coalesce replaceable snapshots, evict only terminal controllers, and
        return HTTP 429 when all configured resident slots are active.
  - [x] Bound Replay/Backtest QMD History derived-stream concurrency and issue
        one cross-sectional Scanner signal query per run window.
  - [x] Bound Historical Scanner and QMD materialization threads to four active
        builds per family; cap coordination registries at 256 terminal/active
        entries with terminal TTL eviction while preserving active work.
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

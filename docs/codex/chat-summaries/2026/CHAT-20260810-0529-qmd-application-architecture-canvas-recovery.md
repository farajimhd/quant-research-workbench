# Define and implement the unified QMD and trading application architecture

- Chat started: 2026-08-10 05:29:41 PDT (America/Vancouver)
- Chat ended or last activity: 2026-08-12 09:14:06 PDT (America/Vancouver)
- Summary written: 2026-08-12 09:14 PDT (America/Vancouver)
- Chat/task identifier: `019feba6-725b-70e0-8df7-c3d174d4d890`
- Repository or scope: `D:\TradingCodes\quant-research-workbench`; QMD Live, QMD History, backend, frontend, Market Discovery, Canvas, Strategy, Replay, Backtest, Portfolio, OMS, and operations
- Related task-history entries: `TASK-0188`, `TASK-0136`, `TASK-0134`, `TASK-0130`, `TASK-0126`, `TASK-0096`
- Source completeness: Complete for this chat. Earlier Canvas, Replay, and strategy chats were not re-reviewed; their existing durable summaries are cross-references only.

## Narrative

The chat began as a code review of Market Discovery fields labeled
Unavailable, including Session Context, Opening Range, and ORB State. The user
was not asking whether every unavailable integration should immediately be
built. The substantive question was whether those computations belonged in the
all-ticker scanner at all. The user restated the intended computational funnel:
only absolutely required work should run over the entire eligible market, while
extra indicators and contextual calculations should run over smaller
Watchlists, strategy universes, or one-ticker chart requests.

That clarification corrected an initially scanner-centric framing. QMD is the
shared market-data computation boundary, not simply a table-producing scanner.
QMD Live receives websocket events, QMD History replays ClickHouse events, and
both should reuse shared deterministic computation code. The top-level event
path normalizes and sequences every event and computes only universal safety
and compact market state. Core Scan maintains the minimum rankable all-market
projection. The Scanner itself should retain current rows, combine deltas, and
serve changes rather than independently recomputing every indicator. Narrower
demand is declared by Watchlists, strategy runs, and requests. Charts use the
same registered products but can request richer history and overlays for one or
a few tickers without placing that cost in Core Scan.

The review also distinguished computation from persistence. Existing scanner
rows and historical artifacts contained RSI, MACD, averages, ATR, Bollinger,
microstructure, structure, reference levels, and signal inputs. Their presence
did not prove that they belonged in universal computation or that every value
should be persisted. The agreed model was a capability registry with explicit
scope, inputs, clock, algorithm version, warm-up, cost class, persistence
policy, and source authority. Universal and Core capabilities are locked system
configuration. Administrators may promote or demote eligible capabilities only
through a validated scope contract; optional expensive families remain focused.

The user then widened the task from QMD internals to the whole application.
Strategies, portfolios, Canvas, replay, live, paper, and backtest were already
partially designed and had to consume the same authorities rather than grow
parallel implementations. The UI needed to expose and label every registered
computation, show Universal Ingest as a non-editable first stage beside Core
Scan, and manage scope placement through configuration. Deployment plans could
remain system-generated when users had no meaningful choice. Canvas
Configuration was defined as the administrative place to test and approve each
container; operational pages should start from that exact approved definition
and persist only their user/workspace overlays.

Two missing design dimensions were added. First, manual and semi-automatic
trading require smooth chart loading, not only scanner snapshots. The resulting
design separates fast current tails from progressive historical windows: QMD
Live owns current state and recent continuation, QMD History resolves stored
history, and Canvas merges versioned pages while preserving gaps, cursors, and
source revisions. Requested indicators and overlays are focused computation,
not all-market scanner work. Second, Scanner and strategy rows need external
enrichments such as float, short interest, fundamentals, identity, labels,
News, and SEC evidence. These were assigned to registered bulk query plans with
point-in-time availability, provenance, freshness, null reasons, and Reference
Gateway identity authority rather than per-ticker remote calls or browser-side
joins.

The source model was refined into three market-data tiers. QMD Live receives
websocket events and retains recent data in `q_live` for three days. QMD History
selects between the advancing live tail, verified recent `q_live` coverage, and
older `market_sip_compact` yearly archive tables. Daily session bars follow the
same authority split and are the basis for historical weekly, monthly, and
yearly products. Plans must tile requested windows without overlap, declare
missing intervals, pin or advance revisions according to mode, and never equate
pagination exhaustion with historical completeness. Replay and Backtest must
fail before the first event when a pinned plan contains a gap or depends on a
live continuation.

The user requested a complete, top-down design after review of existing docs and
repository structure. The resulting linked document set under
`docs/2026-08-10-application-architecture` starts with product principles and a
system context, then describes data authority, QMD delivery, capability and
enrichment registries, Market Discovery, Canvas and charts, Strategy and modes,
Portfolio and OMS, service interactions, operations, security, migration, and
an executable backlog/log. The Market AI context was corrected to permit live
QMD input and historical input through QMD History or governed direct
ClickHouse reads. Later scope restrictions made intelligence changes explicitly
deferred. Only QMD Live, QMD History, backend, frontend, Portfolio, OMS, and
operations were permitted implementation targets; other working producer
services were not to be changed.

Implementation proceeded in durable phases. QMD gained shared capability and
signal catalogs, validated computation targets, universal/Core/focused routing,
bounded stores, focused product engines, source-plan evidence, reconnect and
resnapshot contracts, causal Watchlist products, and QMD History acceptance
tools. Backend work added application and enrichment registries, approved
configuration and run-plan resolution, bulk point-in-time joins, bounded
single-flight composition, shared historical mode controllers, progressive
chart endpoints, and non-executing Live/Paper control-plane boundaries. Replay
and Backtest use pinned clocks, revisions, causal Watchlist membership,
Strategy/Portfolio/OMS state, simulation, checkpoints, and Canvas projections.
Portfolio remains capital/risk authority and OMS remains broker-command and
protection authority. Actual broker submission was deliberately not authorized.

Runtime validation changed several designs. A whole-application load test first
failed because every concurrent Scanner request rebuilt the complete population
and the computation-management endpoint serialized a 62-78 MB per-symbol
requirement graph. Scanner composition was moved behind a bounded single-flight
cache with authority evidence preserved, and the ordinary planner endpoint was
split into a compact summary with explicit detailed access. A post-close run
then passed 1,808 requests in 30 seconds with zero errors. Point-in-time
acceptance proved that AAPL enrichment at two cutoffs advanced only facts whose
availability time had passed. QMD History testing preserved explicit archive,
recent, live, and scheduled-closed segments and exposed real millisecond
archive-to-Recent gaps rather than claiming false parity.

Near completion, the user found two regressions. First, generated Rust output
had been placed inside `services/qmd-gateway/target`, making the repository more
than 1.5 GB. The exact ignored tree was 1.69 GB and was deleted, along with
Python caches. Repository `.cargo/config.toml`, Python/pytest guards, managed
frontend execution, and the QMD launcher now direct build, cache, and log output
to `D:\TradingML\runtimes` and reject repository runtime roots. A small cache
recreated by an already-running parallel intelligence process was removed again;
unrelated intelligence source changes were preserved.

Second, Canvas Management no longer showed the user's approved predefined
containers. The backend application registry still advertised retired
`signal_stream`, while the frontend's approved schema had replaced it with
Strategy Activity. The mismatch caused the frontend to hide the entire addable
catalog. The backend was corrected to the exact 22-container contract, the
frontend now preserves only the verified intersection during adapter drift, and
the Portfolio preview fixture was updated for current management fields. All 22
container kinds mounted without an error boundary. Browser validation then
restored the approved five-window default: Scanner, 1 Minute Chart, Portfolio,
Position Manager, and Orders & Fills. This recovery was committed and pushed as
`627f5aa3`.

The final active-session QMD soak initially failed honestly. QMD retained up to
512 heap-heavy compact events for every observed ticker, allowing millions of
rows and roughly 2.5 GB RSS. Startup maintenance also attempted Massive REST
repair across a persisted 17,849-symbol universe, so the service remained
`catching_up`. This violated the same computational-funnel principle that began
the chat. QMD was changed to enforce both per-ticker retention and a 250,000
event global cap; global eviction advances the same explicit cursor-expiry
evidence as per-ticker eviction. During streaming hours, whole-market REST
repair is deferred while coverage gaps remain explicit. Existing focused ticker
activation repair remains available for Watchlist, Strategy, structure, and
chart demand. Whole-market repair resumes after hours.

All 114 QMD tests passed. The corrected optimized release processed 4,307,038
events across 12,321 symbols over 225 seconds, reached `running`, stabilized at
733.1 MB RSS under the 1.5 GB budget, and recorded zero required-lane failures.
The simultaneous active application profile completed 9,832 Scanner,
Watchlist, Canvas chart, and computation-plan requests in 60 seconds with zero
errors; route p95 latency ranged from 63.948 to 110.270 ms. Evidence remains
outside the repository under `D:\TradingML\runtimes\qmd_validation`. Validation
processes were stopped. The QMD correction and architecture reconciliation were
committed as `52e7d41b`. `TASK-0188` and its rendered history were added and
pushed in `20d4f8c5`.

## Durable decisions

### Confirmed requirements

- Universal Ingest and Core Scan must remain minimal, locked, and observable.
- Optional indicators, structure, ORB, composites, and rich chart products run
  only for declared Watchlist, Strategy, request, or offline demand.
- Canvas Configuration owns approved container defaults; runtime workspaces use
  those defaults and persist separate user overlays.
- Generated builds, dependencies, logs, caches, reports, and evidence never
  belong in the repository.
- Intelligence producer services remain deferred; non-QMD producer services
  must not be changed while they are operating correctly.

### Architectural decisions

- QMD Live and QMD History share deterministic `qmd_core` computation while
  retaining separate live and historical orchestration responsibilities.
- QMD History is the unified planner for archive, recent, and current-live data;
  coverage, completeness, and revision policy are independent fields.
- All computations and enrichments are registry entries with explicit scope,
  cost, clock, inputs, version, persistence, and authority.
- Reference enrichment is set-based and point-in-time; identity is separate
  from derived cache and from model or strategy evidence.
- Portfolio owns allocation/risk; OMS owns broker commands/protection; Strategy
  owns decisions; Canvas owns presentation and interaction only.
- Bounded caches may improve delivery but must preserve revision, freshness,
  cursor-expiry, and gap evidence.

### Rejected approaches

- Computing every available indicator over all tickers because a Scanner column
  exists.
- Treating chart requests or Watchlist calculations as Core Scan work.
- Hiding all Canvas containers because one adapter identifier drifts.
- Claiming complete history from exhausted pagination or filling source gaps by
  inference.
- Running whole-market REST repair during the live session.
- Introducing an unapproved duplicate archive or executable broker path.

### Assumptions and unresolved uncertainty

- The current 250,000-event cache budget passed representative active traffic
  but remains configurable and should be re-evaluated after material feed or
  consumer changes.
- Exact immutable storage for an older pinned source revision has no approved
  retention authority yet.
- Real archive-to-Recent millisecond gaps remain data facts until repaired or
  certified by the owning coverage producer.

## Delivered outcomes

- Complete linked architecture: `docs/2026-08-10-application-architecture`.
- Canonical implementation ledger and evidence: `14-implementation-backlog.md`
  and `15-implementation-log.md` in that directory.
- Completed task-history entry: `TASK-0188`.
- Restored and browser-validated 22-container Canvas catalog and approved
  five-window layout: commit `627f5aa3`.
- External-runtime enforcement and repository cleanup; final repository was
  approximately 85 MB with no QMD `target`, root `target`, `__pycache__`, or
  `.pytest_cache` directories at handoff.
- Active-session QMD global memory bound and streaming repair policy: commit
  `52e7d41b`.
- Task-history linkage: commit `20d4f8c5`.
- Passing validation: 114 QMD tests, nine lifecycle guards, managed frontend
  production build, 21 focused Canvas/registry/preview/lifecycle tests, all
  Canvas kinds mounted, 4.3-million-event active soak, and 9,832-request active
  application profile.

## Unfinished or hanging work

1. **Immutable old-revision reads.** Current state: active runs detect revision
   drift and fail closed or restart. Why: no approved QMD snapshot/cache storage
   authority or retention budget exists. Next action: approve the storage
   authority and retention contract, then implement immutable reads. Owner:
   user/system architecture plus QMD. Related task: `TASK-0188`.
2. **Archive-to-Recent coverage holes.** Current state: QMD History reports the
   real gaps explicitly and Replay/Backtest reject incomplete pinned plans.
   Why: coverage evidence is missing at several session boundaries. Next action:
   repair or certify those intervals through the authoritative coverage
   producer and rerun parity acceptance. Owner: QMD/market-data operations.
   Related task: `TASK-0188`.
3. **Full-market archive performance.** Current state: archive tables are
   ordered by ticker/ordinal and cannot meet a broad event-time SLO through
   query tuning alone. Next action: approve either a QMD-owned time-order cache
   or a Market SIP physical projection. Owner: user/data architecture; Market
   SIP changes require renewed authorization. Related task: `TASK-0188`.
4. **Compatibility retirement.** Current state: measured compatibility routes
   remain. Why: deferred Market AI and Text Intelligence callers have not
   migrated. Next action: migrate and prove zero callers before removal. Owner:
   deferred intelligence work. Related task: `TASK-0188`.
5. **Executable Live/Paper deployment.** Current state: configuration,
   preflight, Portfolio/OMS validation, non-executing proposals, Replay, and
   Backtest are implemented; broker submission is disabled. Next action:
   separately authorize IBKR Gateway/Supervisor deployment and authenticated
   paper-order acceptance, then repeat Portfolio and OMS admission immediately
   before submission. Owner: user/trading operations. Related tasks:
   `TASK-0188`, `TASK-0134`.
6. **Deferred intelligence integrations.** Current state: Market AI direct/live
   QMD integration, News/SEC/Text FeatureUpdates, embedding/model promotion,
   strategy model observations, and research-job lifecycle migration were not
   changed. Next action: resume each under its owning intelligence task after
   current work completes. Owner: intelligence programs. Related task:
   `TASK-0188` and the applicable intelligence ledger rows.

## Unavailable or incomplete source chats

- Earlier Canvas/QMD design chat `019f70cb-ac2e-7952-b33c-f86a3312b077` was
  not re-read for this summary; use
  `CHAT-20260717-0857-design-tape-quote-and-canvas-market-intelligence`.
- Replay design chat `019fae9a-4c3c-7413-bb66-04e34a0352f0` was not re-read;
  use `CHAT-20260713-0925-foundational-canvas-historical-trading-workspace` and
  the current architecture documents for superseding decisions.
- Strategy/OMS chat `019f9e98-eec0-73b3-a9ee-5742a1f5fc7a` was not re-read;
  use `CHAT-20260726-0624-canvas-strategy-order-management`.

## Handoff to the next chat

Read `TASK-0188`, the architecture `README.md`, `14-implementation-backlog.md`,
and `15-implementation-log.md` first. Preserve the computational funnel, the
three-tier QMD source plan, explicit gap/revision evidence, the approved Canvas
catalog, Portfolio/OMS authority separation, and external-runtime rules. Do not
promote optional calculations into Core Scan, create a repository-local build,
change a deferred producer service, fabricate source continuity, or enable
broker submission. The next permitted action should be selected from the six
gates above; immutable revision storage, archive projection, Market SIP changes,
and executable brokerage all require explicit user approval or data-operations
authority.

# ARTE: user requirements from the design conversation

## Purpose and source

This file summarizes the user's messages and annotation comments in the ARTE design
conversation. It records intent, decisions, corrections, and open questions.
It is an organized summary, not a verbatim transcript or an implementation report.

Original source scope: the conversation supplied with this task, from the
initial live-chart latency concern through the ARTE rename and the request for
this record. Later confirmed corrections are appended with new source labels.
Conversation identifier: `01a06023-7d1e-7c63-82c6-fc12916f17e1`.
Individual message dates are not inferred. Source labels identify grouped passages,
not exact message IDs.

The source labels are document-local references. They do not require the chat, its
storage location, or the parent application to be available at build or runtime.

## How to read this record

- **Confirmed:** an explicit user requirement, approval, or correction.
- **Preference:** a direction expressed by the user, not an exact implementation mandate.
- **Exploratory:** an idea or question that still needs engineering evidence.
- **Superseded:** an earlier direction replaced by a later user message.
- **Engineering detail:** an assistant-proposed mechanism, not an independent user demand.

Later explicit user decisions take precedence over earlier proposals. Do not interpret
approval of one point as approval of every mechanism in the surrounding response.
The linked design documents define implementation contracts and validation gates.

## 1. Purpose and motivation

**Confirmed. Sources: U01, U02, U10, U13.**

- The main purpose is to run strategies on live market data.
- The user observed charts, trades, and bars pausing, then updating in bursts.
- The user wants the least practical latency between data arrival and broker orders.
- Workstation compute should support strategy execution.
- Laptop charting and strategy-log inspection should be separate from execution.
- The current strategy is somewhat working, but its robustness is not established.
- The design must account for updated code and useful existing abstractions.

The observed chart behavior is user-reported motivation. It is not proof that the
strategy or provider caused the delay. Performance claims still require measurement.

Design owners: [Architecture](02-architecture.md),
[Performance and operations](08-performance-operations.md).

## 2. Standalone boundary and existing source

**Confirmed. Sources: U02, U04, U13, U14, U15, U19.**

- Build an alternative implementation focused on efficient strategy execution.
- Do not modify existing application source files.
- Copy useful source into the new root, then enhance the copy.
- Apply the same rule to frontend source. Remove stale or extra code only in the copy.
- Preserve useful designs and authority boundaries. Configuration does not require UI ownership.
- Treat strategy, OMS, portfolio, and other configurations as engine inputs.
- Include required dependencies in the standalone distribution.
- Include the requested IBKR browser-login support and reference-gateway responsibilities.
- Do not depend on the current backend, frontend, reference service, or broker supervisor.
- Use ClickHouse for persistence. The user does not want a dependency on the current app.

The user first suggested using existing supporting services. Later messages clarified
that their required responsibilities must be included in the new distribution.
Bundled dependencies are allowed. Dependence on an existing app installation is not.

Design owners: [Charter](01-charter.md), [Architecture](02-architecture.md),
[Deployment](10-deployment.md).

## 3. Historical and live market data

**Historical direction as of U18, later amended by U25. Sources: U03, U05,
U14, U15, U16, U17, U18.**

- Allocate a separate ClickHouse database for the new system.
- Receive live data through WebSocket.
- Acquire historical data and repair gaps through REST.
- At that point, the user proposed not requiring flatfiles or
  `download_update_events` in the new system. U25 supersedes this exclusion.
- Use a unified event authority for historical and live trade/quote data.
- Preserve execution/participant timestamps where the source supplies them.
- Preserve the local receive timestamp for live latency inspection.
- Convert received trades and quotes to compact persisted events without blocking Live.
- Avoid redundant information, including ordinary REST/WebSocket overlap.
- Explain historical/live differences and manage their use explicitly.
- Do not claim historical data supports a condition when its required fields are absent.

### Legacy archive protection

**Confirmed. Source: U15.**

The existing `market_sip_compact.events_YYYY` tables are expensive established
assets. The earlier boundary was: do not change their schema, rewrite their
contents, or enrich them in place; only the existing
`download_update_events` path may append new rows. The proposed REST-based
system would not replace or modify those assets. U25 now allows certified
read-only historical use and calls for a Rust/ClickHouse rewrite of flatfile
digestion. Its eventual write target remains undecided.

### Ordinal decision

**Confirmed. Sources: U16, U17, U18.**

The user initially asked how ordinals would support backfill and gap fill. They then
noted that an ordinal-keyed table requires its ordinal before insertion.
After the proposed new storage-key design, the user explicitly accepted dropping
the ordinal requirement.

Do not reintroduce a persisted dense ordinal as a prerequisite for event insertion.
The exact source key, replay ordering, and optional in-memory array indexes are
engineering choices. They still need validation.

### Earlier acquisition plan

**Superseded. Sources: U03, U05, U14 -> U16, U17.**

Earlier messages proposed keeping historical flatfile import and REST recent-gap fill.
The user wanted to replace a fixed three-day check with the last certified archive date.
They explained that flatfiles often arrive the following morning, leaving a recent day
to be filled through REST. They also wanted the importer to trigger level checkpoints.

The later REST-historical direction removed the importer/flatfile dependency
at that time. U25 reverses that exclusion while retaining the underlying
requirements: acquire only missing coverage, repair all required products,
and produce the next session's historical seed after the session completes.

Design owners: [Events](03-events.md), [Data lifecycle](04-data-lifecycle.md).

## 4. Readiness, maintenance, and retention

**Confirmed. Sources: U02, U03, U04, U05, U12, U14.**

- Determine essential computations from strategy requirements at startup.
- Start only the services and calculations that those requirements need.
- Current strategies do not require News, SEC, or BarGPT. Do not add them.
- Keep historical acquisition and gap repair in a separate maintenance service.
- Gaps are critical at startup. Fill them before allowing strategy execution.
- Gap repair must satisfy all dependencies, not only event-row coverage.
- Persist required bars, indicators, and level state through their designated paths.
- Persist only products that are actually needed.
- Keep historical bar storage to a short configurable window for backtests.
- Persist required live-derived state for use on the following day.
- Run whole-session historical level calculation after the market session is complete.

The user described preventing MDE from running with a gap. The design implements
this as a trading-readiness gate. It may receive and buffer live data while repairing.
That receive/buffer distinction is an engineering mechanism, not a separate user quote.

Design owners: [Data lifecycle](04-data-lifecycle.md), [V7 state](05-v7-state.md).

## 5. Unified Structure and V7

**Confirmed latest direction. Sources: U12, U14, U15.**

- Unified Structure Levels are essential strategy inputs.
- Warm the streaming level book from historical checkpoints.
- Track important levels as live events arrive.
- Support historical backfill and completed-session persistence.
- Keep useful state resident overnight for the next session.
- Store authoritative historical checkpoints in ClickHouse, not solely on disk.
- Use historical V7, with access to the completed session, to create tomorrow's seed.
- Use streaming V7 levels for intraday strategy decisions.
- Streaming levels may be cached or persisted separately for audit.
- Do not promote streaming state into the authoritative historical daily seed.

### Interval-based storage

**Confirmed direction with schema flexibility. Sources: U14, U15.**

The user recalled a level-row contract with start/end intervals. Its benefits were
reduced redundancy and causal queries. The user asked for a new V7-compatible contract
inspired by half-open intervals. Fields may be added or removed as V7 requires.

The user did not require copying the old experimental schema or its algorithm.
Exact fitting payloads, manifests, and deduplication layout remain engineering details.

### Every-event requirement

The user wants event-by-event tracking without missed important levels. The design
preserves V7's defined causal observation boundaries. It does not assume that this
requires a complete model refit on every trade or quote.

Design owner: [V7 state](05-v7-state.md).

## 6. Compute, memory, and process placement

**Confirmed objectives; Rust is a preference. Sources: U02, U04, U05, U09, U14.**

- Use workstation resources to support low-latency execution.
- Keep current-session events and required historical/calculated state in memory.
- Avoid fetching decision inputs from ClickHouse during the hot path.
- Use concurrency and vectorized operations where effective.
- Analyze and trade many tickers without one ticker unnecessarily blocking another.
- Make repeated historical backtests very fast.
- Rust is the user's preferred implementation direction.
- Consider MDE and strategy in one process to avoid HTTP transport overhead.

The user asked whether shared memory or a memory address would be faster than HTTP.
Same-process domain state is the adopted design direction. Raw pointers are not a
user requirement. Ownership, bounded queues, and exact concurrency limits are
engineering mechanisms that must preserve causal order and shared cash correctness.

The desire for no latency is a performance objective, not an established guarantee.
The user did not supply numerical latency targets or hardware budgets.

Design owners: [Architecture](02-architecture.md),
[Performance and operations](08-performance-operations.md).

## 7. Latency monitoring

**Confirmed. Sources: U05, U06.**

- Capture receive timestamps for live events.
- Compare receive time with available trade/quote source timestamps.
- Audit WebSocket latency during Live.
- Continue reporting unresolved latency issues.
- Treat a large timestamp gap as dangerous for trading.
- Consider changing the provider if measured provider latency is unacceptable.

Exact thresholds, clock-error handling, reporting intervals, and recovery hysteresis
were not specified by the user. They remain explicit implementation/release settings.
The design must distinguish provider/feed delay from stale reports and local backlog.

Design owner: [Performance and operations](08-performance-operations.md).

## 8. Strategy, OMS, and broker protection

**Confirmed. Sources: U02, U07, U08, U14.**

- Preserve separate strategy, portfolio, and OMS authorities.
- Reject exposure-increasing orders without a proper stop and profit target bracket.
- Apply this rule to entries, additions, reversals, and other exposure increases.
- Protection should remain at the broker if the engine loses connectivity.
- For long orders during applicable market hours, keep the target within the upper
  LULD band and the stop within the lower band. Reverse the relationship for shorts.
- Avoid equality at a band. The user agreed to a configurable buffer of several ticks,
  potentially wider when latency rises.
- Study actual broker requirements for real and Paper trading.
- Define broker communication and order-notification handling explicitly.
- Support simultaneous positions in the same ticker across multiple accounts.
- Allow different cash parameters for each account.

The notification request does not specify a blanket warning-suppression policy.
Message-category allowlists, confirmation handling, partial-fill tests, and continued
order/fill monitoring are engineering requirements in the broker design.
The user did not authorize live order submission through this documentation work.

Design owner: [Trading and broker](06-trading-broker.md).

## 9. Backtesting and debugging

**Confirmed. Sources: U10, U11, U14.**

- Provide a way to debug and validate an engine that normally consumes live events.
- Run strategies against historical data.
- Reuse useful existing backtest practices and abstractions.
- Keep one authority with central shared modules where appropriate.
- Historical backtests must not impact Live.
- Strategy contracts, logs, and related evidence must be consistent across modes.
- Make repeated historical runs fast enough for strategy iteration.

The exact division between historical replay, recorded-live replay, and fault
simulation is an engineering design. So are semantic hashes and acceptance fixtures.
Do not imply that historical data can reproduce missing original receive timestamps.

Design owner: [Backtest and validation](07-backtest-validation.md).

## 10. Frontend and operator workflow

**Confirmed latest direction. Sources: U01, U14, U15, U19.**

- Prioritize Backtest, Debug, and Live pages.
- Support launching backtests, studying results, and following live strategies.
- Reuse useful frontend contracts/components when practical.
- A minimal reduced app is acceptable. The full existing feature set is not required.
- Copy frontend source into the new root before adapting it.
- Do not require the current app to run or serve the new frontend.
- Keep charts and logs separate from the strategy's execution-critical path.

The earlier suggestion of using the current app directly was narrowed by the later
explicit copy-only requirement. Reuse means copied source and compatible contracts,
not a shared running backend or a parent frontend dependency.

Design owner: [App and control API](09-app-contracts.md).

## 11. Deployment and repository transition

**Confirmed. Sources: U04, U05, U19, U20.**

- Use separate deployment and versioning for this implementation.
- Deploy to the workstation runtime; the user starts the deployed runtime.
- Support laptop runtime testing as well.
- Provide one deployment script and one run script.
- Keep compatible unchanged services running across deployment.
- Restart only relevant changed services and required dependents.
- Support separate named laptop/workstation configuration profiles.
- Begin inside a dedicated root within the current repository.
- Prepare comprehensive, organized design documents before implementation.
- Use clear language and short sentences.
- After the initial implementation is independent, move to a standalone repository.
- Name that repository and project ARTE: Automated Real-Time Trading Engine.
- Use `arte` as the project folder name.

Dynamic device allocation was an earlier suggestion. Named predefined profiles were
the later direction. Hardware validation may still check those profiles; silent
resource changes are not implied by selecting a device name.

The user accepted ARTE after rejecting earlier names. No rejected name is an alternate
project identity. MDE remains the agreed market-data component name.

Design owner: [Deployment](10-deployment.md).

## 12. Decisions that must not be attributed directly to the user

The following are engineering proposals or release details, not exact user mandates:

- Specific ClickHouse table keys, codecs, merge engines, or revision layouts.
- The proposed database spelling `arte`, as distinct from approval of a separate database.
- Particular Rust crate boundaries or a compiled-only strategy interface.
- Exact cash reservation locks and broker session serialization mechanisms.
- ClickHouse command acknowledgment and unknown-submission recovery protocols.
- Exact latency thresholds, retention periods, worker counts, or memory budgets.
- Particular API route names, manifest fields, hash algorithms, or test thresholds.
- Full automatic authentication without user intervention.

These details require engineering validation and, where material, user approval.
The [implementation plan](11-implementation-plan.md) lists unresolved gates.

## Source map

The excerpts identify the relevant user message or annotation. They are not the full
messages. Related comments in the same turn are grouped under one source label.

| Label | User message anchor | Main contribution |
|---|---|---|
| U01 | "sometimes the trades and bars stop updating" | Latency concern; workstation execution and laptop observation |
| U02 | "configs are like an arguments to the main function" | Config independence; standalone copy; minimal essentials; Rust preference |
| U03 | "historical fill using download_event_update and gap fill" | Separate historical/repair responsibilities |
| U04 | "there should be a separate deployment and versioning process" | Memory/concurrency, no optional enrichment, bundled broker support, deploy/run |
| U05 | "first gap needs to be filled via rest api" | Startup gate, receive timestamps, persistence, selective restart, device profiles |
| U06 | "auditing websocket latency in live and keep reporting" | Continuous latency reporting and trading concern |
| U07 | "any order that does not have proper stop loss and profit target" | Mandatory brackets and directional LULD bounds |
| U08 | "agree" on exposure increases and buffered bands | Scope includes entries/adds/reversals; buffer approval |
| U09 | "wouldn't it be faster if they live in a same process" | In-process MDE/strategy instead of HTTP |
| U10 | "How can we debug, validate the engine?" | Historical backtesting and validation |
| U11 | "all contracts, logs and etc of strategy must be the same" | Shared authority and Live/Backtest isolation |
| U12 | "highly dependent on Unified Structure Levels" | Historical warmup, streaming tracking, maintenance and overnight continuity |
| U13 | "review the codebase again, many things have been updated" | Current-code review; robustness not yet proven; standalone persistence boundary |
| U14 | "How the system use data from historical and live" | Data semantics, retention, interval levels, broker accounts, core UI pages |
| U15 | "we cannot alter market_sip_compact.events_[year]" | Legacy protection, copy-only source/UI, new V7 intervals, historical-only daily seeds |
| U16 | "a separate database in clickhouse allocated for the new system" | Unified REST-maintained history, richer timestamps, ordinal question |
| U17 | "for streaming you should use websocket but for historical we can use rest" | Transport clarification, no redundancy, ordinal insertion concern |
| U18 | "if you want to drop the ordinal, I am fine with it" | Explicit approval to remove persisted ordinal requirement |
| U19 | "prepare the folders inside the repo" | Design-first scaffold, portable new root, clear docs, later repository extraction |
| U20 | "ARTE is a good repo name, rename all and update design doc" | Final project identity |
| U21 | "summarize, organize and add them to the doc in a separate file" | This user-requirements record |

## Maintenance rule

### Implementation follow-up

**Confirmed. Sources: U22, U23.**

- Implement ARTE primarily in Rust, using the existing code as a copy source where useful.
- Use concurrent and vectorized approaches consistent with the design.
- Do not run any service for testing until the user copies the new code to a new repository.
- Use the latest strategy version as a starting point, even though it is still changing.

| Label | User message anchor | Main contribution |
|---|---|---|
| U22 | "do not run any service for test until I copy the new code to a new repo" | Implementation authorization with a service-testing restriction |
| U23 | "take the latest version, but it is being updated" | Latest strategy as a pinned starting point, not an automatically changing dependency |

Preserve superseded decisions as history, not active requirements. Add later user
corrections with new source labels. Update the owning design document when a change
is accepted. Never fabricate a user approval from an assistant recommendation.

## Later backtest direction

**Confirmed. Source: U24, user's Strategy 350 and backtest notes.**

- Consider the latest Strategy 350 as a replacement for the prior starting
  strategy. Treat its changing source as a pinned candidate, not an approved live
  release.
- The scanner, data catalogue and rule-set design govern backtest preparation.
  Their vectorized bar path supersedes a different current implementation path.
- Start with completed 100 ms bars so several days can be backtested quickly.
  Consider coarser timeframes only if measured speed warrants the fidelity cost.
- Read required bars, indicators and historical level books from ARTE ClickHouse.
  Optimize historical level construction over bars and live level updates over
  streaming data.
- Generate historical market signals and Watchlist results efficiently from bars.
- Design and validate the contemplated compact bar contract before materializing it.
- Persist all logs and run evidence in ClickHouse, compactly and compressed.
  SQLite is forbidden, including a backtest spool.

| Label | User message anchor | Main contribution |
|---|---|---|
| U24 | "latest strategy called 350" and "Logs ... Clickhouse, using SQLite is forbidden" | Replacement candidate; vectorized 100 ms bar backtest; catalogue/rules; compact bars; ClickHouse-only evidence |

## Latest source and documentation correction

**Confirmed. Sources: U25 and U26. These supersede the REST-only historical
boundary but do not approve a new importer write target.**

- Reorganize ARTE documentation into shorter, clearer sections without
  deleting earlier decisions, context, or information. Mark changed decisions
  as superseded rather than silently removing them.
- Permit certified `market_sip_compact.events_YYYY` as historical input.
  Its compact contract now has a delayed-trade reporting bit, and the current
  `download_update_events` insertion path computes it for future imports.
  This changes the earlier reason for excluding the archive from ARTE.
- Do not rely on uninterrupted Live uptime for historical Backtest coverage.
  Include flatfile digestion for missed periods as well as REST gap repair.
  Historical recovery does not recreate original local receive timestamps.
- Preserve and use the existing `arte` V7 level intervals, coverage, and
  builder checkpoints when compatible. Do not discard that efficient contract
  merely because ARTE has an earlier alternative seed proposal.
- Preserve and use persisted `arte` bars and indicators so multi-day Backtest
  preparation can be fast. Verify their source and calculation identity.
- Reimplement the necessary `download_update_events` semantics in Rust and
  ClickHouse inside ARTE. Discuss the importer's write target and detailed
  contract before authorizing writes; do not simply copy/execute the Python
  script or assume permission to append to yearly tables.

| Label | User message anchor | Main contribution |
|---|---|---|
| U25 | "boundary that might not help ... flatfiles digestions" | Read-only compact history, flatfile resilience, retained `arte` V7 and bars/indicators, documentation reorganization |
| U26 | "download_update_events ... rewritten ... in Rust and Clickhouse" | Rust/ClickHouse importer target; write destination remains for discussion |

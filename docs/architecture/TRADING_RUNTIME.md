# Shared Trading Runtime

## Boundary

`src/trading_runtime` is the single order, execution, account, position,
portfolio, risk, and run-journal authority. Within it,
`PortfolioManagementEngine` owns account-specific sizing, allocation,
reservations, portfolio controls, and reconciliation, while
`OrderManagementEngine` is the exclusive broker-command authority: strategies
emit semantic intents and cannot place or modify orders directly. Runtime modes
change only three dependencies:

| Mode | Market source | Broker | Clock |
|---|---|---|---|
| Live / Paper | live `qmd-gateway` | `IbkrClientPortalAdapter` | wall clock |
| Replay | Rust `qmd_history_gateway` | `SimulatedBrokerAdapter` | controllable historical clock |
| Backtest | Rust `qmd_history_gateway` | `SimulatedBrokerAdapter` | maximum-speed historical clock |
| Backtest Debug | the exact backtest event window/cursor | `SimulatedBrokerAdapter` | stepped historical clock |

The broker-facing contract retains Client Portal names such as `acctId`,
`cOID`, `conid`, `orderType`, `auxPrice`, `tif`, and `outsideRTH`. Strategies
must not construct a different order type for simulation. Live and simulated
brokers both expose accounts, preview, place, warning reply, modify, cancel,
live orders, executions, positions, account summary, and ledger resources.

## Historical semantics

The date selected for Backtest is an exclusive anchor: the configured number
of prior exchange sessions is used. The Replay date is inclusive and replay
starts at 04:00 America/New_York on that session. Backtest Debug resolves the
same window as Backtest and must use the original strategy revision,
configuration, event cursor, simulation configuration, and checkpoints.

Only strategies persisted with `automatic=true` can run in Backtest or
Backtest Debug. Strategy definitions are immutable by `(strategy_id, revision)`.

Live and historical market sources are separate Rust binaries. The existing
QMD crate exports `qmd_core`, and both binaries compile against its canonical
event decoder and enriched-bar engine. Historical condition/indicator tokens
and tape ids are restored through the canonical ClickHouse reference tables
before canonical events reach the runtime.

## Simulation

The simulated broker implements IBKR order states and request fields. It
supports MKT, LMT, STP, STOP_LIMIT, MIDPRICE, TRAIL, and TRAILLMT validation;
quote-aware execution; trade fallback; deterministic liquidity participation;
partial fills; brackets; OCA sibling cancellation; DAY/GTC handling;
`outsideRTH`; commissions; per-account state; cash, positions, summary, and
ledger. It does not imitate network/session faults; those are validated against
the paper Client Portal Gateway.

## Persistence

Every runtime fact first commits to a SQLite WAL journal and outbox. This is the
crash-recovery boundary for order commands and checkpoints. The trading journal
gateway mirrors records to `q_live` tables with the fixed `tr_` prefix:

- `tr_strategy_v1`, `tr_run_v1`, `tr_run_account_v1`
- `tr_journal_v1`, `tr_signal_v1`, `tr_order_event_v1`
- `tr_fill_v1`, `tr_trade_v1`
- `tr_portfolio_v1`, `tr_position_v1`
- `tr_checkpoint_v1`, `tr_reconcile_v1`

Ticker-scoped strategy campaigns additionally persist their current recovery
state in the local `strategy_assignments` WAL table. Every assignment command,
evaluation, semantic intent, state transition, and resulting order event also
enters the generic journal/outbox, so the typed local recovery row never
becomes unaudited state.

## Strategy decision boundary

Strategies now emit broker-neutral semantic intents before broker requests.
The shared runtime records the decision and intent, then gives it to portfolio
management. Portfolio management binds the explicit account, calculates or
resizes quantity, commits a durable capacity reservation, and passes only an
approved intent to order management. Order management invokes the configured
planner, performs its final execution-safety validation, selects a bounded
execution tactic, and only then submits or modifies it.
Normalized causal strategy
observations can arrive on indicator, signal, bar, manual, position, or order
events without making raw market-data handlers a second strategy authority.

The detailed price tactics, bracket roles, full profit-pocket modification,
shortability gate, broker-warning policy, failure states, and deployment gates
are documented in [IBKR Order Management](IBKR_ORDER_MANAGEMENT.md).

The per-account policy, multi-account session model, portfolio decision,
reservation, allocation, synchronization, recovery, mode-parity, API, and
Canvas contracts are documented in
[Multi-Account Portfolio Management](PORTFOLIO_MANAGEMENT.md).

The built-in `long-momentum-campaign@2` contract and its Canvas presentation
are documented in [Long Momentum Campaign](LONG_MOMENTUM_CAMPAIGN.md).

ClickHouse is the durable audit/analytics store, not the synchronous command
queue. Outbox rows are acknowledged only after the generic journal row and the
corresponding typed row are accepted.

## Current cutover status

The new authorities and services are implemented. The stale strategy catalog,
`src/backtest` execution engine, and `/api/backtests/*` routes were retired in
the controlled cutover. Immutable saved-run artifacts remain readable through
the canonical trading-state adapter; they are evidence, not executable
strategy definitions or proof of replay/live brokerage parity.

Replay setup accepts one exchange date, a 04:00-20:00 New York entry clock,
and initial simulated cash. Approval snapshots the global Canvas profile and
its revision. `/api/trading/replay/preflight` verifies the QMD History service
identity, canonical day coverage, runtime storage, Canvas symbols, selected
automatic assignments, and explicit source-account to simulated-account
mapping before the run can be created.

`ReplayRunController` is the transport authority. It reads canonical QMD
events for the full extended session, causally warms market and indicator
state before the approved entry clock without evaluating strategy orders, and
then pauses. Play, pause, event-time step, fixed speed, maximum speed,
forward-only seek, and stop change transport state; they never mutate event
timestamps. Derived QMD frames enter the same `TradingRuntime` through
account-bounded `StrategyObservation` values before the first later raw market
event. The shared runtime, portfolio/risk engines, order planner,
`SimulatedBrokerAdapter`, and immutable strategy revision remain the only
trading authorities.

Each run writes `manifest.json` and `journal.sqlite3` beneath
`D:\TradingML\runtimes\trading\replay\<run_id>`. The manifest records the
approved definition and Canvas snapshot; the WAL journal owns lifecycle,
strategy decisions, orders, fills, assignments, and checkpoints. WebSocket
updates publish the bounded run clock/status projection, while the Canvas read
API projects canonical broker state and run-journal strategy evidence. The
frontend renders the existing `CanvasWorkspaceSurface` with the approved
profile, so Replay does not copy Canvas layout, settings, link, chart, scanner,
or trading-container implementations and does not poll Live performance.

Backtest remains a separate maximum-speed/debug lifecycle. Its setup reports
the current dependency rather than invoking Replay's interactive controller
or the retired prepared-bar routes.

Market status has one UI contract with mode-specific authority. Replay,
Backtest, and global Canvas preview derive pre-market (04:00-09:30), regular
(09:30-16:00), after-hours (16:00-20:00), and closed state from their
America/New_York clock. Active Replay advances the status with its event-derived
cursor. Live does not infer state in the browser: the backend exposes QMD's
standardized `/snapshot/status` Service Core payload alongside gateway health,
and the top bar maps that payload or shows `Unavailable` when it cannot be read.

Canvas layout and container testing for the new shared workspace are global
configuration under `Configuration -> Canvas`. The main canvas owns the
persisted user-selected default layout and a registry of focused child canvases;
Replay and Backtest do not expose mode-specific canvas designers. Containers
may move between registered canvases or open as linked copies in a new tab.
New managed canvases inherit the saved default layout, falling back to the
current main layout, and their names are direct open actions in the registry.
Seven neon color groups persist a shared symbol and bar-interval context for
containers whose definition explicitly declares a `single-symbol` link scope.
The current shared set enables Chart; generic Scanner, News, Orders, SEC/XBRL,
portfolio, execution, strategy, and journal containers do not expose linking.
The chain control alone carries the neon accent while title bars remain neutral.
Its popover contains color selection and one status row per same-color container
with the current ticker; ordinary container settings use a separate internal
settings control. A compact title marker is rendered only for linked containers
and uses that exact link-group color; source readiness never creates a competing
title dot. Link popovers dismiss on outside pointer interaction. Focus canvas routes deliberately omit the
application sidebar. The current Live page still uses its legacy canvas
persistence until the planned migration, so it does not yet consume the new
global profile. Once migrated, run pages may toggle compatible features for an
active run without owning another layout authority.

The configuration page uses a fixed New York point-in-time preview that defaults
to 09:45 and derives synchronized ET, browser-local, and UTC timestamps through
seconds. The clock is global canvas context and deliberately contains no ticker
or editing controls. The header otherwise contains only Set default and a
right-aligned management toggle. The collapsible right sidebar separates saved
Canvases, compound Groups, and the Container library into distinct sections;
only its content body scrolls, while the title and reset action remain fixed.
Opening management never changes canvas geometry. The canvas forms an isolated stacking
context so arbitrary container layer values cannot render above management.
Document-level horizontal overflow is
clipped while the canvas owns horizontal scrolling. Chart and scanner content is calculated by QMD History from
canonical events. News, SEC, and XBRL content is read from their persisted
tables with an as-of cutoff. Portfolio, orders, executions, strategy state, and
journal content use explicitly marked IBKR-shaped configuration fixtures,
because global canvas configuration has no active trading run from which those
resources could truthfully be read. Changing a container setting changes the
rendered preview and persists independently from the global window geometry.
The setting control is rendered inside its container; it never creates a
page-level configuration sidebar. Container title bars are deliberately dense
and expose linked-open, reset, title-bar minimize, fullscreen maximize, and
close actions without adding vertical page chrome. Title-bar minimize/restore
and fullscreen maximize/exit use distinct icons and accessible names.

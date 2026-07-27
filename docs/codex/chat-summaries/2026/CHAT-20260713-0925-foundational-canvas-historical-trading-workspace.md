# Establish the Foundational Canvas and Historical Trading Workspace

- Chat started: 2026-07-13 09:25:43 PDT (America/Vancouver)
- Chat ended or last activity: 2026-07-14 09:06:43 PDT (America/Vancouver) before the later summary request
- Summary written: 2026-07-27 11:15:32 PDT (America/Vancouver)
- Chat/task identifier: `019f5c4c-5aa0-7552-abbf-78aaa21d6d4c`
- Repository or scope: `D:\TradingCodes\quant-research-workbench`; frontend product architecture, shared Canvas workspace, historical QMD, IBKR-shaped trading runtime, Replay and Backtest foundations
- Related task-history entries: `TASK-0050`, `TASK-0051`, `TASK-0052`, `TASK-0144`
- Source completeness: Complete for every accessible completed turn; the final summary request itself was active while this file was written

## Narrative

The chat began as a diagnosis-first frontend review. The initial rendered audit
found that the live-trading session gate and populated News operations page were
strong, but the application did not yet behave like the integrated
research-to-trading workbench described by the README. Only Live and service
operations were routed; browser history could change the hash without changing
the visible page; Live overrode the global UI scale; several service pages
blocked on large detail requests; and operational pages exposed backend
structure with little progressive disclosure. The first recommendation was
still too page-oriented and briefly proposed separating Trading, Operations,
and Research applications.

The user corrected that direction twice. First, they clarified that news, SEC,
replay, backtesting, strategies, and research are trading capabilities rather
than a separate product. Service administration could be lightweight, but
trading-relevant data must remain integrated. Second, they identified the
actual design authority: the Live page's canvas containing movable containers.
That changed the governing model from conventional route families to one
Canvas-container workspace. Live, Replay, Backtest, and Market Intelligence
would be operating contexts or saved layouts; charts, scanners, news, SEC,
portfolio, orders, and diagnostics would be containers subscribing to shared
symbol, time, account, strategy, and mode context.

The first implementation extracted the duplicated Live and Replay window
systems into the shared `WorkspaceCanvas` authority. It consolidated geometry,
dragging, resizing, keyboard movement, fullscreen and minimized state, canvas
management, and persistence while keeping Live and Replay namespaces
independent. Live stopped forcing 80 percent scale, the application navigation
was reduced to the implemented Live, Replay, and lightweight Service Health
surfaces, and Backtest or Market Intelligence entries were intentionally
withheld until they adopted the shared workspace. Commit `84fbf0fe7` delivered
this foundation after a 24-scenario browser matrix covering both modes,
light/dark themes, compact/normal viewports, and 80/100/125 percent scale.
This became completed `TASK-0050`.

The discussion then moved to the backend required for the same workspace to
operate consistently in Live, Replay, Backtest, and Debug. The user defined
historical events as the source of truth; bars must be calculated from events;
strategies, brokers, portfolios, orders, and related authorities must be shared
across modes; strategies must persist and be deployable across accounts; and
all transactions, portfolio states, trades, and lifecycle events must be
durably logged. Replay and Backtest require a simulated broker, while the live
QMD Gateway must remain live-only. A separate historical gateway should mirror
QMD's consumer contract.

The user also required all brokerage-facing abstractions, including portfolio
and account state, to follow IBKR Client Portal schemas. The implementation
therefore established typed IBKR-shaped orders, executions, accounts,
positions, portfolio snapshots, statuses, warnings, modifications, and
cancellations. A deterministic simulated broker gained market, limit, stop,
and stop-limit behavior, partial fills, liquidity participation, brackets, OCA
cancellation, commissions, session rules, and portfolio lifecycle. A shared
runtime, risk authority, strategy revisions, crash-safe SQLite journal/outbox,
and ClickHouse `q_live.tr_*` schemas were added. Live order handling gained
preview, warning-reply, modify, and cancel paths. The work deliberately
retained event-derived bars as strategy inputs where existing automatic
strategies depended on bar features, while removing bars as an independent
brokerage authority. Commit `0708bb689` delivered this foundation and recorded
that the legacy `/api/backtests/*` cutover, authenticated IBKR reconciliation,
and complete feature-strategy migration remained unfinished under
`TASK-0051`.

The first historical gateway implementation was Python. The user rejected that
as the final architecture and asked for Rust without allowing the live QMD
source of truth to drift. Copying the entire gateway was considered and
rejected because it would duplicate normalization, ordering, bars, indicators,
stores, and future fixes. The accepted design turned the existing Rust
`qmd-gateway` crate into the shared `qmd_core` library while retaining the live
binary and adding a separate Rust historical binary in
`services/qmd_history_gateway`. The historical service reads canonical compact
events from ClickHouse, exposes bounded snapshots and streams, and derives bars
through the shared core. Historical replay was removed from the live QMD
process, making the live/historical source boundary structural rather than a
configuration convention.

Live-data validation exposed important schema issues before handoff. Compact
condition values were token identifiers rather than raw Massive condition
codes, and tape bits were dense encoded values. Reference inversion and
validation were moved into shared `qmd_core::CompactEventReferences` so both
binaries use one decoder authority. The initial historical port also collided
with the News gateway on 8796; the default moved to 8801 and health checks
began validating service identity. Exact-limit overflow handling, websocket
error payloads, timeframe validation, ClickHouse configuration, and reference
row parsing were tightened. Commit `bd0e6afcb` passed the live and historical
Rust suites, Python runtime and terminal tests, and current ClickHouse checks
that returned compact events, built an enriched minute bar, and streamed 2,860
AAPL quote/trade events into the Python consumer.

With the source architecture established, the user required the frontend to
finish Replay and Backtest before migrating the result to Live. The emerging
historical-first workspace introduced dedicated setup pages, one-day Replay
semantics, global Canvas configuration, and source-aware containers. The user
rejected mode-specific Canvas configuration: layout and container definitions
must be global under Configuration > Canvas, while mode-specific enablement may
remain inside Live, Replay, or Backtest. Configuration had to render real
container content at a representative 09:45 timestamp so users could configure
what they could see, rather than manipulate empty abstract cards. These
foundations were delivered through `83ae209c5`, `581542f8e`, and `8c811f9bf`
and became the continuing umbrella `TASK-0052`.

The Canvas interaction was then refined through direct browser feedback. New
canvases initially opened empty because their persisted `openIds` were empty;
they were changed to inherit the saved default or current layout. Canvas names
became direct links. Minimize/restore and maximize/fullscreen-exit received
distinct icons. The QMD History launcher became idempotent: an already healthy
service at the resolved address returns successfully, while an unrelated or
unhealthy port owner produces an actionable error. Containers gained internal
link controls and could open linked copies on chromeless focus canvases.

Linking itself was repeatedly corrected. The original A/B/C model became seven
color groups, then the user clarified that generic multi-symbol containers
must never link. Linking became an explicit single-symbol capability; at that
time Chart was the only eligible container, while Scanner, generic News,
account Orders, portfolio, executions, strategy, SEC/XBRL, and journals
remained independent. Invalid stored assignments were normalized away. The
palette became seven neon colors, but color was confined to the chain control
rather than tinting whole title bars. Link popovers listed linked members as
container, ticker, and status; ordinary settings moved behind a separate
neutral control. The link control moved into the title bar, its marker matched
the selected group, and popovers closed on outside interaction. Commits
`2c996081e`, `44b43b049`, `06de0a280`, `34c820625`, `c66835cde`, and
`00de7cc61` captured the progression rather than treating earlier iterations
as final requirements.

The Canvas page hierarchy was also rebuilt. A large in-flow container library
that pushed the canvas down was first replaced with an overlay and then moved,
with canvas registry and reset controls, into a collapsible right management
sidebar. The top Canvas area was stripped of ticker input, editable trading
date/time controls, refresh/status clutter, and duplicate Main labels. It
became a compact telemetry strip containing only ET, browser-local, and UTC
clocks through seconds, Market status, and right-aligned Set default and Manage
actions. Semantic color and icons distinguished clocks and market phases
without gradients. The cells were left-packed, a flexible slot after Market
was reserved for Replay/Backtest Debug controls, and an explicit divider
separated UTC from Market. The management sidebar and application collapse
arrow were promoted above container stacking contexts.

Overflow ownership became explicit: the document, page shell, and focus pages
cannot scroll horizontally; only the canvas owns horizontal scrolling, while
its internal surface may grow in both axes as containers move or resize.
Container title bars lost the redundant visible pan glyph but retained a move
cursor over draggable empty space. Focus links began encoding the requested
container, and `TradingWorkspace` accepted explicit initial state so a new
focus page could restore a Chart without depending solely on a localStorage
side effect.

The Chart was initially reported as not rendered. The first defect was that
its controls were withheld until the combined preview completed; it was
changed to mount immediately with truthful loading and empty states. The
shared historical chart then exposed QMD's supported `1s`, `10s`, `30s`,
`1m`, `5m`, and `1h` intervals plus canonical shared-core indicators.
Overlays remained on price while RSI, MACD, ATR, returns, distance, volume,
volatility, and trend metrics could create compact independent panes. The
remaining blank series was diagnosed upstream: Canvas selected the computer's
previous weekday, July 13, although QMD History had zero coverage that day;
July 10 returned bars. The fundamental fix added QMD History
`/coverage/latest`, initialized Canvas to its latest canonical covered session
at 09:45 ET, kept chart period bounds derived from preview context rather than
hardcoding a start date, and distinguished an uncovered day from a covered day
with no bars for the ticker. Commit `b208c60ee` completed that correction after
Rust, Python, frontend, browser, ClickHouse, bar, and indicator checks.

The chat ended with an operational correction. The assistant had restarted QMD
History for validation and initially misreported it stopped because the
Windows listener table did not show the process even though `/health` remained
reachable. The user supplied the launcher's contradictory output. PID 11304
was then identified through the health endpoint, terminated with the required
permission, and `/health` was verified unreachable. This established the
durable operational rule later encoded in repository guidance: every server
started for a task must be stopped and verified at task completion.

## Durable decisions

- The Canvas-container workspace is the product interaction authority; modes
  and layouts compose workflows rather than creating independent frontend
  architectures.
- Service health remains lightweight and secondary, while news, SEC, market
  data, replay, backtesting, and strategy work remain trading capabilities.
- Live QMD is live-only. Replay and Backtest use a separate read-only Rust QMD
  History binary built on the same `qmd_core`.
- Historical canonical events are authoritative; bars and indicators are
  derived through shared Rust logic and are not stored as a parallel truth.
- IBKR Client Portal-shaped contracts govern orders, executions, accounts,
  positions, and portfolios in both real and simulated brokerage paths.
- Canvas configuration is global. Mode pages may enable features but must not
  create separate layout authorities.
- Linking is an explicit single-symbol capability, not a generic property of
  every container. Color accents belong to the link control, not the title bar.
- Page shells never own horizontal scrolling; the Canvas alone may expand and
  scroll horizontally and vertically.
- Rejected approaches: separate Trading/Operations/Research products, copied
  QMD implementations, a Python historical gateway as the final service,
  mode-specific Canvas configuration, generic linking, and hardcoded chart
  start dates.

## Delivered outcomes

- Shared Live/Replay Canvas primitives and corrected navigation in
  `84fbf0fe7` (`TASK-0050`).
- IBKR-shaped runtime, simulated broker, persistence, and live order foundation
  in `0708bb689` (`TASK-0051`).
- Shared Rust QMD core and Rust historical gateway in `bd0e6afcb`.
- Historical-first setup and global Canvas foundations in `83ae209c5`,
  `581542f8e`, and subsequent `TASK-0052` commits.
- Multi-canvas, focus-copy, linking, management, telemetry, overflow, chart,
  indicator-pane, and latest-covered-session corrections through
  `b208c60ee`.
- Repeated production builds, Rust/Python tests, real-browser matrices across
  themes/scales/viewports, ClickHouse queries, and live event/bar checks as
  recorded above.

## Unfinished or hanging work

### Complete the unified runtime cutover

- Current state: shared IBKR-shaped authorities exist, but the July 14 state
  still left legacy backtest routes and some feature-dependent strategies on
  the earlier models.
- Why unfinished: the chat delivered the foundation, not the complete runtime
  migration or authenticated broker validation.
- Exact next action: implement the runtime-compatible strategy loader and
  shared historical run-controller/journal reads, then bind actual run,
  result, and debug flows.
- Dependency or owner: trading-runtime implementation; authenticated IBKR paper
  environment for final reconciliation.
- Related task-history identifier: `TASK-0051`, `TASK-0052`.

### Finish Replay and Backtest as complete Canvas consumers

- Current state: setup, one-day Replay, global Canvas, and historical preview
  contracts exist, but real run content and completed Backtest Debug are not
  fully bound.
- Why unfinished: later work concentrated on the reusable Canvas and market
  intelligence before closing the run-controller workflow.
- Exact next action: continue from current `TASK-0052`; do not recreate
  per-mode Canvas configuration.
- Dependency or owner: historical trading workspace owner.
- Related task-history identifier: `TASK-0052`.

### Migrate Live only after historical completion

- Current state: the shared primitives exist, but the repository still marks
  the older Live canvas as a legacy surface pending migration.
- Why unfinished: the user explicitly chose to finalize Replay and Backtest
  before applying the resulting platform to Live.
- Exact next action: migrate Live to the finalized global container profile
  after the historical run/debug flows are complete.
- Dependency or owner: `TASK-0052`.
- Related task-history identifier: `TASK-0052`.

## Handoff to the next chat

Read `TASK-0050`, `TASK-0051`, and `TASK-0052`, then this summary. For the
subsequent Canvas market-intelligence expansion, read
`CHAT-20260717-0857-design-tape-quote-and-canvas-market-intelligence`; for the
strategy and order-management continuation, read
`CHAT-20260726-0624-canvas-strategy-order-management`. Do not reverse the
Canvas authority, shared Rust QMD core, live-versus-historical source boundary,
IBKR-shaped contracts, global configuration, or single-symbol linking model.
The most important next action remains completing the shared historical
run/debug controller before migrating the finalized Canvas platform to Live.

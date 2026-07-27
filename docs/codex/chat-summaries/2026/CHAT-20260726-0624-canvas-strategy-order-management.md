# Rebuild Canvas Strategy, Signals, and Low-Latency Order Management

- Chat started: 2026-07-26 06:24:10 PDT (America/Vancouver)
- Chat ended or last activity: Active when summarized on 2026-07-27; latest completed turn ended 2026-07-27 08:59:28 PDT
- Summary written: 2026-07-27 10:52:37 PDT (America/Vancouver)
- Chat/task identifier: `019f9e98-eec0-73b3-a9ee-5742a1f5fc7a`
- Repository or scope: `D:\TradingCodes\quant-research-workbench`; QMD indicators and market signals, Scanner, strategy abstraction, long-only campaign, Canvas order entry, trading runtime, and IBKR order management
- Related task-history entries: `TASK-0122`, `TASK-0125`, `TASK-0126`, `TASK-0130`, `TASK-0134`, `TASK-0139`
- Source completeness: Partial only because the chat remains active; all completed turns through 2026-07-27 08:59:28 PDT were accessible and reviewed

## Narrative

The chat began with the user asking for a refresher on the nearly completed
Canvas strategy configuration and recommendations for strategies to add. The
first answer incorrectly treated the old Long Momentum v11 registry as the
current authority. The user rejected that assumption: all existing strategies
were stale after the major refactor, and the relevant subject was the strategy
abstraction recently defined for Canvas and QMD.

That correction established the governing boundary. QMD was responsible for
causal market measurements and reusable observations, not account-level trade
decisions. A strategy would combine indicators, market signals, news, SEC
information, portfolio state, and risk context into semantic actions. The
runtime would journal and validate those decisions, and the broker adapter
would execute authorized orders. Canvas would configure and present the chain
without recreating signal or strategy logic in React.

The initial review found 134 stale files under `src/strategies`, 22 legacy
backtest-engine files, old strategy and backtest routes, obsolete registry
defaults, an approximately 5,000-line Strategy page, and stale adapters and
documentation. The user authorized removal of all of it, but agreed that the
cutover had to preserve immutable historical evidence readers. The user also
rejected the QMD Micro, Tactical, and Context regime model as unhelpful and
declined to adopt the initially suggested strategy lineup without further
alignment.

The controlled cutover became `TASK-0122` and commit `4d13da5ec`
(`refactor: retire stale strategies and surface QMD signals`). It removed the
legacy strategy and backtest execution surfaces and the QMD episode/regime
engine while retaining the shared trading runtime and saved-run projection.
It also made streaming indicator snapshots and scored active market signals
available to Scanner. A Canvas request-state defect was corrected so an
initial historical build or service failure no longer appeared as a valid
empty market.

The discussion then clarified the data vocabulary. The user wanted indicators,
market signals, news signals, SEC signals, and future signal domains, with QMD
treated as an indicator type or producer rather than a separate product
category. QMD indicators were event-native: their internal state could change
on every quote or trade. Three clocks had to remain distinct:

- input basis, identifying the events or upstream values that mutate state;
- calculation timeframe, identifying the represented window such as 100 ms,
  one second, one minute, or session;
- publication or evaluation cadence, identifying when developing or final
  values reach consumers.

A one-minute calculation could publish a developing value every 100 ms without
becoming a 100 ms indicator. Event-native QMD state could update for every
event, fast Scanner and strategy snapshots could publish at 100 ms, closed
window signals could publish on their declared close, and news or SEC signals
would update when source events arrived. The user accepted this model and
asked for a generic presentation layer capable of drawing any strategy on a
chart.

`TASK-0125` implemented that taxonomy in commit `584fb87ae`
(`feat: add composable trading taxonomy`). The shared authority defined
indicator types, signal domains and producers, mandatory signal scores, and
explicit clocks. Automatic strategy definitions declared normalized inputs and
a presentation policy. QMD catalogs projected the same vocabulary, Scanner
added Market, News, SEC, and Strategy views, and a strategy-agnostic chart
adapter rendered entries, exits, optional waits or holds, confidence, and
invalidation levels. The stale Canvas fixture was explicitly excluded.

This work was interrupted for more than two hours during browser validation.
The implementation, tests, and builds had already completed; the stall came
from temporary server launch attempts. Windows exposed conflicting `Path` and
`PATH` entries, and a lower-level child-process launch inherited shell output
handles, preventing the parent from returning as a properly detached command.
The user required that future work resolve the path problem first and ask
before stopping a server whose ownership was uncertain. Later validation
reused an existing Vite listener read-only and left it running.

The next phase reorganized market signals. The user agreed that QMD should
provide a compact observation catalog rather than strategy-shaped setups.
One-second windows were added to the supported closed-window set.
Session-level break, structure break, and level rejection were challenged
because swing and break indicators already carried the underlying structure;
strategies could combine those indicators with signals instead of duplicating
them as detectors. Sudden simultaneous price and volume increase became the
reusable `price_volume_expansion` observation rather than a named setup.

Commit `a9d4561f2` (`feat: replace legacy QMD market signals`) replaced the old
detectors with directional flow acceleration, price-volume expansion, VWAP
transition, liquidity dislocation, liquidity recovery, and flow-price
divergence. Their lifecycle events carried causal normalized scores and
explicit clocks, making them reusable in chart, Scanner, Signal Stream,
replay, and live processing.

The user then noticed a chart item called QMD Decision. The old representation
was a fixed Buy/Sell/Wait action, which crossed the agreed boundary by looking
like strategy authority. It was redesigned as a continuous
confidence-weighted Flow-Structure Composite indicator. A separate
`flow_structure_alignment` market signal used that indicator and required
three of five canonical 100 ms observations to persist directionally before
activation. Its signed score was QMD-owned and rankable. Live and historical
paths paired each completed indicator row with its source bar before signal
evaluation, eliminating a parallel calculation. Commit `95d4ea144`
(`feat: add ranked flow-structure alignment signal`) delivered this work and
removed the old historical decision-event stream.

The Scanner still displayed `QMD indicators` and old signal fields. The
correction renamed built-in views to generic `Indicators` and `Signals`,
retained QMD only in type and producer metadata, expanded signal columns to
include producer, domain, score, rank, confidence, timeframe, input basis, and
update trigger, and migrated saved built-in settings without overwriting
genuine custom views. This was commit `20ad00438`
(`fix: align scanner with signal and indicator taxonomy`).

The user then showed that both tabs contained only dashes. The backend lacked a
complete historical cross-sectional producer for the new fields, accepted
invalid zero-row cache artifacts, and used an implicit timezone when joining
durable UTC timestamps. A full July 24 replay through the shared Rust engines
processed 70,337,016 canonical events. It produced 9,273 distinct 100 ms
indicator rows, 4,308 active signals, and 20,000 bounded lifecycle events. The
Canvas payload joined 9,266 indicator rows and 2,477 securities with active
signals across 12,309 base rows. Browser checks confirmed score-ranked Signals
and generic Indicators with `type=qmd`, `producer=qmd`, and a 100 ms
publication interval. Commit `0b0e1a072`
(`fix: populate historical scanner signals and indicators`) fixed cache
integrity, UTC lookup, mixed-case ticker identity, polling, and historical
production.

With the evidence layer stable, the user described the intended automated
behavior. It was a long-only, stateful strategy that could be enabled
automatically or take over after a manual entry. Manual versus automatic
initiation belonged to assignment and runtime authority, not two unrelated
strategies. It would observe swing breaks, CHoCH, QMD flow evidence, VWAP,
MACD, price and volume expansion, news, volatility, and structure across
configurable timeframes. It could enter, add, fail fast after a failed break,
protect with adaptive stops, take repeated profits, re-enter after confirmed
exits, and perform final exits from configurable evidence. Every choice had to
be hyperparameterized rather than hard-coded.

The user also recognized that the definition did not run itself: a strategy
engine had to evaluate it continuously while execution remained separate.
`TASK-0130` and commit `875f3c461`
(`feat: add configurable long momentum campaign`) implemented
`long-momentum-campaign@1` with manual, managed-after-fill, and automatic
assignment authorities. It added typed inputs and hyperparameters, causal
entry, confirmation and veto policies, adds, full profit-pocket and re-entry,
failed-breakout exits, QMD and MACD exits, volatility and structure protection,
and a LULD-aware target. The runtime translated semantic intents into IBKR
brackets and OCA groups. Durable assignments and decisions supported
saved-log-only historical chart overlays. Canvas displayed immutable
definition and parameter tables, order-entry controls in the reserved
Charts-and-Quotes cell, and forward strategy presentation. Historical overlays
were drawn only from saved strategy logs at or before the Canvas clock.

The IBKR review exposed an important remaining boundary. Although the
architecture said the runtime owned fills, positions, and recovery, that was
still partly an intended contract. The user clarified that strategies must
never place, modify, or cancel broker orders directly. They must emit requests
to order management. Order management had to define every action for broker
acknowledgements, warnings, fills, partial fills, modifications, cancellations,
disconnects, and ambiguous outcomes. Application-side confirmation could not
add latency in paper or live trading, but automatic broker replies had to
remain explicitly allowlisted rather than blindly accepted.

Execution tactics were urgency-dependent and price protected. Very urgent
long buys would submit at the ask and advance by bounded tick increments within
less than a second. Urgent orders would try the touch once. Regular orders
would seek midpoint or price improvement before moving toward the touch. The
same logic would be mirrored for sells and covers. Short entries would require
current IBKR shortability and be skipped and logged when unavailable. Profit
pocketing would update the protected main order group, sell quickly near the
bid, and re-enter only after a confirmed fill when enabled.

`TASK-0134` and commit `114937195`
(`feat: add exclusive low-latency order management`) implemented
`OrderManagementEngine` as the only broker-command authority. Strategies now
emit semantic intents, and direct strategy `OrderRequest` objects are rejected.
The implementation added a serialized command lane, cached pre-trade risk,
persistent IBKR HTTP connections, broker websocket state, bounded sub-second
limit repricing, shortability gates, separate warning-suppression and
confirmation allowlists, bracket child-role tracking, full target modification
for protected profit pocketing, fill-gated re-entry, durable state transitions,
measured decision-to-submit latency, and outcome-unknown reconciliation without
duplicate submission. Direct public order routes were retired. The
long-momentum definition advanced to immutable revision 2, and Canvas gained an
order-management evidence table.

Validation for the final OMS change included 87 focused tests, Python
compilation, the frontend production build, automated light 80 percent, dark
100 percent, and parchment 125 percent captures, and focused browser
inspection. No authenticated IBKR paper order was submitted, so broker
acceptance and end-to-end latency remain unproven.

## Durable decisions

- Confirmed requirement: QMD owns causal indicators and reusable scored market
  observations; it never owns account-level entry, exit, sizing, risk, or order
  authority.
- Architectural decision: indicator input basis, calculation timeframe,
  evaluation trigger, and publication cadence are separate explicit clocks.
- Architectural decision: QMD is an indicator type or producer, not a signal
  domain. Every rankable signal has a score.
- Architectural decision: strategies are immutable, composable definitions
  evaluated by an engine. Manual, managed-after-fill, and automatic initiation
  are assignment authorities around the same strategy.
- Architectural decision: strategies emit semantic intents only.
  `OrderManagementEngine` exclusively plans, submits, modifies, cancels,
  reconciles, and reports broker orders.
- Confirmed requirement: historical chart strategy presentation is
  saved-log-only; Canvas must not synthesize past decisions.
- Confirmed requirement: fast execution uses immediate price-protected limits,
  persistent connections, cached risk, and bounded repricing without
  application confirmation delays.
- Confirmed requirement: unknown IBKR warnings fail closed; suppression and
  automatic confirmation are separate reviewed policies.
- Confirmed requirement: short entries require current broker shortability and
  are skipped and logged when unavailable.
- Rejected approach: retaining or migrating the pre-refactor strategy catalog,
  QMD regimes, fixed QMD Buy/Sell/Wait actions, or duplicated structure signals.
- Rejected approach: launching long-lived validation servers through blocking
  PowerShell or inherited-output child processes.
- Unresolved uncertainty: actual IBKR order and latency behavior has not been
  validated in an authenticated paper session.

## Delivered outcomes

- Removed the legacy strategy/backtest execution surface while preserving
  historical evidence reading (`TASK-0122`, `4d13da5ec`).
- Added the shared taxonomy and generic chart presentation
  (`TASK-0125`, `584fb87ae`).
- Replaced stale QMD signals and QMD Decision with reusable scored observations
  and the Flow-Structure pair (`TASK-0126`, `a9d4561f2`, `95d4ea144`).
- Migrated and populated Scanner Signals and Indicators
  (`20ad00438`, `0b0e1a072`).
- Added the configurable long-only campaign and Canvas order entry
  (`TASK-0130`, `875f3c461`).
- Added exclusive low-latency order management and durable OMS evidence
  (`TASK-0134`, `114937195`).

## Unfinished or hanging work

### Authenticated IBKR paper acceptance

- Current state: deterministic adapter and OMS tests pass, but no authenticated
  paper order was submitted.
- Why unfinished: broker-side behavior and timing cannot be proven from mocks.
- Exact next action: execute the paper matrix for brackets, protective fills,
  target modification, cancellation, warning chains, short denial, disconnect
  recovery, and measured decision-to-submit latency.
- Dependency or owner: user-authorized authenticated IBKR paper session.
- Related task-history identifier: `TASK-0134`.

### Continuous live strategy controller

- Current state: definitions, engine logic, assignments, intents, runtime
  planning, and OMS exist, but the continuous live run controller is not ready.
- Why unfinished: the normalized observation bus is not wired to a persistent
  live assignment loop with restart recovery.
- Exact next action: connect active assignments to the observation stream,
  restore durable run state, evaluate the replay-compatible engine, and send
  semantic intents only through `OrderManagementEngine`.
- Dependency or owner: trading runtime implementation after OMS paper
  acceptance.
- Related task-history identifiers: `TASK-0052`, `TASK-0130`, `TASK-0134`.

### Broker policy review

- Current state: tactics, state transitions, warning policy, recovery, and
  deployment gates are documented in
  `docs/architecture/IBKR_ORDER_MANAGEMENT.md`.
- Why unfinished: the user has not reviewed and approved concrete message-ID
  allowlists and paper transcripts.
- Exact next action: review the document and paper evidence, then version the
  approved policy without broadening unknown-warning behavior.
- Dependency or owner: user review plus authenticated broker evidence.
- Related task-history identifier: `TASK-0134`.

## Handoff to the next chat

Read `TASK-0122`, `TASK-0125`, `TASK-0126`, `TASK-0130`, and `TASK-0134`,
followed by `docs/architecture/TRADING_TAXONOMY.md`,
`docs/architecture/LONG_MOMENTUM_CAMPAIGN.md`, and
`docs/architecture/IBKR_ORDER_MANAGEMENT.md`. Do not restore the deleted
strategy registry, QMD regimes, fixed QMD Buy/Sell/Wait action, frontend
strategy logic, or direct broker routes. The next substantive action is the
authenticated paper acceptance matrix; after that, wire the continuous live
strategy controller to the normalized observation bus and keep all broker
commands inside `OrderManagementEngine`.

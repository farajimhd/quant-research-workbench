# Long Momentum Campaign

## Boundary

`long-momentum-campaign@10` is the current post-refactor automatic strategy. It is
long only and deterministic. It consumes normalized causal observations; it
does not calculate a second copy of QMD, Generic Structure, VWAP, MACD, news,
or market signals.

The strategy owns:

- trigger, confirmation, veto, add, profit-taking, exit, and re-entry policy;
- resolved hyperparameters and their declared optimization space;
- per-assignment strategy state;
- semantic actions and their causal evidence.

The strategy never receives a broker and cannot emit broker orders. Portfolio
management owns account-specific sizing, allocation, capacity reservations,
and portfolio-policy approval. The shared order-management engine exclusively
owns IBKR-shaped order placement/modification and broker-command recovery. QMD owns
reusable market observations. Canvas owns presentation and semantic assignment
commands, never trading decisions or broker commands.

Earlier revisions remain immutable historical evidence. Revision 10 requires an
Early Squeeze Move campaign, absolute liquidity and volume attraction, price
above VWAP, a causal 1-second swing-high break, and a positive/open 1-second
MACD: line above signal, line above zero, signal above zero, and positive
histogram. It also separates below-entry loss handling from profitable-position
management and keeps reentry eligible for the lifetime of the active Early
Squeeze campaign after the prior episode is flat. Older assignments are never
silently reinterpreted as revision 10.

## Runtime objects

| Object | Lifetime |
|---|---|
| Strategy definition | Immutable by strategy ID and revision |
| Strategy assignment | Account and ticker campaign, possibly spanning several flat-to-flat episodes |
| Strategy observation | One causal point-in-time input snapshot |
| Strategy evaluation | One saved decision for every observation |
| Strategy intent | Broker-neutral requested position change |
| Order plan | IBKR-compatible parent, target, stop, trailing, or exposure-reduction orders |

Assignments support `watching`, `entry_pending`, `managing`,
`reentry_cooldown`, `paused`, `disabled`, `completed`, and `error`. Permissions
independently control observe, enter, add, reduce, exit, and re-entry.

## Evidence and clocks

Every input declares a key, role, timeframe, evaluation mode, maximum age,
weight, and optional score/confidence threshold. The initial definition uses:

- closed 100 ms QMD flow/structure composite evidence;
- 1-second causal Generic Structure;
- 1-second VWAP and MACD confirmation;
- scored price/volume expansion, VWAP transition, divergence, dislocation, and
  company-news events.

The strategy may evaluate on indicator updates, signal events, bar closes,
manual commands, position events, and order events. A deployed revision stores
one resolved value for each parameter. Lists of candidate values belong only
to its parameter space and are never passed unresolved to live execution.

## Order behavior

An entry intent is split into integer-sized structural-target slices. Each
slice has one parent plus broker-held protective children:

- a profit-taking limit when a valid target exists;
- a hard structural/volatility stop;
- a trailing stop;
- OCA sibling behavior so one full protective fill cancels the others.

The parent alone carries `cOID`; each child refers to that value through
`parentId`. Internal strategy metadata stays in the journal and is never sent
as an IB Algo `strategy` field or an unknown CPAPI request field.

Profit-target fills reduce one named slice and leave the other slices protected.
A full strategy exit reprices every remaining target child without expanding a
single child beyond its slice; the corresponding stop remains its OCA sibling.
Protection reconciliation is campaign-local: it counts only causally processed,
still-open entry quantity, including committed inactive bracket children, and a
completed campaign cannot adopt a later re-entry position. Re-entry is evaluated
only after the prior episode is broker-confirmed flat and fresh VWAP, positive
MACD, liquidity, and swing-high-break evidence passes.

While price is below the episode entry, the first available loss authority wins:
the broker-held protective stop, bearish CHOCH, closed 1-second MACD, or price
below VWAP. Above entry, the structural target ladder and profitable-position
management remain authoritative; MACD closure is a later fallback rather than a
loss-path delay.

Execution tactics, warning policy, shortability gates, latency measurement,
failure handling, and paper/live deployment gates are documented in
[IBKR Order Management](IBKR_ORDER_MANAGEMENT.md).

## Persistence and Canvas

SQLite WAL remains the synchronous crash boundary. Assignments are stored in
`strategy_assignments`; commands, state transitions, evaluations, intents,
orders, and broker responses also enter the generic journal/outbox.

Canvas Strategy displays the immutable definition, evidence contract, resolved
parameters, search space, and saved decisions. Charts render strategy markers
only from saved records at or before the Canvas clock. Enabling or arming a
strategy does not synthesize historical markers. Charts & Quotes uses its
reserved center cell for assignment-aware order entry and control.

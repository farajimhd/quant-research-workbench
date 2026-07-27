# Long Momentum Campaign

## Boundary

`long-momentum-campaign@2` is the current post-refactor automatic strategy. It is
long only and deterministic. It consumes normalized causal observations; it
does not calculate a second copy of QMD, Generic Structure, VWAP, MACD, news,
or market signals.

The strategy owns:

- trigger, confirmation, veto, add, profit-taking, exit, and re-entry policy;
- resolved hyperparameters and their declared optimization space;
- per-assignment strategy state;
- semantic actions and their causal evidence.

The strategy never receives a broker and cannot emit broker orders. The shared
order-management engine exclusively owns risk validation, IBKR-shaped order
placement/modification, fills, positions, recovery, and journaling. QMD owns
reusable market observations. Canvas owns presentation and semantic assignment
commands, never trading decisions or broker commands.

Revision 1 remains immutable historical evidence. Revision 2 introduces the
explicit `patient`, `regular`, `urgent`, and `very_urgent` execution parameters,
full-position protected profit-pocket modification, and the order-management
feedback contract. New assignments use revision 2; revision 1 assignments are
not silently reinterpreted as revision 2.

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
- 5-second VWAP and MACD confirmation;
- scored price/volume expansion, VWAP transition, divergence, dislocation, and
  company-news events.

The strategy may evaluate on indicator updates, signal events, bar closes,
manual commands, position events, and order events. A deployed revision stores
one resolved value for each parameter. Lists of candidate values belong only
to its parameter space and are never passed unresolved to live execution.

## Order behavior

An entry intent becomes one parent order plus broker-held protective children:

- a profit-taking limit when a valid target exists;
- a hard structural/volatility stop;
- a trailing stop;
- OCA sibling behavior so one full protective fill cancels the others.

The parent alone carries `cOID`; each child refers to that value through
`parentId`. Internal strategy metadata stays in the journal and is never sent
as an IB Algo `strategy` field or an unknown CPAPI request field.

Adds receive their own protected bracket. Revision 2 profit pockets close the
full campaign episode by modifying its existing target child to the selected
bounded execution tactic, preserving its stop/trailing siblings without an
unprotected cancel-replace interval. It deliberately does not expose an unsafe
partial scale-out that could leave the remaining position with stale
protective quantities. If no reusable target exists, order management submits
the planner's standalone protected exit group. Re-entry is evaluated only
after the prior exit fill is broker-confirmed. The LULD target is enabled only
when an authoritative current upper band is available and above price; it is
not a guarantee of execution before a pause.

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

# Long Momentum Campaign

## Boundary

`long-momentum-campaign@1` is the first post-refactor automatic strategy. It is
long only and deterministic. It consumes normalized causal observations; it
does not calculate a second copy of QMD, Generic Structure, VWAP, MACD, news,
or market signals.

The strategy owns:

- trigger, confirmation, veto, add, profit-taking, exit, and re-entry policy;
- resolved hyperparameters and their declared optimization space;
- per-assignment strategy state;
- semantic actions and their causal evidence.

The shared runtime owns risk validation, IBKR-shaped order placement, fills,
positions, recovery, and journaling. QMD owns reusable market observations.
Canvas owns presentation and commands, never trading decisions.

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

Adds receive their own protected bracket. Revision 1 profit pockets close the
full campaign episode through an exit OCA and then allow immediate re-entry;
it deliberately does not expose an unsafe partial scale-out that could leave
the remaining position with stale protective quantities. A full exit first
cancels only protection owned by this strategy revision, then atomically
submits an aggressive exit, fallback stop, and trailing fallback as one OCA
group. Re-entry is evaluated only after the prior exit fill is reflected in
position state. The LULD target is enabled only when an authoritative current
upper band is available and above price; it is not a guarantee of execution
before a pause.

## Persistence and Canvas

SQLite WAL remains the synchronous crash boundary. Assignments are stored in
`strategy_assignments`; commands, state transitions, evaluations, intents,
orders, and broker responses also enter the generic journal/outbox.

Canvas Strategy displays the immutable definition, evidence contract, resolved
parameters, search space, and saved decisions. Charts render strategy markers
only from saved records at or before the Canvas clock. Enabling or arming a
strategy does not synthesize historical markers. Charts & Quotes uses its
reserved center cell for assignment-aware order entry and control.

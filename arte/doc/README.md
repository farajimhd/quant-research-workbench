# ARTE design index

Design baseline: 2026-09-15.

ARTE means Automated Real-Time Trading Engine. Its repository name is `arte`.
These documents specify ARTE. They do not describe a deployed
implementation. `Must` states a requirement. `Proposed` identifies a design choice
that still needs validation. A release gate must pass before the relevant feature
can be enabled.

| Read order | Document | Owns |
|---|---|---|
| 1 | [Charter](01-charter.md) | Scope, hard boundaries, requirement IDs |
| 2 | [Architecture](02-architecture.md) | Processes, authorities, startup, shutdown |
| 3 | [Event contracts](03-events.md) | Identity, clocks, ordering, compact storage |
| 4 | [Data lifecycle](04-data-lifecycle.md) | REST, gap repair, certification, retention |
| 5 | [V7 state](05-v7-state.md) | Historical seeds, streaming state, causal intervals |
| 6 | [Trading and broker](06-trading-broker.md) | Strategy, accounts, brackets, IBKR |
| 7 | [Backtest and validation](07-backtest-validation.md) | Shared semantics, replay, acceptance |
| 8 | [Performance and operations](08-performance-operations.md) | Resources, latency, monitoring, failure policy |
| 9 | [App and control API](09-app-contracts.md) | Copied UI, commands, observations, debug |
| 10 | [Deployment](10-deployment.md) | Packaging, profiles, selective restart, extraction |
| 11 | [Implementation plan](11-implementation-plan.md) | Milestones, release gates, open decisions |
| 12 | [References](12-references.md) | External specifications and source-copy policy |
| 13 | [User requirements record](13-user-requirements-record.md) | Organized user intent, approvals, corrections, and source map |
| 14 | [Implementation status](14-implementation-status.md) | Implemented code, incomplete features and deferred service validation |

The user requirements record separates explicit user decisions from engineering
proposals. Consult it when resolving why a requirement exists or which earlier
direction a later message replaced.

## Superseded proposals

- No live REST polling. Live uses WebSocket.
- No flatfile or legacy importer dependency in this project.
- No production reader for the parent application's canonical event database.
- No persisted dense ordinal as a prerequisite for event insertion.
- No promotion of streaming V7 state into an authoritative daily seed.
- No shared parent frontend or backend at runtime. Copy required UI source here.
- No blanket event duplication when REST overlaps WebSocket.
- No HTTP between MDE and strategy calculations.
- No requirement to restart unchanged services during deployment.

Do not revive these proposals through implementation convenience.

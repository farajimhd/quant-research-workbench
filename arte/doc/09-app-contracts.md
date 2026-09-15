# App and control contracts

## Scope

Copy the required frontend source into this project's `apps/` area. Remove unnecessary
features only in the copy. Keep Backtest, Debug, and Live as the initial product scope.
The parent frontend, parent backend, and parent API proxy are never required.

Reuse familiar components and wire formats when they fit these contracts. Otherwise
adapt the copied UI or build the minimal page here. No compatibility promise may
force a runtime dependency on the parent app.

This document specifies behavior, not final route names or screen styling.

## API responsibilities

| Area | Required operations |
|---|---|
| Configuration | Discover supported strategies, validate and resolve effective configuration |
| Readiness | Inspect source capabilities, warmup plan, coverage, seed and broker readiness |
| Backtest | Create, pause, step, resume, cancel and inspect isolated runs |
| Results | Read summaries, compare pinned runs and page through evidence |
| Live | Observe readiness, positions, orders, protection and latency incidents |
| Control | Explicit authorized arm/disarm and protective commands |
| Debug | Inspect exact causal observations and rule outcomes |

Commands carry command ID, target run/account, expected state/version, actor, and
validated payload. Repeated command IDs return the same outcome. Reject stale or
unauthorized commands. UI selection alone is not trading authorization.

## Shared event envelope

Include schema version, run ID, mode, sequence, domain event time, input boundary,
code/config identity, event type, and referenced evidence. State which clock a field
uses. Distinguish simulated and broker fills without changing their domain schema.

The UI reads observer projections. It cannot mutate engine memory or connect directly
to ClickHouse or the broker. Reads must not trigger engine restoration or full-state
serialization. Heavy results and chart queries have separate budgets.

## Live observation

Use resumable sequences and bounded snapshots. Detect missing observer messages and
resynchronize. Coalesce chart frames if needed, but never drop authoritative journal
records. A slow browser cannot backpressure the strategy or source receiver.

Closing the browser leaves Live running. Restarting the observer API does not restart
the live runtime. Show stale UI delivery separately from stale market data.

## Saved review and debugging

Saved Review loads durable projections. Resume restores execution only after an
explicit command and state validation. Pagination pins a causal boundary so incoming
events do not move the row being inspected. Hidden panels stop expensive polling.

Debug must show source mode, missing timestamps, historical assumptions, V7 seed,
rejection reasons, account cash, and bracket status. Do not present retrospective
levels or simulated fills as observations known to Live.

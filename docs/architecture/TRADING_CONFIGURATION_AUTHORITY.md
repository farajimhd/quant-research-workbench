# Trading Configuration Authority

## Invariant

Configuration pages define and publish versioned application behavior.
Replay, Backtest, Backtest Debug, Live, and Paper consume approved revisions
and must never contain copied configuration or mode-specific implementations
of the same rules.

This is an application architecture rule, not a Replay convenience. A feature
is incomplete if a strategy, account policy, portfolio limit, OMS behavior,
protection rule, Canvas layout, container setting, or presentation choice must
be changed separately in a run page.

## Ownership

| Configuration page | Owns |
|---|---|
| Canvas | Canvases, layouts, groups, containers, link contexts, and presentation settings |
| Strategies | Immutable executable strategy identity, revision, and parameters |
| Assignments | Strategy-to-account-key and instrument bindings |
| Portfolio & Risk | Allocation, exposure, capability, loss, drawdown, freshness, and emergency limits |
| OMS & Protection | Execution urgency, limit protection, timing, stop construction, and trailing behavior |
| Accounts & Sessions | Stable application account keys and mode-specific broker/simulated session bindings |
| Approved Revisions | Whole-profile validation, immutable publication, revision history, and current runtime authority |

Run pages own only scenario and transport inputs. Replay owns the historical
date, entry clock, playback speed, pause, step, fast-forward, stop, and
simulation funding. Backtest owns its historical window and execution pace.
Live owns session startup and operational commands. None of them owns a second
strategy, portfolio, OMS, account, or Canvas configuration.

## Publication contract

Draft sections are mutable and non-executable. Publication:

1. validates every section with the same domain constructors used at runtime;
2. captures the complete current Canvas registry;
3. serializes the complete profile canonically;
4. records a SHA-256 content hash and immutable revision in the trading
   journal; and
5. makes the newest approved revision the authority for new runs.

An active run pins the revision id, number, content hash, approval timestamp,
and complete payload. A later draft edit or publication cannot mutate it.
Unsupported or missing configuration blocks run creation explicitly.

## Runtime consumption

Replay uses the approved strategy identity and parameters to build the shared
strategy implementation. Approved assignments are cloned into explicit
simulated account boundaries. Account bindings and approved portfolio policies
construct the shared `PortfolioManagementEngine`; OMS and protection settings
are merged into the strategy-to-OMS contract and shared order planner. The
approved Canvas profile is rendered by `CanvasWorkspaceSurface`.

`initial_cash` configures `SimulatedBrokerAdapter` funding only. It is not a
portfolio policy and cannot bypass allocation, exposure, planned-risk, loss,
drawdown, account capability, execution, or protection controls.

Run-local assignment actions are operational state. They may arm, pause,
disable, or add an assignment for an already approved strategy/account
boundary and approved historical stream. They may not define a new strategy
revision or replace the pinned portfolio/OMS configuration.

## Completion gate for another mode

Live or Backtest Debug is not migrated until it:

- loads one approved configuration revision through the shared configuration
  service;
- uses `TradingRuntime`, `PortfolioManagementEngine`, and
  `OrderManagementEngine` rather than mode-local copies;
- renders the approved Canvas profile with `CanvasWorkspaceSurface`;
- records the pinned revision identity and content hash in its run/session
  evidence; and
- proves that configuration changes require publication in one place only.

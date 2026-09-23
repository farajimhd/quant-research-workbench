# CLI, control API, and operator UI

Read `../doc/09-app-contracts.md` and `../doc/10-deployment.md` before adding
an app or changing a command. This applies to copied frontend source too.

- Configuration is an input to the engine, not UI-owned runtime state. CLI
  and UI use the same validated, versioned command and observation contracts.
- Backtest, Debug, and Live are the initial views. Copy required frontend
  source into ARTE and remove unused code in the copy; never import or proxy
  the temporary parent app at runtime.
- The API and UI observe durable projections. They do not own market data,
  strategy, portfolio, OMS, or broker authority. Closing the UI must not stop
  Live; a slow browser must not backpressure execution.
- Commands need identity, actor, target, expected version, permission, and
  idempotent outcome. UI selection does not arm trading. Saved Review cannot
  resume execution without an explicit validated command.
- Show source mode, missing clocks, historical assumptions, causal evidence,
  account cash, protection, and stale delivery separately from stale data.
  Do not present retrospective levels or simulated fills as live observations.

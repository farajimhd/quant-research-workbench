# ARTE project instructions

ARTE means Automated Real-Time Trading Engine. The project root is `arte`.
Keep Market Data Engine (MDE) as the market-data component name.

## Boundary

- Treat this directory as the project root, including after repository extraction.
- Do not modify files outside this root for this project.
- Do not import, execute, link, or discover code from the temporary parent tree.
- Do not use parent configuration, service managers, databases, UI servers, or caches.
- Copied source must live here. Record its origin before adaptation.
- Follow the design index in `doc/README.md`. Flag conflicts before changing a contract.

## Data and execution

- Use WebSocket for live events and REST for historical acquisition and repair.
- Do not access legacy event tables or flatfiles from production components.
- Never alter the legacy `market_sip_compact.events_YYYY` tables.
- Use the new configured ClickHouse database and explicit `live_market_ssd` policy.
- Validate actual part placement. Never fall back to `default` or its aliases.
- Never substitute absent timestamps or convert retrospective evidence into live evidence.
- Only historical V7 creates authoritative next-session seeds.
- Backtest must not possess live broker credentials or control capability.
- Reject exposure increases without complete approved bracket protection.

## Delivery

- Do not start any service for testing until the user has copied ARTE to its new repository.
- Until that transition, allow builds, static checks, and in-process unit tests only.
- Do not launch HTTP servers, databases, broker gateways, containers, or network integration tests.

- Keep generated output and secrets outside the source root.
- Require an explicit external runtime root. Do not invent a fallback directory.
- Set `PYTHONDONTWRITEBYTECODE=1` for any Python invocation.
- Use bounded concurrency, queues, and memory. Expose rejected and failed work.
- Do not claim a validation passed unless it ran and its scope is recorded.
- Keep all documentation links portable and internal, except public references.
- Review and stage only project-owned changes.

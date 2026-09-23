# ARTE project instructions

ARTE means Automated Real-Time Trading Engine. The project root is `arte`.
Keep Market Data Engine (MDE) as the market-data component name.

## Boundary

- Treat this directory as the project root, including after repository extraction.
- Do not modify files outside this root for this project.
- Do not import, execute, link, or discover code from the temporary parent
  tree at ARTE build or runtime. Development may inspect and pin parent
  source as provenance for an ARTE-owned copy or rewrite.
- Do not use parent configuration, service managers, UI servers, or caches.
  The sole permitted external historical database read is certified
  `market_sip_compact.events_YYYY` under the source contract below; other
  parent-owned database products require an explicit design decision.
- Copied source must live here. Record its origin before adaptation.
- Follow the design index in `doc/README.md`. Flag conflicts before changing a contract.
- Follow narrower `AGENTS.md` files below this root. Flat Rust modules are
  governed by the `src/AGENTS.md` in their owning crate; a sibling module
  directory's instructions do not cover the flat parent file.
- Treat these instructions as implementation guardrails, not proof that
  existing code complies. If code conflicts with an explicit user decision in
  `doc/13-user-requirements-record.md`, surface the conflict and correct the
  design or implementation inside ARTE before claiming completion.

## Data and execution

- Use WebSocket for live events. Select certified compact history or an
  ingestion-owned flatfile generation for historical coverage; use REST for
  recent historical acquisition and repair.
- Certified `market_sip_compact.events_YYYY` is an allowed read-only historical
  source. Check source-day coverage, schema, trade-reporting revision, and
  required field capabilities before admitting it to a run.
- Include ARTE-owned Rust/ClickHouse flatfile digestion for historical
  acquisition and outage recovery. Reimplement the required
  `download_update_events` semantics; do not execute or depend on that Python
  script. Only the ingestion authority may open raw flatfiles; strategies,
  Backtest, charts, V7, and repair consumers read certified ClickHouse products.
- Keep existing `market_sip_compact.events_YYYY` rows and schema unchanged.
  The new importer's write target is undecided. Do not grant ARTE a legacy
  write path without a separate explicit decision.
- REST remains the recent historical/gap-repair path; WebSocket remains Live.
- Write ARTE operational tables to the configured `arte` database using the
  explicit `live_market_ssd` policy. Validate actual part placement. Never
  fall back to `default` or its aliases. Read-only yearly compact source parts
  retain their designated source storage policy; ARTE does not move them.
- Never substitute absent timestamps or convert retrospective evidence into live evidence.
- Only historical V7 creates authoritative next-session seeds.
- Maintain the existing `arte` V7 interval/coverage/builder-checkpoint contract
  and persisted market-day bars/indicators. Reuse them only through verified
  source, calculation, availability, and compatibility identities.
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

# Campaign execution and root certification

[Design overview](../certified_research_dataset_design.md) · [Exact controller schemas](12d-schema-campaign.md)

## Implemented controller core

`Campaign` freezes the launch configuration, source epoch and bounded unit arguments. The ClickHouse plan stores normalized numeric unit/dependency IDs, product names and argument hashes. Dataset definitions and unit rows are immutable: changed, extra, missing-on-verification or duplicated metadata fails closed.

The scheduler uses topological dependency accounting and a heap of ready memory reservations. It does not scan the entire graph after each completion. Incremental progress counters avoid repeated full-state recounts. Independent consumers run concurrently within worker and memory budgets; failed parents block descendants. Long chains are processed iteratively, without recursion.

Each registered producer has an independent authority verifier. A successful function return cannot certify a unit. Resume reads actual persisted product receipts and output fingerprints. Progress history is diagnostic, not proof of data readiness.

## Final seal and restart

The final root hashes the immutable plan, frozen source and all independently verified unit outputs. It is inserted only after every unit verifies, then read back exactly. A previously sealed campaign resumes without inserts and rechecks the complete authority chain. Changed outputs or duplicate seals are rejected.

Keeper suspension/loss invalidates the writer permanently, including a reconnect retaining the session ID. An invalidated claim cannot publish or release uncertain ownership. Source guards and physical SSD placement checks remain required before writers.

## Operator inspection

`python -B scripts/prepare_certified_research.py status --dataset-id <UUID> --format json` reads plan/progress/seal metadata without changing it. It explicitly reports `inspection_only`; a stored seal alone is not a fresh data-integrity verification.

## Verification and remaining integration

Concrete reference registration now connects SEC facts, derived fundamentals, short families and float supply to the controller's producer/verifier contracts. Each call owns its ClickHouse client. A real AAPL SEC→derived campaign successfully published its root seal and resumed through those concrete handlers. Regular-price, allocation and corporate producers still require full planning/registration, together with source acquisition and repair orchestration.

### Shared market execution

`MarketHandlers` registers aggregation, technical indicators and V7 as campaign units in the same global worker/memory pool. It does not start a nested executor. One canonical aggregation builds shared staging; bars and liquidity are independently published/read back before the aggregate unit can certify. Its output hash binds both product receipts. Indicators and V7 then run independently and require the exact verified aggregate parent. V7 alone fetches its bounded bar input; SQL consumers remain in ClickHouse.

The standalone market pilot uses the same stage implementations. An isolated live ClickHouse campaign verifies one aggregate insert, a root seal, no-insert sealed resume, immutable request-scope checks and rejection of missing or altered shared parent evidence. This qualifies the execution wiring, not all-universe scan throughput. Request/engine factories still need full launch-time calendar, identity, context and certified predecessor discovery.

`planning.market_units` makes the execution edges explicit: each admitted listing/session waits for certified source/reporting repair and the universe; indicators and V7 both wait for its shared aggregate. Indicator sessions form their own predecessor chain, and V7 sessions form a separate chain. Later aggregation can run ahead within resource limits. The first session requires certified seed/genesis units for both stateful products; missing seeds or duplicated listing/session membership stop planning. The caller must still derive the complete admitted session set and prerequisite units from actual authority.

Historical reporting repair no longer hydrates or reconstructs imported flatfiles. It requires source-bound staged sidecars, checks the stored mapping against canonical authority and retains sidecars after success. Live tests cover classification parity, low-bit/quote preservation, monthly bounds, idempotent mutation and mapping-tamper rejection. Missing sidecars block the repair; they require an explicit ingestion-owned versioned path.

Tests cover a 10,000-unit reuse/failure chain, real ClickHouse plan/authority/seal publication, read-only sealed resume and rejection after output tampering. Keeper reconnect/session-loss fencing has deterministic coverage.

The local Keeper probe currently lacks the required secure LAN endpoint configuration. No plaintext fallback was used and no production campaign was started. The full launcher must still register all concrete acquisition/repair/reference/market handlers, build their dependency-derived plans and qualify campaign-scale performance. This controller core is not yet the complete launch path.

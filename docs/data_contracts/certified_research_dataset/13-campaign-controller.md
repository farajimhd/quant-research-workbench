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

Concrete reference registration now connects SEC facts, derived fundamentals, short families and float supply to the controller's producer/verifier contracts. Each call owns its ClickHouse client. A real AAPL SEC→derived campaign successfully published its root seal and resumed through those concrete handlers. Regular-price, allocation and corporate producers still require full planning/registration, together with market/source acquisition and repair orchestration.

Historical reporting repair no longer hydrates or reconstructs imported flatfiles. It requires source-bound staged sidecars, checks the stored mapping against canonical authority and retains sidecars after success. Live tests cover classification parity, low-bit/quote preservation, monthly bounds, idempotent mutation and mapping-tamper rejection. Missing sidecars block the repair; they require an explicit ingestion-owned versioned path.

Tests cover a 10,000-unit reuse/failure chain, real ClickHouse plan/authority/seal publication, read-only sealed resume and rejection after output tampering. Keeper reconnect/session-loss fencing has deterministic coverage.

The local Keeper probe currently lacks the required secure LAN endpoint configuration. No plaintext fallback was used and no production campaign was started. The full launcher must still register all concrete acquisition/repair/reference/market handlers, build their dependency-derived plans and qualify campaign-scale performance. This controller core is not yet the complete launch path.

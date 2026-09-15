# Implementation plan and unresolved gates

Project: ARTE (Automated Real-Time Trading Engine). Repository name: `arte`.

## Delivery sequence

| Phase | Deliverable | Exit condition |
|---|---|---|
| 0 | This folder scaffold and design baseline | Internal links, boundaries and requirement coverage checked |
| 1 | Typed contracts, source inventory and fixtures | Identity, clocks, revisions and seed schemas approved |
| 2 | REST/WS adapters and new database migrations | Overlap, corrections, pagination and storage placement pass |
| 3 | Shared bars, indicators, detectors and V7 modules | Numerical, discrete, prefix and seed publication tests pass |
| 4 | Isolated historical runtime and prepared caches | Deterministic portfolio replay and restart pass |
| 5 | Live market path, repair and latency controls | Representative bursts, outages and handover pass |
| 6 | Portfolio, OMS and packaged broker support | Multi-account, bracket, ambiguity and Paper tests pass |
| 7 | Copied minimal frontend and observer API | Backtest, Debug and Live operate without parent app |
| 8 | Deploy/run scripts and hardware profiles | Selective restart, rollback and resource isolation pass |
| 9 | Independent-root acceptance | Parent-inaccessible build and runtime test pass |
| 10 | Separate repository transition | User authorizes repository creation and migration |

Do not interpret the sequence as permission to submit live orders. Live activation
and strategy acceptance remain separate approvals.

## Decisions that are settled

- The project root is temporary inside the current repository and portable later.
- Existing files and canonical tables are not modified.
- Historical acquisition and repair use REST; Live uses WebSocket.
- The new event store has no dense persisted ordinal requirement.
- Store no redundant whole payloads for ordinary REST/WebSocket overlap.
- Historical V7 owns daily seeds; streaming state is separate.
- ClickHouse is the only external durable persistence service.
- MDE and strategy share the live process and in-memory domain state.
- Backtest shares contracts, not live processes, accounts or mutable state.
- Complete brackets and buffered official LULD checks gate exposure increases.

## Decisions requiring evidence

| Gate | Evidence needed | Blocked capability |
|---|---|---|
| Event key | REST/WS sequence correspondence, scope and corrections | Final event DDL and canonical writers |
| Enrichment layout | Minimal storage, as-known replay and read cost | Final revision/enrichment implementation |
| Acquisition scope | Universe, historical depth, entitlement, cost and throughput | Broad REST backfill |
| Cross-channel order | Precision ties and stable merge contract | Historical parity claims |
| V7 baseline | Copied source/config hashes and representative fixtures | Rust port acceptance |
| Seed availability | Observed or declared historical publication schedule | Point-in-time seed validation |
| Device budgets | Measured memory, peak rates and query contention | Approved hardware profiles |
| Latency thresholds | Clock uncertainty and strategy timing requirements | Live arming |
| Broker adapter | Session workflow, account permissions, pacing and partial protection | Broker release |
| Historical LULD | Official recorded coverage or explicit modeled alternative | Official-band historical claims |
| Durability | ClickHouse acknowledgment/recovery semantics and measured latency | Crash-safe order submission |
| Retention | Run pins, seed dependencies and recovery requirements | Automated expiry |

An unresolved gate must be visible in readiness output. Do not implement a silent
fallback to the parent app, flatfiles, stale seeds, or approximate missing data.

## Definition of initial implementation complete

- Required code and dependencies exist inside the distribution boundary.
- Historical acquisition creates a usable certified seed from REST data.
- Backtest and recorded-live replay use shared contracts and pass parity tests.
- Paper validates account isolation, brackets, recovery and communications.
- Live ingestion meets approved budgets with no unexplained drops.
- The copied UI works without the parent backend.
- Deploy/run entry points pass selective-restart and extraction tests.
- Remaining strategy-performance limits are documented honestly.

## Documentation maintenance

Update the owning document when a contract changes. Record whether a change is a
new user decision, a measured implementation choice, or a failed acceptance gate.
Keep sentences short. Define fields and failure behavior. Do not describe planned
features as implemented. Keep run-specific output outside this source tree.

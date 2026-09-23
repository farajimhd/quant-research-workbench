# Implementation plan and unresolved gates

Project: ARTE (Automated Real-Time Trading Engine). Repository name: `arte`.

## Delivery sequence

| Phase | Deliverable | Exit condition |
|---|---|---|
| 0 | This folder scaffold and design baseline | Internal links, boundaries and requirement coverage checked |
| 1 | Typed contracts, source inventory and fixtures | Identity, clocks, revisions and seed schemas approved |
| 2 | Certified compact-source reader, Rust/ClickHouse flatfile ingestion, REST/WS adapters, and database migrations | Source capabilities, overlap, corrections, pagination and storage placement pass |
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
- Existing parent source files are not modified. Yearly compact tables remain
  read-only until the importer write target is explicitly decided.
- Historical preparation may read certified yearly compact events, ingest
  flatfiles through ARTE-owned Rust/ClickHouse code, or use REST for recent
  repair; Live uses WebSocket.
- The new event store has no dense persisted ordinal requirement.
- Store no redundant whole payloads for ordinary REST/WebSocket overlap.
- Historical V7 owns daily seeds; streaming state is separate.
- Maintain compatible existing `arte` V7 interval, coverage, and terminal
  builder-checkpoint tables instead of introducing a duplicate seed authority.
- Use completed persisted `arte` bars and indicators for Backtest when their
  source, reporting revision, unit attempts, seed mode, and coverage match.
- ClickHouse is the only external durable persistence service.
- MDE and strategy share the live process and in-memory domain state.
- Backtest shares contracts, not live processes, accounts or mutable state.
- Do not add backward compatibility for obsolete strategy candidates, SQLite
  artifacts, or superseded ARTE contracts. Pin and migrate accepted runs only
  when a new contract needs them.
- Strategy 350 is the current replacement candidate; pin exact source and
  configuration before porting and do not infer release approval from its number.
- Historical preparation starts from vectorized completed 100 ms bar batches
  selected by the scanner, catalogue and rule sets. ClickHouse holds all run
  evidence and compact logs; SQLite is prohibited.
- Complete brackets and buffered official LULD checks gate exposure increases.

## Decisions requiring evidence

| Gate | Evidence needed | Blocked capability |
|---|---|---|
| Event key | REST/WS sequence correspondence, scope and corrections | Final event DDL and canonical writers |
| Enrichment layout | Minimal storage, as-known replay and read cost | Final revision/enrichment implementation |
| Acquisition scope | Universe, historical depth, entitlement, cost and throughput | Broad REST backfill |
| Flatfile import target | Decide whether an ARTE-owned importer writes only ARTE source tables or may append to yearly compact tables; prove isolation and recovery | Any importer writer |
| Existing archive capability | Yearly source-day certificates, `event_meta` reporting revision, field support, point-in-time identity | Read-only archive admission |
| Existing derived products | `arte` market-day builds and V7 interval/checkpoint coverage, exact source/calculation compatibility | Backtest and next-session seed admission |
| Cross-channel order | Precision ties and stable merge contract | Historical parity claims |
| V7 baseline | Copied source/config hashes and representative fixtures | Rust port acceptance |
| Seed availability | Observed or declared historical publication schedule | Point-in-time seed validation |
| Device budgets | Measured memory, peak rates and query contention | Approved hardware profiles |
| Latency thresholds | Clock uncertainty and strategy timing requirements | Live arming |
| Broker adapter | Session workflow, account permissions, pacing and partial protection | Broker release |
| Historical LULD | Official recorded coverage or explicit modeled alternative | Official-band historical claims |
| Durability | ClickHouse acknowledgment/recovery semantics and measured latency | Crash-safe order submission |
| Retention | Run pins, seed dependencies and recovery requirements | Automated expiry |
| Strategy 350 | Frozen source/configuration, causal rule and decision parity | Candidate backtest and live activation |
| Compact bars | Lossless schema, source coverage, codec and multi-day query benchmarks | Materialized 100 ms backtest path |
| Bar fidelity | Event versus bar signals, V7 transitions and order/P&L comparison | Coarser timeframe or parity claims |

An unresolved gate must be visible in readiness output. Do not implement a silent
fallback to the parent app, an uncertified flatfile or compact day, stale seeds,
or approximate missing data. Flatfiles are ingestion input only.

## Definition of initial implementation complete

- Required code and dependencies exist inside the distribution boundary.
- Historical acquisition selects or creates a usable certified seed from a
  compatible compact archive, ARTE flatfile import, or REST generation.
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

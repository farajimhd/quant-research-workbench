# Project charter

## Objective

Run configured strategies on live data with low, measured latency. Keep market
state and required calculations in memory. Support fast repeatable backtests
without disturbing Live. Keep Backtest, Debug, and Live operator views optional.

Correctness, latency, and strategy profitability are separate acceptance claims.
Zero latency is not a promise. Rust performance does not prove trading correctness.

## Requirements

| ID | Requirement | Owning document |
|---|---|---|
| R01 | All source and documentation live inside this root | [Deployment](10-deployment.md) |
| R02 | No changes to existing parent files, including frontend files | [Deployment](10-deployment.md) |
| R03 | No parent app, service, source path, or configuration dependency | [Architecture](02-architecture.md) |
| R04 | Separate ClickHouse database; no legacy table writes or reads | [Data lifecycle](04-data-lifecycle.md) |
| R05 | Live WebSocket; historical and gap-fill REST | [Data lifecycle](04-data-lifecycle.md) |
| R06 | Preserve source clocks and missing-field semantics | [Events](03-events.md) |
| R07 | No persisted dense ordinal; immediate source-keyed insertion | [Events](03-events.md) |
| R08 | Avoid duplicate payloads and unnecessary derived persistence | [Events](03-events.md) |
| R09 | Repair all declared dependencies before trading readiness | [Data lifecycle](04-data-lifecycle.md) |
| R10 | Historical V7 alone publishes next-session seeds | [V7 state](05-v7-state.md) |
| R11 | Live structure updates follow the algorithm's causal contract | [V7 state](05-v7-state.md) |
| R12 | Shared domain code and audit schemas across Live and Backtest | [Validation](07-backtest-validation.md) |
| R13 | Backtest cannot affect live credentials, state, or reserved resources | [Operations](08-performance-operations.md) |
| R14 | Resident state, bounded concurrency, appropriate vectorization | [Operations](08-performance-operations.md) |
| R15 | Monitor feed age and block stale exposure increases | [Operations](08-performance-operations.md) |
| R16 | Mandatory broker-held stop and target for exposure increases | [Trading](06-trading-broker.md) |
| R17 | Fresh official LULD constraints with an approved tick buffer | [Trading](06-trading-broker.md) |
| R18 | Concurrent multi-account trading with separate cash mandates | [Trading](06-trading-broker.md) |
| R19 | Bundle required broker and reference responsibilities | [Deployment](10-deployment.md) |
| R20 | One deploy entry point and one run entry point | [Deployment](10-deployment.md) |
| R21 | Named laptop/workstation profiles and selective service restart | [Deployment](10-deployment.md) |
| R22 | Copy and reduce UI; support Backtest, Debug, and Live | [App contracts](09-app-contracts.md) |
| R23 | Extract this root into a new repository after the initial implementation | [Deployment](10-deployment.md) |

## Scope exclusions

- News, SEC, BarGPT, and general-purpose enrichment.
- The parent application's scanner presentation and application registries.
- A general strategy plugin marketplace or initial WASM plugin host.
- Automatic strategy promotion based on backtest profitability.
- Automatic live activation after deployment.
- Changes to existing `market_sip_compact.events_YYYY` tables.
- A second persistent database service, such as SQLite, Redis, or PostgreSQL.

The old archive remains unchanged. Its existing importer is outside this project.
The new system acquires its own required historical coverage through REST.
Large historical acquisition is an explicit capacity and cost gate.

## Configuration principle

Configuration is an input to the engine. It is not owned by the UI.
Every run pins an effective configuration, version, and content hash.
The CLI and UI submit the same validated command contracts.

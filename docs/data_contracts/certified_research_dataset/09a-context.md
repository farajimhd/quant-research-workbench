# Context planning

[Overview](../certified_research_dataset_design.md) · [Previous](09-execution.md) · [Next](09b-launcher.md)

Understand how launch parameters determine earlier data and compatible predecessor state.

### Implemented predecessor readers

`market_context.indicators` consumes a verified dependency locator and loads one certified state row per selected resolution (eight by default). It checks listing/date, recipe, frozen source/rules, exact frame coverage and the exact state receipt without reading bars. Indicator discovery returns this dependency form; chronological technical authorities expose the same form for the following session.

`market_context.structural` consumes the verified V7 dependency locator, checks the pinned calendar and derives its prior AH binding from certified compact bars before restoration and one day rollover. This requires a compact prior-bar read for prefix validation, but no event scan or V7 fit replay. It refuses missing intervening sessions, unsupported symbol changes and absent corporate-adjustment evidence. Split-factor resolution and its verified evidence must come from the corporate producer; this reader does not infer a factor of one from missing data.

These readers connect certified outputs to chronological continuation. Initial genesis/rename/adjustment producers and the complete launcher's request assembly remain unfinished; callback-based fixture execution does not prove that full production path ready.

`adjustments.resolve` now verifies a corporate-history receipt and dated listing/security membership, selecting executions and revisions publicly available by 04:00 New York. It covers effective dates after the prior session through the new session, including intervening weekends. Invalid contexts/ratios and unconfirmed pending announcements block continuation. An empty certified history can yield factor one only under the upstream complete-history coverage contract; a publication-range receipt by itself cannot prove that contract.

`indicator_continuation` applies the resolved price factor once to EMA/MACD, close, ATR and gain/loss state; sample counts stay unchanged. The structural reader requires the corporate scope in production and binds the same adjustment proof to its rollover authority. Technical/V7 request lineage has an explicit adjustment hash; V7 lineage also binds the regular-close boundary. Causal adjustment evidence excludes full-history receipt hashes and provenance metadata, so later unavailable actions do not alter earlier checkpoint state; the full receipt remains a separate dependency for source certification.

Corporate-history acquisition/coverage admission, price-adjustment graph units and complete request assembly remain unfinished. This resolver does not turn the existing undated corporate snapshots into historical public evidence.

`MarketRuntime` now supplies shared request and engine methods for all planned market dates. It verifies full-session dated listing membership, frozen day source bindings, corporate receipts and certified predecessor dependencies; clients and restored engines remain private to each call. Technical and V7 branches resolve their own predecessor independently. `planning.market_units` requires a corporate-history parent for both branches, while aggregation remains independent of stateful consumers. `source.market_day_hashes` combines each day's canonical and reporting evidence, including native-clock fingerprints, without depending on future source days.

The bounded live test exercises request resolution and AH engine restoration from real fixture receipts. The full launcher still needs initial seed/genesis/rename producers, complete corporate acquisition and admission, combined plan installation, and measured production-scale qualification.

## On this page

- [Context planner](#context-planner)

## Context planner

### Planning from launch requirements

The planner resolves each dependency into ranges/seed requirements, takes their union and records why each prior unit is needed. Context-only units have no training labels or objective trades. Launch parameters specify lookbacks, resolutions, indicator periods, session start, ranking windows and selected consumer adapters.

### Context by dependency

| Requirement | Planned context |
| --- | --- |
| RL 120-row observation | 119 prior actual rows plus current causal history; absence carries masks, not invented candles; resolve beyond one session only under the declared adapter contract |
| Current RL RVOL | Exact prior trading session's same-clock volume; additional selected baselines add their explicit session windows |
| Optimizer histories | Elapsed-clock window/confirmation dependencies, full ranked-union advancement and prior bank tail; model boundaries stay unchanged |
| EMA/MACD | Compatible predecessor state per frame/identity, or explicit bootstrap/rebuild origin; arbitrary 60/90-day truncation is not exact equivalence |
| V7 | Latest compatible causal predecessor and required retained observations; incompatible 2025 seed requires rebuild of its dependency prefix |
| Fundamentals | Last eligible fact before range plus updates and required comparable periods; float resolver's existing 520-day eligibility is a versioned rule, not universal fact retention |
| Float price/splits | Measurement-date price window (current resolver up to seven days) and intervening known corporate actions |
| Short data | Last public observation before start plus rolling-window inputs; settlement/trade date alone is insufficient |
| Session-only replay | Prior boundary market-state checkpoint and any declared causal history; fresh account state or separately certified scenario checkpoint |

### Reuse, source bounds and bootstrap

Events available from 2019 are not a mandate to rebuild every product from 2019. Planning first tests compatible seeds and sparse dependency queries. Fail/report if source bounds cannot satisfy a required dependency. Proven-no-prior-history bootstrap differs from missing history.

[Overview](../certified_research_dataset_design.md) · [Previous](09-execution.md) · [Next](09b-launcher.md)

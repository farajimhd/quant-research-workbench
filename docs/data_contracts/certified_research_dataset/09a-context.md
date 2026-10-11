# Context planning

[Overview](../certified_research_dataset_design.md) · [Previous](09-execution.md) · [Next](09b-launcher.md)

Understand how launch parameters determine earlier data and compatible predecessor state.

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

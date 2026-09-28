# Repository map

| Area | Responsibility | Start here |
|---|---|---|
| `research/` | Versioned models, experiments, and shared research MLOps | `research/AGENTS.md` |
| `services/` | Runtime services, gateways, APIs, and operational status | `services/AGENTS.md` |
| `pipelines/` | Historical acquisition, backfills, normalization, and checkpoints | `pipelines/AGENTS.md` |
| `src/` | Shared production/domain code | Root `AGENTS.md`, then relevant module docs |
| `frontend/` | Browser application and Canvas/configuration UX | `frontend/AGENTS.md` |
| `tests/` | Focused and integration validation | `tests/AGENTS.md` |
| `scripts/` | Repository launchers and maintenance utilities | Root `AGENTS.md` |
| `docs/` | Durable architecture, operations, and continuity records | `docs/AGENTS.md` |

The laptop repository is the source of truth. Laptop runtime output belongs under `D:\TradingML\runtimes`; workstation runtime output belongs under `\\DESKTOP-SAAI85T\Workstation-D\TradingML\runtimes`.

Before creating or changing a numbered trading strategy, read
`docs/architecture/STRATEGY_CREATION_STANDARD.md`; its publication and
producer/preflight boundaries are binding across `src/`, `frontend/`, and
Backtest/live execution.

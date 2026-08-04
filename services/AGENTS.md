# Services instructions

- Keep acquisition, normalization, canonical persistence, service APIs, orchestration, and presentation responsibilities explicit.
- Services must expose stable contracts, health/readiness semantics, actionable structured status, and graceful interruption/restart.
- Health must represent active failures and dependency readiness; historical failure counts must not make a healthy service appear degraded.
- Historical and live service paths must consume the same canonical schemas and preserve point-in-time identity and timestamp semantics.
- Run dependency and connectivity preflights before downstream work. Required failures must stop the workflow rather than produce partial validity.
- Keep terminal output compact and useful: current work, queued/completed units, active failures, recovery, and last trustworthy snapshot.
- For terminal, CLI, console, progress, monitoring, or TUI design, use the `design-terminal-ui` skill. If unavailable, follow `docs/codex/skills/design-terminal-ui/SKILL.md`.
- Trace the authoritative source and contract before changing a terminal surface. Account for update rate, latency, freshness, lifecycle, failure, normal/compact dimensions, and live/idle/degraded/recovery states. A compile check alone is not terminal UX validation.
- Multi-worker output must show stable per-worker stages and accurate overall totals without message floods or flicker. Retain the last useful state and current focus during steady state.
- Write logs, caches, screenshots, and runtime outputs only under the machine-specific runtime root. Stop servers started for the task.

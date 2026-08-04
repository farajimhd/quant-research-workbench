# Services instructions

- Keep acquisition, normalization, canonical persistence, service APIs, orchestration, and presentation responsibilities explicit.
- Services must expose stable contracts, health/readiness semantics, actionable structured status, and graceful interruption/restart.
- Health must represent active failures and dependency readiness; historical failure counts must not make a healthy service appear degraded.
- Historical and live service paths must consume the same canonical schemas and preserve point-in-time identity and timestamp semantics.
- Run dependency and connectivity preflights before downstream work. Required failures must stop the workflow rather than produce partial validity.
- Keep terminal output compact and useful: current work, queued/completed units, active failures, recovery, and last trustworthy snapshot.
- Write logs, caches, screenshots, and runtime outputs only under the machine-specific runtime root. Stop servers started for the task.

# Repository instructions

## Instruction scope

- Follow this file and any more-specific `AGENTS.md` files below the files being changed.
- Start with the requested directory and inspect neighboring areas only when imports, contracts, tests, or runtime behavior require it.
- Consult `docs/codex/REPO_MAP.md` or the applicable `docs/codex/task-profiles/` procedure when scope, ownership, or the required workflow is unclear.

## Source and runtime authority

- Laptop source of truth: `D:\TradingCodes\quant-research-workbench`.
- Laptop operational root: `D:\TradingML`; generated artifacts belong under `D:\TradingML\runtimes`.
- Workstation code copies: `\\DESKTOP-SAAI85T\Workstation-D\TradingML\codes`.
- Workstation generated artifacts: `\\DESKTOP-SAAI85T\Workstation-D\TradingML\runtimes`.
- Workstation secrets: `\\DESKTOP-SAAI85T\Workstation-D\TradingML\secrets`; never copy secrets into code, artifacts, logs, manifests, or prompts.
- Make and validate code changes in the laptop repository first. Synchronize workstation code only after commit and push.
- Never store logs, screenshots, plots, caches, checkpoints, metrics, manifests, prepared data, downloaded dependencies, or other generated output in the repository.
- Set `PYTHONDONTWRITEBYTECODE=1` for every repository Python invocation; Python launchers must set it themselves so `__pycache__` never appears in source.
- If the required runtime root is unavailable, stop and request access; do not write to an alternate location.

## Change discipline

- Lead with the concrete outcome or root cause and distinguish facts, assumptions, judgment, and uncertainty.
- Do not silently use workarounds, weaken requirements, expand scope, or modify neighboring projects. Explain any necessary deviation before implementation.
- For diagnosis, review, explanation, or planning requests, do not modify files unless asked.
- For implementation requests, preserve unrelated behavior, review every modified file, and validate the real runnable path proportionally to risk.
- Never claim a test, runtime check, synchronization, commit, or completion that did not occur.
- Stop servers, workers, browser sessions, and child processes started for the task when finished.

## Continuity and task history

- For work that changes a durable architecture, service, data authority, or active task area, read `TASK_HISTORY.md` Current Focus plus only the relevant `TASK_HISTORY.csv` row and linked `CHAT_SUMMARIES.md` narrative. Skip continuity reads for bounded fixes unless continuity is plausibly material; never load the complete archive.
- Treat the latest user clarification as authoritative and reconcile it explicitly with stale history. Use `docs/codex/CHAT_SUMMARY_PROMPT.md` when creating or refreshing narrative summaries.
- Read-only questions and transient status checks do not require a new task row.
- At the conclusion of substantive work, ask the user whether task history should be updated before creating or changing a `TASK_HISTORY.csv` row. If requested, keep `current_focus` accurate, record progress, validation, remaining dependency, and program contribution, then run `python scripts/render_task_history.py` and commit the CSV and rendered Markdown together.

## Correctness and validation

- Keep one authority for each concern and reuse shared contracts across historical, live, UI, and backfill paths.
- Preserve raw/source, normalized, canonical, and model-ready layers with provenance, versioning, explicit timezone semantics, point-in-time identity, idempotency, and restart-safe checkpoints.
- Do not silently drop, skip, truncate, or overwrite authoritative data. Expose counts and reasons for rejected, deferred, retried, or failed work.
- For dataframe and bulk-data work, prefer native Polars expressions, lazy execution, predicate/projection pushdown, and vectorized operations over Python row loops when practical.
- Bound concurrency, queues, batches, and memory; measure representative bottlenecks before redesigning for performance.
- Validate table relationships, keys, ordering, partitioning, deduplication, final-row semantics, and timezone-aware timestamps before declaring data work complete.
- Long-running work must expose active, queued, completed, skipped, retried, and failed units and support graceful restart.
- Generated commands must include safe complete defaults. Use repository launchers where available.
- Use deterministic code for source preservation, structural parsing, identity, deduplication, hashes, timestamps, and other integrity-critical invariants. For agreed agentic stages, require validated structured final output rather than hidden reasoning or conversational filler.

## Delivery

- After validating a file-changing task, stage only the durable source files changed for that task, use a meaningful conventional commit message, commit, and push the configured branch. Never stage, commit, or push unrelated pre-existing user changes.
- Do not commit generated artifacts, caches, logs, screenshots, temporary files, or secrets.

## How To Work With Me

These instructions apply across coding, research, data engineering, services,
operations, and product UI work in this repository.

### Collaboration And Alignment

- I usually begin with a high-level idea, brainstorm alternatives, and then align
  with the agent on a design. Once we align, implement the agreed design fully
  and exactly. Do not silently omit parts, reinterpret the design, or add a
  materially different approach.
- If new evidence makes the agreed design unsafe, inconsistent, or inefficient,
  explain the conflict and proposed deviation before implementing it.
- Treat my latest clarification or retraction as authoritative. Reconcile it
  with earlier decisions and explicitly call out any remaining contradiction.
- If I ask to define, review, diagnose, or plan first, do that before changing
  code. Do not collapse a requested design checkpoint into implementation.
- Preserve working behavior outside the requested seam. Avoid unrelated cleanup,
  broad rewrites, or speculative features unless they are required for the
  agreed result.
- For large requests, maintain a concrete checklist of every agreed requirement
  and verify each item before finishing.

### Evidence And Diagnosis

- Ground conclusions in the current code path, logs, run artifacts, database
  rows and schemas, runtime behavior, or official API documentation as
  applicable. Do not give a generic answer when exact local evidence is
  available.
- Lead operational and debugging answers with the concrete verdict or root
  cause. Trace the failure end to end and distinguish confirmed facts,
  inferences, and remaining uncertainty.
- Reproduce issues using the exact failing symbol, accession, timestamp, row,
  request, command, or run when possible. After the targeted reproduction,
  check whether the same defect affects similar historical cases.
- Before tuning parameters or changing a strategy, verify that the code actually
  implements the intended rules. Separate implementation bugs, data problems,
  execution-model limitations, and genuine strategy weakness.
- When results regress, inspect the relevant logs, orders, trades, outputs, and
  code together. Do not assume that fewer events or different parameters are an
  improvement without evidence.

### Architecture And Implementation

- Maintain one clear authority for each concern. Reuse shared logic across
  historical, live, UI, and backfill paths instead of creating parallel
  implementations that can drift.
- Keep concerns separate: data acquisition and normalization, strategy logic,
  execution, persistence, orchestration, and presentation should not own each
  other's rules.
- Prefer modular, config-driven code with explicit arguments and visible
  defaults. Avoid hidden globals, duplicated constants, and laptop-only paths.
- Historical and live paths that promise the same output must produce the same
  schema and semantics. A backfill is not complete if it fills only a subset of
  what the live path produces.
- Put code, data, manifests, logs, and runtime artifacts in their designated
  roots. Do not place generated scripts in data-only directories or scatter one
  run's outputs across unrelated locations.
- Update nearby documentation when behavior, commands, schemas, defaults, or
  operational assumptions change.

### Performance And Resource Use

- Write efficient code. For dataframe and bulk-data work, prefer native Polars
  expressions, lazy execution, predicate/projection pushdown, and vectorized
  operations over Python row loops whenever practical.
- Measure the real bottleneck with representative data before redesigning for
  performance. Report useful timings or resource evidence when performance is a
  core requirement.
- Bound concurrency, queues, batches, and memory use. More workers are not
  automatically better; tune download, CPU processing, database writes, and GPU
  work independently for the available machine.
- Keep interactive and market-data hot paths responsive. Move durable slow work
  to controlled background processing without sacrificing correctness or
  observability.

### Data Correctness And Integrity

- Do not silently drop, skip, truncate, or ignore data. If pressure requires
  buffering or deferral, use a durable recovery path and expose counts and
  reasons. If loss is unavoidable, fail loudly and explain the impact.
- Treat database integrity, gap-free coverage, idempotency, and restart safety as
  first-class requirements. Coverage or checkpoint state must advance with
  successful work so restarts resume rather than repeat or overlook gaps.
- Preserve complete canonical source data. Apply market-hours, strategy-window,
  or presentation filters in the consuming layer unless the stored dataset is
  explicitly defined as filtered.
- Make time semantics explicit and timezone-aware. Storage may use UTC, while
  market logic and UI labels should use the appropriate exchange or user
  timezone without mixing naive and aware timestamps.
- Validate table relationships, keys, ordering, partitioning, deduplication, and
  final-row query semantics before declaring a data pipeline complete.
- Logs and manifests may record statuses, counts, reasons, paths, and secret
  presence, but must not copy raw sensitive data or secret values.

### Services, Commands, And Long-Running Work

- Run dependency and connectivity preflight checks before starting downstream
  work. Required failures must stop the workflow instead of allowing partially
  valid jobs to continue.
- Long-running work must continuously show what is active, completed, queued,
  skipped, retried, or failed. Progress totals and units must be accurate and
  actionable, with reasons available in structured logs.
- A steady-state terminal should retain the last useful state and show current
  focus; do not leave the operator staring at a generic `polling`, `queued`, or
  stale status.
- Support graceful interruption and restart. Stop child processes, workers,
  servers, browser sessions, and terminal helpers when the parent exits or the
  task finishes.
- Prefer runnable Python launchers with complete safe defaults for operational
  jobs. Generated commands should include all required arguments so manual runs
  do not drift from the validated configuration.

### Product And Terminal UX

- Optimize for compact, readable information hierarchy. Avoid oversized titles,
  redundant summaries, unused whitespace, raw dictionary dumps, and controls
  that consume space without helping the current task.
- Put primary status and actions where the user is already working. Show live
  results as work progresses instead of redirecting the user to a disconnected
  progress-only view.
- Use consistent reusable components and stable column layouts. Keep key values
  visible, align related metrics, group related information, and move secondary
  details into dialogs, tooltips, or expandable areas.
- Tables and charts should use the available viewport, remain readable at narrow
  sizes, and expose active filters, timezones, legends, and status meanings.
- Verify visual changes from rendered screenshots at representative normal and
  compact viewport sizes. Data being present somewhere is not enough if it is
  clipped, misaligned, stale, or hard to interpret.

### Validation And Handoff

- Review every modified file for correctness, consistency, and unintended scope
  before finishing.
- Validate the real runnable path in proportion to risk: compile, targeted unit
  tests, smoke tests, build, config resolution, representative data checks, and
  runtime-copy verification as applicable.
- Report exactly what was validated and what could not be validated. Never imply
  that an unrun test or inaccessible environment was confirmed.
- Do not commit temporary files, caches, generated logs, screenshots, local
  runtime output, or secrets.

### Task History

- Maintain `TASK_HISTORY.md` as the chat-independent, repository-level history
  of user-requested outcomes and the overall direction of the work.
- One row represents one durable task even when it spans multiple chats, design
  iterations, commits, commands, failures, and validation runs. Do not create
  rows for individual messages, transient errors, status checks, or small
  corrections that belong to an existing outcome.
- Create a new row when work begins on a materially separate outcome that can be
  completed, blocked, cancelled, or superseded independently. Use the earliest
  trustworthy request time as `Started`; do not invent timestamp precision for
  consolidated historical work.
- Before every git commit, update each task materially affected by that commit.
  Refresh its status, `Last updated`, concise progress, validation evidence,
  remaining dependency, and contribution to the broader program. The task-
  history update must be included in the same commit as the work it describes.
- Mark a task `Completed` only when the requested outcome is genuinely delivered
  and sufficiently validated. Use `Blocked`, `Cancelled`, or `Superseded` when
  those states are more accurate; never infer completion merely from a commit.
- Keep summaries outcome-oriented and compact. Record important decisions,
  implementation milestones, failures that changed the design, and final
  results, but do not reproduce chat transcripts or create a commit-by-commit
  changelog.
- Small follow-up fixes to a completed outcome should update its existing row.
  Create a new task only when the follow-up materially changes the intended
  capability or can proceed independently.

For every code change you make:

1. Review the modified files for correctness and consistency.
2. Generate a concise but meaningful git commit message that clearly describes the change.
3. Stage all relevant files using git add.
4. Create a commit using the generated commit message.
5. Push the commit to the currently configured remote branch.

Rules:

* Never skip the commit/push step unless there are errors.
* If git push fails, explain the reason and suggest the exact fix.
* Do not use generic commit messages like "update" or "fix stuff".
* Use conventional-style commit messages when appropriate, for example:

  * feat: add momentum confirmation filter
  * fix: resolve premarket entry regression
  * refactor: simplify rolling snapshot aggregation
  * perf: optimize quote merge pipeline

Before pushing:

* Ensure the code builds/runs successfully when possible.
* Avoid committing temporary/debug files, caches, logs, or secrets.
* Show the final commit message after committing.

---------------------------------------------------------------------------------
Research/Training Workflow Instructions

These instructions apply to research, ML training, model-versioning, experiment-management, and workstation-training work in this repo.

The source of truth is the laptop repo:

D:\TradingCodes\quant-research-workbench

The workstation is used for heavier training and is reachable from the laptop through the shared drive:

SSD Drive: \\DESKTOP-SAAI85T\Workstation-D
HDD Drive: \\DESKTOP-SAAI85T\Workstation-G

Do not treat workstation runtime folders as the source of truth. Make code changes in the laptop repo first, validate locally when possible, commit, push, and then sync the required runtime code to the workstation so the heavy scripts can run in the workstation.

General structure:

1. Version-specific research code belongs in the version folder, for example:

   research\<model_family>\vN\

   A training-capable version should normally include:
   - config.py
   - model.py
   - data.py
   - losses.py or objectives.py, if applicable
   - metrics.py, if version-specific metrics are needed
   - train.py
   - run_train.py
   - train_<job>.py for separate jobs such as linear probing, fine-tuning, evaluation, or embedding export
   - run_<job>.py for each runnable job
   - notebooks for inspection/plotting/debugging when useful
   - README.md with the version purpose, default data roots, default run command, and important assumptions

2. Shared engineering utilities belong in:

   research\mlops\

   Use this only for stable, cross-version engineering code such as:
   - environment loading
   - secret redaction
   - W&B initialization
   - metrics JSONL writing
   - run manifests
   - checkpoint helpers
   - path conventions
   - profiling helpers
   - seed/device helpers
   - command/launcher helpers

   Do not move version-specific model logic, data representation, losses, target construction, or experimental training behavior into mlops unless explicitly requested.

3. Runtime scripts should prefer Python launchers over PowerShell-only workflows.

   Each runnable job should have a Python launcher with defaults embedded in a visible config dictionary, for example:
   - run_train.py
   - run_linear_probe.py
   - run_finetune.py
   - run_export_embeddings.py

   The launcher should:
   - be runnable with `python run_train.py`
   - print the equivalent CLI command
   - allow simple command-line overrides
   - resolve the runtime root correctly whether executed from the laptop repo or copied workstation runtime
   - use unbuffered/clear progress printing from the underlying trainer

   PowerShell scripts are optional convenience wrappers, not the only way to run.

4. Workstation runtime setup:

   After implementing and validating a version locally, sync the required code to a workstation runtime folder under the workstation’s configured code/runtime root.

   The runtime folder should contain enough code to run without depending on the laptop path:
   - the version folder, e.g. research\<model_family>\vN\
   - shared mlops code required by that version, e.g. research\mlops\
   - any package __init__.py files needed for imports
   - root-level convenience launchers only if useful

   Workstation-copied files must be adjusted so defaults point to workstation-available data roots, checkpoint/output roots, and environment discovery paths. Do not leave laptop-only absolute paths unless they are intentionally valid on the workstation.

   After syncing, verify on the laptop side that the workstation copy contains:
   - the expected version name
   - the expected model/training parameters
   - the expected data roots
   - the expected output root
   - the expected W&B project/run naming
   - the expected run_<job>.py launchers

5. Experiment outputs should be organized under a single run directory per job.

   Logs, metrics, checkpoints, W&B files, configs, artifacts, and manifests should live under that run directory instead of being scattered across unrelated folders.

   Runtime folder names can be stable. Experiment identity should come from the run manifest and run name, not from the runtime folder name.

6. Secrets and .env handling:

   Never copy `.env` files into workstation runtime folders, experiment output folders, checkpoints, notebooks, W&B configs, logs, or manifests.

   Secrets should be loaded through environment variables or configured env-file discovery. Manifests may record only whether a secret is present or missing, never the value.

   Redact values for keys matching patterns such as:
   - *_KEY
   - *_TOKEN
   - *_SECRET
   - *_PASSWORD

7. Every run should record enough information to reproduce it.

   A run manifest should include, when applicable:
   - model family and version
   - job type
   - run name or run id
   - git commit
   - command args
   - resolved data roots
   - resolved output root
   - checkpoint path
   - source checkpoint path for downstream jobs
   - W&B project/run id
   - secret presence status only, never secret values

8. Before finishing code changes:
   - review modified files
   - run compile/smoke tests when possible
   - sync workstation runtime when the task affects workstation training
   - stage only relevant files
   - commit with a meaningful conventional commit message
   - push to the configured remote branch

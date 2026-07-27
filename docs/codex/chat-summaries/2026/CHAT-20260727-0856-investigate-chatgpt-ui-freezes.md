# Investigate ChatGPT UI Freezes and Establish Durable Cross-Chat Continuity

- Chat started: 2026-07-27 08:56:48 PDT (America/Vancouver)
- Chat ended or last activity: Active when summarized on 2026-07-27
- Summary written: 2026-07-27 10:43:20 PDT (America/Vancouver)
- Chat/task identifier: `019fa44b-0973-7c81-9672-939236736821`
- Repository or scope: `D:\TradingCodes\quant-research-workbench`; Codex desktop task performance, repository/runtime separation, frontend and IBKR runtime paths, and cross-chat governance
- Related task-history entries: `TASK-0135`, `TASK-0136`, `TASK-0137`, `TASK-0138`
- Source completeness: Complete for the 12 task turns available when this summary was written

## Narrative

The chat began with an operational problem rather than a repository feature:
the ChatGPT/Codex desktop interface repeatedly became frozen or very difficult
to use when another task was working. The initial investigation treated
parallel tasks as a supported workflow and looked for local evidence instead
of assuming that concurrent execution was inherently unsupported or that the
machine lacked resources.

The evidence pointed to a client-side scaling and state-synchronization problem.
The affected task, "Review gateway terminal design," had grown to an
approximately 849.6 MB local history file containing 58,411 records. Fifty-seven
of fifty-eight history requests took more than one second, commonly 3.4 to 3.9
seconds. During background streaming, the renderer emitted 529 ResizeObserver
layout-loop errors and 350 "Item not found in turn state" errors. There was no
out-of-memory event, renderer crash, Windows Application Hang event, or
corresponding CPU shortage; about 32 GB of RAM remained free. A secondary
problem caused 36 failed Git snapshot captures because a generated path reached
Windows' traditional 260-character limit while Git and Windows long-path
support were disabled.

The diagnosis therefore separated three contributors: an exceptionally large
task history, a desktop renderer/state bug exposed by concurrent background
streaming, and failed diff bookkeeping that added avoidable work. The
recommended recovery was to let background work finish, start a genuinely new
task with a compact handoff rather than fork the oversized history, archive the
old task after verifying continuity, completely quit and restart the desktop
app, install any available update, disable the Pets/avatar overlay if enabled,
and consider enabling Git long paths. App Repair was preferred over Reset, with
Reset reserved as a last resort after backing up local sessions. No app setting
or task was changed during that diagnosis.

The user's next hypothesis was that an earlier agent had disobeyed repository
instructions and created runtime artifacts inside the codebase, making task
loading more expensive. Inspection showed that the existing `AGENTS.md`
described the laptop repository and workstation shares but did not define the
exact laptop runtime root, workstation `codes` root, workstation `runtimes`
root, or the durable-source/generated-output boundary clearly enough. This
supported the user's concern about ambiguity, although it did not prove that
repository artifacts caused the renderer errors.

The user then supplied the intended authorities. The laptop source repository
was `D:\TradingCodes\quant-research-workbench`; laptop-generated artifacts had
to go under `D:\TradingML\runtimes`. The workstation authority was
`\\DESKTOP-SAAI85T\Workstation-D\TradingML`, divided into `codes`, `runtimes`,
and `secrets`. Output authority followed the machine executing the process.
`AGENTS.md` was updated at the top to distinguish durable code, tests,
launchers, configuration, documentation, and intentional fixtures from logs,
caches, checkpoints, screenshots, audits, metrics, W&B data, prepared data,
and other generated output. It also prohibited falling back into the
repository when runtime access was unavailable. This became `TASK-0135` and
commit `a79e56c16`.

The user separately asked for stronger global wording. The resulting general
instruction prohibited generated artifacts in any source repository without
accidentally banning legitimate tests, documentation, launchers, configuration,
or explicitly requested small fixtures.

With the policy established, the user requested a complete repository audit and
relocation of runtime or unrelated content. The review found 1,196 tracked
files, no ordinary untracked files, and 26,608 ignored files totaling roughly
15.8 GB at the first inventory stage. No tracked binaries, datasets, model
weights, images, audits, or other clear runtime artifacts were found. The
generated population consisted primarily of Rust `target` trees, smoke-test
checkpoints, runtime and temporary trees, W&B runs, UI-review captures,
generated news evaluations, backtests, prepared data, service logs, caches, and
frontend output.

The cleanup used a recoverable quarantine rather than deletion. Original
repository-relative paths were preserved under
`D:\TradingML\runtimes\analysis\quant-research-workbench-repo-artifact-quarantine\20260727T093557`.
The first pass ultimately moved 18,777 files totaling 17,506,728,709 bytes
(16.304 GiB). Git confirmed zero tracked deletions and zero ordinary untracked
files. `.env` and `.vscode/settings.json` remained because they were local
configuration, not runtime products. Two live areas were deliberately left:
118.77 MiB of `frontend/node_modules` used by two esbuild processes and 15.03
MiB of IBKR supervisor logs held by a running workflow. That work was recorded
as `TASK-0136` and commit `fe9eb22c6`.

The user then asked whether frontend builds could live outside the codebase and
whether IBKR logs should follow the service runtime convention. Code inspection
showed that merely changing Vite's `outDir` would be incomplete because
`node_modules` and caches would remain inside `frontend`. It also disproved the
assumption that all other service logs shared one workstation runtime root:
News, SEC, and Text Embed used configured market-data locations, while IBKR
uniquely defaulted to repository-relative
`tmp/ibkr_gateway_supervisor`; the backend duplicated that fallback.

The fundamental fix introduced shared runtime-path authority and
`scripts/run_frontend.py`. The launcher synchronizes durable frontend source
into `D:\TradingML\runtimes\quant-research-workbench\frontend`, installs or
reuses dependencies there, and runs development, build, preview, and UI-review
commands from the external workspace. Vite dependencies, caches, `dist`, and
review evidence no longer need to live in the repository. The backend serves
the external production build. IBKR defaults were changed to the laptop runtime
authority or workstation runtime share according to execution location, while
preserving explicit environment overrides. Both stopped IBKR files, totaling
15,772,278 bytes, were moved. Three path-authority tests, six Python syntax
checks, task-history rendering, backend path resolution, and an external
frontend build of 1,782 modules passed. Commit `2b4a63908` delivered this work.

The original frontend dependency tree still could not be moved because its
processes remained active. When the user clarified that no frontend task was
intentionally running and authorized termination, process inspection found two
orphaned npm/Vite/esbuild trees whose command lines explicitly referenced the
repository. Only those trees were terminated. The remaining 7,831 dependency
files, 124,543,912 bytes, were moved into quarantine. The README was expanded
to make `scripts/run_frontend.py` the only supported frontend package/build
entry point and to warn against direct repository-local npm commands. A clean
external rebuild again transformed 1,782 modules. The final cleanup total
became 26,648 files and 17,647,310,582 bytes (16.435 GiB), leaving only the
intentional ignored `.env` and `.vscode` configuration. Commit `b6492feb5`
documented the workflow and completed `TASK-0136`.

When asked whether these repository changes fixed the desktop freeze, the
answer remained deliberately qualified: they removed file-watching,
indexing, and stale-process pressure, but did not modify desktop history
loading, renderer state synchronization, or task concurrency. The app-level
problem still required a full restart, a fresh task, and a concurrent-task
reproduction before it could be considered resolved.

The conversation then shifted from cleaning one repository to preserving the
reasoning contained in long chats. The user explained that chats define their
thoughts and tasks and that future chats need more context than the concise task
ledger provides. The first proposal—to append long narratives directly to
`TASK_HISTORY.md`—was rejected because the Markdown file is generated from CSV
and because a monolithic narrative would recreate context bloat. The aligned
design kept `TASK_HISTORY.csv` as canonical task state, kept
`TASK_HISTORY.md` generated, made `CHAT_SUMMARIES.md` a concise index, and
stored detailed summaries separately.

The reusable prompt was added as
`docs/codex/CHAT_SUMMARY_PROMPT.md` in commit `acfe6ca83` and tracked as
`TASK-0137`. Its length policy evolved through explicit user corrections.
First, language that seemed to discourage long summaries was replaced by
comprehensiveness-first wording in `54b54cad2`. The user then identified that
unbounded summaries would make the document grow indefinitely, so `d3a320ab6`
introduced a 2,000-word cap, a short index, and year-sharded files. The final
clarification relaxed the detailed limit to 3,000 words, made section budgets
flexible, capped index entries at 150 words, and required one titled file per
chat with its normalized start date and time in the filename; this became
`2605ba652`.

Finally, the user required every future agent to recover this context at task
startup and keep ongoing task status current. `AGENTS.md` gained a mandatory
continuity protocol: read current focus, inspect relevant canonical CSV rows,
read the summary index when present, open only relevant detailed summaries and
their continuations, reconcile history with the latest user message, report
material gaps, and never invent missing context. Materially separate durable
work must be recorded as In progress near the beginning, updated at meaningful
milestones and before commits, and rendered back to Markdown. Commit
`048381f50` made this policy durable.

The current request activated that design. The task metadata established the
exact start time and identifier, the complete task was reviewed in two pages,
`TASK-0138` was opened before implementation, and the first year-sharded chat
summary and root index were created together with renderer support so future
task-history generation cannot erase the summary index.

## Durable decisions

- Confirmed requirement: repositories contain durable source-controlled
  project files only; generated execution, test, audit, dependency, cache,
  model, log, and evidence files belong under the executing machine's approved
  runtime root.
- Architectural decision: laptop code is authoritative; workstation `codes` is
  a synchronized executable copy, workstation `runtimes` owns generated output,
  and workstation `secrets` must never be copied into code or runtime results.
- Architectural decision: frontend npm dependencies, builds, caches, and UI
  evidence run from the external frontend workspace through
  `scripts/run_frontend.py`; direct npm installation in the repository is
  prohibited.
- Architectural decision: `TASK_HISTORY.csv` remains canonical concise task
  state. `CHAT_SUMMARIES.md` is a bounded index, while one timestamped and
  titled file per chat stores narrative history under
  `docs/codex/chat-summaries/<YYYY>/`.
- Confirmed requirement: each detailed summary is limited to 3,000 words and
  each index entry to 150 words; later events receive more detail.
- Confirmed requirement: agents review current focus, relevant task rows, the
  summary index, and relevant chat files at startup. The latest user
  clarification overrides stale history.
- Rejected approach: moving only Vite `dist`; that would leave dependencies and
  caches in the repository.
- Rejected approach: placing all detailed narratives directly in generated
  `TASK_HISTORY.md` or one indefinitely growing summary document.
- Unresolved uncertainty: repository cleanup may reduce resource pressure but
  has not been shown to fix the desktop renderer/state defect.

## Delivered outcomes

- Diagnosed the freeze using local history, renderer-error, resource, and Git
  snapshot evidence; no settings were changed during diagnosis.
- Added explicit laptop/workstation storage authority and stronger global
  repository/runtime-separation language.
- Quarantined 26,648 generated files totaling 16.435 GiB without deleting
  tracked source or moving secrets.
- Externalized frontend execution and IBKR log output; external frontend builds
  passed with 1,782 modules.
- Added and iteratively refined the durable chat-summary prompt.
- Added mandatory startup continuity and early active-task tracking to
  `AGENTS.md`.
- Created the first `CHAT_SUMMARIES.md` index and detailed year-sharded summary,
  and extended the task-history renderer to preserve the summary index.

## Unfinished or hanging work

### Desktop UI freeze verification

- Current state: the local cause is strongly evidenced as oversized-history
  loading plus concurrent renderer/state-update failure, with repository and
  stale-process pressure removed as secondary contributors.
- Why unfinished: the desktop app itself was not restarted or modified in this
  chat, and the failure has not been reproduced after cleanup in a fresh task.
- Exact next action: fully quit and restart the app, continue work in a new
  summary-backed task, run another task concurrently, and record whether the UI
  freezes. If it does, collect app logs, timestamps, task IDs, memory/CPU, and
  history-request timings for support.
- Dependency or owner: user for restart/reproduction; future diagnostic agent
  for evidence collection.
- Related task-history identifier: none; the original freeze investigation was
  diagnosis-only.

### Oversized historical task

- Current state: "Review gateway terminal design" remains an approximately
  849.6 MB historical task.
- Why unfinished: it was not archived or deleted because the user did not
  authorize that action and its history may still be valuable.
- Exact next action: confirm that its important context is represented in task
  history or a dedicated summary, then archive it rather than fork it.
- Dependency or owner: user approval.
- Related task-history identifier: related gateway tasks must be matched before
  archival.

### Git long-path failures

- Current state: the initial diagnosis found repeated snapshot failures at the
  260-character Windows path boundary.
- Why unfinished: global Git and Windows long-path settings were diagnosed but
  not changed.
- Exact next action: decide whether to enable `git config --global
  core.longpaths true` and, if needed, Windows long-path support, then confirm
  snapshot failures stop.
- Dependency or owner: user approval for a global configuration change.
- Related task-history identifier: none.

### Broader chat-summary population

- Current state: this is the first detailed chat summary; other historical
  repository chats are indexed only by existing task history and app metadata.
- Why unfinished: the user requested only this chat in the current task.
- Exact next action: use `docs/codex/CHAT_SUMMARY_PROMPT.md` to summarize other
  selected long chats, newest or highest-priority first, without indiscriminate
  loading.
- Dependency or owner: user selection or a separately authorized inventory.
- Related task-history identifier: `TASK-0137`, `TASK-0138`.

## Handoff to the next chat

Read `TASK_HISTORY.md`, `CHAT_SUMMARIES.md`, this file, and `TASK-0135` through
`TASK-0138` before related work. Preserve the external runtime authorities and
do not restore npm, logs, caches, or test output inside the repository. The most
important next action is to verify the desktop freeze after a full app restart
and fresh summary-backed task; app-level remediation remains unproven.

---
name: design-terminal-ui
description: Design, implement, review, or improve terminal and console interfaces. Use for CLI output, Python scripts, Rich or Textual displays, curses applications, service monitors, progress dashboards, multi-worker jobs, operational consoles, logs, prompts, and interactive TUIs, whether evolving an existing terminal interface or creating one from operational and data requirements. Do not use for browser-based frontend pages.
---

# Design Terminal UI

## Objective

Act as an operational UX designer and terminal-interface engineer. Make terminal
tools compact, stable, trustworthy, and immediately understandable during
normal operation, high activity, degraded service, failure, and recovery.

Do not organize the primary interface around program internals. Organize it
around the operator's questions, decisions, risks, and next actions.

## Start with the operation and its data

Before choosing panels, tables, progress bars, or colors, determine:

- The script or service responsibility and its place in the larger workflow.
- The operator, operating context, and decisions the interface must support.
- The authoritative data sources, schemas, units, identifiers, timestamps,
  ordering, relationships, and lifecycle states.
- Why each value or message is useful to the operator.
- Which information is critical, primary, supporting, diagnostic, or historical.
- The production model: one-shot, bounded batch, multi-worker, polling,
  streaming, steady-state service, or interactive control.
- Data and event arrival rates, burst behavior, latency, freshness, and expected
  idle periods.
- The meaning of completed, active, queued, skipped, retried, failed, blocked,
  waiting, degraded, stale, and recovered states.
- The consequences of incorrect progress, hidden failure, stale state, dropped
  messages, or unsafe interruption.

Inspect the real run chain, status objects, logs, checkpoints, representative
payloads, and terminal behavior when available. Do not infer operational meaning
from labels alone.

## Select the correct interface form

Use the smallest form that supports the work:

- For a one-shot command, show the request context, meaningful result, warnings
  or failures, and any required next action.
- For a bounded batch, show truthful overall progress, current unit and stage,
  worker activity when useful, failure reasons, elapsed time, and final outcome.
- For a steady-state service, show health, dependency state, operating mode,
  current focus, latest trustworthy result, freshness, lag or queue state, and
  recent actionable warnings.
- For an interactive TUI, add navigation or controls only when users benefit
  from inspecting, filtering, or acting without leaving the terminal.

Do not build a live dashboard for a short command. Do not reduce a long-running
or safety-relevant process to an unstructured log stream.

When no terminal UI exists, derive the initial information architecture from
the workflow, data lifecycle, update rate, risk, and terminal constraints. Make
routine layout and styling decisions autonomously. Ask for direction only when
a choice changes operational semantics, safety, control flow, or required
information.

## Establish attention and hierarchy

Map importance to screen position, persistence, grouping, contrast, and update
behavior. In most operational interfaces, prioritize:

1. Critical health, outcome, or failure.
2. Current activity and current focus.
3. Progress, coverage, freshness, or lag.
4. Warnings requiring action.
5. Recent meaningful messages.
6. Secondary metrics and diagnostics.
7. Detailed logs available on demand or in a file.

Adapt this order to the actual operator task. Keep information near the status
or action it explains. Avoid redundant labels when column context, placement,
format, or a stable convention makes the meaning self-evident; retain labels
when removal could confuse status, units, scope, or time semantics.

## Design for rate and change

- Separate high-rate events from the lower-rate information an operator can
  perceive and act on.
- Aggregate or summarize noisy events without silently losing durable evidence.
- Keep high-volume detail in structured logs while surfacing counts, trends,
  reasons, and representative failures in the live interface.
- Preserve stable spatial positions for values users compare over time.
- Bound refresh frequency to avoid flicker and unnecessary CPU use.
- Prevent bursts from flooding the screen or pushing critical state away.
- Retain important warnings long enough to be noticed.
- Preserve the last trustworthy snapshot during expected idle periods or
  temporary dependency loss, clearly labeled with its timestamp and stale state.

## Layout within terminal constraints

- Treat terminal width and height as hard constraints.
- Keep the most important state visible without scrolling.
- Do not place essential panels below the visible viewport.
- Prefer stable regions and aligned columns for comparable values.
- Use whitespace and separators to group information without wasting rows.
- Adapt at narrow widths and short heights; collapse or remove secondary detail
  before compressing critical content beyond readability.
- Anticipate long identifiers, messages, paths, large values, and missing data.
- Truncate only secondary content and preserve access to the full value elsewhere.
- Avoid raw dictionaries, JSON, or object dumps in the primary interface.

## Make status and progress truthful

- Distinguish active, completed, queued, skipped, retried, failed, paused,
  blocked, waiting, degraded, and stale states when they are meaningful.
- Use `waiting`, `idle`, or `polling` only with what is being awaited, the last
  meaningful result, and the next expected action.
- Match progress units to the real work partition and durability boundary.
- Advance displayed coverage or completion only after the corresponding work is
  actually successful.
- Do not display a percentage when the total is unknown or misleading.
- Prefer completed counts and current stage over false precision.
- Use rate and ETA only when their estimates are sufficiently stable to help.
- Keep the final success, interruption, or failure state visible.

## Present data according to its meaning

- Choose columns, ordering, alignment, precision, units, abbreviations, and
  emphasis from the operator's questions and the semantics of each field.
- Make identifiers, status, time, progress, failure reason, and freshness easy
  to distinguish when they drive decisions.
- Format values for comprehension without changing their underlying meaning.
- Label timezones and scopes whenever they affect interpretation.
- Keep column order stable across refreshes when comparison matters.
- Avoid wrapping that destroys row comparability.
- Use tables, trees, panels, progress bars, sparklines, or plain text only when
  they improve understanding of the actual data.

## Use styling deliberately

Assume the terminal controls the typeface. Use weight, case, spacing, alignment,
symbols, borders, and color to build hierarchy.

Decorative color is allowed when it strengthens product identity, orientation,
grouping, or comprehension. Keep it coherent and ensure it does not compete
with operational severity, obscure text, or make the interface unintelligible
without color. Reserve strong semantic treatments for states where rapid and
reliable recognition matters. Respect `NO_COLOR` or an equivalent mode when
applicable.

Avoid excessive nested borders, giant ASCII logos, decorative banners, and
large styled regions that consume the working viewport without improving the
operator's understanding.

## Handle failure, recovery, and interruption

- Fail required preflight checks before downstream work begins.
- Identify the failed dependency or stage and its operational impact.
- Distinguish transient retry, degraded operation, and permanent failure.
- Provide actionable reasons rather than a generic `failed` label.
- Preserve partial progress and restart information when supported.
- Do not blank a useful surface merely because new data is temporarily
  unavailable.
- Handle Ctrl+C and termination gracefully.
- Stop child processes, workers, browser sessions, and helpers when the parent
  exits.
- Restore terminal state on exit.

## Preserve non-interactive behavior

- Detect whether output is attached to an interactive terminal.
- Provide readable plain output for pipes, redirected files, CI, and log
  collectors.
- Do not emit cursor-control sequences into redirected output.
- Separate results, diagnostics, and failures appropriately between stdout and
  stderr.
- Make machine-readable output explicit instead of mixing it with the human UI.
- Preserve exit codes and automation semantics.

## Avoid weak terminal patterns

Do not default to large banners, raw configuration dumps, permanent generic
`polling` messages, progress disconnected from real units, a new log line for
every refresh, rapidly changing panel sizes, disappearing errors, essential
information below the fold, empty panels, or blank screens during dependency
loss.

Avoid labels that merely restate self-evident values or repeat an already clear
column or panel context. Do not remove labels when unit, scope, state, or time
meaning would become ambiguous.

## Render, inspect, and iterate

After implementation:

1. Run the real terminal entry point.
2. Exercise normal operation and relevant idle, degraded, failure, recovery, or
   completion states.
3. Inspect a representative normal terminal.
4. Force compact width and compact height behavior.
5. Check attention order, clipping, wrapping, panel placement, alignment,
   refresh stability, stale-state presentation, message retention, and progress
   accuracy.
6. Verify non-interactive or redirected output when applicable.
7. Test graceful interruption when safe and relevant.
8. Correct visible and operational defects before handoff.
9. Report exactly which modes and states were inspected.

A successful import or compile check is not evidence that the terminal UX works.

## Learn from corrections

When the user corrects a terminal design, classify the lesson as a general
operational principle, durable preference, service-specific convention, shared
component defect, measurable compact-layout regression, or one-off decision.
Do not generalize a one-off choice. Express reusable feedback as an operational
principle rather than a copied prohibition.

When the user explicitly asks to retain the lesson, update the appropriate
personal skill, repository instruction, shared component, or terminal smoke
test. Prefer tests for clipping, interruption, progress, and non-interactive
output; use skill guidance for judgment-based principles.

## Completion standard

Do not declare the terminal interface complete until the operation and data
have been understood, the attention hierarchy serves the operator's decisions,
status and progress are truthful, relevant degraded states are handled, normal
and compact output are readable, interruption and non-interactive behavior are
safe where applicable, the rendered interface has been inspected, defects found
during inspection have been corrected, and remaining uncertainty is reported.

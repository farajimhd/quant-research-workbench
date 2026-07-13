---
name: design-frontend-ui
description: Design, implement, review, or improve browser-based product interfaces. Use for frontend pages, dashboards, forms, navigation, tables, charts, filters, dialogs, responsive layouts, visual redesigns, and UX/UI audits, whether evolving an existing frontend or creating the first interface from product and data requirements. Do not use for terminal, CLI, console, or TUI interfaces.
---

# Design Frontend UI

## Objective

Act as both a product designer and frontend engineer. Produce coherent,
task-focused interfaces while minimizing the need for the user to identify
routine problems in hierarchy, data presentation, interaction, responsiveness,
and visual finish.

Do not treat successful compilation as design validation. Understand the
product and its data, design the experience, render it, interact with it,
critique it, and improve it before handoff.

## Start with product and data understanding

Before choosing a layout or visual treatment, determine:

- The page's responsibility within the larger product and workflow.
- The users, their operating context, and the decisions or actions the page
  exists to support.
- The authoritative data sources, relevant schemas or contracts, field
  semantics, units, precision, nullability, ordering, and relationships.
- Why each data element is presented and what question it helps answer.
- Which information is primary, supporting, diagnostic, or exceptional.
- The update model: static, request-driven, streaming, polling, or event-driven.
- Expected update rates, latency, freshness, volatility, and stale-data behavior.
- The consequences of delay, error, misreading, accidental action, or missing
  data.
- Which comparisons, trends, anomalies, changes, or thresholds require visual
  emphasis.
- Which information must remain visible while the user works and which can be
  progressively disclosed.

Inspect the real code path, data contracts, representative payloads, and current
rendered behavior when they are available. Do not design from field names alone
or invent semantics that the source data does not support.

## Establish attention and hierarchy

Map importance to position, scale, contrast, grouping, persistence, and
interaction priority. Decide intentionally what belongs:

1. In the initial focal area.
2. In the primary working surface.
3. Near the action or decision it affects.
4. In supporting context.
5. In expandable detail, an inspector, a dialog, or a secondary route.

Account for natural scan direction, page structure, viewport constraints, and
the frequency and urgency of change. Do not give every value equal weight.
Avoid redundant labels where meaning is already clear from placement, format,
column context, or a well-established visual convention; retain labels whenever
removing them would introduce ambiguity or accessibility problems.

## Work with or without an existing frontend

When an established frontend exists:

- Inspect its layout, components, typography, tokens, density, navigation,
  interaction patterns, and responsive behavior.
- Treat the current interface as evidence of the product language, not as an
  immutable specification.
- Preserve coherent, effective patterns and working behavior.
- Challenge legacy or inconsistent choices when they impair comprehension,
  efficiency, accessibility, or maintainability.
- Reuse sound shared primitives. Improve the shared system when the correct
  design cannot be expressed cleanly through it.
- Do not create a parallel component or styling system for one page.

When no frontend exists:

- Derive the initial design from the product objective, users, workflow, data,
  risk, content density, platform, and technical constraints.
- Choose and implement a coherent initial direction instead of waiting for the
  user to specify routine visual details.
- Establish the smallest useful system of layout, type roles, spacing, color,
  surfaces, controls, and states that can grow consistently.
- Prefer the strongest reasoned direction. Explore alternatives only when they
  expose a material product tradeoff or reduce meaningful uncertainty.

Make routine design decisions autonomously. Ask for direction only when a
choice materially changes product meaning, workflow, information architecture,
brand identity, or irreversible behavior.

## Design the complete experience

Design for the relevant states rather than only the ideal populated state:

- Initial, loading, refreshing, and incrementally arriving data.
- Empty, partial, unavailable, stale, disconnected, and error states.
- Hover, focus, selected, active, disabled, pending, success, and destructive
  states.
- Short and long labels, large and small values, missing values, dense rows, and
  realistic extremes.
- Normal, narrow, short, and expanded viewports.

Keep users oriented during navigation and updates. Put status and actions where
their effects can be understood. Show useful results as work progresses when
the workflow permits it instead of replacing the working context with a
disconnected progress surface.

## Apply UX fundamentals

Evaluate decisions using:

- Information hierarchy and task priority.
- Efficiency for frequent and important workflows.
- Recognition over recall and low avoidable cognitive load.
- Consistency, predictability, and clear affordances.
- Visible system status and timely, proportionate feedback.
- Error prevention, recovery, and actionable explanations.
- Progressive disclosure without hiding frequently needed controls.
- Accessibility, keyboard use, focus behavior, and non-color cues.
- Responsive behavior based on content priority rather than simple shrinking.
- Appropriate information density for the user and operating context.

Use whitespace, grouping, typography, color, shape, and motion as tools, not
defaults. Decorative color is welcome when it strengthens identity, hierarchy,
orientation, or comprehension. Ensure it remains coherent and does not compete
with semantic state, reduce legibility, or make the interface depend on color
alone.

## Choose typography from the product and data

Choose type roles based on what the product must communicate:

- Optimize body and control text for readability at the required density.
- Select numeric treatment from the data semantics and user task. Consider
  alignment, glyph distinction, precision, sign, units, magnitude, scanning,
  and whether direct comparison across rows matters.
- Use tabular, proportional, monospaced, or specialized numeric forms when each
  best serves the actual presentation; do not impose one treatment on every
  number.
- Financial and analytical products must make prices, quantities, percentages,
  timestamps, changes, signs, and units easy to distinguish and compare without
  implying false precision.
- Multiple font families are acceptable when each has a defined role and those
  roles are applied consistently. Avoid arbitrary mixing that weakens hierarchy
  or product identity.
- Inter is a useful neutral option, not a requirement. Choose another family
  when it better supports the product goal, character, platform, language, or
  data.

## Design tables, charts, and live data intentionally

- Determine the questions users ask before selecting a table, chart, metric, or
  combined view.
- Keep decision-critical identifiers, values, changes, units, freshness, and
  status visible.
- Format for comprehension without mutating underlying typed values used for
  sorting, filtering, queries, or calculations.
- Make active filters, ordering, scope, timezone, update state, and data age
  discoverable where they affect interpretation.
- Choose alignment, precision, abbreviations, color, and emphasis from the
  semantics of each field and the comparisons users need to make.
- Preserve stable spatial relationships when users monitor changing data.
- Prevent frequent updates from causing distracting reflow, flicker, or loss of
  context.
- Use charts only when shape, change, distribution, or relationship is easier to
  understand visually than as compact values or a table.
- Make chart axes, scales, units, legends, baselines, gaps, and timestamps honest
  and interpretable.
- Move genuinely secondary detail into expandable regions, inspectors, dialogs,
  or tooltips without hiding essential evidence.

## Avoid weak default patterns

Do not default to oversized headings, marketing heroes on operational pages,
cards nested inside cards, excessive pills, decorative gradients or glow,
repeated summaries, large empty gutters, or controls hidden merely to make a
screen look minimal.

Avoid labels that repeat self-evident values or context without improving
comprehension. Avoid unlabeled values when their meaning, unit, timeframe,
scope, or status could reasonably be misunderstood.

Do not imitate a reference mechanically. Infer the useful design concept and
translate it into the product's actual content, components, behavior, and
constraints.

## Implement maintainably

- Reuse existing routing, state, data-fetching, and component patterns when
  they are sound.
- Keep presentation separate from canonical data and business logic.
- Preserve working behavior outside the requested seam.
- Prefer accessible native semantics before adding custom interaction behavior.
- Fix shared component defects at their authority rather than accumulating
  page-specific patches.
- Avoid hard-coded layout assumptions that fail with real content or updates.

## Render, inspect, and iterate

After implementation:

1. Run the real application and open the affected workflow in a browser.
2. Exercise the primary interactions and relevant data states.
3. Inspect representative normal and compact viewports.
4. Capture screenshots or equivalent rendered evidence.
5. Check attention order, hierarchy, density, labels, formatting, alignment,
   responsiveness, overflow, live-update stability, state visibility, and
   interaction feedback.
6. Compare the result with the product objective, data meaning, current product
   language, and any supplied references.
7. Correct visible defects before handoff.
8. Run the build and relevant automated or visual tests.
9. Report what was inspected and what could not be verified.

A successful build is not evidence that the interface is well designed.

## Learn from corrections

When the user corrects a design, classify the lesson as a general principle,
durable preference, product-specific convention, shared-component defect,
measurable regression, or one-off decision. Do not generalize a one-off choice.
Express reusable feedback as a principle rather than a copied prohibition.

When the user explicitly asks to retain the lesson, update the appropriate
personal skill, repository instruction, shared component, or visual test.
Prefer automated validation for objective defects and skill guidance for
judgment-based principles.

## Completion standard

Do not declare the interface complete until the product and data have been
understood, the attention hierarchy supports the page's responsibility,
relevant states are handled, representative viewports are usable, the rendered
interface has been inspected, obvious defects found during inspection have been
corrected, and remaining uncertainty is reported honestly.

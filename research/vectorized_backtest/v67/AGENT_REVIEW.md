# Bounded training-evidence review

This document replaces all prior separate-replay and exhaustive endpoint-test
requirements for jobs with review_window=training-evidence-review-v1.
Historical evidence remains immutable. Do not restart retired diagnostic services.

1. After each sealed generation5,10,15,..., the live optimizer prepares the review
packet from existing training results, then opens a120-second review window before
next-generation intake. Finish within60-120 seconds; the hard deadline includes
submission. Timeout continues normal evolution and records the missed review.
2. Keep optimizer processes, resident RAM/GPU sessions, caches and population
state alive during the window. Do not close lanes, deactivate data, clear caches,
restart processes or unload sessions to pause. Existing memory bounds still apply.
3. Use the packet, selected parent genomes, network summaries, existing training
receipts/traces, chronological/ticker summaries and prior intervention/operator
outcomes. No validation performance may influence selection, edits or retries.
4. Trust recorded automatic validity checks. Do not redo optimizer tests, replay
parents, benchmark every network, launch review GPU workers or run parameter grids.
Investigate apparent inconsistencies only when existing evidence identifies one.
5. Screen the configured256 diverse candidates using compact grouped summaries.
Include champions, descendants, specialists, repairable losers, distinct behavior
and network sizes. Inspect targeted existing traces within the time budget; do not
claim exhaustive inspection of every trade or endpoint.
6. Find higher-level weaknesses: entry/price incompatibility, contradictory entry
and management logic, repeated losses, churn, repeated adds, concentrated profits,
brittle conditions, poor chronological/ticker/session breadth, ineffective legal
network combinations and duplicate behavior. Interpret actual units and masks;
daily-high inputs can be signed ratios, not absolute prices.
7. Learn from previous offspring, controls, failures, survival and descendants.
Do not repeat failed hypotheses without new evidence. Observational associations
are not causal proof. Report all-session and traded-session denominators; broad
profitability is an objective, never a guarantee or a new survival rule.
8. Normally target192 evidence-directed repairs and64 ordinary-mutation controls,
one child per parent. Each proposal names its parent/hash, existing evidence,
exact edit, hypothesis, expected change, tradeoff and falsification. Report genuine
shortfalls; do not pad unsupported edits or reward duplicate behavior.
9. Record review-notes.json using review-notes-template.json. Account for every
selected parent as reviewed, screened, or not_reviewed with an honest reason.
Proposed parents need interpreted evidence and a falsifiable plan, not repeated
endpoint tests. Missing telemetry becomes a request for subsequent NORMAL replay,
not permission to replay the cohort separately or invent evidence.
10. Submit package.json only through review_contract --out PACKET --job ACTIVE_JOB
--publish. The bounded publisher checks identity, notes, evidence hashes and the
existing central construction authority. No new performance gate or pre-admission
replay is required. Do not write inbox files directly or submit after the deadline.
11. Normal v2 intake appends up to256 proposals to the normal1024 successor without
replacing fresh/elite/crossover/mutation slots. Normal next-generation training
replay evaluates them; unchanged selection returns1024. Keep20% score-independent
exploration, fitness, diversity and causal execution contracts unchanged.
12. At the next five-generation review assess actual offspring outcomes and
subsequent descendants. Before normal replay, describe offspring as hypotheses,
not improvements. Prior stop/target fallback failures remain recorded.
13. Show preparing/reviewing/submitted/admitted/evaluated/timeout states separately
in the progress panel, with exact edits and parent comparisons. Missing performance
is unavailable, never zero. Safety failures remain fail-closed; a deadline does not
bypass validity. Explicit user stop pauses automation and run; no automatic restart.

No subagents by default. No new review-owned feature caches, independent replay
services, or exhaustive qualification campaigns. The optimizer owns the window;
the chat heartbeat only interprets ready evidence and submits within that window.

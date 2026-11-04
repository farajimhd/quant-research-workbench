# Training-only agent review

When enabled by `agent_intervention.POLICY`, review each newly sealed multiple
of five generations under v2 (historical v1 used ten). Use `agent_review_packet` to export a training-only
packet. Read only its packet, selected parents and training traces. Validation
must never inform parent choice, edits, hypotheses, acceptance or retries.

Read `prior-interventions.json` before designing another cohort. It compares each
earlier manual offspring's first full training replay against its source parent.
Inspect the ordinary-mutation controls and failed hypotheses as well as winners;
do not repeatedly propose edits that failed. Existing training-only
`offspring-audit.json` and reproduction operator outcomes supply earlier
evolution history. Operator groups overlap and are observational comparisons.
Future moves inside training sessions may inform offline diagnosis and edits,
but offspring inputs and execution must remain causal. Never encode future
prices, date-specific outcomes or validation observations into a candidate.

An agent reviews the evidence and writes a package using the exported template.
This is not an automatic weight-tuning algorithm. Legal network structure,
weights, routing and policy genes may change; feature/execution contracts may
not. Explain each offspring's hypothesis and its parent identity. Include
ordinary mutations as controls when useful. Do not claim improvement before
full replay. An empty reviewed package is valid only after investigations are finished and no proposals qualify.

Review 256 distinct active candidates, explicitly including evolved champions,
descendants, session specialists and repairable losing strategies. The v2 exporter
mixes score, net, chronological robustness, breadth, session champions and network
sizes, deduplicating observed session profit/activity. It reports shortfalls rather
than padding with identical behavior. Recorded dominant-family labels alone must
not collapse the review to four parents; report ancestry concentration separately.

Scripts summarize and verify evidence; the agent interprets it and chooses edits.
Review all 256 compact summaries, group candidates by diagnosed weakness, then
inspect targeted training trade/input traces. Use bounded batches of 16 summaries;
do not dump entire genomes or allocate an agent per candidate. Each offspring needs
its own parent identity and evidence-linked hypothesis. Normally aim for 192
directed repairs and 64 ordinary-mutation controls, one offspring per reviewed
parent. If evidence supports fewer proposals, record the reasons; never pad blindly.
Previously displayed validation results must not influence subsequent edits.

Publish one atomic JSON package in `phase-2/global/agent-reviews/inbox`.
At the next generation boundary, v2 appends at most 256 proposals, at most one per
parent, to the normal 1,024-member successor. No normal fresh, elite, mutation or
crossover slot is replaced. Up to 1,280 candidates receive full replay on review
rounds; unchanged training-only diversity reproduction produces the next 1,024.
Existing bounded GPU batches still apply; extra candidates imply extra compute,
not guaranteed equal wall time. The source review must be within its five-generation
epoch and arrive no later than epoch + 10. Historical v1 decisions remain immutable.
Do not queue multiple outstanding reviews or accumulate missed epochs.

The intake decision (including no intake) is frozen before replay, then bound
into the generation seal. Checkpoint consumption prevents a review from being
applied twice. The original checkpoint/successor remains intact; the explicit
intake receipt explains the augmented replay cohort. New IDs retain
the verified source parent's ancestry. No review or agent runs inside a GPU
worker. No ready package means replay continues without waiting for an agent.

For a recurring chat follow-up: inspect only lifecycle and consumed review
epochs first. Stay quiet while unchanged. When a new review is due, export one
packet, inspect compact summaries before detailed traces, design the reviewed cohort,
validate it with `validate_package`, and atomically publish it. Use no agents by
default and at most one isolated reviewer if a concrete question needs it.
Report admissions/failures, not repetitive status. Stop reviewing when the
campaign finishes or the user stops it. Local scheduling requires the app and
computer to remain available; delayed reviews never stall replay.

## Mandatory endpoint review contract (v66-endpoint-review-v1)

The exporter writes endpoint-review-template.json for every selected parent and
branch. Copy it to endpoint-review.json and investigate every network endpoint,
price endpoint, sizing, input dependencies, routing and ownership. Each check
must have a reason and either hashed packet-local training evidence or an explicit
untested/not-applicable limitation. Never call a summary-only review complete.

For each candidate, reconstruct the training entry funnel: eligible observations,
signals, invalid/missing prices, requests, fills and closed trades, by session and
ticker. Admission counters overlap and do not account for every pre-order failure;
missing telemetry is unknown. Trace missing swings, stop/target relations, spread,
size and affordability separately. Inspect winning, losing, repeated-loss and
boundary management paths. Audit arithmetic domains, nonfinite/overflow behavior,
impossible predicates, ignored parameters, duplicate/dead expressions, masks,
causality, branch ownership, stop <= bid-.03 and the mandatory500bps boundary.

Report all-eligible and traded denominators for ticker/session profitability,
activity, win rate, drawdown, turnover and profit concentration, plus chronological
subperiods. Greater than50% profitable coverage is a reported objective, not a
new survival rule or guarantee. Zero-profit trading sessions remain active.
Run justified training-only cost/spread/threshold comparisons; label accounting
stress separately from execution replay. Do not tune using validation.

For every supported mismatch, prioritize a causal offspring repair with expected
behavior, tradeoffs and falsification. Suppressing invalid signals alone does not
recover missed trades. Preserve parents and compare full training replay with
frozen parents and ordinary controls. Review prior failures, survival, descendants
and duplicate behavior each epoch; do not repeat unsupported edits. Keep normal
1024 reproduction and20% exploration unchanged; aim192repairs/64controls, report
shortfalls without padding. Replay never waits for this investigation.

Publish ONLY with python -B -m research.vectorized_backtest.v66.review_contract
--out PACKET_DIRECTORY --job ACTIVE_JOB --publish. This checks all selected
parent identities, endpoint coverage, evidence hashes, plans and package validity
before atomic intake. Proposed parents must have no untested endpoints/checks.
Deferred/rejected parents must state limitations; an empty package with any untested check is rejected and
must not be described as an exhaustive completed investigation. Save traces under
the packet directory and use {path,sha256} references. Do not bypass this publisher
with ad-hoc writes. It is an off-lane publication gate, not an optimizer hot-path
change. Use a separately receipted review deployment for existing pinned campaigns.


### Finish investigations instead of closing them as deferred
Persist active investigations under agent-reviews/investigations/<parent>/ with
immutable spec.json, per-session hashed receipts and state.json. Resume incomplete
work on each follow-up, even between review epochs. Prioritize17006 until its
entry/price diagnosis and supported repair comparisons reach a measured outcome.
An epoch deadline never means an incomplete investigation is a completed review.
Do not submit empty intakes to clear the schedule while checks remain untested.

Use candidate_investigation for the diagnosed17006 swing-price comparison: it
runs unchanged full chronological execution with an observing wrapper, parent,
stop fallback, target fallback, combined fallback and ordinary control. It records
pre-order reasons, every action network's activity/known/nonfinite counts and
per-ticker signal coverage, plus order journals and episode metrics. Parent replay
must match frozen training receipts; fail closed on differences. All150 training
days are required for the final comparison. Missing data or failure remains an
active actionable investigation, not permission to weaken endpoint checks.

Inspect actual results, intervention tradeoffs, ticker/session profit breadth,
chronological subperiods and robustness before claiming a repair. Save exact
payloads and tests. A measured unsuccessful repair is a completed experiment;
an unrun comparison is not. Propose at most one child per parent for normal full
training replay/selection. If the original source epoch expires, rebind only an
identical current parent program to fresh sealed evidence; otherwise follow the
nearest relevant current descendant with a new explicit comparison. Never forge
recency, modify old packages or overwrite evaluated genomes.

### Apply the workflow to every configured candidate
Run review_backlog --packet PACKET --job ACTIVE_JOB --out REVIEW_ROOT/backlog
after export. This inventories every selected parent's actual branches, networks,
price endpoints and ten checks, with an immutable identity bound to training,
execution and objective configuration. Reconcile each epoch without resetting
unfinished work; current source bindings are separate from accumulated evidence.
Inventory creation is not investigation completion. Start with17006, then continue
the remaining configured candidates; do not substitute the17006 experiment for
the general process.

candidate_investigation defaults to --profile configured for any candidate and
runs its unchanged parent plus a seeded ordinary control using the active job.
Use --comparisons FILE for manually justified exact repair payloads, parent hash,
hypothesis, expected_change, tradeoff and falsification. It records every actual
leaf's network thresholds/sizing, minimum ages and price expressions in spec.json;
per-leaf program hashes link observer telemetry even for routed candidates.
The specialized --profile17006 is only for17006's predefined experiment (spell
the CLI option as --profile 17006). Bound simultaneous diagnostic shards to four,
check resources first, and verify all training days before combining results.
No automatic operator grid or automatic tested labels are permitted. Process
the persistent backlog through replay, trace interpretation, robustness and
qualified publication; missing evidence remains an explicit active task.

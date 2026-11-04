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
full replay. An empty reviewed package is valid.

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

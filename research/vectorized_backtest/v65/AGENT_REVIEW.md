# Training-only agent review

When enabled by `agent_intervention.POLICY`, review each newly sealed multiple
of ten generations. Use `agent_review_packet` to export a compact training-only
packet. Read only its packet, selected parents and training traces. Validation
must never inform parent choice, edits, hypotheses, acceptance or retries.

An agent reviews the evidence and writes a package using the exported template.
This is not an automatic weight-tuning algorithm. Legal network structure,
weights, routing and policy genes may change; feature/execution contracts may
not. Explain each offspring's hypothesis and its parent identity. Include
ordinary mutations as controls when useful. Do not claim improvement before
full replay. An empty reviewed package is valid.

Publish one atomic JSON package in `phase-2/global/agent-reviews/inbox`.
At the next generation boundary, at most 64 proposals, at most four per parent,
replace fresh, never elite or crossover, slots. Their full training replay and
the unchanged diversity selection determine survival. At least 141 of the
normal 205 fresh slots remain when the maximum is admitted. The source review
must be within its ten-generation epoch and arrive no later than epoch + 20.
Do not queue multiple outstanding reviews or accumulate missed epochs.

The intake decision (including no intake) is frozen before replay, then bound
into the generation seal. Checkpoint consumption prevents a review from being
applied twice. The original checkpoint/successor remains intact; the explicit
intake receipt explains the next cohort's changed fresh slots. New IDs retain
the verified source parent's ancestry. No review or agent runs inside a GPU
worker. No ready package means replay continues without waiting for an agent.

For a recurring chat follow-up: inspect only lifecycle and consumed review
epochs first. Stay quiet while unchanged. When a new review is due, export one
packet, inspect compact summaries before detailed traces, design a small cohort,
validate it with `validate_package`, and atomically publish it. Use no agents by
default and at most one isolated reviewer if a concrete question needs it.
Report admissions/failures, not repetitive status. Stop reviewing when the
campaign finishes or the user stops it. Local scheduling requires the app and
computer to remain available; delayed reviews never stall replay.

# Requested changes for the next optimizer update

Recorded 2026-10-21. Pending implementation; do not modify or restart the
current V34 experiment to apply these changes.

1. Remove the $300 per-stock planned-risk restriction. Retain the risk metric
   for reporting. Apply the removal consistently to candidate eligibility and
   the corresponding entry/addition risk-budget checks, rather than leaving
   the same restriction enforced elsewhere.
2. Increase the maximum stop-distance limit from 20% to 30%. Keep execution
   admission, candidate eligibility, configuration validation, optimized CUDA
   paths, tests and displayed limits consistent when implementing the update.
3. Preserve valid terminal-state requirements and all unrelated search,
   execution, data-integrity and resource-admission behavior.

## Phase scope clarification

Eligibility checks apply in both phases. In Phase 1, each candidate is checked
on the one session being optimized locally. In Phase 2, it must pass on every
training session in the global evaluation. This does not mean that Phase 1
omits the checks. Validation remains display-only and cannot affect selection.

These are future behavior changes, not a compute-only optimization. Preserve
existing results and use an explicitly versioned, source-bound run when the
update is implemented; do not silently reinterpret existing results.

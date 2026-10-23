# V42 initial global screening

Phase 2 screens 3,072 unique candidates on all 24 training sessions, then
retains 1,024 by descending global objective (stable index ties; feasible
candidates only). All 200 ordinary global generations remain size 1,024.
Screening never opens validation or consumes the optimizer's training RNG.

Each session contributes up to 96 candidates: five evaluated-generation
ranges (1–20 through 81–100), each supplying eight objective-ranked candidates
and eight sampled candidates (two from each profit percentile band: 0–10,
10–30, 30–60, 60–100), followed by 16 from the final archive's existing order.
Sampling uses a separate fixed seed. Distinct evaluated generations are
preferred within each selection. Canonical identities are deduplicated globally.

Historical payloads come from each sealed checkpoint's bounded 1,024-member
historical profitable reservoir. Full past populations were not retained.
Selected records are checked against their generation seal, serial, eligibility,
objective, profit and canonical payload; source generation and ancestry survive
the phase boundary. Missing allocations fill from the same session, then other
sessions, then fresh candidates; every shortfall/substitution is recorded.
The target is 2,304 local plus 768 fresh candidates.

Three cohorts of 1,024 use existing bounded replay batches, memory admission,
GPU guards and distributed workers. Completed cohort receipts support exact
resume. Selected numeric rows are gathered from verified training receipts
for global generation 1, avoiding a second replay. Validation remains the
ordinary display-only shortlist after selection; reproduction is unchanged.
The extra two cohorts cost additional initial evaluation time, not a 3x
population throughout the campaign.

`phase2_activation.install` supports an explicitly audited controller handoff:
it loads only the committed initialization policy and the receipt-reuse runner
over original immutable numerical modules. Policy file hashes are separately
bound into the seed pool. Never overwrite old deployments or change existing
local checkpoints/source hashes. Never use it after global initialization has
already started. A handoff must verify the exact old controller is dead and
its lock is archived, keep both worker identities alive, and preserve STOPs.

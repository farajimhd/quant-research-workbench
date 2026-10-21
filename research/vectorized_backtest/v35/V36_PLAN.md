# V36 crossover plan

Pending implementation. Preserve the current V35 run and its results.

## Agreed direction: multiple children per selected parent pair

Initial proposed distribution: one child 75%, two children 20%, three children
5%. Each child gets an independent crossover draw and the existing mutation
process. These probabilities are a starting proposal, not measured optimal
settings.

Keep the total population, species offspring allocations and evaluation budget
fixed. Siblings consume existing offspring slots; truncate the sibling request
at the remaining species allocation. Retain ancestry restrictions and semantic
duplicate rejection. No validation feedback. Preserve deterministic RNG and
resume state. Record requested/realized sibling counts, distinct parent pairs,
sibling similarity and offspring survival using existing training results.

## Additional ideas for discussion, not yet authorized for implementation

- Reciprocal sibling construction: alternate the primary parent across siblings.
- Matched-module exchange by input/temporal role, beyond exact UID homology.
- Output-distribution-aware module interfaces to reduce saturation or inactivity
  after exchange, using a fixed bounded training-only probe and no fitness.
- Richer nonlinear composition operators at crossover, using existing supported
  neuron operations, without hard-coding initial strategy families.
- Linked inheritance of action heads and their related sizing/policy genes.

Measure each addition separately before combining proposals. Do not assume
that added operators or sibling counts improve profit or generalization.

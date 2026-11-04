# Inherited reference

This document is retained from V64 for design provenance. Historical performance
and qualification statements below are not V66 results. See README.md for V66
input, initialization, and feature changes; workstation evidence lives in its runtime.

# Whole-share execution revision

This revision changes financial semantics and must
not inherit old fractional-share replay results through a source-only transition.
The order contract is `explicit-top5-whole-share-v2`; the cost formula is unchanged.

- Entry quantity is `floor(1000 / candidate_buy_limit)`. The neural entry-sizing
  multiplier is ignored. Fills may cost less due to price improvement or partial
  liquidity; fees are separate. Prices above the budget cannot buy one share.
- Adds use `floor(200 / candidate_buy_limit)`, independent of neural sizing.
- Every requested and filled quantity is a whole share. Reduction fractions
  retain their prior sizing rule, rounded down; exit sells remaining inventory.
  Internal floating-point storage does not permit fractional holdings.
- The central construction authority canonicalizes the add limit to five for
  sampling/crossover/mutation policies, including routed strategies.
- A compiled strategy-side request guard checks the per-position count before
  creating an add order. The count increments once on the first share filled;
  subsequent partial fills do not increment it. A pending fifth add may finish.
  The count resets only after full closure (or initialization of a new episode).
- The guard runs in the chronological request adapter immediately before order
  creation, beside accounting for efficiency. It is not an execution rejection
  rule and introduces no input or recurrence to GPU neural inference. Suppressed
  signals are exposed as `strategy_add_limit_suppressed`, with no rejected-order
  penalty because no order was submitted.

CPU fixtures exercise fixed sizing, partial liquidity, whole-share reductions,
the five-add boundary, counter resets, and exact chunked/unchunked accounting.
Fresh campaigns require new financial replays. The approved runtime configuration
is six workers, 256-clock blocks and event-based transfers. Do not repeat the
128-clock stress tests that preceded the workstation freeze.

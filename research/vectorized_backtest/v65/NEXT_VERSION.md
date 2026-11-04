# Requirements for the version following V65

Status: planned, not implemented. User approved recording this requirement on
2026-11-04 after inspection of candidate 286812 from the completed 300-generation
V65 campaign. This does not authorize modifying that campaign or its genomes.

## Semantics-preserving construction canonicalization

Extend the shared construction authority beyond connectivity validation:

- Detect identical subexpressions within and across composed action graphs.
- Propagate conservative output ranges and availability semantics to identify
  provably impossible decisions and constant Boolean expressions.
- Simplify equivalent Boolean compositions and prune newly unnecessary nodes.
- Account explicitly for execution-inactive genes and heads; avoid fictitious
  diversity from fields ignored by the deployed execution contract.
- Preserve causal feature contracts, missing-input masks, float32 operation and
  comparison semantics, trigger and effective sizing outputs, routing priority,
  explicit price rules, and whole-share execution behavior.
- Define persistent mutation-slot and genealogy remapping before deduplication;
  document any change to the future evolutionary search distribution separately
  from equivalence of current execution behavior.
- Version canonical identity and retain original-to-canonical hashes, applied
  rewrite rules, node counts, and proof/qualification receipts in runtime output.

Qualification must cover missing inputs, threshold boundaries, CPU/GPU agreement,
all action heads, routing, explicit prices, and full training-replay equivalence
for representative existing genomes. Do not substitute approximate arithmetic
rewrites or training-only inactivity for a proof of semantic redundancy.

An intentionally inactive management head is legal; represent it explicitly and
compactly rather than requiring every action to fire. Keep structural legality,
semantic simplification, and financial selection as separate concerns. Do not
silently change the V64 diversity or fitness rules as part of canonicalization.

## Evidence motivating the requirement

V65 validates bounds, allowed inputs, topology, and graph reachability. Reachable
nodes can still feed impossible decisions. Whole-network composition shares
identical complete branches but does not generally deduplicate overlapping
subgraphs. Exact training-record grouping does not simplify individual genomes.
Score-first selection uses node count as a tie-breaker, so a small financial gain
can retain a larger redundant candidate. These observations motivate the work;
they are not evidence that simplification improves out-of-sample profitability.

Further candidate-audit findings should distinguish confirmed contract defects,
provable inefficiency, and trading hypotheses requiring independent evaluation.

## Additional investigation backlog from the completed campaign

These are review findings and proposed comparisons, not approval to change
execution or selection policy. Evidence is retained under the campaign runtime
in `candidate-audit-20261104` (training only).

- Make routed management ownership explicit: the compact executor recomputes
  the first eligible entry branch each clock, falling back to branch zero when
  none qualifies. Compare this with persistent entry-branch ownership.
- Specify whether stop updates may loosen risk. Current updates replace the
  stop rather than enforcing a monotonic trailing stop. Audit actual loosening
  events before claiming a performance effect.
- Reconcile exposed policy fields with effective execution. Entry/add sizing,
  management cooldown and profit gating must not appear configurable when the
  executable path ignores them. Record effective policy in candidate reports.
- Audit executable bid/ask spreads, quote age and trade/quote disagreement at
  admission. Current stop/target ordering relative to the buy price does not
  ensure the stop lies below the executable bid. Keep source anomalies distinct
  from legitimate illiquidity; investigate journal-confirmed extreme episodes.
- Detect reversed or bypassed spread predicates, negative-threshold crossing
  predicates, and validity predicates masquerading as magnitude filters.
- Measure near-equivalent behavior and lineage concentration separately from
  exact identity. Any proposed selection change requires its own comparison.
- Compare explicit hysteresis, turnover restraint and execution-cost stress
  against unchanged controls; structural simplicity alone does not prevent churn.

Do not infer generalization from these training results, or claim counterfactual
improvement without replay. Fixed-turnover cost sensitivity is not a substitute
for spread, fill, delay or liquidity perturbation replay.

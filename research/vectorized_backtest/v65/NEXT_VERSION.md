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

## Entry-branch ownership and mandatory admission boundary

User-directed next-version requirements recorded on 2026-11-04. These are
execution-contract changes, separate from semantics-preserving canonicalization;
the completed V65 artifacts remain immutable.

- Bind an entry order to its originating branch when submitted; retain that
  identity through partial fills. On first fill, that branch owns the position's
  exit, add, reduce, stop-update and price instructions until fully closed.
- Management does not require the owner's entry condition to remain true. Do
  not switch ownership to another qualifying branch or fall back to branch zero.
  Persist ownership in checkpoints and order/position receipts. Cancel residual
  orders appropriately before resetting ownership and admitting a new episode.
- Independent lifecycle exits (stop/target execution, session end and existing
  rank-loss rules) remain executor-owned. Ownership is execution state, not an
  implicit new neural input.
- Enforce a global spread gate outside candidate logic, shared by all execution
  paths. Revised per user feedback: positive finite bid and ask, ask >= bid,
  spread_bps = 10000 * (ask - bid) / bid <= 500. There is no fixed-dollar
  spread ceiling. The user-specified 500-bps (5%) maximum corresponds to USD
  0.05 at a USD 1 bid, USD 0.50 at USD 10, and USD 5 at USD 100. Candidate
  networks may impose stricter spread filters within this boundary, but cannot
  override or relax the global ceiling. This maximum is not a target spread or
  a claim of empirically qualified profitability. Keep the bid denominator
  explicit and consistent.
- Apply the gate to new entries and additions, at submission and each attempted
  fill, including residual partial orders. A failed gate permits no further
  exposure-increasing fill. No genome, branch, crossover, mutation, price rule
  or agent intake can relax it. Version and freeze limits in the job contract.
- Never use the spread gate to block exits, reductions, protective liquidation
  or order cancellation. Specify residual-order handling and record rejection
  reasons and counts rather than hiding skipped fills.
- Require proposed initial stops to be positive and <= current bid - USD 0.03;
  reject invalid entries rather than silently moving their stops. Respect the
  applicable tick grid. This does not replace the spread gate or decide whether
  later stop updates may loosen protection.

Qualification must exercise boundary equality, tick rounding, missing/crossed
quotes, pending and partial orders, adds, owner changes attempted by routing,
restart/resume, and exit availability during wide spreads. Compare unchanged
controls using training-only replay before adopting the new executable version.
Quote provenance/freshness and trade/quote disagreement still require their own
investigation; passing a spread test is not proof of quote validity.

## Mandatory construction gate for every generated candidate

User clarified that validity and efficiency must be enforced for all generated
candidates, including crossover and mutation results. Use the shared central
authority as the final gate for founders, immigrants, crossover, mutation and
agent-supplied offspring; operator-local checks may reject bad proposals early
but must not become independent definitions of validity.

- Validate operator domains, finite bounded parameters/results where required,
  input and edge contracts, availability semantics, and supported topology.
- Canonicalize provably redundant expressions, constant logic, unreachable
  decisions and execution-inactive genes before identity and budget checks.
  Revalidate afterward; reject invalid results with explicit reasons and use
  bounded retries without padding or weakening the contract.
- Represent intentionally disabled management actions compactly; distinguish
  these from invalid arithmetic and impossible entry logic. Do not invent a
  replacement strategy or remove a branch merely because it was inactive on
  observed training data.
- Where a rewrite cannot be proved safe, retain the expression and expose the
  unresolved case; never label an unsound rewrite as simplification. This gate
  cannot promise discovery of every semantic equivalence in arbitrary graphs.
- Qualification must cover every generation path, missing data, float32 and
  threshold boundaries, temporal crossing semantics, effective sizing, price
  rules and routing, with receipts for rejections and canonicalization.

## Additional investigation backlog from the completed campaign

These are review findings and proposed comparisons, not approval to change
execution or selection policy. Evidence is retained under the campaign runtime
in `candidate-audit-20261104` (training only).

- Measure the effect of the approved entry-branch ownership contract against
  V65's per-clock entry routing and branch-zero fallback.
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

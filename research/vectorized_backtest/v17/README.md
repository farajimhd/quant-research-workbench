# V17: robust global/session costs and effective rule evolution

Copied from V16, retaining the vectorized Torch/Triton feature, rule and
assumed-fill position engines. V16 remains immutable. V17 starts 1024 unique
random strategies with seed 172237; no previous strategies, scores, RNG state
or result caches are imported. Certified V16 compact/history/continuous-indicator
banks are reused read-only. The indicator definition remains
`v16-continuous-period-bank-v1` because its mathematics and feature IDs are unchanged.

## Costs

For a finite vector x, U10(x) is its sum minus the fractional highest 10 percent
of positive contributions. The fraction uses the total observation count;
negative contributions are never removed. Empty position vectors return zero.
Full net profit always remains the financial reporting/ranking authority.

P_s is complete net session profit, T_s=U10(complete-position profits in s),
and R_s=.05*drawdown_s+.005*risk_dollar_hours_s+.0004*capital_dollar_hours_s.
Minimize the independent global cost:

`-U10(P_1,...,P_N) - .10*N*mean(worst 20 percent raw session profits) + sum(R_s)`.

Minimize each local cost:

`-T_s + R_s + .25*max(0,-mean(T_u for u != s))`.

For one session, the other-session term is zero. Inactive sessions remain in
the global tail and other-session denominator. Existing terminal-valid and
traded-candidate eligibility is unchanged. There is no additional concentration,
complexity, trade-tail or fixed-$1000-shortfall penalty. Both masks and exact
fractional tail boundaries fail closed on invalid/partial/nonfinite ledgers.
One vectorized session x candidate financial panel serves scoring, local selection
and diagnostics. Segmented NumPy sorting computes position trims without trade loops.

## Preserved selection

The global/local population allocation remains 512/512, with the existing elite,
champion, fresh-immigrant and crossover quotas, 5-generation ancestry exclusion,
tournaments, family-uniform features, deduplication and random 20-85 percent
mutation rate over eligible crossover children. Each selected child receives
1-5 distinct successful effective field edits. Validation shortlist and successor
remain frozen before display-only validation; no validation feedback is permitted.
The existing search/rule/policy envelopes and sizing/fees are retained.

## Semantic checks and identity

Initialization, mutation and crossover reject reachable comparisons of a formula
with itself, provably inactive entries and implicit constant management signals.
Explicit constant management signals remain legal. Checks are structural and
data-independent; no low-frequency rule is rejected from observed outcomes.
Canonicalization removes unreachable nodes and unused node metadata while
preserving numerical operations, temporal windows and missing-evidence masks.
An always-true formula with feature-validity dependencies is never replaced with
unconditional TRUE. Only provably execution-irrelevant policy/age genes are ignored
for identity. Mutation does not count unchanged effective identity as a success.
Retry budgets remain bounded, with cumulative rejection reasons persisted in
rule-quality.json and terminal/W&B scalar diagnostics. Semantic caches are bounded.

## Evidence and reporting

Session panels retain full profits/scores and append local trimmed profits,
other-session loss penalties, drawdowns and risk/capital hours. Candidate rows
include global/local removed profit, concentration diagnostics and known rule-gate
observation counts through one register-only CUDA reduction with five integer
outputs per candidate (add counts are zero when policy forbids additions). These
counts precede position-state and age eligibility;
they are not executed-order counts. Objective view is O; financial ranks remain
based on full net profit. Reporter network work stays outside the optimizer.

`audit_saved` traces bounded legacy outliers through the existing certified
assumed-fill evaluator. Optional entry/exit clock ledgers are disabled during
normal optimization. Audits reconcile complete-position profits and certified
input marks, label carried-price terminal liquidation and retain hashes. They
are not broker-realism or data-source certification claims.

## Qualification and launch

Commit/push laptop source before immutable workstation deployment. All artifacts
belong under D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v17.
Stop V16 at a completed generation boundary and preserve its checkpoint,
sealed receipts, partial artifacts and ownership/worker stop evidence.

`python -B -u -m research.vectorized_backtest.v17.qualify_v16 --baseline-code
<immutable-v16-code> --campaign <stopped-v16-campaign> --stop-receipt <receipt>
--output <v17-qualification>` runs CPU/CUDA contract tests and compares identical
1024-member workloads on two complete training sessions, batch512/two workers,
cold and warm, without result reuse. Every baseline financial field must match
exactly. The warm speed gate allows at most 5 percent timing noise. It also measures
V17 objective/reproduction overhead. This is representative full-session evidence,
not a claim of measured complete 30+6-session generation speed.

Launch with `python -B -u -m research.vectorized_backtest.v17.run_train --training
<certified-training.json> --validation <display-only-validation.json> --output
<v17-campaign> --qualification <qualification.json> --v16-stop <stop-receipt>
--commit <deployed-commit>`.
Safe defaults: 1024 population, 512 batch, 2 workers, 50 generations, seed172237,
4 GiB capture cache/worker, 8 GiB gate budget, $1000 entry/$200 add, $0.005/share
and $1 minimum order fee. The launcher prints the equivalent command, verifies
matching source qualification and stopped ownership, and publishes active identity.
The measured full-generation receipt includes selection, validation and publication.

# V45: global-profit elites and ticker/session parent selection

Authorized laptop test: August 13, 2026, 50 generations, population 1024,
seed 2020, the exact original V42 initial 6144 programs and post-initialization
RNG. Original full-session financial results are verified and rescored; old
objective scores, archives and offspring are never imported. V42/V43/V44 code
and preserved experiments remain unchanged.

## Scores and financial authority

Global ranking = total training net profit after the original costs and fees.
Case score = case net profit - 0.05 * case realized drawdown, in dollars.
Cases merge certified venue listings by economic security and session. Complete
position P&L reconciles to ticker totals and full-session profit. Case drawdown
uses cumulative complete-position exits with simultaneous exits aggregated; it
is not intraposition mark-to-market drawdown. Full-session mark-to-market
drawdown remains displayed. All original terminal/risk/execution eligibility
checks remain. No broad, concentration, tail, outside-loss, trade-count or
complexity term affects these scores. Inactive case score is zero.

## Exactly 1024 population slots

- 102 unchanged elites: highest positive global net profit, canonical deduplication.
- 205 baseline fresh candidates / immigrants (one route, not two allocations).
- 358 global crossover slots: globally profit-ranked positive eligible parents.
- 359 ticker/session crossover slots: positive case-ranked eligible parents.

If fewer than 102 distinct positive global elites exist, unused elite slots
become fresh candidates. Every failed bounded crossover becomes a fresh slot,
charged to its original route. Requested/realized/fallback counts are sealed.
Mutation is not another allocation: preserve the 52.5% mutation schedule on new
candidates, original parameter/structural operators, and unchanged elites.
Preserve original 80% within / 20% between species compatibility, strict ancestry,
uniqueness guards, sibling counts/orientation and bounded attempts. Species
state/behavior/stagnation remain reported; offspring quotas now come from the
explicit global/case routes rather than profit-weighted species allocations.

## Adaptive case parent distribution

Each generation independently ranks positive financially eligible programs in
each case. Take rank 1 from each occupied case, then rank 2, and continue until
1024 distinct programs or positive candidates are exhausted. Canonical duplicates
retain case memberships without consuming additional archive slots. Rotate the
case ordering each generation so a partially filled last round is fair.
There is no fixed per-case n: it depends on positive ranks and duplicate overlap.
This is a bounded historical parent archive, not 1024 extra replay candidates.
Programs, complete same-session metrics, source generation and pedigree persist
in the checkpoint and are readmitted for parent selection without new replay.

The 359 case offspring slots are divided equally across occupied admitted cases;
integer remainders rotate. Parent A is rank-biased within its case. Parent B is
rank-biased in another case when ancestry/species-compatible candidates exist;
otherwise the same case is allowed under the same compatibility rules. Global
parents use the existing size-three-tournament rank bias on global profit.
Birth records identify both selection route and initiating/mate cases. New
children receive complete-session evaluation; financial eligibility is mandatory.

The current runner explicitly rejects multiple sessions. The single Aug13 test
cannot establish cross-session generalization. Profitable case specialists can
lose elsewhere; all global profit and drawdown remain visible for assessment.
No validation data is opened or fed into selection/RNG.

## Launch and follow

Use an immutable runtime snapshot with the original V42 dependency tree:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -m research.vectorized_backtest.v45.launch --job D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v42/distributed-399e034e1/full-job.json --output D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v45/aug13-mixed-p1024-s2020-g50 --initial-pool D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v43/aug13-trade-only-p1024-s2020-g50/initial-pool.json --authorize-run
python -B -m research.vectorized_backtest.v45.progress --active D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v45/active-test.json
```

Task Scheduler owns training outside the app process tree while logged in.
The version-owned scheduler helper must not be inserted into the hashed original
V42 dependency package. No reboot recovery or automatic optimizer retry.
STOP under output/local/2026-08-13 requests a sealed-boundary stop. Inspect exact
owner identity and seals before using run_test to resume. The launch helper
refuses an existing active output. Preserve all historical evidence.

Rank remains global net-profit rank. The terminal shows Global P&L $ and Best
case $. Key 1 or 2 sorts global profit; key 5 sorts best case score. Sorting is
read-only. Scores, adaptive memberships and reproduction counts are sealed per
generation. All generated artifacts belong beneath D:/TradingML/runtimes.

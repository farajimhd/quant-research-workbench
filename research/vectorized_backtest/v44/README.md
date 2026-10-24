# V44: preserve specialists, evaluate broader offspring

Independent V43 fork for the authorized August 13, 2026 laptop experiment:
50 generations, population 1024, seed 2020, exact original V42 initial 6144
programs and post-initialization RNG. V42 and V43 sources/deployments are unchanged.
The sealed original financial receipt is reused and rescored, never old scores
or offspring. All V42 execution, fees, risk eligibility, operators, sibling/mask
crossover, ancestry restrictions and 10% elite / 20% fresh budgets remain.

## Objective

Cases are all economic securities in the fixed certified replay universe for
August 13. Venue listings merge by certified security_id. Inactive cases are
zero; every loss and winner is retained. Case P&L reconciles both to complete
position episodes and full-session net profit. Drawdown D_c is realized equity
drawdown from complete position exits, aggregating simultaneous exits first.
It is not per-ticker intraposition mark-to-market drawdown. The original full-day
mark-to-market drawdown remains reported separately. Budget B=$1000 is fixed,
never realized exposure or hindsight opportunity size.

u_c = (P_c - 0.05 D_c) / 1000
S_c = u_c - mean_{j != c} max(-u_j, 0)
Specialist = max_c S_c
Broad = mean_c min(max(u_c, 0), 1)
        - mean_c max(-u_c, 0) - 0.25 CVaR_10%(max(-u_c, 0))

CVaR averages the largest ceil(0.10 * case_count) losses, including zero losses
in that fixed tail width. The single-case outside penalty is zero. Scores are
dimensionless and higher is better. No top-trade/ticker/session removal,
trade-count reward, mandatory activity or complexity cost. No validation access.
The current runner rejects multi-session input explicitly; this experiment does
not claim session generalization.

## Archive and selection

Keep up to two distinct programs per case, only financially eligible candidates
with positive case utility and specialist score for that case. Archive has at
most 256 unique members; deterministic round-robin case admission takes one
per case before second places, prioritizing case champion scores if full.
Programs, complete same-session metrics, source generation and pedigree persist
in the checkpoint. Historical archive parents rejoin the breeding pool with
verified preserved metrics; newly generated children receive full replay.

Half the existing elite slots first preserve specialists across distinct best
cases; the other half select broad leaders. Species/offspring allocation uses
best rank across the broad and specialist routes rather than adding unlike
scores. Species descriptions and stagnation evidence use actual broad scores;
a species containing positive specialists remains available. Alternating mating
slots prefer specialist first parents; compatible unrelated second parents with
different best cases are preferred when available. Original within/between
species routes, bounded attempts, uniqueness and mutation operators remain.
Every child's original parent scores and specialist targets are in birth records.
Archive admission and scoring consume no training RNG.

These are initial research settings, not a calibrated profitability claim.

## Run and observe

From an immutable runtime snapshot (not the source checkout):

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -B -m research.vectorized_backtest.v44.launch --job D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v42/distributed-399e034e1/full-job.json --output D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v44/aug13-specialists-p1024-s2020-g50 --initial-pool D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v43/aug13-trade-only-p1024-s2020-g50/initial-pool.json --authorize-run
python -B -m research.vectorized_backtest.v44.progress --active D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v44/active-test.json
```

Task Scheduler owns training outside the desktop app process tree. It survives
app updates while logged in; reboot recovery and automatic retries are not
implemented. A STOP file under output/local/2026-08-13 requests a sealed-boundary
stop; inspect exact owners and seals before resume with run_test. The launch
helper refuses an existing output. Both scores and best specialist case are
sealed in scores.json; specialists.json seals archive membership each generation.
Rank remains raw-net-profit rank. Key 1 sorts profit, 2 broad, 5 specialist;
3 win rate, 4 risk. Display sorting does not affect reproduction.

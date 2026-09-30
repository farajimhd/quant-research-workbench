# Extended-hours strategy research

## Authorized scope

The September 30, 2026 campaign uses Strategy 1 as the immutable baseline and
August 18 and 19, 2026 as development sessions. Initial cash is $10,000 for
each independent session. Preserve the existing sizing, exposure and cost
contracts initially. Trade only premarket and after-hours. Additional sessions
will be supplied for validation; repeated optimization on these two days is
in-sample research, not proof of a repeatable edge or live approval.

Publish each changed trading behavior under the next strategy number according
to `docs/architecture/STRATEGY_CREATION_STANDARD.md`. Retain failed runs and
their exact source/configuration identities. No ticker-specific exclusions or
post-hoc removal of losing trades constitutes a strategy backtest.

## Fresh session boundaries

The fixed executor now distinguishes a fresh flat account starting intraday
from checkpoint resume. An after-hours start uses a 43,200,000 ms offset from
04:00 ET; only completed buckets strictly after 16:00 are executable. Earlier
activation evidence retains its original timestamp and five-minute expiry.
Warm-up never restores previous positions, orders, purchases or cash effects.

V7 geometry remains a producer-owned, certified interval product. Its producer
loads the prior-session checkpoint and advances completed one-second bars
through the day, including regular hours. The consumer verifies source hashes,
coverage and causal visibility; it does not rebuild V7 or trade regular hours.
The current V1 seed policy is provisional `legacy-unfiltered`; this limitation
must remain visible and must not be silently replaced by a new source policy.

## Reproducible commands

Use the repository's `ml4t` interpreter and set `PYTHONDONTWRITEBYTECODE=1`.
The base Python environment may lack the required Keeper dependency.

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
& "$env:USERPROFILE/miniconda3/envs/ml4t/python.exe" -B scripts/clickhouse/smoke_strategy_one_app_route.py --session 2026-08-18 --start-time 04:00 --minutes 330 --cash 10000 --apply
& "$env:USERPROFILE/miniconda3/envs/ml4t/python.exe" -B scripts/clickhouse/smoke_strategy_one_app_route.py --session 2026-08-18 --start-time 16:00 --minutes 240 --cash 10000 --apply
& "$env:USERPROFILE/miniconda3/envs/ml4t/python.exe" -B scripts/clickhouse/report_strategy_one_trades.py
```

The launch command invokes the public app route in-process and persists the
normalized ClickHouse journal. It does not claim browser acceptance. The report
is a read-only projection, with immutable outputs under
`D:/TradingML/runtimes/strategy_one_research`. It rejects incomplete or nonflat
runs; failed-run diagnostics must identify open exposure separately.

## Initial evidence

Both premarket runs and August 19 after-hours completed and their saved journals verified:

| Session | Run | Net P&L | Fees | Closed-position drawdown |
|---|---|---:|---:|---:|
| August 18 | `b62fa860-7570-4976-b23f-58b416b56a42` | $154.38 | $279.37 | $1,126.02 |
| August 19 | `1cd937c3-2dd1-4369-a477-bc74e532c94d` | -$2,689.70 | $375.50 | $2,771.29 |
| August 19 after-hours | `b06469c5-fded-4f7a-a944-d0837d17af81` | -$574.00 | $186.34 | $583.37 |

These drawdowns use closed episodes, not marked-to-market intratrade equity.
Entry volumes use pinned completed one-second bars; float and exit attribution
remain explicitly unavailable when their source evidence is missing.

The first fresh August 18 after-hours run,
`8b4a1def-215a-4647-af52-122c0371f48d`, failed at source exhaustion with BBNX and
MRVI still financially active. Its journal verified, and its first fill was
16:00:00.400 ET. Seven closed positions are not a complete session result.
Strategy 1's stop/target-only path has no guaranteed session-end flattening;
inventing closing fills or dropping residual positions is forbidden. Explicit
session-end behavior belongs in a new numbered strategy.

The fresh-start change passed 140 focused composition, execution, controller,
scheduler and stateful tests. New numbered strategy publication remains
incomplete. Keeper connection warnings appeared
during teardown even on successful runs and reads; they remain unresolved.

## Strategy 2 research specification

The first increment preserves the completed-bar entry, re-entry, resistance
adds, sizing, and stop/target rules. Its sole trading change is explicit
extended-session control, published as a separate Backtest-only number:

| Window (ET) | Last acquisition decision | Cancel remaining acquisitions | Begin liquidation attempts |
|---|---|---|---|
| 04:00–09:30 | Strictly before 09:25 | After the completed 09:25 bucket | 09:29 |
| 16:00–20:00 | Strictly before 19:50 | After the completed 19:50 bucket | 19:55 |

Liquidation needs a fresh certified quote and fills only against subsequent
certified liquidity. Sparse policy clocks are not market rows. Missing quotes,
partial fills, and residual orders remain visible; session-end exposure fails
the run rather than becoming an invented closing fill. The immutable release
pins the inherited configuration, rule specification, source commit and source
fingerprint. Strategy 1 retains its original release and action whitelist.
Strategy 2 public resume remains closed pending a real interrupted-run
equivalence check; typed recovery tests alone do not constitute that acceptance.

Baseline research hypotheses, not measured counterfactual results:

- Repeated losses and fees deserve attention: August 19 premarket YJ had nine
  positions totaling -$780.31, while August 18 XOS had twelve totaling -$286.20.
  A short cooldown alone cannot explain losses separated by minutes.
- A blanket initial reward/risk threshold could remove profitable SLE entries
  that benefited from later target increases and adds. Compare full shared-cash
  reruns before adopting it.
- BTCT's August 19 after-hours positions repeatedly exited during a broader
  rise. Test position management and net capture of the move, rather than
  selecting successful trades after the fact. Several exits lack exact causal
  attribution, so price alone must not be labeled a stop or target.

Every subsequent trading increment needs its own number and retained full-run
evidence. Closed-position drawdown is currently available; it is not a substitute
for a verified marked-to-market account drawdown.

## Excursion diagnostics

`scripts/clickhouse/analyze_strategy_trade_excursions.py` produced immutable
diagnostics for the three completed baseline runs (71 positions) under
`D:/TradingML/runtimes/strategy_one_research/baseline_excursions_v1`.
Each run used one vectorized aggregate SELECT against its pinned 100 ms and
one-second bar attempts. In-position windows exclude ambiguous fill buckets;
five- and fifteen-minute post-exit windows stop at the requested session end.
Percentages are descriptive price extrema, not executable fills, account
drawdown, or counterfactual P&L. Final weighted entry can include later adds.

Twenty-six of 69 positions with available post-exit fifteen-minute extrema
had a subsequent high at least 10% above weighted exit. Only four losing
positions had an in-position high at least 5% above final weighted entry.
These observations motivate testing selective re-entry as well as management;
they do not establish that simply holding losers would have improved returns.
The eight unavailable after-hours diagnostic windows remain explicit:
GO has no subsequent persisted bars in those windows, and URG has only bars
with invalid extrema. Six boundary/failure-gate tests and repeated immutable
readback/conflict checks passed.

Strategy 2 implementation validation before publication: 250 execution,
journal and recovery tests passed, including 24 focused second-strategy cases.
The frontend build passed; all 12 setup display scenarios had no detected
layout issues, and the real saved August 19 after-hours view rendered its
13 closed positions and -$574.00 net result. The latest full-market Strategy 1
app preflight passed in 21.3 seconds with no blockers. These checks do not yet
establish a successful Strategy 2 full-session run or profitable edge.

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

## Strategy 2 full-session evidence

Published 903 normalized nodes from source commit
`a6c2d7709f2017bbed5cf1c548f3be03c69bf25e`, configuration
`strategy-one-2:19235772-4956-446b-805e-9fe01969d399`, payload hash
`5b6c7caacdbbd911e8cbf33eff625e2f28c44f24ec07342b8dcf019d9fae4ed3`.
The matching workstation checkout is
`D:/TradingML/codes/quant-research-workbench-strategy2-a6c2d7709`.

| Session | Run | Result | Net P&L | Execution seconds |
|---|---|---|---:|---:|
| August 18 PM | `11883b78-2ad4-42a8-881a-9085bd878beb` | Completed; 26 closed | $154.38 | 58.860 |
| August 19 PM | `d85c15dc-0098-4eb1-8498-c4a347c6d134` | Completed; 32 closed | -$2,689.70 | 58.089 |
| August 18 AH | `d2bac302-a8ba-4310-9f64-c30da59514bb` | Failed; terminal persistence incomplete | unavailable | 17.625 |
| August 19 AH | `719d1d41-2194-462a-9ecf-e3e0b5f2c75a` | Completed; 13 closed | -$574.00 | 36.132 |

Both premarket runs match Strategy 1 exactly on ticker, entry/exit timestamps,
weighted prices, quantity, fees and net P&L for every episode. After-hours
August 19 differs only for GO: the exit average and fees changed, with net P&L
worse by $0.00407. Strategy 2 therefore has no measured profitability improvement.
Completed reports are in `strategy_one_research/strategy2_sessions_v1` under
the runtime root. Their drawdown remains closed-episode drawdown.

The August 18 failure has two distinct causes. Residual exposure is real:
MRVI has no certified liquidity rows after 19:50, and BBNX has one fresh quote
at 19:55:01 but no subsequent bucket to fill a newly submitted exit. The
failure-recording path also raised before advancing the final controller
cursor; terminal persistence failed with a cursor/clock mismatch. Its observed
writer inventory was zero, so there is no complete economic journal to report.
The failed attempt is retained in ClickHouse's run inventory, and its CLI
diagnostic is preserved in
`strategy-optimization-20260930/strategy2-20260818-afterhours-failed.json`.
Do not recreate missing financial facts or label this a completed run.

## Strategy 3 research specification

Preserve Strategy 2 sizing, management and session cutoffs, but require the
entry's certified activation strictly after the current extended-session open:
after 04:00 for premarket and after 16:00 for after-hours. An activation
completing exactly at 16:00 still contains the last regular-session bucket and
is excluded. Implement this as an additional vectorized necessary-condition
mask before survivor market I/O. Do not reset or fabricate episodes, rebuild
producer products, or change the V7 prior-checkpoint/regular-session warm-up.

Cold-verified Strategy 1 entry evidence shows BBNX and MRVI activated at
15:57:36 and 15:57:33.200; GO, URG and QS activated at 15:59:56.400,
15:59:50.100 and 15:59:27. Those first after-hours proposals will be ineligible,
but their losses cannot simply be subtracted: freed cash changes later trades.
Pinned SELECTs, attempts, hashes and activation facts are retained in
`strategy-optimization-20260930/pinned-session-exit-activation-diagnosis.json`.

Correct completed-boundary failure persistence before this next campaign.
Strategy 2's published source and results remain unchanged. Strategy 3 needs
new publication, four full-session runs, and its own persisted review evidence.

Implementation checks passed: 270 execution/journal regressions, 90
configuration/publication/saved-reader checks, and 76 final integrated
session-window, source-seal, activation and real-controller tests. The
activation policy applies to new entries, including reentry; held-position
management and adds remain inherited. These checks precede full-session
Strategy 3 acceptance and do not establish profitability.

## Strategy 3 full-session evidence

Published 910 normalized nodes from source commit
`bded1dfac7a60145e57553a48e70d560a1558ed0`, configuration
`strategy-one-3:3d0bac17-d306-4163-821f-db70fa417804`, payload hash
`3ad54031a8b0d1fb65e7552aad0d5134c4207b18f9b11fff089f619b8b28ffcb`.
Pinned workstation checkout:
`D:/TradingML/codes/quant-research-workbench-strategy3-bded1dfac`.

| Session | Run | Closed | Net P&L | Closed-episode max drawdown | Execution seconds |
|---|---|---:|---:|---:|---:|
| August 18 PM | `3a89c954-f344-498d-acfc-b75ec78b624e` | 26 | $154.38 | $1,126.02 | 65.046 |
| August 19 PM | `84b996f8-6184-44a8-9bfc-b0069868a47e` | 32 | -$2,689.70 | $2,771.29 | 65.738 |
| August 18 AH | `c20dfbac-9cff-4bb8-92e6-3ece4dda01f2` | 11 | $130.32 | $1,007.07 | 39.555 |
| August 19 AH | `3bec6b8a-9afa-42b2-bbfc-ffff9cebfecb` | 10 | -$829.78 | $888.25 | 37.626 |

All four sessions completed flat with zero failed journal-writer units.
Reports are immutable under `strategy_one_research/strategy3_sessions_v1`.
Premarket aggregate economics are unchanged. August 19 after-hours worsened
by $255.77318 versus Strategy 2; removing early proposals changed available
cash and later position sizes. August 18 after-hours now completes, but ten
losses totaling $1,007.07 precede one BIVI winner of $1,137.39. This is fragile
in-sample evidence, not an established edge. Timings include concurrent
research/read activity and are not isolated performance benchmarks.

The user requested backend refresh after completed changes or saved backtests.
Managed backend restart also refreshed its active frontend dependent. The
app API was verified to list all four Strategy 2 attempts and all four
completed Strategy 3 runs; the incomplete Strategy 2 attempt remains explicitly
uncommitted, without a verified review.

## Strategy 4 research specification

Disable position adds only. Preserve Strategy 3 entry, reentry, session-origin
activation, initial sizing, stop/target management and session exits. Continue
protection confirmation before declining adds, with a runtime guard against
direct add submissions. Preserve all preceding numbered releases.

YJ's nine August 19 premarket episodes netted -$780.31. Allocation of the
observed fills to their actual exits attributes about -$246.83 to seed lots
and -$533.49 to add lots. One winning episode benefited from adds, so removing
adds has an upside cost as well as a risk hypothesis. These allocations are
not counterfactual P&L: only a complete shared-cash rerun can test the change.
The worst YJ loss lasted 6.4 seconds without crossing a completed 30-second
boundary; loosening the 30-second trailing rule would not address that trigger.
Read-only mechanics evidence is retained at
`strategy-optimization-20260930/yj-next-increment-mechanics-research.json`.

Strategy 4 implementation validation: 274 execution/journal regressions,
97 configuration/publication/saved-reader checks and 102 integrated checks
passed before publication. Tests preserve Strategy 3 protection state while
blocking Strategy 4 add submission before journal or Portfolio side effects.

Risk diagnostic correction: closed-episode drawdown omits unrealized swings.
Verified broker-match V5 snapshots for the completed baseline sessions contain
complete cumulative marked-equity extrema: PM August 18 $2,964.00312; PM
August 19 $3,451.01126; AH August 19 $662.40217. Root/child hashes and terminal
committed cursor were checked; Keeper was not independently attested in this
diagnostic. A separate scoped report field is planned; do not overwrite the
older reports or confuse this metric with a synchronized equity curve.
Evidence: `strategy-optimization-20260930/diagnostic-findings-v1.json`.

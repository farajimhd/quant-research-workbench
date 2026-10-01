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

## Strategy 4 publication and execution continuity

Published 914 normalized nodes from source commit
`90bfb9cabc9e73b16d808a38c0edad63a076b093`, configuration
`strategy-one-4:192cc325-9e9b-473d-913d-619b8c5665a7`, payload hash
`12eaccdc9c86a63b8534430ddd5fa0a6f671a71dce70a6ab512fb58f6a4a6b56`.
August 19 PM run `85c73e70-7db7-4b85-828c-3414a599df0f` completed with
32 trades, -$1,548.96358 net, $301.715 fees and $1,878.332645 closed-episode
drawdown in 58.481 execution seconds. The backend was restarted and its
history API confirmed the saved Strategy 4 review is available.

A concurrent task committed transport change `4a6b50a91`. Two subsequent
laptop preflights correctly rejected source-fingerprint drift before creating
runs. Execution was moved to the already-pinned workstation checkout
`D:/TradingML/codes/quant-research-workbench-strategy4-90bfb9cab`; both attempts
failed before processing market rows because the original workstation listener
probe reported no reachable private IPv4 endpoint. Their terminal persistence
also lacked an initialized manager checkpoint. Preserve these attempts as
incomplete infrastructure failures, not financial results:
`722c2daf-5a24-49bf-9470-32564d28f3d5` (PM18) and
`e88500bb-4719-4e8d-9cc5-1f31154be363` (AH19).
Bounded CLI diagnostics are saved under `strategy-optimization-20260930`.

To retain the exact approved source without reverting concurrent work, a
managed laptop checkout was created at
`C:/Users/g835l/.codex/worktrees/strategy4-reproduction/quant-research-workbench`,
pinned to `90bfb9cabc9e73b16d808a38c0edad63a076b093`. Remaining Strategy 4
runs use that checkout. Generated artifacts remain under the runtime root.

## Exit and liquidity hypotheses

The user asked to examine 30-second trailing, rising targets during pullbacks,
failed resistance, and declining liquidity/trade counts. A verified 58-position
premarket audit found 107 acknowledged completed-30-second-low stop updates,
17 three-resistance updates and 50 target increases. YJ's worst loss used the
unchanged initial stop (reference ask 6.03, stop 5.43); its two winners used
30-second trailing. Its three target increases did not precede an observed
touch of the original targets, so they do not explain those particular losses.
The target rule can rerank on any price-bearing 100 ms bar without a new
resistance break; that is a testable design choice, not a proven execution bug.

No explicit declining-activity or failed-resistance exit exists. A descriptive
12-position sample found a past-only five-second volume/trade-count decline
with later trading opportunity in three of six losers and four of six winners.
This is not a discriminating exit rule, and later volume does not guarantee
liquidation at an assumed price. Exact evidence and limitations are in
`entry-risk-and-acknowledged-protection-audit-v1.json` and
`liquidity-fade-diagnostic-v2.json` under the campaign runtime directory.
The earlier liquidity v1 artifact is retained but superseded by v2's exclusion
of the entry-straddling one-second bucket.

## Strategy 4 completed comparison

| Session | Run | Closed | Net P&L | Closed-episode max drawdown | Execution seconds |
|---|---|---:|---:|---:|---:|
| August 18 PM | `4556ef68-92fb-4434-97fb-aa6410139e58` | 26 | -$340.41 | $946.68 | 49.871 |
| August 19 PM | `85c73e70-7db7-4b85-828c-3414a599df0f` | 32 | -$1,548.96 | $1,878.33 | 58.481 |
| August 18 AH | `afae0755-80d0-4be8-b1b7-4d7747bec436` | 11 | $53.44 | $619.70 | 31.758 |
| August 19 AH | `c3982672-a27c-4df9-94f3-fdd77c7533d2` | 10 | -$319.13 | $526.29 | 34.930 |

All four completed flat with zero journal-writer failures; reports are under
`strategy_one_research/strategy4_sessions_v1`. The backend was refreshed after
saved results and the app API confirmed all four completed reviews. Removing
adds improves the sum of independent-session P&Ls by $1,079.713165 versus
Strategy 3, but the sum remains -$2,155.05846. This sum is not a compounded
multi-session account return. Both August 18 windows lose profitable add
contributions; both August 19 windows improve. Closed-episode drawdown falls
in every window. Retain the no-add ablation as a useful control, not a profitable
strategy or a claim that all adds should permanently be removed.

## Strategy 5 research specification

Starting from Strategy 4, disable only subsequent completed-30-second-low
stop trailing. Retain the initial completed-30-second-low stop, three-resistance
stop steps, all target behavior, no-add policy, entry/reentry, sizing and session
constraints. This directly tests whether fixed-duration bar lows cut useful
pullbacks too early. It may also retain losers longer and increase drawdown;
the earlier audit does not establish that the trailing rule is defective.
Do not combine this test with target gating or a liquidity-fade exit. Those
remain separate hypotheses requiring their own executable causal evidence.

Strategy 5 execution validation passed 291 tests across 20 files; the
configuration/report lane passed 115 combined checks. The prior numbered
release/compiler/publisher files remain unchanged. The new read-only report
projection preserves old outputs, attributes exits through exact causal journal
sequence and command/intent lineage, and reports broker-observed drawdown
separately with asynchronous/stale-mark limitations. Saved-performance cache
keys include the new projection version.

## Strategy 5 completed comparison and Strategy 6 hypothesis

Published 920 normalized nodes from source commit
`17dbfc517ddc39db6e22ff6af4c560ca8de3ae51`, configuration
`strategy-one-5:6005fe00-ed35-4ff0-901f-21bee324b968`, payload hash
`4eed607b0a66740b90fe2e66508e23adb32f17660bd266cad9c5e7b6e5eb92ca`.
All four full sessions ran from the pinned laptop reproduction checkout,
completed flat, and published with zero journal-writer failures. Backend
restarts and the history API confirmed all four saved reviews are available.

| Session | Run | Closed | Net P&L | Closed-episode DD | Broker-observed DD | Execution seconds |
|---|---|---:|---:|---:|---:|---:|
| August 18 PM | `4f4ec7a1-658e-44d6-9c9c-b392c501ec62` | 19 | $70.63 | $1,074.34 | $1,878.41 | 59.196 |
| August 19 PM | `53f255ce-4a13-42cd-b8e0-699fb6bbe61a` | 26 | -$1,353.12 | $2,731.26 | $2,707.89 | 73.316 |
| August 18 AH | `96345a55-b8a4-47fa-acd8-c4c25f1366f6` | 11 | -$800.33 | $1,168.99 | $1,387.10 | 40.427 |
| August 19 AH | `57f21ce3-196a-41ec-ae6f-af10657009d9` | 7 | $166.12 | $291.66 | $828.32 | 50.367 |

Independent-session net sums improve from Strategy 4's -$2,155.05846 to
-$1,916.69530, a $238.36316 gain, but broker-observed drawdown worsens in
every session. Strategy 4's corresponding broker-observed values were
$1,531.58 / $2,254.23 / $842.37 / $596.13. These asynchronous extrema retain
stale marks without an age limit and are not synchronized liquidation equity.
Removing fixed-bar trailing alone is therefore not an accepted improvement.
Longer holding increases processed market rows, while execution remains under
74 seconds per session in this run; fewer journal events do not imply less
market evaluation work. Keeper disconnect warnings occurred, but terminal
completion, journal writes and subsequent verified report reads succeeded.

Immutable Strategy 5 reports are under
`strategy-optimization-20260930/strategy5_reports_v4`. The separate
`verified_reports_v4/refresh-index.json` records refreshed reports for all
14 prior completed runs (300 positions), zero unavailable final exit reasons,
unchanged economics and saved contexts, and old/new source hashes. One
attestation read failed closed and succeeded on a fresh-process retry.

Strategy 6 will inherit Strategy 5 and freeze only the initial profit target.
Initial protection, structural stop ratchets, the working-target stop ceiling,
entry/reentry, sizing and session policies remain unchanged. This isolates
target escalation without introducing resistance-failure or liquidity exits.
It can truncate large winners and alter later cash allocation; neither the
earlier YJ audit nor these aggregate results establish that target escalation
is defective. All results remain development evidence from two dates.

Strategy 6 execution checks passed 304 tests across 22 files, plus three
registry checks. Configuration/publisher/report checks passed 123 tests;
root integration checks passed 129. Review also extended the approved-source,
extended-session-window and disabled-public-resume guards to number 6;
66 focused checks passed after that correction. The real OMS test retains
the initial target and fills only on a later certified liquidity bucket.
Prior numbered release/compiler/publisher files remain unchanged.

## Strategy 6 completed comparison

Published 925 normalized nodes from commit
`2d7544338e56c3b199e43799807aa18e84bbdcfc`, configuration
`strategy-one-6:9f77185d-6c87-4597-93ba-a6092bbedace`, payload hash
`3c80cce551e59d566bacf5cc21a25c6097574dfb44a3893b31e6e0dbf8206844`.
Runs used the pinned laptop `strategy6-reproduction` checkout. All completed
flat with zero journal-writer failures. The restarted backend's history API
confirmed all four saved reviews. Report timestamps verify extended-window
entries/exits; certified market plan/build identities match Strategy 5.

| Session | Run | Closed | Net P&L | Closed-episode DD | Broker-observed DD | Execution seconds |
|---|---|---:|---:|---:|---:|---:|
| August 18 PM | `1a37b878-3c5f-448f-a083-feecd01a5c1a` | 22 | $714.84 | $1,142.99 | $1,521.83 | 51.585 |
| August 19 PM | `ea8fbc28-6b5c-41eb-a5eb-b94df568172a` | 27 | -$1,157.81 | $2,568.91 | $2,534.86 | 66.840 |
| August 18 AH | `0b78f130-54a9-43c2-8773-03257af62a66` | 12 | -$657.73 | $782.35 | $976.55 | 38.022 |
| August 19 AH | `69b0e779-09f1-45f7-a2ad-81d785e1b4ce` | 7 | $610.89 | $320.90 | $703.24 | 46.190 |

Fixed initial targets improve net P&L and broker-observed drawdown in all four
windows versus Strategy 5. Independent-session net sums improve $1,426.89153
to -$489.80377. Closed-episode drawdown rises in PM18 and AH19, illustrating
why different drawdown definitions must not be conflated. Strategy 6 still
loses money overall; no repeatable edge is established. Preserve the prior
reports and failed/inferior trials. Evidence is under
`strategy-optimization-20260930/strategy6_reports_v4`, with hashes, timing,
flatness and window checks in `strategy6-four-session-manifest-v1.json`.

## Strategy 7 research specification

Inherit Strategy 6 and restore only subsequent completed-30-second-low
trailing. Keep fixed initial targets, no adds, structural stop ratchets,
entry/reentry, sizing and session controls unchanged. This completes the
four target/trailing combinations: Strategy 4 moving/on, 5 moving/off,
6 fixed/off, and 7 fixed/on. The purpose is to test interaction, not assume
that either trailing choice is universally superior. Separate failed-resistance
and liquidity exits remain unimplemented hypotheses.

Strategy 7 validation passed 391 execution checks across 24 files, 134
configuration/report checks and 162 root integration checks. Prior sealed
release/compiler/publisher files remain unchanged. Strategy 6's detailed
comparison is `strategy6-vs5-four-session-comparison-v1.json`: the same-price,
same-quantity MSS entry gained from a 407-share target fill before its final
602-share stop. Final exit labels alone do not describe partial-exit economics.

## Strategy 7 completed comparison and research checkpoint

Published 925 normalized nodes from commit
`849070be672bb0f44049a48b48a2d8a397cc1240`, configuration
`strategy-one-7:b536692f-9ef6-4f49-befd-a228bd073d4f`, payload hash
`7e97fbb6eec5980383c46765f3b3725982b4965448022893a6be9e2e038d1cfa`.
All four pinned-laptop sessions completed flat with zero writer failures.
Restarts and the app history API confirmed all four saved reviews. Verified
reports and `strategy7-four-session-manifest-v1.json` retain source hashes,
timing, matching certified market plans and extended-window timestamp checks.

| Session | Run | Closed | Net P&L | Closed-episode DD | Broker-observed DD | Execution seconds |
|---|---|---:|---:|---:|---:|---:|
| August 18 PM | `6821c5b5-40fd-4362-8710-bcf3a98d7d5f` | 29 | -$293.84 | $959.67 | $1,171.60 | 51.721 |
| August 19 PM | `02f0d9f8-f62d-4ae6-b398-16efc27a4c20` | 33 | -$1,476.33 | $1,895.88 | $2,173.89 | 53.805 |
| August 18 AH | `61ca1dcf-cee0-49b0-afbc-58edb3a34c5c` | 12 | -$352.89 | $502.92 | $726.58 | 31.144 |
| August 19 AH | `c01c54a5-f0d2-4beb-9bff-f2a467ad9c3b` | 10 | -$319.13 | $526.29 | $596.13 | 30.677 |

Strategy 7 sums to -$2,442.189585, $1,952.385815 below Strategy 6, despite
lower broker-observed drawdown in every window. It improves P&L only in
August 18 AH versus 6. Across the controlled target/trailing matrix, independent
session sums are Strategy 4 -$2,155.06; 5 -$1,916.70; 6 -$489.80;
7 -$2,442.19. Therefore retain 6 as the P&L research reference and 7 as a
lower-drawdown comparison, not as accepted profitable strategies. Fixed targets
helped without the fixed-bar trail but did not universally help with it.

Next work should diagnose Strategy 6's initial-risk and failed-resistance
losses before selecting another single change. A branch from 6 must record
that explicit parent rather than pretend to inherit 7 or change prior seals.
The naive liquidity-fade signal remains unaccepted because it also triggers
on winners. No Strategy 8 implementation or publication exists at this
checkpoint. Both reusable subagents completed; no child processes remain.
The research goal remains active and additional certified dates are pending
the user's notification. Task-history CSV updates have not been requested.

## Strategy 8 research specification

The initial-risk audit matched 68 filled Strategy 6 episodes to 69 typed entry
proposals (one unfilled). Winners had wider median initial stops than losers
(10.44% versus 6.21%), so a width-only rejection is not supported. CDTG's
$663.16394 loss reconciles to $444.44 original reference stop risk, $183.96
adverse acquisition, $23.34394 unchanged-stop fill gap and $11.42 fees. Its
filled average was 4.85% above the original reference ask. Entry chasing also
occurred in winners; this is an execution hypothesis, not counterfactual P&L.
Immutable evidence: `strategy6-initial-risk-summary-v1.json` and
`strategy6-entry-slip-stop-gap-audit-v1.json` under the campaign runtime root.

A separate position-local certified-bar/V7 observer found downward crossings
in 39/50 losers and 15/18 winners; fresh quotes qualified 36/50 and 15/18.
Frozen geometry and contiguous completed seconds were checked. This does not
reconstruct original manager acknowledgements or prove later executable fills.
The overlap rejects adopting that simple signal without further evidence.
Retain `strategy6-failed-resistance-observer-v2.json` and its terminal summary.

Strategy 8 explicitly branches from Strategy 6, not Strategy 7. Change only
entry/reentry acquisition: set the existing typed execution envelope's maximum
buy price to the original proposal reference ask. No discretionary percentage
is fitted. Preserve adaptive urgency, persistence until cancelled, completion
of partial fills, sizing, costs, initial stop, structural stop ratchets, fixed
initial target, no adds and extended-session controls. Thus an unfilled order
may wait for a pullback; measure delayed entries and missed winners as well as
price improvement. Shared Portfolio/OMS remains the execution authority and
fills require later certified liquidity. No per-row screening loop or new
producer product is introduced. Publication and four-session results remain
pending at this specification checkpoint.

Strategy 8 integration validation passed 206 release, intent, real OMS/broker,
normalized cold-read, session-window, registry and source-guard checks, plus
67 related execution, management, static-gate and prior-release checks. The
cap test supplies certified price-level fixtures: rising prices cannot fill
or reprice above the original ask, a later pullback can partially fill, and
the remaining acquisition remains capped. Cold OMS recovery retains the cap.
The real loader already joins the pinned certified execution-price product;
no intrabucket distribution is inferred. Public resume and live remain closed.

## Strategy 8 completed comparison

Published 931 normalized nodes from commit
`833ce27687fcec5f90cd8602533409235f5e40ac`, configuration
`strategy-one-8:bb1f983a-869f-4b70-b3f3-44c9032bded8`, payload hash
`d642e40bcee00bdb047d15376aea5d70796d58b49b4f153b97ca0d5549202421`.
All four pinned-checkout sessions completed flat with zero writer failures.
Reports retain the same certified market-plan/build identities as Strategy 6;
all position timestamps fall within the requested extended-session window.

| Session | Run | Closed | Net P&L | Closed-episode DD | Broker-observed DD | Execution seconds |
|---|---|---:|---:|---:|---:|---:|
| August 18 PM | `1a297325-7e8e-47f7-b7f8-7648c5768003` | 22 | $995.65 | $958.58 | $1,341.75 | 53.468 |
| August 19 PM | `824e6d34-bf39-4e72-bb84-a7dff6cdbcb0` | 27 | -$1,011.17 | $2,444.93 | $2,460.48 | 67.637 |
| August 18 AH | `81599cf6-7a5d-4032-8dbc-41c99d2afe98` | 12 | -$558.27 | $740.37 | $884.58 | 35.652 |
| August 19 AH | `972dc45c-5501-4088-a3b2-d892c6fcf781` | 7 | $610.89 | $320.90 | $703.24 | 43.962 |

Independent-session net sums improve $526.904025 to $37.100255. P&L and both
drawdown measures improve in three windows and remain unchanged in AH19.
The largest ticker contribution is CDTG +$195.97; AIXC contributes +$65.44.
This is near break-even with material drawdown, not an established edge.
Execution remains comparable to Strategy 6; these single-run timings are not
a controlled performance benchmark.

`strategy8-four-session-comparison-v1.json` retains report/log hashes and
ticker deltas. `strategy8-entry-cap-audit-v1.json` cold-verifies all 69 typed
entry proposals and matches 68 filled episodes: every weighted acquisition
price is at/below its original ask. All but one episode share their parent's
ticker/first-fill time; no broad delayed-entry or missed-winner claim follows.
This audit does not independently check each individual fill. Reports are in
`strategy8_reports_v4`; no prior artifacts were overwritten.

App verification caught the history SQL whitelist still ending at 7. The
read-only inventory was extended to 8 with a query-level regression across
all eight numbers (17 tests passed). This changes saved-run visibility only;
Strategy 8 execution stays reproducible from its original pinned checkout.
Backend restarts followed the code increment and each saved backtest.
The goal remains active. Keep Strategy 8 as the next research reference,
preserving Strategy 6/7 comparisons and all other trials. Strategy 9 has not
been specified or published; diagnose remaining losses before another change.

## Development target and Strategy 9 rule checkpoint

The user raised the development objective to **at least +$500 net in each
of the four sessions**, with $10,000 starting cash, capturing big moves and
exiting early when follow-through fails. Strategy 8 meets two windows;
PM19 requires $1,511.174935 improvement and AH18 $1,058.26994. The active
goal includes the full four-session target and preserves additional-date
validation as the boundary for any repeatable-edge claim.

Read-only probes use certified saved market plans and completed bars/MACD,
plus exact co-terminating quote freshness. Original proposal ask supplies the
entry reference, avoiding future final-acquisition VWAP. A simple below-entry
negative MACD exit would also flag YJ and BTCT before their large gains.
The half-original-stop-distance condition narrows the signal: at completed
five-second boundaries, negative five-second MACD plus this price loss has
fresh-quote-qualified observations in 24/50 losers and 4/18 winners, excluding
the six largest winners. This diagnostic is an observed-position screen,
not a simulated exit, subsequent-fill proof or P&L prediction. Preserve
`strategy8-early-failure-probe-v1.json`, `v2.json` and `v3.json`; v3 SHA256
`b857b44f2b886bf15bcd3bf3f06126cc6843425e01aed153a3ab9611149c7ca7`.

The next single change branches from 8. A held position may propose an exit
at an exact completed five-second boundary when its completed five-second
close is at/below `(original_reference_ask + initial_stop) / 2`, completed
five-second MACD line is strictly below signal, and the current fresh bid
still satisfies that loss threshold. Require a whole five-second bucket
after the first filled bucket, quote age at most 1,000,000 microseconds,
positive remaining quantity and no pending exit. Missing data/quotes skips
that proposal; existing broker protection remains authoritative. No delayed
retry based on stale evidence. Existing session liquidation takes precedence;
normal broker consumption happens before strategy evaluation and any new exit
must wait for later certified liquidity. Initial stop, targets, sizing, costs,
entry cap and structural ratchets inherit 8.

`strategy_followthrough_failure.py` implements only the pure predicate and
scalar witness. Twenty-one checks cover missing/forming inputs, first-held
bucket exclusion, quote recovery and exact freshness, positive/negative
momentum, half-distance boundary and future-tail independence. It is not
registered or connected to orders. Before publishing number 9, implement its
normalized source witness and cold-read validation, persist its first-held
boundary in the manager contract, route it through shared Portfolio/OMS,
validate partial-fill acquisition cancellation and protection precedence,
and seal the complete release. Four-session execution is still pending.

## Strategy 9 implementation checkpoint

The single-change release now derives from the exact published Strategy 8
revision and payload hash. The manager evaluates only held positions at
completed five-second boundaries; shared Portfolio and OMS own liquidation,
including cancellation of a partially filled entry remainder. The normalized
`trading_followthrough_failure_v4` witness binds the immutable exit to its
original typed entry, prices, assignment and committed ancestor chain.
Manager snapshot v3 and its first-held child preserve the first filled bucket
across recovery without altering occupied v2 schemas. Older releases retain
their existing rules and checkpoint row shapes.

Review corrected a quote-age unit mismatch before publication: the rule now
accepts the full specified 0..1,000,000 microsecond interval. Explicit edge
checks cover both sides of the one-second boundary. A 320-test integration
selection passed, including manager routing, cold witness graph recovery,
checkpoint publication and shared OMS partial-entry cancellation. Database
layout/grant acceptance, publication and four certified session runs remain
pending; these checks establish implementation behavior, not profitability.

## Strategy 9 publication and dated scope repair

Strategy 9 was committed and pushed as `adb2f3d2f64d19385f4125a8d4beb21e1cd43a8c`.
Its immutable configuration is
`strategy-one-9:a9249a01-2049-4745-a61c-8e245a9be127`, payload
`76965e35cd33a9bfdd1f7d6ad1ad5b41efd18084d03d946c4524a0e273715293`.
The new witness and manager checkpoint tables were installed through managed
layout commands; exact runner grants and SSD placement passed before launch.
The backend and frontend restarted successfully.

Both PM launch attempts stopped before creating a run: a newly introduced
dated LGHL exclusion changed global candidate/activation/HOD tokens, while
occupied entry coverage still correctly pinned its original full-source
tokens. This is a certification mismatch, not Strategy 9 P&L evidence.
Preserve `strategy9-PM18-v1.log` and `strategy9-PM19-v1.log` as failed preflight
attempts. No Strategy 9 session result exists.

The user explicitly chose to apply LGHL exclusion and repair certification.
Read-only candidate certification proves LGHL has zero candidate boundaries
on both dates (empty-row hash
`533f299d0a601074820f3339b1fd12347da5bdff14e3c2a700004ba67fd2693a`).
The repair verifies the original full candidate, activation, HOD and entry
products, then binds an exact empty-candidate scope projection and exclusion
list into a distinct execution entry token. It neither overwrites producer
coverage nor certifies missing structural data. Retained candidate arrays,
activation prices and HOD contexts must match exactly; removal of nonempty
candidate boundaries fails closed pending a separately certified product.
Live read-only August 18 preflight now reports both V7 seed and entry evidence
ready. Strategy 9 source remains immutable; Strategy 10 will pin this repaired
source while inheriting the same early-failure trading behavior.

## Strategy 10 results and next failure-window experiment

Strategy 10 was committed/pushed as `95865abbeb066780dbbfdacdca1fc1c55010bac3`,
published as `strategy-one-10:e3ba035b-bad3-483f-b446-0be227ef9831`, payload
`2298535fcef63b1a0b81a9ed2a1ddaff33a40f4cfe2a04f078e0eeca470c405e`.
All four full-universe app-route runs completed with $10,000 initial cash,
zero open lifecycles and zero failed journal units. The backend was restarted
after saved runs, and the app history exposes all four completed V4 reviews.
Actual position timestamps were converted to America/New_York before checking
the PM/AH windows; no regular-hours entry or exit was observed.

| Session | Run ID | Net P&L | Broker-observed marked drawdown |
|---|---|---:|---:|
| PM18 | `bdbd0487-bc09-4a97-a450-a25fd4a9a792` | 795.08018 | 1123.26780 |
| PM19 | `5a6b99ed-ad8e-4978-94b4-c4cb00bdfe4e` | -832.29078 | 2365.25656 |
| AH18 | `16849ab4-3576-4a5b-b4a4-c41966717e08` | -464.698265 | 938.20319 |
| AH19 | `e7ba3b14-5c05-4b4a-93c2-b9269d352389` | 714.47128 | 710.00055 |

Total net is 212.562415, up 175.46216 from Strategy 8, but only two sessions
reach the $500 objective. AH drawdown increased despite improved AH P&L.
Drawdown uses asynchronous broker-observed marks that can be retained without
an age limit; it is not synchronized equity or guaranteed liquidation value.
The comparison verifies identical retained market build/plan tokens versus 8;
the new exclusion scope separately binds LGHL's certified zero candidates.
Immutable comparison and per-position reports are under
`D:/TradingML/runtimes/strategy-optimization-20260930/strategy10-four-session-comparison-v1.json`
and `strategy10_reports_v4`. Float remains unavailable without a dedicated
as-of reference reader; reports include completed entry volume/trade counts.

Read-only entry diagnostics reject broad positive-MACD and minimum
reward-to-risk gates as the next experiment: both screen out meaningful
winners. The rising 10s histogram screen rejects 21 historical losers and
three winners, including PFSA +754.34, so it also risks damaging big moves.
Evidence files `strategy10-entry-momentum-diagnostic-v1.json` (SHA256
`59b4db741809e4d522447f4a834230563d4cd1b8aa2f14b95fad7f7dd6d3fedb`)
and `strategy10-entry-reward-risk-diagnostic-v1.json` (SHA256
`a42d9f5839271a470cdea32f414a15b9989bedc906c9ef3510271b037b6aa100`)
use exact completed producer indicators and original typed proposal prices.
Their outcome-selected screens are not simulated counterfactual returns.

XOS PM18 lost 488.009745 versus 8, mainly because two same-time entries exited
on failure after approximately 255 and 155 seconds, whereas 8 later closed
those holdings for +91.89823 and +231.20. Five additional XOS entries explain
only -46.844765 of observed P&L. A stricter reentry rule therefore does not
address the dominant observed damage. Reentry evidence is retained as
`strategy10-reentry-xos-evidence-20260930T220441Z.json`, SHA256
`7b022e5227d90b4ce52fbfe793718584ee2c2fbd61cad993ce509969dac7d6bb`.

The next single-change experiment derives from exact Strategy 10. Limit its
unchanged half-original-stop-distance/negative-completed-5s-MACD/fresh-bid
failure exit to the first **60,000 ms inclusive after first held boundary**.
No exit follows from elapsed time alone; later positions retain existing
broker stop, fixed target, structural ratchets and session liquidation.
First held means the first completed broker bucket containing quantity,
not order submission, signal time or final fill VWAP. Reuse the existing
normalized witness fields and checkpointed first-held state, with a new
number-specific source seal. Missing data skips only the current proposal.
The 60s bound is a fixed research hypothesis, not a selected optimal value.

In the older observational Strategy 8 probe, the narrowed window qualifies
17 losers and one winner (WFF), versus 24 losers/four winners without the
window. That probe does not establish whole-post-fill-bar and current-bid
parity with actual exit execution, nor predict P&L. Only a new full-session
Strategy 11 run can establish the resulting allocation and fills.

`strategy_early_followthrough_failure.py` contains the pure, unpublished rule;
37 focused tests passed across it and the unchanged original rule. Strategy
11 is not registered or published yet. Its manifest, runtime dispatch,
normalized source sealing/cold recovery and four-session results remain
required before adoption. The four-session goal stays active.

The Strategy 11 implementation checkpoint now includes its exact Strategy 10
parent, new manifest and source proof, exclusive manager dispatch, and the
inclusive age guard before runtime admission and at normalized persistence/
cold-recovery boundaries. It reuses the occupied scalar witness and v3
first-held checkpoint schemas; no database migration is required. Old 9/10
predicates retain unbounded eligibility. A 316-test integration selection
passed, including original releases, forged late sources, manager restoration,
shared Portfolio/OMS routing, source-guard mutations and normalized journal
recovery. Publication and four actual session backtests remain pending.

### Strategy 11 publication, results and entry audit (2026-09-30)

The preceding implementation checkpoints are superseded by completed publication
and four full-session runs. Exact source commit:
`6c660db9a9b18a7a7ee9a450f434d023c335057e`; configuration
`strategy-one-11:895d462c-df55-41df-95a4-b066c14cc6e9`; payload SHA256
`6af195caed51284948e8809e53d0b679ab79b8064ff1fa736f3733d05a150dc2`.
All four start independently at $10,000, remain extended-hours only, exclude
LGHL under the repaired certified scope, finish flat, and expose V4 review
in the application. Backend/frontend managed refreshes completed after saves.

| Session | Run ID | Net P&L | Change from 10 | Broker marked drawdown |
|---|---|---:|---:|---:|
| Aug 18 PM | ea12956f-315c-41d7-91ab-bd1fdf0263d4 | +1296.44093 | +501.36075 | 1207.076355 |
| Aug 19 PM | 73f51391-9285-47db-9b3e-f88b3116b860 | -885.27167 | -52.98089 | 2378.30708 |
| Aug 18 AH | 6ff8c4d9-a716-4d03-b0f3-21c81670746e | -502.15300 | -37.454735 | 974.737925 |
| Aug 19 AH | ca9852eb-30b2-4548-aaca-a1374795fb98 | +714.47128 | 0 | 710.00055 |

Total net +623.48754; two of four sessions exceed +500. Strategy 11 improves
aggregate P&L but worsens observed marked drawdown in three sessions; it is
not an accepted all-session solution. The asynchronous retained-mark limits
above still apply. Four-session comparison SHA256:
`9b418c6d27bfcb8adcbbd5752de8fd4ab183003b6b5c2dc1585878a61de2c779`.
Reports are under the campaign runtime root `strategy11_reports_v4`;
`strategy11-app-history-verification-v1.json` verifies application availability.

The user's Strategy 10 VTIX example was audited before another rule change.
On Aug 19 AH, proposal 17:42:45.2 ET filled 1,541 shares at 17:42:45.3 for
2.36; stop 2.21 and third-overhead target 2.66 match certified producer facts.
Completed 1s BOS occurred at 17:42:34, closing 2.285 above the confirmed
2.27 swing high. The supported break persists; a green entry candle and
clearing every visible V7 resistance are not requirements. Entry was inside
an unchanged resistance band 2.34909–2.38835, consistent with those rules.

The 161 earlier nearby static candidates lacked the required persisted
late-HOD V7 gate, so their protection/entry was invalid; ten also closed at
or above prior HOD. Completed 5s MACD was bearish at 17:42:40, briefly bullish
at 17:42:45 (histogram +0.00005108), and bearish again at 17:42:50. The actual
proposal passed all four completed MACD gates. The 22 subsequent static
candidates through 17:42:49.8 occurred while holding the position; adds are
disabled. Failure exit filled 17:43:00.1 at 2.28, net -138.69 including fees.
This is evidence of late-entry weakness rather than an identified fill bug.

Cold source checks recertified original candidate, activation, pivot, HOD,
entry content, prior-day seed and causal V7 interval seals. Independent
necessary gates, proposal clocks, nearby protection geometry and late-HOD
admission reconcile. VTIX's Aug 18 seed has 116 levels; the covered stream
includes 238 valid completed RTH price seconds before AH. Charts label
candles/indicators by bucket start: at proposal the completed 1s sample ends
17:42:45 and plots at 17:42:44; candle 17:42:45 is still forming.

The acquisition audit covers all 69 Strategy 11 positions / 230 individual
BUY fills plus the Strategy 10 VTIX fill. Zero configured-model violations
were found in source/command/fill causality, later-bucket activation, original
ask caps, successful reprices, initial protection, certified execution prices
and volume budgets. Maximum first-fill delay is 5.1 seconds; partial fill
delay is 31.9 seconds. Real exchange queues, sell/stop execution and independent
recomputation of producer pivot derivation remain outside this audit.

Authoritative artifacts under `D:/TradingML/runtimes/strategy-optimization-20260930`:

- `strategy10-vtix-entry-audit.md`: concise timeline and audit limits.
- `strategy10-vtix-market-entry-audit-v1.json`, SHA256
  `014dbf74a74858cfdcc89b00b902cbeea01df70b9a076098e1114ab44084c372`.
- `strategy10-vtix-source-geometry-audit-v3.json`, SHA256
  `c873653c7c25d0773ad58687a4b0994e5378951204148f95f882b388ea325d8f`;
  supersedes v2's nonspecific late-HOD reason labels.
- `strategy11-entry-execution-audit-v4.json`, SHA256
  `447dd2399bd2ad1c8710e3066588e196cd21582452bd3945e97603c35c26542a`;
  v1–v3 retained but superseded after fixing audit decimal/clock conversion
  and incorporating valid preceding limit reprices.

No runtime trading rule changed during this audit. The next incremental
hypothesis should address weak or late momentum admission, measured against
all four sessions and big-move winners. The +500-per-session goal stays active;
repeated development on these two days still requires new certified sessions
for independent validation.

### Strategy 12 single-change specification (2026-09-30)

The next research increment inherits exact Strategy 11 configuration
`strategy-one-11:895d462c-df55-41df-95a4-b066c14cc6e9`, payload SHA256
`6af195caed51284948e8809e53d0b679ab79b8064ff1fa736f3733d05a150dc2`.
Its sole change is entry/reentry admission: the supported completed 1s BOS
must be at most **30,000 ms old inclusive** at the completed 100ms proposal
boundary. Missing BOS rejects; malformed/future clocks fail integrity
validation. An existing holding is unaffected by break age. Preserve the
60s early-failure exit, all existing sizing/costs, session policy, ask cap,
fixed target, structural stops, no adds and LGHL scope certification.

The rule uses existing producer-certified scalar timestamps and one native
columnar comparison before survivor scheduling, plus the same scalar rule at
sequential financial admission. It adds no indicator, structure calculation,
source query or mutable checkpoint field. Publication still requires exact
source sealing, registered guards, real-route tests and full-session runs.

Screening Strategy 11's 69 actual proposals found that the 30s age rule
rejects 11 losing positions and zero winners, with rejected historical net
-744.787185. This is not a counterfactual return: later candidates, allocation,
reentry and fills can change. A 5s maximum rejects nine winners; shorter
squeeze-episode lifetimes also reject major winners. Broad rising 1s/5s
histogram gates damage existing winners. Other momentum hypotheses remain
research candidates, not inferred profitability claims.

Read-only source diagnostics are under the campaign runtime root:
`strategy11-entry-momentum-diagnostic-v1.json` (SHA256
`e8cd58083063f5702d94b51a414e3a26452f642f7eb35b8636c73fca8c1f076f`)
and `strategy11-entry-phase-diagnostic-v1.json` (SHA256
`dcecb6a3c7c22c528c16ce242f9f4fedfebc60ccb6f76a9b6886e34534026783`).
Initial pure/static baseline selection passed 40 tests. A synthetic one-million
candidate native rule pass took 12.9–13.5 ms over five repetitions; this is
rule throughput only, not evidence of full-backtest runtime. Strategy 12 is
not yet published or backtested at this checkpoint.

The complete certified source population audit covers 147,362 candidate facts
across both dates under the authorized LGHL exclusion. Strategy 12 changes
only the recent-BOS rejection bit: PM survivors reduce 1342 to 1215 on Aug 18
and 1943 to 1768 on Aug 19; AH survivors remain 248 on Aug 18 and reduce 364
to 359 on Aug 19. Other rejection bits are identical. Compiling the added
rule increases the measured whole static-gate pass by approximately 7–8 ms
per date. Artifact `strategy12-certified-static-population-audit-v1.json`,
SHA256 `5edcc67c44d1cdc51fcdc552239f861c1b5e1926730c177f34ef714561e83b32`.
Its first audit-script attempt failed on a NumPy uint8/negative-complement
conversion before producing results; the explicit positive bitmask rerun
passed. No source product or backtest was changed by that diagnostic.

Strategy 12 implementation acceptance: 403 focused integration tests passed
in 39.26 seconds, covering native/scalar parity, old-number controls,
activation/session rules, actual coordinator dispatch, release/source mutation
checks, forged stale entry rejection before Portfolio, raw normalized entry
sealing, cold entry/source recovery, inherited first-held failure recovery,
manager snapshots, Portfolio/OMS admission and fixed execution. Live and
public interrupted resume remain closed. No table or grant migration is
needed. Two reused worker lanes completed, with no failed or interrupted
workers and no newly spawned agents in this increment. Publication and four
actual backtests remain pending at this source checkpoint.

### Strategy 12 publication and four-session results (2026-09-30)

Strategy 12 is published from committed/pushed source
`6502720526611e019fe677e436155d82b05bdad2`, source fingerprint
`b48ff006b0bd132b7c0e1c325a563ddb85ba4c53e600e49674c4bbfab4951365`.
Exact configuration `strategy-one-12:322bd3bf-bfd5-4c47-be38-fb77c9f15444`
has 970 typed nodes, payload SHA256
`ad3a9bae8615d43c24d6de4bb8d68869c7aa02cb1512f1c541ddab0586c1fb87`,
certified token `d16ac993442af18b3a52774fa09284576c06509edacae621860a47a63f8bb0cc`.
Pinned clean laptop/workstation reproduction checkouts retain this exact source.

| Session | Run ID | Net P&L | Change from 11 | Marked drawdown | Positions | Execution seconds |
|---|---|---:|---:|---:|---:|---:|
| Aug 18 PM | 314e4e00-410f-4849-97b8-fa09a48a221f | +1394.54187 | +98.10094 | 1218.190215 | 21 | 54.207 |
| Aug 19 PM | 586e144c-65df-4a12-80de-8b5142c27335 | -425.668395 | +459.603275 | 2012.850445 | 20 | 62.447 |
| Aug 18 AH | 1e57886b-e64d-4ea4-a0bc-1984fc529923 | -502.153 | 0 | 974.737925 | 13 | 36.846 |
| Aug 19 AH | 6ded21b1-84bd-4567-9281-01d8f4b71fca | +723.352565 | +8.881285 | 710.71233 | 6 | 47.012 |

Total net +1190.07304, improvement +566.58550 over 11. Two of four sessions
exceed +500; the full objective is not achieved. The worst-session observed
marked drawdown falls by 365.456635, but PM18/AH19 individual marked drawdowns
increase slightly. AH18 is unchanged: no otherwise eligible candidate in that
session was rejected. Drawdown remains asynchronously marked with the previously
recorded stale-mark limits. This is an exploratory improvement, not a validated
repeatable edge or an all-session accepted strategy.

All four runs completed, drained eight journal units each with zero failed
units, finished flat from independently initialized $10,000 accounts, and
match retained market build/plan tokens from 11. Positions/fills are verified
within the selected extended-session windows. Backend/frontend refreshes
completed after source change and every saved backtest. The app history API
returns all four as completed with V4 review available; proof artifact
`strategy12-app-history-verification-v1.json`.

Cold normalized entry auditing verifies all 60 actual proposals, their exact
source links to all 60 filled positions, and the new maximum BOS age. Observed
maximum ages per run are PM18 27000, PM19 24900, AH18 23400, AH19 20700 ms.
Artifact `strategy12-actual-entry-clock-audit-v1.json`, SHA256
`cd326b019a0c9391837ade2ce8fc8b7cc2e3c47b0860d680bc2db679fd861d30`.
The original VTIX case still qualifies because its BOS was only 11.2 seconds
old; its late, weak MACD timing is a separate unresolved entry hypothesis.

Immutable per-position reports and input-volume/trade-count tables are under
`D:/TradingML/runtimes/strategy-optimization-20260930/strategy12_reports_v4`.
The full comparison `strategy12-four-session-comparison-v1.json` has SHA256
`da86a1fc3d4d180d8cce71a2ac2208f57801981da39204282a068a4548812302`.
Float remains explicitly unavailable without a dedicated as-of reference
reader. No current float or uncertified data was substituted. The next
increment must address remaining admission/management losses while retaining
big-move winners; the campaign goal stays active.

### Strategy 12 completed-entry momentum screen

The next read-only screen uses the 60 actual Strategy 12 proposals and their
certified completed 5-second MACD values. Requiring histogram greater than
0.1 times the absolute MACD line would reject 16 losing and two winning
positions, including the VTIX entry. Their historical net totals -1525.600185,
but the rejected winners include BIVI +422.47 and WFF +172.90. Increasing the
ratio to 0.25 rejects five winners, including PFSA +754.34, GNPX +343.825 and
SLE +335.80. Neither screen is a counterfactual portfolio backtest: changed
cash, subsequent admissions and execution can change results. These
development-day screens do not establish an optimal threshold or an edge.

Artifact `strategy12-5s-momentum-strength-diagnostic-v1.json`, SHA256
`23fc6c3f049178a322b6dfc96f99410b8e0ba9d6a0b9886dc3d63c43bb6d6ac3`,
retains all rejected positions and session totals. No published strategy was
changed by this diagnostic. A further numbered increment must preserve
completed-source causality, native vectorized filtering and normalized entry
attestation before testing this hypothesis against the remaining losses.

### Strategy 13 work in progress: rising completed momentum

Selected next hypothesis: retain Strategy 12, with one additional necessary
entry/re-entry condition. At the completed 100 ms proposal clock, the MACD
histogram must be strictly rising on either the latest completed 1-second
bucket or the latest completed 10-second bucket, compared with that
resolution's immediately preceding bucket. An equal histogram rejects that
branch. A missing/null observation rejects only its branch; forming, stale,
nonadjacent or invented-value evidence is an integrity failure. Existing
holding management, sizing, costs, sessions, recent BOS age and V7 warm-up
remain inherited. This is not a holding-age exit or a timed trailing stop.

Exact producer Float64 bits were independently SELECTed under the four
certified saved market plans. Artifact
`strategy12-entry-momentum-diagnostic-exact-v2.json`, SHA256
`a418644c89b216b6b579d77e4d3d8ff128474aa2dac99d5158a9b327f29b76c6`.
The resulting direction screen rejects four losses and zero winners among
the 60 actual Strategy 12 entries: AH18 CAST -135.52 and BIVI -225.61,
PM18 SGLY -138.46, PM19 CAST -67.496. Historical rejected net is -567.086;
it is not counterfactual strategy profit. Artifact
`strategy12-momentum-direction-screen-exact-v2.json`, SHA256
`7931f69ed7dde3925b4a482cd7f7d3154301002acf0b6c5082cd55f3f7292d33`.
This screen preserves the AH18 BIVI winner that the strength filter removes.
It does not reject the original VTIX entry and cannot alone establish the
four-session goal or repeatable edge.

The proposed reusable pure native mask is implemented in
`src/trading_runtime/strategy_rising_momentum_entry.py`. Inputs are aligned
(N, 2) completed source clocks and Float64 MACD line/signal values, ordered
1s then 10s; output is shape (N,). It calculates no indicator and authorizes
no order. Exact-source validation agrees on all 60 entries. Three measured
one-million-row passes took 0.0990, 0.0964 and 0.0980 seconds. Evidence
`strategy13-pure-rule-real-source-validation-v1.json`, SHA256
`3c1e0ce041b99b7bd774ead74af8b40e756679b344a74afc1201d291a2dc4946`.
The focused new-rule plus recent-BOS suite passed 30 tests, including missing
adjacent source, forming/stale rejection, numeric overflow, empty batches,
prefix independence and input immutability. This is pure-rule acceptance,
not runnable Strategy 13 acceptance.

Remaining implementation: bind exact current/adjacent producer bucket values
and attempt identities to the certified columnar candidate projection; add
typed normalized entry witnesses and the same scalar admission validation
before Portfolio and during cold recovery; certify source/dispatch mutations;
pin the exact Strategy 12 parent and publish immutable Strategy 13 only after
integration tests. Then run all four actual app-route backtests, preserve
per-position reviews, and refresh backend after each save. No Strategy 13
release, configuration or backtest exists at this checkpoint, and no Strategy
1-12 behavior was changed. No worker was spawned or reused in this checkpoint.

### Strategy 13 integration acceptance before publication

The proposed rule now runs through the certified native static filter,
sequential adapter and guarded Portfolio submission. Exact current/adjacent
producer values are loaded as Arrow Float64 under the certified technical
attempt. Build identities are 64-character content hashes; attempts are UUIDs.
No indicator is recomputed. Two normalized rows per admitted entry preserve
both resolutions, nullable values and their original source identities in
`trading_rising_momentum_entry_v4`. Cold reads use IEEE bit projections to
avoid JSON precision changes. Direct and compound publication require the
same 2:1 parent relationship and rising rule. Occupied manager snapshots retain
their existing scalar references and resolve momentum from committed entry
authority; they do not duplicate or invent market evidence.

Full-population source audit covers 147,362 certified candidate keys, with
LGHL exclusion and existing rejection bits preserved. New survivor counts:
PM18 1,215 -> 1,164; AH18 248 -> 244; PM19 1,768 -> 1,634; AH19 359 -> 352.
The first diagnostic loaded every candidate, taking 42.069 / 48.728 seconds.
The runnable path first applies inherited Strategy 12 necessary gates and
reads momentum only for their survivors. Full certification and full candidate
identity remain intact; absent evidence for any necessary survivor fails
closed. The second source audit matches every survivor and inherited bit,
using 14 queries/date and 1.092 / 1.017 seconds for 1,463 / 2,127 requested
keys. Artifact `strategy13-certified-static-population-audit-v2.json`, SHA256
`b529495174b64998c5786c03add2d4e5cdf9dfb9ca8ce17957f084273c39a1c4`.
The slower v1 evidence remains retained.

The combined integration suite passed 535 tests in 44.90 seconds, including
old release behavior, source mutation rejection, exact nullable Float64
durability, compound commits, cold manager source recovery, bounded projected
loading and narrow operator storage/grant plans. Separate durability worker
suite passed 120 tests; projected-source worker suite passed 64 tests.
Sixteen modules' reviewed canonical AST observations bind the new authority
routes; normal run fingerprint validation also pins the complete source tree.
These are implementation checks, not profitability or interrupted-run
equivalence acceptance. Live and public interrupted resume remain closed.

Next operational step: commit/push this reviewed source, pin clean laptop and
workstation checkouts, install only the new normalized table with explicit
`live_market_ssd` and validate part placement, extend runner grants by its
exact SELECT/INSERT surface, then publish the exact pinned Strategy 12-derived
Strategy 13 configuration. Four saved app-route backtests and position audits
remain required. No Strategy 13 profit is claimed at this checkpoint.
Two existing workers were reused for independent source/performance and
durability lanes; both completed, with no newly spawned or interrupted workers.

### Published Strategy 13 admission failure and Strategy 14 successor

Strategy 13 source `5f566e9b289b1d751b568ba9c548914a6984bb96` was committed,
pushed and pinned on both machines. The operator created only the new momentum
table, verifying 19 commit-v4 tables and SSD placement; zero rows inserted.
The runner has exact 127 arte SELECT, 98 arte INSERT and 5 system SELECT grants.
Published configuration `strategy-one-13:90261e0b-c028-4efb-8c7d-e3537430dac3`
has 987 nodes, payload
`4e8b91296a7144c202b1a0e3c7a26c99e304a77d212a15c831e2b61eb179db41`,
certificate `083975053c47e9ab8fbe967f82b060d48379cbce31d64d1d22fdb7544119c821`,
approved source fingerprint
`920eea29a37b8c14c76ac4bd3787bdb60e8c3780122b44ce9d39274a0a8a41db`.
Backend/frontend refresh completed after the source commit and publication.

The first actual PM18 app-route preflight rejected that configuration before
creating a run: `selected_numbered_revision` still had a literal identity
regular expression ending at number 12. The previously passing integration
suite covered registry, compiler, runtime, source proof and durability but
missed this actual selector. Retain `strategy13-pm18-app-route-v1.log` and the
immutable published release; no successful Strategy 13 backtest or profit
exists, and its published metadata is not overwritten.

Strategy 14 is the explicit successor, pinned to that exact published Strategy
13 parent and payload. It preserves every momentum/management/sizing/cost
policy. Its sole correction admits the successor through the actual selector
and extends the same guarded runtime and normalized evidence to its numbered
identity. New tests call the real selector for 13 and 14, assert subsequent
certified-release verification, compare all inherited policies and recover a
committed Strategy 14 entry with exact Float64 source evidence. No new table,
market-product rebuild, indicator calculation or broader permission is needed.

The history SQL also ended at number 12; it now explicitly includes 13 and 14,
so completed successor runs appear in the existing app history. History tests
cover every admitted number through 14. The successor integration suite passed
539 tests in 55.65 seconds. A final cross-number companion parent guard was
added and its journal/configuration suite passed 121 tests in 12.26 seconds;
expanded actual history tests passed 23 tests. Review preserves all other
selector checks and old release semantics.

### Strategy 14 published four-session acceptance and actual entry audit

Source `7ff7527be9e6d4c0d1396f0baa24b4438ec0f151` was committed/pushed and
pinned on both machines before publication. Configuration
`strategy-one-14:5166c8f6-6c35-4e35-bd3e-4fe67091bce3` has 988 nodes,
payload `725b5e6b99e4406c353c6dc8020902dfe9fc1859f292b17678c0a2618f23d988`,
certificate `ac89dfbdfad59cd033fe7a47f1ceb1982be2fc9d3dfcab8c63b24bfd1d4253b3`,
approved source fingerprint
`b4f46ad46e76d32cccad00a88d6f5ebfa89c66f64fb722908ecd360cba3749fd`.
Projection certificate
`7a082f56532367fda1308013f45e4e227dbd6cfc283a90d8990ea535d1d8bb7c`.
Each run started independently with $10,000; all completed flat, exclude LGHL,
stay within their requested extended session and share the Strategy 12 market
build and market-plan token. AH keeps the inherited prior-day V7 seed and
completed intraday/RTH-warmed interval authority; there is no regular-hours
trading. Every saved run triggered the managed backend/frontend refresh.

| Session | Run ID | Positions | Net P&L | Delta vs 12 | Marked drawdown | Execution seconds |
|---|---|---:|---:|---:|---:|---:|
| PM18 | 53bcc041-5861-42c6-88e6-1516f5e0352f | 21 | +1397.479805 | +2.937935 | 1218.190215 | 53.756 |
| PM19 | af61cdf0-7c88-4d25-92bb-044025816c51 | 20 | -425.668395 | 0 | 2012.850445 | 62.512 |
| AH18 | 2aa6d459-f71e-4d57-b052-ff4a5d5b86b8 | 11 | -138.316615 | +363.836385 | 843.171540 | 35.527 |
| AH19 | a5ba7ef1-7c96-43a2-856e-809e2d73da73 | 6 | +723.352565 | 0 | 710.712330 | 44.184 |

Total +1556.84736, improvement +366.77432; still only 2/4 sessions reach
+$500. AH18 marked drawdown improves by 131.566385, other session drawdowns
are unchanged. The worst session remains PM19 at 2012.850445. These marks
retain the prior limitations: asynchronously retained marks can be stale and
are not a synchronized executable liquidation-equity path. All eight journal
units per run drained with zero writer failures. Native rule/source loading
does not materially slow these measured actual runs relative to Strategy 12.

Artifact `strategy14-four-session-comparison-v1.json`, SHA256
`5434683b9a1cfa03a1e10d8fede2a8b9bec8a8d02fd76bb5f842ff232e2a46e4`.
The earlier outcome screen was not counterfactual profit: PM18 SGLY entered
0.5s later and still lost; PM19 CAST later became eligible at unchanged fill
prices and retained the same loss. AH18 actually removed CAST/BIVI losses,
retained its BIVI winner and changed subsequent available cash/quantity.

Independent cold entry/source audit verified all 58 typed admitted proposals
and linked positions, exact Float64 values reread from their original
technical attempts, current/adjacent completed clocks, market-plan identities
and recent-BOS bounds. Artifact `strategy14-actual-entry-source-audit-v1.json`,
SHA256 `7321f337d9cee11cec353581f3ed75897e651321998eea6bd09771e2b4fc9d3e`.
Human position tables sorted by P&L, including fill/proposal times, both MACD
histogram comparisons, volume and trade count, are in
`strategy14-entry-audit-v1.md`, SHA256
`bee2c697bcc9b464327c822b3727e9e12618b7756585ace2becb62ff22427d3a`.
Float remains explicitly unavailable without a dedicated historical as-of
reference reader; no current float is substituted.

VTIX AH19 remains at 17:42:45.3 fill (17:42:45.2 proposal), net -138.78.
The completed 1s histogram rises 0.0023868276543747204 ->
0.002846307772235969; completed 10s rises 0.0016485837636941586 ->
0.004620486473341526. Both satisfy the rule. Its BOS age is 11.2s. A red
fill-time candle and price under overhead resistance are not exclusion rules,
and a chart bar labeled by its opening clock differs from the completed source
ending at proposal time. This case is a setup-quality weakness, not evidence
of an entry-clock bug. It was retained rather than silently filtered away.

Final operational review found the trade-report script also ended at number
12, so initial reports had correct financial/position evidence but omitted
numbered-release metadata and labeled themselves Strategy 1. Commit
`2b8e177b4` extends only reporting admission; 18 tests and 17 subtests passed
(3 unrelated optional tests skipped). Reprojected immutable reports are under
`strategy14_reports_v4_v2`; initial reports remain intact. Exact comparison
confirms positions, financial evidence, context, committed sequence and market
identities are identical; only source/report metadata changes. Artifact
`strategy14-report-metadata-correction-v1.json`, SHA256
`2c5fa8e7b99147e7b1cfdc5b8ad7ca6b919173ae6ee903532759e4a93138845f`.

The transient campaign helper initially inherited inline private credentials
when launching services that already had managed file credentials. Dedicated
credential validation correctly failed closed, returning HTTP 500 for history.
Restarting from the clean shell fixed it; the runtime helper now strips only
the managed inline credential keys before service restart. No credential
values were logged or persisted. Actual app history confirms all four completed
Strategy 14 runs and their saved-review availability. Artifact
`strategy14-app-history-verification-v1.json`, SHA256
`d864de9ad98e259b46a3eb720de81b408fcff1f5044388905eeeae30561b1fbb`.

The next initial-eligibility-freeze hypothesis was screened, not published.
Requiring an activation episode's first Strategy 12 eligible candidate to
have rising momentum adds only one observed Strategy 14 rejection, CAST PM19
-67.496, and rejects zero observed winners. It cannot resolve the remaining
session targets, and ignores financial/held/cash permissions when selecting
the initial candidate. Artifact `strategy14-initial-momentum-screen-v1.json`,
SHA256 `9f6bc2bfae742a0ad2e2cb53776c676903a71fffe72f0033678d070396cdbb85`.
Do not treat that amount as saved backtest profit or publish an increment
solely from this outcome-selected screen. The ongoing goal remains active;
no repeatable edge or four-session success is claimed.

A separate completed-candle diagnostic checked all 58 Strategy 14 entries
against 116 exact source 1s/5s candle keys in two bounded Arrow queries, with
zero missing/invalid candles. At VTIX's 17:42:45.2 proposal the 1s bar ending
17:42:45 (chart opening label 17:42:44) is green, open 2.32 / close 2.35.
The 5s bar ending at the same clock (opening label 17:42:40) is red, open
2.362 / close 2.35. The bar opening 17:42:45 is forming and is not an entry
input. Both source clocks are causal. A blanket green 1s rule rejects 13
observed losers and 8 winners, with observed rejected net +1214.004725;
green 5s rejects 9 losers and 4 winners, rejected net +1300.17272. Either
green rejects 3 losers / 1 winner (+606.55785); both green rejects 19 losers /
11 winners (+1907.619595). Positive rejected net indicates substantial
observed winner cost, not saved profit. No candle filter was published.
Artifact `strategy14-completed-candle-screen-v1.json`, SHA256
`11d4b706862b91d20b3ae53b56d2537ffbb9d50fc480a552494842ff89105ef3`.
Next research should investigate weak structural setups and liquidity fading
without removing these strong trades. Both reused workers finished all lanes;
zero new workers spawned, zero agent failures or interruptions, no live child
processes remain. Keep the approved goal active and preserve all rejected
diagnostics, original reports and pinned releases for later review.

### Strategy 15 integrated: holding-wide failure exit, not yet backtested

Read-only Strategy 14 diagnostics retained all 58 positions. Initial-stop
percentage exclusions disproportionately removed large winners. The completed
five-second failure screen found seven losers and three eventual winners whose
first fresh half-risk/negative-5s-histogram observation occurred after 60 seconds.
These are descriptive observations, not counterfactual returns. The source-pinned
screen uses original typed proposal asks, certified bars/indicators/liquidity and
fresh co-terminating quotes. Its conservative first-fill-plus-100ms boundary is
not a reconstruction of the manager checkpoint. V1 is retained; V2 corrects the
bid threshold to the same original-risk threshold as the completed close.
`strategy14-held-failure-probe-v2.json` SHA256:
`f807ddf268b9841de8cec94f98952500531889311825b89aef7abc313399ff65`.

Strategy 15 derives from exact published Strategy 14 revision
`strategy-one-14:5166c8f6-6c35-4e35-bd3e-4fe67091bce3`, payload
`725b5e6b99e4406c353c6dc8020902dfe9fc1859f292b17678c0a2618f23d988`.
Its sole behavioral change removes the 60-second eligibility limit from the
existing completed-five-second half-original-risk failure exit. Negative MACD,
completed close and fresh qualifying bid remain required; elapsed time alone
never causes an exit. All entry, reentry, initial protection, structural ratchets,
sizing, costs, session limits, exclusion and source warming policies are inherited.
The manifest removes the superseded explicit early-window rule contract.

Typed exit witnesses, cold recovery and restored manager dispatch retain the
11–14 inclusive early window while admitting 15 to the original unbounded rule.
Exact momentum companions, source guards, parent linkage and bounded vectorized
entry loading extend to 15. Actual selector, history and report admission include
15 before publication. No producer calculation or new operational table is added.

504 integration tests passed in 34.84 seconds, including restored manager
boundaries, old/new release policies, normalized commit/cold paths and source
mutation guards. Separate focused checks passed 103 tests and 35 manager/release
tests. Managed backend and frontend restart reached ready. Strategy 15 source
integration is accepted; publication, four actual saved app-route backtests,
per-position review and comparison remain required. No Strategy 15 profit is
claimed. The goal remains active and Strategy 14 remains at two qualifying sessions.

Strategy 15 source was committed and pushed as
`ac05ab5ab88fe904112ec712098f6235eff06632`, pinned in laptop
`strategy15-reproduction` and workstation
`quant-research-workbench-strategy15-ac05ab5ab` checkouts. Publication certified
revision `strategy-one-15:9612bd76-0faa-4b40-a13a-54515c2fb53f`, 983 nodes,
payload `fdc313a3e446d7d8f7e9b038da573c367ccfc5e5cc8d56fd94a919f842c2ccd2`,
release certificate `e23614ed9ce1a4e5f253287af8e91bf0930779ca8823a81a89da60a1144a27ae`,
source fingerprint `b2c8d92b4adee72c2cfa64a6fb314ff19d67008367bdb135af64e5aa6b2acae2`.

The first two actual app-route attempts launched no journal run: required
`causal_v7_seed` preflight rejected changing V7 interval physical parts during
the cold read. Both failed logs/manifests remain immutable under runtime
`strategy15-four-session-campaign-v1.json` and `-v2.json`. No source or
certificate gate was changed. A read-only five-second inventory comparison
then observed equal full product fingerprints; the next ordinary certified
attempt is tracked separately in `strategy15-four-session-campaign-v3.json`.
A stable inventory sample is not certification and never authorizes trading by
itself. Four saved runs and their reviews still remain required.

### Strategy 15 actual failure and Strategy 16 operational correction

Strategy 15 PM18 run `2f67ae6e-b2d7-4052-b427-01d95e043d47` completed flat,
25 positions, net +951.795870, broker-observed marked drawdown 1139.903080.
Against Strategy 14's +1397.479805 and marked drawdown 1218.190215, net fell
445.683935 while marked drawdown improved 78.287135. XOS accounts for -493.954710
of ticker delta. Two formerly profitable holdings were cut into losses, combined
-514.918125 impact. Four additional entries netted +5.069110; extra reentries
were not the main regression. The exact saved-report decomposition is retained
in `strategy15-pm18-xos-regression-v1.json`, SHA256
`06c3c1ad87d7f84dbf563e62723c5f2dd87864bf69fdd6088b4ad2074384ac56`.

PM19 run `b21867bf-92bb-4880-82fd-bfe2acfd5fa6` passed source preflight but
failed at session liquidation. The factory emitted the Strategy 15 session
reason; runtime admission and typed memory still fell back to Strategy 14's
reason. No PM19 profit or completed terminal evidence is claimed. Campaign V3
stopped at this confirmed terminal failure; its run/log and the completed PM18
report remain immutable. No Strategy 15 process remains active.

Strategy 16 is the explicit operational successor to the exact published 15
configuration. It preserves all 15 trading policies and replaces the three
independent reason expressions with one installed-number-validated
`numbered_session_exit_reason` authority. Factory, runtime admission and typed
memory use that same function. A normalized manifest policy exposes this repair.
Reviewed source seals bind the immutable reason map, helper and all three full
calling functions. No source products, tables or trading-clock rules change.

640 integration tests passed in 41.27 seconds, including actual factory-to-runtime
to-memory admission for every session-exit number 2–16, rejection of the wrong
predecessor reason, four independent source mutations, whole 5s failure inputs,
restored manager window dispatch, original release inheritance and extended-only
sessions/public-resume guards across the installed catalog. The first integration
attempt retained 611 passing tests and one stale unapproved-number assertion;
that assertion now rejects 17 while new 16 durability checks pass. Backend and
frontend restart reached ready. Publication and four actual Strategy 16 backtests
remain required; source tests establish engineering acceptance, not profitability.

Strategy 16 correction source committed/pushed as
`44ab627f741a3862df7289925c8ad24b81de5acb`, with clean pinned laptop
`strategy16-reproduction` and workstation
`quant-research-workbench-strategy16-44ab627f7` checkouts. Published revision
`strategy-one-16:d8219332-9f66-4cbb-aa18-e3125d98a3fa` has 991 typed nodes,
payload `7a6d9671b35e8817f2d2b29b3631c4c46698fe23e1b2141a74e319018358a328`,
release certificate `c73822bba98ad690fc7244fc14175a29c4045cd1dc03e2f1e43788da276b0f54`,
source fingerprint `7c7af7bd4e20234a29feb9665e8adbab016a431e7b6d77efb8de0c8ac34e75c3`.
The actual app-route campaign is tracked in runtime
`strategy16-four-session-campaign-v1.json`, with $10,000 separately in each
of the same four extended sessions. Its services refresh follows every saved
run. No completed Strategy 16 session or profit is claimed at publication.

### Strategy 16 completed evidence and next bounded entry experiment

All four Strategy 16 actual app-route runs completed with independently funded
$10,000 accounts, no open terminal positions, the certified LGHL exclusion and
the same market builds/plan tokens as Strategy 14. Preferred cold reports are
under runtime `strategy16_reports_v4`. Net P&L and broker-observed marked
drawdown respectively were PM18 $951.795870 / $1,139.903080; PM19
-$355.839695 / $1,988.317965; AH18 -$99.450035 / $806.104960; AH19
$723.352565 / $710.712330. Total net was $1,219.858705, with only two of
four sessions meeting the $500 target. These are development results; marked
drawdown retains asynchronous, potentially stale-mark limitations.

Run IDs in that order are `187661ae-fd4a-4925-ae60-ea301a1ece1d`,
`7ef5d305-16a6-436d-81b8-e4754cc02cb5`,
`c3e3f813-6e4f-4d03-8134-211423daa454`, and
`1e288b22-d9a8-4e17-a520-c6e515ca6a72`. Comparison artifact
`strategy16-four-session-comparison-v1.json` has SHA-256
`11633600a95c885656ee558146fd75cec77a868aea29ddd576a6c6a39e4a955c`.
Independent audit checked all 62 filled entries against exact producer Float64
momentum values, completed clocks, source attempts and recent BOS authority:
`strategy16-actual-entry-source-audit-v1.json`, SHA-256
`c543b6defa4cf97a306690a5928f6d6dd6de08ef5c29c94bd97fd88ffad7e943`.
All four persisted runs were visible and reviewable through actual app history:
`strategy16-app-history-verification-v1.json`, SHA-256
`868ad34cc308f3df9a31a2370fa39cd86f0ddf992137bedddced5bced69db133`.

The unbounded failure exit reduced drawdown slightly, but lost $336.988655
aggregate net versus Strategy 14. Earlier XOS evidence attributes the main
PM18 regression to cutting two established winners, not negative additional
entry churn. Retain this failed experiment rather than promoting it.

Read-only screens on Strategy 14's exact 58 entry witnesses explored requiring
positive completed 10s histogram growth relative to its preceding value.
`strategy14-momentum-resolution-screen-v1.json` SHA-256
`79ae7df5d27414ce49393471eb70cae7acbe900ce07b9a67418cb732945fb9b0`
shows requiring both 1s and 10s rising discards too many winners.
`strategy14-ten-second-growth-screen-v1.json` SHA-256
`5246035b847af80437511a6cc72a22ae624c3c212614b84a8528d02aba6b1328`
screened fractional growth thresholds 0, 5%, 10%, 15% and 20%. At 10%, the
remaining observed trades sum to PM18 $905.375680, PM19 $512.569085,
AH18 $508.034695 and AH19 $1,033.117850. These sums are NOT strategy
returns: changed entry timing, sizing, available cash and reentries require
actual counterfactual execution. Repeated development on these dates is
exploratory. The next candidate should branch from Strategy 14's early failure
management with one entry change, then undergo a complete immutable backtest;
no Strategy 17 release or profitability is claimed here.

Latest user operational instruction supersedes routine restarts after saves:
restart services only when necessary for testing or activating source changes.
Do not refresh services simply because a backtest or report was saved.

### Strategy 17 implementation acceptance

Strategy 17 branches directly from the exact published Strategy 14 configuration
and changes entry/reentry eligibility only: completed 10s histogram must be
positive and strictly greater than its adjacent predecessor plus 10% of that
predecessor's absolute value. The original 1s/10s witness, source identities and
Float64 journal columns are reused. Shared numbered dispatch applies this rule
to the native static gate, scalar adapter, intent factory, typed projector/cold
reader and manager restore; Strategies 13–16 retain their previous predicate.
There are no new market products or tables. Missing 10s values reject, malformed
clocks or arithmetic overflow fail closed. Native arrays remain unchanged and
row independent. The original first-held 60s failure window is retained.

784 integration tests passed in 51.14s (`strategy17-integration-tests-v2.log`
and `strategy17-integration-validation-v2.json` under the campaign runtime root).
This includes actual selected-number source admission, direct typed commit/cold
restore, weak-witness manager rejection, independent source mutations, extended
windows, session reason authority and existing normalized journal/OMS tests.
The first broad run retained 765 passing tests and seven stale catalog/source
mutation assertions; those tests now exercise the installed successor and keep
their fail-closed assertions. A separate initial selector run retained 359
passing tests and found the missing literal 17 selector branch, now corrected.
The first new cold fixture lacked 10s observations and correctly rejected; its
test input now carries strong completed 10s values. No source gate was weakened.

Two existing workers were reused for the four-file release lane and two-file
pure reducer lane; both completed without child agents. The release lane passed
25 tests. The reducer lane passed 21 tests and measured 1 million rows in 121ms,
excluding source reads and execution. Release evidence is
`strategy17-release-lane-validation-20261001T012111Z.json`, SHA-256
`3eb3da8d9739eb0c5760ba270a5f7adb3ca2d9b52e349f623eb2eb699450a3c2`.
Actual publication and all four immutable counterfactual backtests remain
required; implementation acceptance does not establish profitability.

The reviewed Strategy 17 source is committed/pushed as
`2d33a775424841c4a2d686a3a0c199ad65592f96` and pinned in managed laptop
`strategy17-reproduction` and workstation
`quant-research-workbench-strategy17-2d33a7754` checkouts. Publication completed
as `strategy-one-17:4613cbda-319a-4847-ba03-63fa5af580a8` with 996 typed
nodes, payload
`d0b4205c9532afc034849ed439dd397af8494973095dc683424a7f929ca83fe4`,
publication certificate
`2fa496d0a71560514efa488d5314c77d8d81410cc624d305be17450026844952`,
source fingerprint
`304e10e1d0ac125db9e825002a1a34c08579e88eb04ff5e9506b6229bad199d0`
and projection certificate
`5a0f1a55f757a08de49797dbefa040cc001f6042381d8ecbe18eb3a2e877195d`.
The actual app-route campaign started, tracked in runtime
`strategy17-four-session-campaign-v1.json`; one service refresh activated the
new source, with subsequent routine save refreshes disabled. No Strategy 17
completed-session profitability is claimed at this publication checkpoint.

### Strategy 17 completed development outcome

All four actual runs completed with $10,000 independently, flat terminal
positions, LGHL excluded and the same certified market builds/plan tokens as
Strategy 14. Net P&L / broker-observed marked drawdown / position count:
PM18 $659.172985 / $839.971360 / 17; PM19 -$660.163790 / $2,011.829875 /
21; AH18 $54.434535 / $615.445890 / 9; AH19 $50.273530 / $710.554320 /
6. Total net was $103.717260, with only one of four sessions at $500. Retain
the failed trial; do not promote its entry filter. Marked drawdown retains the
documented asynchronous and stale-mark limitations.

Runs in that order are `75a0c191-ed75-49a8-9b8d-0f11e2106d95`,
`e31e8b55-33cb-4154-bf5d-0a616217069a`,
`1d3f6642-ba0b-4067-b219-6bd30b400ea9`, and
`dcfecfa3-533f-4eaf-85a3-0ce66751536b`. Preferred reports remain under
runtime `strategy17_reports_v4`. Comparison artifact
`strategy17-four-session-comparison-v1.json` SHA-256:
`87cc9988551c95574c86c63ef822d58c672d077e4add5bc9d144f7c737c73cbd`.
All 53 actual typed proposals and filled positions passed exact producer
Float64, clock, source-attempt, recent-BOS and positive 10s growth checks:
`strategy17-actual-entry-source-audit-v1.json` SHA-256
`7bccd5a872bbfb1551e54f45d0f06f362abdbef95021b51a582478c0de5a34f7`.
The P&L-sorted entry table is `strategy17-entry-audit-v1.md`, SHA-256
`76bdf225449f2d59c1d95ebd59762ace8227f999636152f99c457bf7649c08cd`.

The earlier exclusion screen did not predict actual counterfactual results.
PM18 had 12 exact entry-clock matches and five changed actual clocks; delayed
PFSA and SLE entries lost substantial parent profit. PM19 had nine exact matches
and 12 changed clocks; EHGO accounted for $404.96 of deterioration and fees rose
from $166.38 to $185.28. Some rejected entries returned later at worse prices;
cash, quantities, reentries and exits also changed. Diagnostic JSON artifacts
`strategy17-pm18-position-diagnosis-v1.json` and
`strategy17-pm19-position-diagnosis-v1.json` have SHA-256 respectively
`498a73ad9a12d09e36873487087e0f7dd10c663e916b0adfa02b24cd686a9bfa`
and `c8db833aae16c8d56ca542949a48af9b7c46813f2960ec4101ec28a03c49540d`.
Requiring 1s rising as well would reject the large YJ PM19 winner; the descriptive
screen `strategy17-premarket-one-second-rising-screen-v1.json` SHA-256
`6ccce2f672d46aa98bca3043ba3db303a71411ee90a9c6dff3cbdd86ce94215e`
does not justify that next rule. Audit original normalized prior-high crossing
evidence for reentries before deciding another behavioral version.

Actual app history initially omitted all four 17 runs because its compact SQL
inventory whitelist still ended at 16; the saved journals and reports were
complete. The inventory query now explicitly admits 17, with 56 history/version/
report tests passing in 3.38s. This is a read-only inventory correction, not a
replacement of the published 17 trading release or its pinned executor. Its
activation requires one backend refresh; routine save refreshes remain disabled.
Actual API visibility must be verified after activation before claiming repair.

The inventory correction committed/pushed as `22cc9dfef`; its managed backend
and frontend refresh reached ready. Actual API history then verified all four
saved Strategy 17 runs as completed and reviewable:
`strategy17-app-history-verification-v1.json`, SHA-256
`d1f81027f2fa1ce954d29ef5ded757e441c70885ac42c136866956c759265d12`.
The failed initial inventory check and diagnostic remain retained. This
correction changes the current backend fingerprint; exact Strategy 17 execution
continues to use its original pinned source, never a repointed approval.

### Subsequent causal research and proposed Strategy 18 scope

The six-run 14/17 reentry audit verified cold entry pages but found that the
prior-position high, adjacent completed closes and closed-position witness are
transient inputs, not retained by the typed entry proposal. It cannot classify
the proposed stronger high-cross rule. Do not label that unavailable evidence
as a proven normalized witness. Artifact
`strategy14-17-reentry-high-cross-audit-v1.json`, SHA-256
`d1534f73c819ecca75f823f3e53fe996ff4fe63b5d5b2214ee9569016c3cc31a`.
Reentries include substantial observed winners; a blanket restriction is not
supported. A future version using that rule would need a durable companion
linking the exact entry intent, prior closed-position authority, high, resistance,
completed-close clocks and certified market provenance.

The exact original-proposal reward/risk screen used typed reference asks and
initial stop/target values, not future marks or average fills. A 2R minimum
descriptively retains PM19 profits but discards too much observed PM18/AH profit;
do not adopt a universal 2R gate from this evidence.
`strategy14-17-original-proposal-risk-screen-v1.json`, SHA-256
`b831c710b78d4620fd4996d4c9b01dc4961dff70876fc2034198b6f4cdb1f747`.

An independent full certified-candidate screen froze the 10s growth predicate
at the first Strategy 12 necessary-condition eligible candidate per
(ticker, MACD activation episode), and required both that frozen result and
current strong 10s momentum. It uses exact certified sources and native ordered
group indexes; holding, cash, permissions, cooldown and closed positions do not
reset the first setup. Later stronger momentum cannot resurrect an initially
weak episode. Artifact `strategy14-17-initial-strong-momentum-screen-v1.json`,
SHA-256 `1b19a1a728015c1e3966b94392731d5582ebb4d1e266a6d3480d368f71001971`.
Descriptive remaining 17 trade sums are PM18 $829.069585, PM19 $87.502465,
AH18 $642.052335 and AH19 $380.092490. These are not counterfactual returns;
released cash, changed sizing and entries require actual execution. This screen
supports testing prevention of late resurrection, not claiming the goal reached.

Proposed Strategy 18 should branch from exact 17 with that single eligibility
change, retaining the early 60s failure exits and all sizing/cost/session/V7
contracts. Before publishing, retain a normalized first-setup momentum companion
for every 18 entry: exact parent entry intent, episode and first-base boundary,
adjacent completed observations and source build/attempt/market-plan provenance.
Compile first-base group indexes once in native arrays over the certified base
gate; only survivors materialize witnesses. Use a separate versioned companion
family and commit/cold-read seals, with SSD table/part placement and exact grants
verified before writers. Do not overload current-clock momentum rows with stale
anchor observations or hide the anchor in generic metadata. Static filtering,
scalar admission, manager restore and cold review must enforce the same anchored
rule, preserving prefix behavior and old consumer semantics. No Strategy 18
source, release, publication or backtest is claimed at this research checkpoint.

### Strategy 18 first-setup compiler groundwork

The staged pure rule and certified compiler are now implemented in
`strategy_initial_strong_momentum.py` and
`backtest_strategy_initial_momentum.py`. They are not connected to numbered
execution or published release admission yet. Native sorting/group reduction
selects the first Strategy 12 base-eligible candidate per ticker/episode over
the full certified population. Both current and first observations must pass
the unchanged Strategy 17 strong-10s rule. Financial permissions, cash, position
closure and survivor pruning cannot reset the first-setup selection.

The compiler binds candidate, entry and momentum tokens plus exact candidate
keys, episode starts, base rejection mask, first indexes and eligibility in its
content seal. Reconstruction recomputes selection; output arrays are detached
and immutable. Sparse lookup materializes exact producer observations only for
admitted candidates. Scalar source/clock validation does not by itself prove
initiality; the full certified compiler supplies that proof.

Focused native/scalar/source/static-gate/Strategy 17 admission regressions passed
85 tests in 5.12s. An earlier run passed 55 and failed two fixture checks because
separately constructed fixtures had different technical attempt UUIDs; fixtures
were corrected to retain the same source attempt, without weakening validation.
The repaired focused suite passed 57 tests in 1.98s before the broader check.

Actual read-only certification of both source days verified exact equality of
the new compiler's first indexes and eligibility with the independent research
screen, across PM/AH populations. Retained artifact
`strategy18-initial-compiler-certified-validation-v1.json` SHA-256
`a02b32e4e6f6a02923437c64cfe54408dc82ae3391741ded3d1f66cdd94a77ab`
under the campaign runtime root includes source/selection tokens and counts.
This is implementation validation, not a Strategy 18 backtest or profit claim.
Normalized companion persistence, commit/cold recovery, source certification,
numbered execution integration, publication and all four actual sessions remain
required. No service restart or workstation source synchronization was needed
for this staged compiler. One existing worker completed the pure-rule lane;
zero new agents, failures or interruptions, and no worker remains active.

### Strategy 18 normalized entry integration

Strategy 18 now has a numbered Backtest-only release, pinned to exact Strategy
17 configuration `strategy-one-17:4613cbda-319a-4847-ba03-63fa5af580a8` and payload
`d0b4205c9532afc034849ed439dd397af8494973095dc683424a7f929ca83fe4`.
Its only behavioral change is requiring both current and first-base-setup
strong-10s momentum. Sizing, costs, fixed target, structural stop ratchets,
extended-session activation/cutoff/liquidation, recent BOS, no adds, no 30s
trailing and the inclusive first-held 60s failure window remain inherited.

`InitialMomentumSelectionWitness` keeps the typed first-setup witness with
candidate/entry/selection plan tokens. Native source execution compiles the
selection over full visible certified candidates before survivor pruning and
threads it through the static gate and sparse scalar admission. Earlier
versions reject this new witness; 18 requires it. Manager restoration validates
both strong observations and episode/source identity and recovers the full
anchor from the committed source rather than copying market values into the
manager snapshot.

The new `trading_initial_momentum_entry_v4` normalized companion retains two
completed producer observations per 18 entry, original entry and episode clocks,
first-setup boundary, exact Float64 values and source/build/attempt/market and
selection tokens. Writer, direct and compound publication, commit-family
readback, cold entry pages and per-source recovery require the exact 2:1
companion graph. IEEE-bit projections preserve producer precision. Per-source
recovery separately rereads and compares the committed anchor, rejecting forged
selection tokens. The companion belongs to the existing SSD contract and exact
grant authorities; layout/provisioning include it, but no DDL or grant change
has yet occurred at this checkpoint.

Registry, parent, configuration publication/selection, session-exit reasons,
saved history (including compact SQL), reports and capability/source guards now
explicitly admit 18. Existing approved trading release artifacts were not
replaced. Reviewed source seals include the whole initial rule, certified
compiler and companion modules plus shared native/scalar/publication/recovery
routes; mutation tests reject weakened first-setup selection and companion
cardinality. Runtime source-seal change receipts are retained as
`strategy18-reviewed-source-seal-delta-v1.json` and
`strategy18-cold-anchor-source-seal-delta-v2.json`.

Final integrated validation passed **724 tests in 58.86s**. Coverage includes
all numbered configuration releases, pure/native rules, certified source gates,
scalar entry factories, direct/compound cold commits, manager snapshots,
execution/coordinator/runtime admission, history/review, typed projection and
exact grants/layout. Earlier runs exposed stale uninstalled-number/catalog and
source-mutation fixtures, and a stale reviewed per-source hash after adding the
anchor reread; all were repaired and included in the final passing run. The new
direct/compound tests also exercise exact anchor restoration, missing-anchor
rejection before inserts and forged per-source selection rejection. CLI help
and scoped diff checks passed. Two existing workers completed the normalized
companion and release lanes; zero new agents, worker failures or interruptions.

Next required steps: commit/push and pin this exact source locally and on the
workstation; create only the missing companion table after explicit SSD policy
validation; reconcile exact runner/operator grants and verify actual placement;
publish the unique immutable 18 configuration; execute and cold-audit all four
certified $10,000 extended-hours sessions. AH still requires prior-day V7 seed
and regular-session warmup. No Strategy 18 publication, backtest, P&L or target
achievement is claimed yet. No service restart was needed during integration.

### Strategy 18: pinned publication and actual four-session campaign

Source `1174cc640ac8f13f63fe59ba163e7a657501285b` was committed,
pushed and verified clean in local and workstation reproduction checkouts.
Only the missing `arte.trading_initial_momentum_entry_v4` companion was
created, after the `commit-v4` layout planner confirmed that no other table
was missing. Its explicit storage policy is `live_market_ssd`; existing
contract placement passed preflight. Exact runner grants were reconciled
without rotating its credentials. The empty table had no active parts before
writers. Installation/grant receipt: runtime
`strategy18-initial-companion-install-and-grants-v1.json`, SHA256
`407f7fcbdea07b62a859e93981b3f5b91166a28505d04bb6646a4665fc1c5986`.

Published 1,006 typed nodes under unique revision
`strategy-one-18:c05b441d-c5cc-4ac6-a4a1-50bac086d16b`.
Payload hash `f1a2b7347021fab5139658571ca462b238829e63751eb517e386d2d2fe5c26db`;
publication certificate
`253862ca7654c739efbe99638923b8a8adc8183936593d35909c50564ab4a346`;
source fingerprint
`0d9d50958dd53fb67e6207902f3cfbe950fb8aa5409786c8861f84106e742dc1`;
fixed-v4 projection certificate
`a8156c8092c43c1c5f0cedb1e4f6af2996f546231768a333cdff39406437a6d0`.

All four actual app-route runs completed from the pinned checkout and were
cold-read into `strategy18_reports_v4`. Each started with $10,000 and retained
certified LGHL exclusion. PM traded only 04:00–09:30 ET; AH traded only
16:00–20:00 ET. The unchanged AH authority uses certified prior-session V7
seeds and completed intraday V7 intervals; flat financial start does not
simulate regular-session trades. No routine services restart was performed,
following the user's latest instruction while away from the office.

| Session | Run ID | Positions | Net P&L | Broker-observed max drawdown | Net change versus 17 |
|---|---|---:|---:|---:|---:|
| Aug 18 PM | `e25439d8-2e46-4ac3-8bea-a3c158eb4b9b` | 15 | +$791.24 | $850.44 | +$132.06 |
| Aug 19 PM | `19577114-c655-4b8e-890b-3559ee729198` | 12 | -$80.60 | $1,567.36 | +$579.56 |
| Aug 18 AH | `5c058462-a0c9-4962-a4e9-70e7e0bcf52c` | 5 | +$664.30 | $400.97 | +$609.87 |
| Aug 19 AH | `753b9e69-b5a4-4914-9d97-89041ac69cd0` | 4 | +$1,060.06 | $332.41 | +$1,009.78 |

Actual total net is **+$2,434.99**, with **3/4** sessions meeting +$500.
The goal remains active: Aug 19 PM still fails. Its twelve positions comprise
one +$1,596.50 YJ winner and eleven losses; reducing false starts while
preserving the large move is the next research concern. Compared with 17,
marked drawdown improved in three sessions but slightly worsened on Aug 18
PM. Broker marks remain asynchronous and can be stale without age evidence;
these extrema are not synchronized liquidation-value drawdown.

Runtime campaign: `strategy18-four-session-campaign-v1.json`.
Comparison: `strategy18-four-session-comparison-v1.json`, SHA256
`3ed4f2be0f6aed841df018158c8e7a875c6241723ae2ad88922ab3f3af8506e1`.
Both development dates remain exploratory; no independent validation or
repeatable-edge claim is made. The independent committed-entry audit below
recompiles first-setup selection and checks exact producer observations and
actual companion part placement.

The completed entry audit passed **36/36** typed proposals and filled
positions. It reconstructed the certified source population and native
first-setup selection at each run's exact horizon, compared all three stored
selection/parent seals, and checked both first/current completed 1s/10s
observations against producer Arrow Float64 values bit-for-bit. Recent BOS,
activation episode, source attempt and market token also passed. Actual
companion cardinality is **72 rows / 36 parents**. The designated journal
storage preflight and metadata reads verified **4 active parts / 72 rows**, all
on `live_market_ssd`, with the exact table policy.

Audit receipt: `strategy18-actual-entry-source-audit-v1.json`, SHA256
`36c566b9b2fc43c89e1aeaa0e3c84906a4ef5c18ef3959a7f19451a3b9f875ce`.
Sorted entry-review table: `strategy18-entry-audit-v1.md`, SHA256
`3018d36b3e1bd23c1bf234ccdfbe3d982c5dbde33454b689ac8d928710154b5d`.
Float remains unavailable because there is no dedicated historical as-of
reference reader; no current float is substituted.

The first diagnostic reconstruction unnecessarily resealed LGHL's empty
candidate set. Production correctly retains the original source seal when an
exclusion removes no candidate rows. Exact anchor clocks/values already
matched; reproducing that existing scope contract resolved the seal mismatch.
The market-only SELECT wrapper also correctly rejected a system-metadata
query; the audit then used the existing journal storage-preflight authority
for those fixed metadata reads. No trading code, backtest or grant requirement
was weakened, and all four actual runs remained completed.

A subsequent original-proposal risk-distance screen is retained as
`strategy18-original-proposal-risk-screen-v1.json`, SHA256
`8c34963496c3dea101211ab9d4c6c67ee1ee7558904561f5471b013515264e8e`.
Maximum initial stop distances of 2–10% reject the sole large Aug 19 PM YJ
winner and both Aug 19 AH winners; this is not a useful uniform next rule.
These saved-position filters are descriptive only, not counterfactual P&L.
Continue from immutable Strategy 18 and investigate remaining false starts
without losing the large moves. No new agents or services were started in this
campaign step; all task-owned backtest, audit and screen subprocesses finished.

### Post-18 research: first setup strength and failed-move exits

All but three of the 36 actual proposal entries occurred at their first
base-eligible setup. The three delayed entries were same-episode reentries
(two winners and one loss); a blanket first-setup-age restriction therefore
does not address the remaining false starts and can remove strong reentries.
The entry review also shows profitable GNPX/BIVI positions with low recent
volume or trade counts, so no blanket liquidity floor has been adopted.

The retained original-proposal risk-distance screen rejects a universal
2–10% maximum initial-stop-distance filter because it removes the largest
PM19 and AH19 winners. A separate read-only completed-bar probe tested
first-minute negative 5s MACD exits at the closer of half the original stop
distance or 1%, 2%, 3%, and 4% of the original proposal ask. It uses whole
post-fill 5s buckets, a conservative 100ms held offset, and co-terminating
valid quotes aged at most one second. This is a descriptive source screen;
it does not reconstruct exact first-held snapshots, execute exits, or infer
counterfactual P&L.

Corrected probe: runtime `strategy18-capped-early-failure-probe-v3.json`,
SHA256 `30b912707cace864cb28c0dfe14cff65636b88e40bef73ecde56980756e6d6fd`.
A 2% cap hits thirteen losing positions but also the +$1,596.50 PM19 YJ winner
(at 44.4s after first fill, proposal ask 3.73 / qualifying bid 3.64) and the
+$843.81 AH19 BTCT winner (at 45.5s, ask 1.35 / bid 1.29). The 4% cap hits no
saved winner but largely preserves existing signal timing. The 2% idea is
rejected as the next strategy change; tighter exits cannot be assumed to
improve results merely because they reduce a loss threshold.

Probe v1's new price mask retained an incorrect old R-suffix parser in its
fresh-bid qualifier; its invalid status is explicitly saved separately.
Probe v2 failed before writing a result because a broad text replacement
changed the old mask's threshold variable. V3 corrects both expressions.
Its final observations happen to equal v1's output on these data; that equality
does not make the v1 implementation valid. Both unsuccessful helpers remain
preserved and no actual strategy or backtest was changed by these diagnostics.

The next research candidate strengthens only the frozen first-setup 10s
histogram-growth requirement. Current/reentry momentum retains the existing
strict 10% requirement. This distinction preserves the ability to reenter a
strong activation episode after its initial acceleration has slowed. Native
first-setup selection, original completed producer observations, cost/sizing,
all exit rules and fixed targets remain the proposed parent authority. An
independent cold-proposal threshold screen and a staged pure native/scalar
50% growth primitive are being prepared; no Strategy 19 registration,
configuration publication, actual backtest or profit is claimed.

The independent cold-proposal first-growth screen is now complete:
`strategy18-first-setup-growth-screen-v1.json`, SHA256
`df50e33bf5d4f562dc4606dec54581c681d43240b82ed30e3c6c4172a7b05d64`.
All 36 actual positions matched their committed entry proposals. A global
first50% rule retains observed PM18 +$521.34 and PM19 +$609.55, but only AH18
+$199.33 because it removes BIVI +$464.97. Both GNPX entries retain their first
69.85% growth despite the later reentry's lower current growth; the major YJ
and BTCT winners also survive. Global60% removes two more PM19 losses but
still discards BIVI. Total screened sums are not the four-session objective.

Therefore the explicit next candidate is **premarket-only first50%**:
first structurally eligible setup boundary strictly before 09:30 ET uses
positive 10s histogram above prior plus 50% of its absolute magnitude; cutoff
and later setup clocks retain the existing strict10% predicate. AH first/current
momentum and all current-entry/reentry comparisons remain unchanged. There
are no ticker or date exceptions. This session distinction is a development
hypothesis from these two dates, not evidence of independent generalization.
The existing native first-selection plan remains the authority; stronger
initial growth does not select a later first setup or reset after closing.

Eight durable source files stage the pure native/scalar scoped rule,
`CertifiedInitialMomentumGrowthPlan` refinement, exact18 parent release,
configuration compiler, publication CLI and focused tests. Strategy19's exact
parent is `strategy-one-18:c05b441d-c5cc-4ac6-a4a1-50bac086d16b`, payload
`f1a2b7347021fab5139658571ca462b238829e63751eb517e386d2d2fe5c26db`.
The refinement reuses the immutable initial selection and adds a new content
seal, preserving current eligibility and exact anchor provenance. It performs
no source reads; producer comparisons are vectorized. Cutoff/scalar/prefix,
Float64/source-clock, missing-data, overflow, immutable-array, forged-mask/seal,
weak-first/later-strong and strong-first/later10% tests passed. Overflow in the
new50% comparison is evaluated only inside its session scope; unchanged10%
validation still applies to all rows.

Root combined validation passed **73 tests in 4.02s**, including existing
Strategy18 scalar admission and original initial compiler tests. Publication
CLI help works and missing approval arguments fail before DB access. The
Strategy19 configuration suite has 4 passing cases and 17 cases blocked by
its deliberately uninstalled registry contract; full publication/registry/
source-certifier integration remains required. No guard was weakened to
make these cases pass.

A read-only full certified-source compiler run on both dates independently
matched all 36 saved proposal classifications and first anchor clocks, and
proved AH eligible masks identical to Strategy18. Full staged compilation
including the parent selection took **0.214s / 0.358s** on Aug18/Aug19.
Receipt: `strategy19-staged-native-certified-validation-v1.json`, SHA256
`85cc1e977aef4d9cb4c4d51c9face6aaff7edf36f3b0e3fbaf7bc6c460799243`.
This is real certified-input implementation validation, not a Strategy19
backtest or profit result. Next: integrate numbered runtime/certification and
normalized entry recovery, validate all runnable routes, commit/push and pin
exact clean source, publish the immutable19 configuration, and execute/audit
all four actual sessions. Strategy18 remains the newest published release and
continues to meet 3/4 targets. No services restarted. Two existing workers
completed four bounded assignments; no new agents, nested agents, worker
failures or interruptions. All root diagnostic subprocesses finished.

### Strategy19 runtime admission and cold-source integration

Strategy19 now dispatches through the numbered Backtest runtime, static/native
first-selection plan, source-bound scalar entry factory, normalized current
and first companions, direct/compound commit verification, cold entry pages,
manager snapshots/recovery, immutable configuration registry and saved history.
Its only trading change remains PM first-setup strict50% growth; AH first and
all current entries retain strict10%, with inherited first-minute exits and
other Strategy18 sizing/protection/session rules. No new table or schema.

Review found an omitted19 branch in cold manager restoration; it now validates
the exact original first selection and scoped growth before state mutation.
New tests exercise qualified/weak manager recovery, exact Float64 cold entries,
cross-number companion rejection, original selection tokens, strict thresholds,
AH preservation, runtime rejection before portfolio admission, and inclusive
60s failure eligibility. Source mutation tests reject weakened50%, expanded AH
scope and substituting current indices for immutable first indices.

Reviewed AST seals include the two new whole modules and manager snapshot
projection. Receipts are `strategy19-reviewed-source-seals-v1.json` (SHA256
`9a801e76e6ffac4a773c64d358da53a0da11e67554a7be1d5af9ec4da3ae7a2e`)
and `strategy19-manager-seal-review-v1.json`. Projection certification19:
`e76d7b58867e8ee58a3220e852b8a18372eb10ef4a2551430a6e0b436d3db45b`.
Existing unsupported-number fixtures now use20; source-mutation fixtures match
the exact installed19 routes and retain rejection assertions. A transient
PowerShell fixture-edit error was repaired from the unchanged committed originals
using exact replacements; focused post-repair validation passed136 tests.
The initial broad run (1113 passed /15 failed) was retained; failures covered
stale fixture literals and a source seal changed during the run. Final broader
validation follows before publication. Existing18 clean source remains immutable.
Two reused workers completed; no fresh/nested agents or interruptions. No service
restart, Strategy19 publication or actual19 P&L claim at this integration stage.

Final integration validation: **1130 passed in 86.60s**; log retained as
strategy19-broad-validation-v2.log. All root subprocesses finished. Next is
clean source pin, immutable publication and four actual extended-session runs.

### Strategy19 published actual four-session result

Implementation commit `69626bca7552e26e46180dd19316caf8b739c13c` was pushed
before clean local/workstation pins were created. Both checkouts were clean.
Local: `C:/Users/g835l/.codex/worktrees/strategy19-reproduction/quant-research-workbench`.
Workstation: `D:/TradingML/codes/quant-research-workbench-strategy19-69626bca7`.
Pinned fingerprint `93ea95657f5e59b638c344eeafb3727c5afa76e9b3394c78014edd85d1e1b6f8`;
exact pinned projection proof `97d7e857a2eea7e591f58ac2161e566577480885329e9a54f50773dffe0a9021`
(the earlier working-tree proof above predates clean-checkout line-ending normalization).
Existing companion storage policy and actual active parts were checked before
strategy writes; no table, grants or credentials changed. Source/storage receipt
SHA256 `0c179073b651326a1226c1eea14053fa93c746299307dcc5de38e13e22448e53`.

Published1019 nodes as `strategy-one-19:24339a6e-8a8c-4c4d-a187-6b756edce405`;
payload `eb0e317c9eaada759e9b4d1d9b6f366506663fefa2e7375ff113c882b6840c8e`;
coverage-last publication certificate
`9e2445a2b85dae6c37a0a998d9b5c6b720b5d98a76f393681bd4534892f7f482`.
Four sequential actual app-route runs completed with independent$10000,
LGHL exclusion, certified market inputs and inherited AH V7 seed/warmup contract.
No services restarted; UI loaded-source refresh remains deferred by user instruction.

| Session | Actual run | Positions | Net P&L | Broker marked maxDD |
| --- | --- | ---: | ---: | ---: |
| Aug18 PM | `7154f5ed-8b05-41b6-a88d-08d3b191c611` | 12 | +$208.762320 | $748.032080 |
| Aug19 PM | `8cb4005f-eea6-48f8-a2f9-15b1a4f4b429` | 8 | +$608.262595 | $1558.262785 |
| Aug18 AH | `58ed195e-5c9a-4b46-bc0b-69ca67fc6d89` | 5 | +$664.302640 | $400.965520 |
| Aug19 AH | `f28b32e4-df35-450d-85b8-d3e9c07f3c36` | 4 | +$1060.057850 | $332.410000 |

Total **+$2541.385405**,3/4 target sessions. Target not achieved. Both AH
results and observed marked drawdowns exactly reproduce18; PM marked DD
improves slightly/broadly but these extrema have asynchronous/stale-mark limits.
PM19 improves from-$80.60 to+$608.26; PM18 declines from+$791.24 to+$208.76.
The source-screen retained-profit sum overstated PM18: actual19 admitted four
new losing XOS positions at04:39:21.5,05:54:46.1,05:57:07.7,06:26:10.7,
and retained quantities/P&L changed. Screened old-position deletion is not an
actual portfolio counterfactual. Retain19 as mixed development result; no claim
of independent edge or universal improvement, no date/ticker-specific switch.

Campaign receipt `strategy19-four-session-campaign-v1.json`, SHA256
`b098d2c3158a1fc012e294abff808f57ec614c05892bc3afaf92546c6b99ccee`.
Comparison receipt `strategy19-four-session-comparison-v1.json`, SHA256
`bbb17d87dce3be7c09727ab270dffc9064dc0ebc4a9ec017b99ecaf6a3705a8b`.
Cold reports/position tables are under `strategy19_reports_v4/<run-id>/`.
The actual audit reconstructed the exact certified native first-selection prefix
and all three tokens for **all29 proposals**, verified exact producer Float64
values/attempts/clocks for current and first observations, recent BOS/current10%
and scoped first50%, and matched all29 filled positions. There are58 new first
companions; table total130 rows on sole active `live_market_ssd` part.
Audit receipt `strategy19-actual-entry-source-audit-v1.json`, SHA256
`f5e4043f7e1848b555513649895a768c50249d3997c7f0be48972aa1812fd991`.
All campaign/audit subprocesses terminated successfully. Keeper teardown retry
messages followed completed receipts; no run failure or service restart was needed.
Next research must restore PM18 opportunity while retaining PM19 false-entry
reduction;18/19 provide saved source evidence for a bounded next hypothesis.

### Additional development day and revealed frozen19 holdouts

User requested three additional days: one development, two holdouts, and then
explicitly requested Strategy19 results on all three, PM/AH separately. Dates
were selected using coverage metadata before trade/P&L reads. Initial split
Aug04 development /Aug05 validation /Aug06 final test was frozen in
`strategy-research-session-split-v1.json` (SHA256
`eb9d023fde67a360fe738b5511b46b9ef33adae333f73f89d479fbf7cb099578`).
The19 baseline source/configuration was frozen before outcomes, protocol receipt
SHA256 `65276493ac09548d09121ef75b01701b4796ee24646ff651fc6075502e92184f`.

Aug06 failed closed before launch: LGHL has15 candidate boundaries, while the
shared LGHL exclusion needs separately certified products for nonempty removed
coverage. Existing empty-scope restoration must not be generalized without a
producer contract. No source guard weakened, excluded ticker reintroduced,
regular-hours run or market repair/rebuild performed. Failed preflight retained.
Root explained replacement before selecting Aug10, based on entry coverage and
zero LGHL candidate count only. Split v2 SHA256
`64972298edfdead87124c8144ff53d66cb8062ba4740963566b08606a3731a0d`.
Final dates: Aug04 development, Aug05 validation baseline, Aug10 final baseline.
These earlier dates are date holdouts, not chronological forward tests relative
to Aug18/19 training. Requested baseline reporting reveals outcomes; Aug05/Aug10
remain excluded from tuning and cannot be described as untouched tests of later
versions. No detailed holdout trade research was conducted; automated source
correctness auditing is distinct from strategy research.

All six actual app-route sessions passed full preflight and completed with
independent$10000, shared dated LGHL policy, no residual open lifecycle and the
same pinned19 source/configuration. No backend/services restart. AH source seed
and persisted interval/entry checks remained required; full source audits follow.

| Date | Role | PM run /net P&L | AH run /net P&L |
| --- | --- | --- | --- |
| Aug04 | Development | `185c2471-b59b-4e15-8c75-c1f47f35d06d` /-$918.137270 | `9b21d379-1cd3-44c3-91b3-46a270b1b00b` /-$747.058030 |
| Aug05 | Validation baseline | `6c9ca763-a871-4bd7-8552-781463a230ca` /-$70.389230 | `01356625-16b3-41a1-bdee-aab8902bcbba` /-$228.535000 |
| Aug10 | Final baseline | `fbb745e8-2378-43c4-bcb1-2acfc5e787ef` /-$575.178295 | `6ec5d809-646e-4968-b397-e4d15007dab4` /-$949.616205 |

Total **-$3488.914030**,0/6 sessions reach target. Positions6/3,6/4,9/9;
broker marked maxDD PM/AH respectively$1330.913940/$776.933040,
$545.467720/$312.780000,$1085.002395/$1223.308805. Marks remain asynchronous
and can be stale. Results contradict independent repeatable edge for19; original
three-of-four targets were development-specific. Canonical receipt
`strategy19-three-additional-days-performance-v1.json`, SHA256
`c45acffadf82970612748507d47337529667642c3724f63d83d0bf19f13a7733`;
actual reports/position tables under `strategy19_additional_reports_v4/<run>/`.
Aug06 rejected attempt remains in original campaign; replacement two actual
runs have their own campaign. No failed result overwritten or counted as profit.

Before the new date outcomes were available, root extracted causal candle
features for all65 original18/19 proposals on Aug18/19, exact certified bars
attempts at saved proposal and immutable first clocks. Completed1s/5s/10s
current and two prior buckets, OHLC integer values, validity, volume/trade counts
were persisted with missing bars explicit. No MACD recomputed or forming/future
bar read. Receipt `strategy18-19-causal-entry-candle-features-v1.json`, SHA256
`138cbe0fc01234ee2cf6424e14435cde43ba6957a180cf9ab185fd0c1fe8bf0e`.
A descriptive screen suggests testing PM first completed1s close above the
immediately prior1s high while keeping first/current10% and AH18 rules: retained
old18 PM sums$1020.41/$1167.15. Current-green and uniform liquidity-growth rules
remove major good moves. These are hypothesis screens, not portfolio profits;
19 already proved deleting old positions is not a portfolio counterfactual.
No Strategy20 source/rule was implemented. Next research includes Aug04 only
alongside original dates; holdout outcomes/details must not become tuning inputs.

Final cold audit passed **all37 actual entries** across the six added sessions:
exact producer Float64 values, first/current clocks, native first-selection
prefix and all three tokens, BOS/current10%, PM first50%, original filled
position linkage, two normalized first companions per proposal and SSD parts.
Receipt strategy19-additional-entry-source-audit-v1.json SHA256
4db41839ebc11e00c59c12c4ec8f9c104c0092a06d71aa5f84508cd4f8a9b057.
All root campaign/review/diagnostic subprocesses finished; no new agents were
spawned or reused this turn. Goal remains active: neither original four-session
target nor repeatable edge is achieved.

### Strategy20 staged first-setup price comparison

Strategy20 is staged from Strategy19: premarket additionally requires the first
setup's completed 1s close to exceed the immediately preceding completed 1s
high, with both producer validity flags present. First PM momentum50%, current
momentum10%, sizing, costs and after-hours behavior remain inherited. Missing
adjacent bars reject without carrying older observations. Integer prices retain
UInt64 precision. The compiler selects the parent's immutable first indices;
later breakouts cannot replace a failed anchor. Arrays and policy/content tokens
are frozen. This stage grants no runtime admission or source certification.

The saved source screen retains all Strategy19 original-date winners but still
loses on Aug04; screened sums are not backtest profit. Aug05/Aug10 remain outside
tuning. Source evidence is under the campaign runtime root in
strategy18-19-causal-entry-candle-features-v1.json and
strategy19-aug04-development-candle-features-v1.json. The staged reducer passed
74 focused existing/new tests; the added anchor compiler passed32 focused tests.
Certified bars loading, normalized price evidence, numbered registration,
release/source certification and actual development backtests remain pending.
No Strategy20 backtest or app activation has occurred.

The staged first-price source loader now pins the exact certified market and
Strategy19 parent, checks each bars attempt against candidate coverage, and
requests only referenced original PM anchors. Each SELECT is bounded to512
exact 1s bucket keys; no AH price read or older-bar fallback is required. Arrow
UInt64 prices and UInt8 binary validity flags are checked without float
conversion. Duplicate/unrequested bars, nulls, wrong source attempts and
malformed flags fail closed. Scope, observations and parent identity have a
separate immutable source seal. Native array operations align returned bars.
Focused source/anchor/parent checks passed62 tests. This is implementation
validation only: actual certified-day execution, normalized price companions,
numbered admission and immutable development backtests remain outstanding.

The source-bound Strategy20 compiler now exposes admitted-only momentum and
price witnesses. Each later candidate's price witness resolves through the
original first index and must match the first MACD witness clock; the witness
uses the certified bars attempt, market token and exact integer producer fields.
After-hours explicitly has no additional price witness. Selection seals include
the source seal as well as the parent-derived comparison. Focused checks passed
65 tests. Numbered runtime admission and persistent price evidence remain
pending; this compiler does not itself authorize an installed strategy.

A separate staged trading_first_price_entry_v4 contract preserves UInt64 close
and prior high plus binary producer validity flags. Its projection/restoration
binds first clock, producer build, market token, candidate/entry/selection seals,
entry/episode clocks and deterministic parent record identity. Parent19 first
and current momentum rules are checked before projection. AH emits no price
companion. Focused new source/selection/journal and existing anchor checks
passed81 tests. This contract is not registered in production writers and no
table was installed: whole-graph sealing, source-token crosslinks, journal
recovery, operator storage/grants and numbered runtime admission remain required.

First-price restoration now requires independently supplied expected witness
and price-source seal: row-contained values cannot authenticate themselves.
The staged graph sealer checks the exact20 entry population, independent typed
source receipts, parent intents/events, run/batch/month scope, deterministic
record identities, timezone-aware source clocks and content hashes. Missing,
extra, duplicate or changed evidence fails closed. Sparse row groups are indexed
once per batch. AH restoration accepts only an empty price companion population.
Focused source/compiler/new-and-existing journal checks passed96 tests. Writer
registration and operational persistence remain pending; no actual20 result
or source-plan deployment is claimed by these staged tests.

### Certified Strategy20 source audit and development big-move study

The new bars loader/compiler ran read-only on all four original certified19
session prefixes. All saved-entry scalar lookups and original anchor checks
passed. Receipt strategy20-certified-price-source-development-audit-v1.json
SHA256 df196008cb0f3585abac6cde4799f25708c631b6856df78ce7a5cbb24a8cca96.
This is source integration evidence, not an actual20 portfolio backtest.

User requested comparing large moves across all development dates. A certified
completed1s-bar study now covers88 earliest Strategy12 structurally eligible
episodes across Aug04/18/19 PM/AH, including episodes19 never traded. Exact
source attempts are pinned; only PM/AH price paths are queried. Native ClickHouse
range windows compute prior60s volume/trades and future15m extremes. Future
extremes label outcomes only and cannot become entry inputs or executable PnL.
This population does not include market moves lacking a Strategy12 episode.
Right-censored near-end observations are marked and excluded from comparison.

There are18 uncensored episodes with at least20% future15m upside; Strategy19
traded7 of them. Median prior60s volume/trades are219227.5/1925 for big episodes
versus247888/2068 for the68 other uncensored episodes. Higher absolute volume
therefore does not separate winners here. Only6/18 big first setups pass PM50%
10s growth, compared with50% of the other cohort: this descriptive observation
questions stronger first-setup momentum as a general opportunity filter. AH
retains10%, so this PM50% feature is not the AH admission rule. Median BOS age
is3.8s versus5.5s; sample is small and ticker/episode outcomes are correlated.
Missed opportunities include SLE63.1%, AMIX57.9%, ZNB48.8% and EHGO31.6% from
the corresponding first anchor. These are hindsight bounds, not tradable gains.
Future15m minimum prices may occur after peaks; do not use them to choose an
early-stop threshold without tracing the price path before each peak.

Artifacts under campaign runtime root:
development-big-move-episode-study-v1.json SHA256
4c4ead0b5b4b777b2915e3568c35501517921bfa14fcbb0dfbe9e695a314d064;
development-big-move-feature-comparison-v1.json SHA256
ebcfb584f139de4d39424c63b8daf1bb14300b424ad3b5505895b8d701b0db0e;
development-big-move-feature-comparison-v1.md contains per-episode source
features, bounds, actual19 position counts/PnL and entry delays. No holdout
sources researched, no float fallback, no services restarted. Both read-only
jobs are terminal. Next: trace missed-anchor rejection and before-peak paths,
complete20 runtime admission, then evaluate actual portfolio behavior.

### Before-peak paths and extended causal feature inventory

User additionally requested fundamentals, float, RVOL, V7 levels and available
ARTE indicators. Read-only enrichment completed for all88 development episodes:
explicit setup-time ticker-facts requests (3 bounded workers,0 failures),
certified producer EMA/RSI/ATR fields at exact completed1s/5s/10s/30s/60s buckets
where certified, and previously certified V7 entry stop/target geometry. ARTE
metadata inventory confirms no persisted RVOL/float field in these market tables.
Aligned prior20-session RVOL and a full causal streamed V7 book remain pending.
Do not query retrospective current-session V7 intervals as setup-time features.

Raw historical reported float exists for34 episodes, but5 of9 big episodes with
reported float predate later known reverse splits (AMIX, GNPX, SGLY, MSS, ZNB).
These are pre_split_report_only and excluded from float comparisons in the
superseding extended-feature-v2 report. No guessed adjustment or outstanding-
share substitution is made. Only4 big and10 other uncensored episodes have
reported float not preceding the last known split; freshness/identity/source
provenance still matters and this cannot establish a low-float entry threshold.
v1 raw-float medians are superseded; artifacts are retained rather than altered.

The18 big-episode trajectories have exact certified 1s paths through15min.
Before-peak lows exclude the anchor and peak candle (intrabar high/low order is
unknown). BIVI PM19 reached21.5% upside after57s with0.6% adverse move beforehand,
but its actual19 position stopped out after1206.8s for-171.05: investigate profit
protection after demonstrated expansion. BIVI AH18 and SGLY PM18 exited at fixed
targets after55.3s/12.9s before larger50.6%/36.9% opportunities; investigate a
protected runner rather than merely raising all targets. GNPX peaked after43s
and reversed below its anchor by120s; YJ's profitable19 position took434s to
first20% and was approximately flat at120s. A blanket short time stop risks
removing that winner. These paths motivate causal experiments, not optimal
executable profits or a new tested policy.

Artifacts under the campaign runtime root:
development-big-move-prepeak-paths-v1.json SHA256
e4c3ce6c98764adb01a0fec2b10c40c120ea80fd0cfd075c662c2df97abe4aff;
development-episode-fundamentals-asof-v1.json SHA256
0ca01c6ae806c4bcdafaea0b45df431b6150d299d531521608857417f20c4732;
development-episode-technical-features-v1.json SHA256
13cfd37ce56f583841ae6d3970f6f942a6c353970246f3c70bab7329e6d81f08;
development-extended-feature-comparison-v2.json SHA256
c226d80e5e3c9c086c2c00149f2973f301e1fc06865a7b082133f5f53ad9a2db.
The paired v2 Markdown report compares float availability, ready RSI, EMA
alignment, causal V7 target room, pre-peak paths and actual19 episode PnL.
All jobs are terminal; no source writers, service restarts, holdout research or
new agents were used. Actual20 backtests and the profit goal remain pending.

### Strategy20 typed proposal and certified projection binding

The shared entry proposal now has explicit optional first_price and
price_source_token fields. Existing installed intent admission rejects nonempty
unpublished price evidence; its numbered whitelist remains1–19. The certified20
binder starts from an exact parent19 proposal, verifies its momentum and original
first-selection witness against the native parent plan, reuses complete19 scalar
validation, then attaches20's native selection, price witness and source token.
All financial/protection fields are preserved. This does not submit an intent.

Certified price projection rebinds the complete proposal against the source
plan and requires event month equal to the certified market month. It returns
immutable normalized rows and an independent FirstPriceEntryAuthority together
for the graph sealer. Changed witness values or source selection fail closed.
The shared intent AST seal was reviewed and updated only for the added
unpublished-evidence rejection guard; positive source certification and a
guard-removal mutation test both passed.138 focused checks passed across
new binding/projection/journal, existing intent/stateful admission, numbered
source/configuration guards, typed projection and cold manager snapshots.
Writer/compound transport, numbered release admission, operational storage and
actual20 development backtests remain pending. No backend restart or registry
activation occurred. All test processes are terminal.

### Strategy20 staged transport, native reload and financial routing

Direct and compound journal transport now carry normalized first-price rows
and independent typed source authorities. Compound batching rekeys the row's
batch identity without changing its market witness. The optional cold-read
source reconstructs expected witnesses from one cached certified native plan;
journal rows provide only identity keys, never their own price authority.
The operational table and write grants remain uninstalled. Cold-prefix and
manager recovery still need the source context propagated through their callers.

Execution preflight now builds the staged20 source plan and combines its price
mask with the inherited full19 gate. The sequential financial adapter checks
exact certified entry facts and activation, preserves parent financial rejections,
and binds a20 proposal only after admission succeeds. Activation evidence is
indexed once per plan. Coordinator checks preserve broker-before-financial order
and carry the complete source witness. Its20 tests use an explicitly simulated
inherited contract; the installed numbered registry still rejects20.

The reviewed source guard now binds the five staged price modules and updated
coordinator. Source-token and dispatch mutations are rejected, and the positive
baseline certificate passes.109 focused checks passed across price source,
native selection, financial adapter, coordinator, existing execution/stateful
paths, companion graph and transport. These are implementation checks, not
Strategy20 portfolio runs. Numbered release, intent admission, complete typed
projection/recovery, operational storage and actual development backtests remain
pending. No new profit result, backend restart or holdout tuning occurred.

### Strategy20 companion graph and explicit cold-prefix context

The normalized current and first-setup momentum families now recognize staged20,
with current strict10% and premarket first strict50% inherited from19. Their
graph seals require matching numbered parents, source clocks and selection
receipts. A source-bound20 fixture successfully seals those four MACD rows
alongside its one integer price companion; cross-number substitutions fail.
This extends evidence representation, not the installed intent/number registry.

The cold-prefix reader accepts one explicit cached certified price source and
forwards it to every commit verifier. Idempotent publication readback forwards
the original independent entry authorities. Neither route permits companions
to authenticate their own source values. The source guard now seals these
readback entry points and the full updated momentum modules.206 focused checks
passed across source certification, companion graph, journal/compound transport
and19 regression paths. Actual20 persisted portfolio runs remain pending; warm
writer snapshots, entry-page/manager recovery, intent admission, numbered release
and operational storage still require integration before publication.

### Strategy20 warm snapshot source identity

The writer-owned warm snapshot API now accepts the same explicit cached native
price source as cold verification. It forwards that source through head-detail
verification and through the full-prefix scan required for a replacement writer.
Cached warm proofs record run, selection token and price-source token; requesting
a cached proof with omitted or changed price authority fails closed. Existing
source-free calls retain their original paths and argument shapes.

Keeper-owned head reuse and replacement-writer checks exercise both source-free
and source-bound contexts. Foreign-run and omitted-source cache reuse are rejected.
The reviewed source seal includes the warm snapshot function, and removing its
cache-identity guard is rejected.105 focused checks passed across warm/cold journal,
OMS observation snapshots, native price selection, companion graph and transport.
Entry-page and manager recovery, complete typed projection, intent/release
admission and operational storage remain pending before actual20 portfolio runs.

### Strategy20 staged native intent and bounded entry recovery

The source-bound staged intent constructor independently rebinds the complete20
proposal, validates the exact certified session and inherits every19 financial
and execution field. Only its numbered deterministic intent identity changes.
Shared entry-evidence projection requires an explicit native source context for20.
The ordinary runtime intent factory and installed numbered registry remain closed.

Bounded entry-page recovery reads current/initial MACD and integer price companions,
seals their full graph against the native plan, and restores the complete proposal.
Run, batch and parent identities are mandatory independently supplied scope.
Direct source reconstruction also compares the recovered price fields. Direct
and compound journal round trips recover exact proposals/intents in the typed
test client; individual source reconstruction retains its inherited exclusive-
batch restriction. No operational table or actual portfolio backtest is installed.

These round trips exposed a readback bug: canonical ClickHouse UTC DateTime64
JSON has no offset, but the price graph requires timezone-aware events. The
schema-aware verifier now restores the declared UTC timezone after canonical
row-hash verification, only for20 entry graphs. Hot-input timezone validation
is unchanged. Both publication modes reject rehashed forged price values against
the independent source.149 focused regression checks passed across journal,
compound/recovery,19, source guards and ordinary intent paths. Manager recovery,
hot typed projection, runtime admission, numbered publication and operational
storage remain pending; no new portfolio PnL or holdout tuning occurred.

### Strategy20 manager source recovery

Manager snapshot recovery exposed an existing Strategy19 round-trip bug:
scalar snapshots deliberately omit entry witnesses, but their shape verifier
called the public capture serializer, which requires those witnesses. A private
scalar encoder now checks the unchanged scalar shape and hashes. Public capture
still validates entry sources; attested recovery and publication join verified
journal entries before returning or publishing a qualified state.

Staged Strategy20 capture and manager restore additionally require the exact
native price source context and run identity. Pending and held snapshots recover
all witnesses from actual typed entry-journal fixtures. First-held boundaries
remain mandatory for held20 positions. Recovery scans retain their existing
bounded paging; the source plan is reused rather than recomputed per entry.
The reviewed source seal covers the scalar encoder and recovery route separately.
Operational runtime wiring, numbered release and actual20 portfolio backtests
remain pending. These tests do not establish profitability.

### Strategy20 memory journal and typed prefix

The memory journal now independently validates staged20 intents against an
explicit native source context before retaining their proposal sidecars. V4
prefix projection requires the same run-bound context and carries integer price
rows together with their independent sealer authority, current MACD and initial
MACD companions. Missing context, foreign run identity and changed source values
are rejected. Protection intents retain their mandatory typed-source guard for20.

An actual memory-journal record now traverses projection, typed publication and
cold entry recovery with exact proposal and intent equality in the typed test
client. This does not activate the numbered strategy or install operational
storage. Runtime admission, source-context transport and release wiring remain
required before the actual portfolio campaign. Existing financial fields,
costs and journal acknowledgement ownership are unchanged.

### Strategy20 cached runtime and publisher context

The certified session runner now constructs one run-bound native price authority
from its compiled20 source plan. It binds that authority to runtime entry
validation and, through the controller, to V4 prefix projection before entry
processing. Neither route recomputes the source plan per trade. Bindings require
the correct run, session/configuration and Backtest mode; rebinding is rejected.
The ordinary installed numbered registry still excludes20 until release.

The publisher and manager-snapshot queue carry the same immutable context to
capture and publication. A real writer-queue fixture checks this transport using
real capture and scalar recovery, with only the publication callback replaced;
it is not operational Keeper publication evidence. The native memory-prefix
fixture additionally exercises the real publisher projection route. Source
guards now cover runtime validation, source binding, publisher forwarding,
controller callback and snapshot worker transport, with dropped-context
mutations rejected. Numbered release, operational price-table provisioning and
actual portfolio PnL remain pending. No holdout tuning occurred.

### Strategy20 operational price storage catalog

The normalized first-price companion now belongs to the V4 operational storage
catalog, commit-layout planner and Backtest runner SELECT/INSERT grant plan.
Its existing table contract declares `live_market_ssd`; runtime storage
preflight verifies schema, policy and actual parts before writers can start.
The runner gains no market writes or DDL privileges. The installer and
provisioner remain dry-run by default.67 focused catalog, provisioning,
installation, transport and source-certification checks passed. Workstation
installation and grant verification must follow this committed source revision.

Workstation deployment from clean detached source `044eccf3f` completed: the
installer verified20 existing commit-profile tables and created exactly the
first-price table, then verified21. The Backtest runner authenticated with exact
129-table SELECT,100-table INSERT and5-table system SELECT surfaces. A separate
storage preflight confirmed the table is MergeTree on `live_market_ssd`, with
zero rows and no parts; actual part placement must be rechecked after its first
write. No source/market rows were inserted. Deployment receipt:
`strategy20-first-price-storage-deployment-v1.json`, SHA-256
`e728dd1e3a5a0679fdcf3a02dd1bf87824b1e3e90cf0eb7ee486c4896e07a34d`.
The initial stdin evidence probe stalled without creating a receipt; its
identified process was stopped. The bounded runtime-file probe completed using
the provisioner's pinned endpoint. Numbered release and actual20 PnL remain open.

### Strategy20 numbered release and cold terminal review

The immutable numbered registry, configuration compiler/publisher, launch
certifier, management, OMS, session liquidation and saved-history paths now
recognize20. The compiler derives only from the exact installed19 revision
`strategy-one-19:24339a6e-8a8c-4c4d-a187-6b756edce405`, payload
`eb0e317c9eaada759e9b4d1d9b6f366506663fefa2e7375ff113c882b6840c8e`.
The sole entry change is the frozen first premarket setup's completed1s close
strictly exceeding its immediately prior completed1s high. Missing/invalid
adjacent source bars reject that original setup without selecting a later one.
AH eligibility, financial parameters, costs, original ask cap, V7 seed/warmup,
targets, protection and inclusive first-held60000ms failure window inherit19.
Live and public interrupted-run resume remain closed.

Terminal publication transports the exact native source through the immutable
writer queue and requires cold prefix verification before snapshot anchoring.
An entry-bearing20 journal fails that audit without native authority; the same
terminal suffix can recover with its original certified authority. Empty sealed
horizons retain the runner's no-entry behavior. Saved review reconstructs its
native source from the independently fenced definition, exact released
configuration, rechecked market/passive-fill/V7 seals and sparse producer reads;
journal price values never supply native authority. Reconstruction occurs only
on a cold attestation, with existing bounded scalar-only financial caching.

372 focused checks passed, including native20 entry-to-terminal cold recovery,
rejection of changed market tokens, inherited financial/configuration equality,
registered Backtest-only execution and complete19/20 source certification.
Saved-review bootstrap unit tests use explicit certified-plan fixtures and do
not substitute for operational execution. Reviewed terminal-route AST receipt:
`strategy20-terminal-source-reviewed-seals-v1.json`. The publisher's real
`--help` path and scoped `git diff --check` passed. Immutable configuration
publication, real20 portfolio runs, populated UI readback and first-write SSD
part placement remain pending; screened19 trade sums are not20 PnL.

Strategy20 publication subsequently completed from source `73bf43e73`:
revision `strategy-one-20:40e320de-bf97-4c0f-9c2b-e7c339325f7f`, payload
`90be668fdfab207d8149710a5646fb6563f4f93433ee691aa3f7e8746bd7cd53`,
release token `f05fac7b6c529f99fd5bccc33948148cc062ff35b320a9390c09615b2221a485`.
The actual Aug18 PM app route passed full-market preflight with $10000 and LGHL
excluded, then failed before processing market rows. Preserved failed run:
`154e92c6-4af0-4e9f-bcb4-eff8e49ea9fe`; campaign/log evidence is
`strategy20-development-six-session-campaign-v1.json` and
`strategy20-pm18-app-route-v1.log`. This is not profitability evidence.

The confirmed binding bug compared the journal's execution-creation month
(September) with its historical market session (August). The repair checks
the exact certified parent market plan token/session instead. A second instance
passed run-month into the price companion; it now uses the parent intent's
historical event month. Native publication and cold recovery explicitly verify
an August entry in a September-created run.59 focused checks passed, followed
by10 native entry/recovery checks after extending the cross-month cold readback.
The repair changes the approved backend fingerprint. The published20 approval
and failed run remain immutable: operational acceptance must use the next
numbered release rather than silently execute repaired code under20's old seal.
No afterhours or additional development20 runs were attempted after this failure.

### Strategy21 repaired historical execution release

Number21 derives solely from the published20 revision above. Entry masks,
frozen-first selection, all inherited policies, rule sets, parameters, sizing,
costs, V7 seed/RTH warmup and exit behavior remain identical to20. The release
binds the repaired execution source under a new immutable approval rather than
altering20's release. Native intent identity, normalized price/momentum
companions, direct/compound cold readback, manager snapshots, terminal audits,
registry, reporting and history now recognize21. A rehashed20 price companion
attached to a21 entry fails its exact numbered-identity check. Ordinary legacy
entry intents remain closed to both native20 and21.

Review also found the shared fixed-session wrapper, protection/session/failure
memory journal and order-command projection lists still stopped at19. These
reachable routes now explicitly admit20/21; parent financial/vector adapters
continue to execute the exact19 financial decision before native rebinding.
The fixed-session callback test exercises cutoff clock advancement and residual
exposure rejection; native source/proposal publication is tested separately.
428 focused checks passed after those final shared-route changes, including
complete20/21 source certification and inherited configuration equality. The
publisher's real help path and scoped diff check passed. Reviewed source receipts
are `strategy21-reviewed-native-release-seals-v1.json` and
`strategy21-reviewed-shared-session-seals-v2.json`. Workstation synchronization,
immutable21 publication and actual six development-session portfolio runs are
the next operational acceptance steps. No additional holdout tuning occurred.

Operational21 publication completed from `eebb7e6a2`, synchronized clean on the
workstation: revision `strategy-one-21:c7b1a834-fb6b-47f0-9ad9-839120e6f898`,
payload `82e62bc27d3a97245bbf73b0a2e1f68d47c193b3189beccb543ee8343be53e00`,
release token `02e182d05a1900e9b832498cf2447a0096a4f880d99ac76310d68eca2d628cec`.
Aug18 PM full-market preflight passed with $10000 and the certified LGHL
exclusion. Actual run `d63ca8d2-97a1-426b-814f-d77c8262da8c` then failed before
market rows: the coordinator's initial-plan type dictionary lacked key21.
The failed campaign/log remain preserved under the runtime root. No later
session was attempted; no21 portfolio PnL exists. Terminal cleanup also lacked
completed evidence state because failure preceded the first market boundary.

The repaired native-plan selector dispatches20/21 to the exact certified price
plan type, retains19's growth plan and18's initial plan, and still verifies
identity of the entry and momentum plans. The coordinator fixture now exercises
installed20 and21 contracts without its old inherited-contract monkeypatch;
actual native financial acceptance/rejection and witness retention are tested.
34 focused checks passed, including full21 source proof. Reviewed receipt:
`strategy21-native-type-selector-reviewed-seal-v1.json`. The changed source
cannot run under21's already-published approval; next operational acceptance
requires a new numbered release. Keep entry/trade settings unchanged until a
complete baseline portfolio run succeeds, and extend the real coordinator test
to that next number before publication. Source correctness tests alone are not
portfolio execution acceptance.

Strategy22 inherits Strategy21's entry and financial policies unchanged and
binds the repaired native-plan dispatch under a fresh numbered release. Its
shared execution, publication, recovery, management, history and reporting
routes explicitly admit22. Real coordinator, cutoff callback and cold entry
recovery tests now include22; unknown23 still fails closed. Reviewed source
receipt: `strategy22-reviewed-native-release-seals-v1.json` (41 nodes).
438 focused checks passed, together with the publisher's real help path and
diff checks. Workstation synchronization, publication and actual six development
session runs remain pending. There is no Strategy22 portfolio PnL yet.

Strategy22 was synchronized and published from `5dcef0878`: revision
`strategy-one-22:be2f7a39-d211-4dde-b9bc-8058b7f89332`, payload
`2f64082aaeee76483335d2b2e1b7d5eaf42b5d4b9e0ce9ec50ec5b44172a43e7`,
release token `256e95c8887919084123f62e425dda50f587522693cb38294a7ab10c23512624`.
Aug18 PM actual run `7d38f078-a993-46f8-8813-78cd17ed522c` passed full-market
preflight and processed267 rows before failing exact numbered entry-intent
validation. Terminal evidence also failed its completed-boundary cursor check.
The failed campaign/log are preserved; the other five sessions were not run.
There is no valid22 PnL. First-price storage remained empty with the required
`live_market_ssd` table policy before and after this failed run.

Regression tests reproduced two reachable legacy intent reconstructions for
native20/21/22: publisher post-commit retention and early-failure exit management.
The repaired publisher revalidates the exact run-owned native authority before
retaining its intent. Management reuses the runtime's cached native constructor
to preserve the exact source entry identity; missing authority rejects the exit.
Entry rules, financial policy and exit thresholds are unchanged.129 focused
checks passed, including full reviewed source certification and native recovery.
Reviewed receipt: `strategy22-native-intent-routing-reviewed-seals-v1.json`.
This changed source needs a new numbered publication before an actual run.

The RVOL availability probe successfully validated SLE's Aug18 baseline against
20 complete prior canonical sessions (Jul21-Aug17), including content hash and
source revision. Receipt: `development-rvol-baseline-availability-probe-v1.json`;
baseline: `development-rvol-sle-aug18-baseline-v1.json`. This is baseline
availability evidence, not an entry-time RVOL comparison or a new trading rule.

RVOL enrichment then completed40 bounded producer units (3 prior20 baselines
and37 current ticker-day prefixes), with0 failures. All88 development anchors
use completed-second volume, separate PM-since04 and AH-since16 numerators and
matching prior20 denominators. Baseline/current artifacts retain hashes and
canonical revisions. Receipt: `development-episode-rvol-comparison-v2.json`,
SHA256 `354f8a7171daa65b1b627341a49e76a29e77409cbf06b2faf64829e4968019bf`;
review table: `development-episode-rvol-comparison-v2.md`.

Known splits crossing a baseline are excluded from descriptive comparisons;
the producer uses reported sizes without split normalization. Corporate-action
coverage remains a limitation. Excluding known split crossings, PM big-move
RVOL median was14.95 (8 ready episodes) versus738.33 (41 other episodes).
AH medians were12780.13 (4 big episodes, including two BTCT episodes) versus
378.78 (15 other episodes). These small correlated cohorts do not establish
thresholds. AMIX's big-move anchor was0.66x, SLE3.75x, and BIVI AH1.00x:
a high universal RVOL gate would discard useful moves. Combine RVOL with
absolute liquidity, trade counts and causal price structure in subsequent
development; no trading rules or holdout outcomes were changed in this study.

Strategy23 inherits exact pinned Strategy22 configuration and trading/financial
policies. Its new source approval includes repaired post-commit entry retention
and early-failure source identity, with exact cached native authority. Shared
execution, publication, cold recovery, reporting and session routes explicitly
admit23; unknown24 remains closed. Native coordinator, writer receipt retention,
early-failure exits, entry recovery and cutoff tests all exercise23.454 focused
checks passed after reviewed source seals were refreshed; real publisher help
and scoped diff checks also passed. Reviewed receipt:
`strategy23-reviewed-native-release-seals-v1.json` (42 nodes;25 common files
independently verified as explicit number-list extensions only). No23 portfolio
PnL exists yet. Publication and six actual development-session runs are pending.

Strategy23 synchronized and published from `2fe173dc7`: revision
`strategy-one-23:990e41b5-6ead-4ca3-b452-4934ed9121f3`, payload
`4f04f8bda3da810140f5a62822424fcb960dd561cb81b9c5ce6d8dd2bca9bd30`,
release token `a5af5288420f64bf45ec0203015723524d313ebe18192697737d00d7be745117`.
Actual Aug18 PM run `b862e721-1cb5-49f1-b23e-5f81333481b0` passed preflight,
processed6954 market rows and committed native entry/manager evidence. It then
failed at broker snapshot publication: `V4 writer snapshot cached price
authority differs`. Terminal evidence also failed unique typed account capture
validation. Other five campaign sessions were not attempted. Preserve failed
campaign/log; no valid23 portfolio PnL exists. The storage probe verified7 native
price rows and1 actual part on `live_market_ssd`, in addition to table policy:
`strategy23-first-price-storage-after-v1.json`.

The source omission also affected OMS-observation, evidence and campaign
snapshots. The repair transports one exact run/session-owned native authority
explicitly from publisher through four frozen queue units to publication and
cold prefix checks. Keeper/head/cursor and cached-authority checks are unchanged;
there is no implicit authority fallback. Queue tests use actual worker threads
and real snapshot projection, with publication transport isolated. Cold-reader
tests verify complete real native entry prefixes, reject missing/foreign
authority and forged price companions, and isolate already-tested snapshot-row
and cursor transport.237 snapshot/publisher/writer checks and4 additional cold
reader checks passed. Reviewed source receipt:
`strategy23-native-checkpoint-transport-reviewed-seals-v1.json` (15 nodes,
including full four snapshot modules and queue validators). This changed source
requires a new numbered approval before an actual portfolio run. Trading rules,
sizing, costs, V7 warmup and failure thresholds remain unchanged.

### Strategy 24 checkpoint-repair release (2026-10-01)

Strategy 24 preserves the exact published Strategy 23 trading and financial rules and includes the committed four-channel native checkpoint authority repair. Explicit numbered dispatch and source certification now admit 24; live trading and public interrupted resume remain closed. Validation: 230 release/execution tests and 149 source-proof/checkpoint/saved-review tests passed, including actual queued native authority transport and cold native-24 entry-prefix verification. These suites overlap; they are not a unique test count. Publisher help and scoped whitespace checks passed. Reviewed AST seal changes and 25 independently verified whitelist-only source diffs are recorded under the campaign runtime root. Publication and six actual development sessions completed with zero failed journal units and cold-verified saved reports. A further 50 runtime/registry/Strategy 19 checks passed.

### Strategy 24 actual development results and complete feature study

Published source 5949e4d83; immutable configuration strategy-one-24:c78b3815-0d1a-4e52-bb4e-084695fc1df8; payload d5f8acab96b83a1ebc88fa5a34defbd14c190d02b64540ad01af6ef43d28382c. Independent initial cash remains 10000 per PM/AH session. Actual net results: Aug 18 PM +824.610800 / AH +664.302640; Aug 19 PM +1201.957285 / AH +1060.057850; Aug 04 PM -973.818530 / AH -747.058030. All four original sessions exceed +500, but all eight Aug 04 positions lost. Six-session total +2030.052015 is exploratory development evidence, not proof of repeatable edge. The original goal remains under research while generalization and failed-breakout management remain unresolved. No new holdout outcomes were accessed.

All 29 positions, entry completed-minute liquidity, exits, P&L and separate drawdown scopes are retained in strategy24-development-performance-v1.json / .md (JSON SHA256 36c0bdf858f8de48d15dff8c18fa79094571fc5fc2fcfd723e96778b22d4b4af). Closed-episode drawdown excludes open-position risk. Broker-observed marks can be asynchronous and stale, so neither is a liquidation-equity guarantee. Post-campaign storage receipt proves 24 first-price rows in 4 parts on live_market_ssd; no fallback storage was used.

Complete development feature comparison now joins all 88 anchors one-to-one across historical float, segmented session RVOL, completed indicators and full causal V7 books. Failed full-population V7 research attempt is preserved: a zero-candidate LGHL listing lacked seed coverage. The second research attempt uses the repository's explicit anchor-ticker market projection, preserves the full parent market pin, and certifies every selected seed. It consumes completed 1s bars from the prior-day seed through each anchor, including RTH before AH. No retrospective current-session level shortcut was used. V7 artifact development-episode-causal-v7-v2.json SHA256 84623d5dccaae9e00c1f45edd8638120f5456629e6518f7f0d94b51994ac9a52, 88 rows, about 62 MB. Merged compact comparison development-complete-feature-comparison-v3.json / .md SHA256 301f3a1e7d5602eec710f82fc407ebb670f79f58762c5063490f00b4707f2586.

Median nearest admitted resistance lower-band room: PM big moves 3.33% vs others 2.19%; AH 6.66% vs 2.57%. These are descriptive small correlated cohorts, not thresholds. Float remains sparse and pre-split reports are excluded from comparisons; RVOL known baseline-window split crossings are excluded but corporate-action coverage is not guaranteed. No feature-based strategy rule changed in this release. Next work should explain Aug 04 failed entries and evaluate incremental causal resistance/follow-through/liquidity changes against all development sessions before touching holdouts.

### Strategy 25 premarket early quarter-risk failure candidate

Cold audit of all 29 Strategy 24 native filled proposals completed against certified source plans. Preferred momentum screen strategy24-native-entry-momentum-audit-v3.json SHA256 ceb8384bbe0df31442a7b26b37eb598df312cc1bb8008e0c08cd292f44fa1def corrects the previous illustrative relative-growth denominator to the exact absolute-prior histogram contract. A universal positive/rising 1s gate removes YJ, BIVI and BTCT winners. A correctly implemented current 10s >20% gate removes no PM trades and removes the AH BIVI winner. Neither gate is supported as the next change. Failed V1 audit helper used an incorrect page property; it is preserved, and V2 correctly uses scanned_through_sequence/exhausted. No strategy rules or backtests were altered by these screens.

strategy24-early-price-failure-screen-v2.json SHA256 99ea4c78e7119f7dbd1eb660b0da1acf893e28eacee6e0a1a233ff572ff047b7 checks 29 actual first-minute paths using exact certified 5s close/MACD and completed 100ms fresh quotes. First-held clocks are diagnostic estimates rounded to the next completed 100ms bucket after actual first fill; no alternative executable P&L is inferred. Completed negative-MACD quarter-risk breaches occur in PM04 DXST, KUST and NUWE before their original stops, without cutting an observed PM winner. Applying that change to AH would cut BTCT, so it is explicitly PM-only. The failed V1 diagnostic helper used an incorrect market-stage key; V2 uses certified broker_100ms authority and is retained separately.

Strategy 25 derives from the exact published Strategy 24 configuration. Sole trading change: first-held PM positions use (3 * original ask + original stop) / 4 instead of half original stop distance for the negative-MACD completed 5s failure rule; the fresh bid must satisfy the same threshold. The first-held 60000ms bound remains inclusive, and the bucket must lie wholly after held quantity. AH preserves the exact half-risk arithmetic and window. Entry selection, sizing, costs, target/protection, prior-day V7 seeds and RTH warming remain inherited. No extra market queries, indicators or per-candidate loops were introduced. A separately numbered normalized witness/factory route preserves all older predicates, identities and default factory behavior; native entry authority and public-resume closure remain mandatory.

Validation: 296 release/source-proof/native execution checks and 149 predicate/runtime/registration checks passed (overlapping suites). Dedicated quarter-vs-half manager dispatch, normalized seal/cold scalar restoration, wrong-number rejection, first-held window, quote freshness, missing/forming data, AH equivalence and resigned policy substitution were exercised. Publisher help and scoped whitespace checks passed. Reviewed AST receipt strategy25-reviewed-native-release-seals-v1.json records 44 nodes and 21 independently verified whitelist-only source files; new predicate, factory and release modules are fully sealed.

### Strategy 25 actual development results (2026-10-01)

Published source bee0dba8cec0527d6586eab99cf4542ebb0dd0d4; configuration strategy-one-25:619a9af3-105a-45f6-80bc-ea7f41545041; payload 27880a3a04125a53e14960698e14f248a1d5233e2e1a253d5b7fea6a3a751240. All six actual app-route runs completed with cold-verified reports, 29 closed positions and zero failed journal units. Each session starts independently with 10000 cash. AH retains prior-day V7 seeds and warming through regular hours; regular hours are not financial backtest sessions.

| Development date | Premarket net | After-hours net |
|---|---:|---:|
| 2026-08-04 | -825.618620 | -747.058030 |
| 2026-08-18 | +824.610800 | +664.302640 |
| 2026-08-19 | +1201.957285 | +1060.057850 |

The four original session results are unchanged and each exceeds +500. Aug 04 PM improves by 148.199910; the other five sessions are unchanged. NUWE and KUST exit earlier with smaller losses; the second DXST exits earlier with a larger loss. Actual cash-dependent sizing also changes ATPC slightly. These are actual portfolio outcomes, not quote-based counterfactual P&L. Aug 04 remains unsuccessful, and no new holdout outcomes were accessed. Total development net is +2178.251925; repeated optimization on these dates does not establish repeatable edge.

Aug 04 PM closed-episode drawdown decreases from 973.818530 to 825.618620; broker-observed marked drawdown decreases from 1259.271410 to 1208.470420. The other session drawdowns are unchanged. Closed-episode drawdown excludes open risk; asynchronous retained broker marks may be stale and are not a guaranteed liquidation curve.

Artifacts under D:/TradingML/runtimes/strategy-optimization-20260930: strategy25-development-performance-v1.json / .md (JSON SHA256 ffd4dc8a5e14014be2da735b928f44f16c4a10fa2302ac0901371db234624c42) and strategy24-strategy25-development-comparison-v1.json / .md (JSON SHA256 524c5446c6c5d325c8b4e46a30840ea8e5a6f01d0daedaa412b376332b233984). Post-campaign typed storage preflight checks first-price and follow-through tables; first-price receipt strategy25-first-price-storage-after-six-v1.json SHA256 99677b4989ce972e2f05f0e5f1755e07b9b92fce3b401e7a58bc0e6a61a992e3 confirms 41 rows and 4 parts on live_market_ssd.

One necessary managed backend refresh also refreshed the active frontend dependency; other services were preserved. Backend semantic readiness, matching desired/running fingerprints and empty drift_components were verified. The actual app history API exposes all six completed Strategy 25 runs with v4_review_available=true: strategy25-app-history-verification-v1.json SHA256 27842cf7c26ba0fbc2071697df263fff8afc5eef75aca5a98e37cf523a37899c. This is API availability verification, not a rendered UI claim.

The actual saved-performance API also cold-verifies Aug 04 PM net -825.618620 and five closed episodes: strategy25-app-performance-verification-v2.json SHA256 f8375501a9949fa4baee70abe88856f8949660df5ddbf2fd6cb217071797625a. The V1 verification helper incorrectly expected summary at the response root and failed; it is preserved. V2 follows the actual report.summary response contract. This was a verification-script error, not a backtest or app failure.

The completed 88-anchor study includes point-in-time reported float, session-aligned RVOL, full causal V7 geometry, volume/trade counts, liquidity and completed technical indicators. Float is too sparse for a defensible hard gate, and high RVOL is not a common requirement across the observed big moves. Missing fundamentals remain unknown; shares outstanding are never substituted for float. Next research should examine missed big-move entries and resistance geometry across the development days, while keeping holdout dates out of tuning.

### Missed-entry admission audit and next candidate (2026-10-01)

Two read-only audits now isolate a concrete next hypothesis without changing an installed strategy. The vectorized one-to-one join of all 88 development anchors recomputes the exact strict comparisons histogram > 0 and histogram > prior + fraction * abs(prior), at fractions 0.10 and 0.50. The 0.50 results match every existing native study result. Uncensored PM cohorts contain 14 anchors passing 10% but not 50%: six with future-15m upside labels >=20% and eight others. These future labels describe outcomes only; they are not decision inputs, executable profits or independent samples. Artifact development-missed-entry-admission-audit-v1.json SHA256 261bfb933fe09efa80b931f10bd994b360055601438a049054dd52e214c27479 includes historical float, segmented RVOL, full causal V7 resistance room, volume/trades and completed technical features for every anchor.

The second audit re-certifies each Strategy 25 PM saved-run market plan, checks its exact token against the original development study, and reads only pinned bars_v1 build/attempt rows for the immediate completed 1s bar and predecessor at those 14 anchors. It applies the installed native first_setup_price_break_mask with exact integer prices and typed clocks/validity flags. No flatfiles, new indicator computation, current reference fallback, RTH financial test or holdout query is used. All 14 anchors were checked; seven pass the existing price rule. Artifact development-relaxed-first-setup-price-audit-v1.json SHA256 158b14427852e5b9079546de6fafd31ec12eb62148bb3d95bbb0dcea451b24c4.

| Development day | Additional anchors passing both first 10% and price break | Big-move labels among these |
|---|---|---|
| Aug 04 | AMIX, SKYQ, VGAS | AMIX |
| Aug 18 | SLE, three XOS episodes | SLE |
| Aug 19 | none | none |

AMIX and SLE are rejected by the first 50% comparison at these study anchors but pass both strict 10% and the unchanged completed-price comparison. This proves those rule-level exclusions, not actual alternative portfolio admission: account availability, current entry conditions, source-selected initiality in the complete compiled population, timing and fills still require a new numbered run. Strategy 19 nonparticipation at a study anchor is not by itself proof of Strategy 25 nonparticipation. The first-setup result is frozen for its activation episode, so lowering its requirement can affect later entries and reentries as well as the anchor.

Next candidate is Strategy 26 derived from exact published Strategy 25: change only PM first-setup growth from strict 50% to strict 10%; preserve current strict 10%, original earliest structural anchor, immediate completed-price breakout, PM quarter-risk failure/AH half-risk failure, all sizing/costs/targets/protection and V7 seed/RTH warmup contracts. Do not reset initiality after a stop or choose a later qualifying setup. Give the native relaxed selection a distinct versioned policy/token and exact typed authority; carry that authority through normalized witnesses, OMS validation, all four snapshots and cold saved review without relaxing old Strategy 19–25 validators. Existing price-source reads remain bounded/vectorized and happen only for source-selected first anchors. This candidate is not implemented, approved or published yet, and no Strategy 26 P&L is claimed. Validate source/native/scalar parity and run all six development sessions before considering a frozen holdout evaluation.

### Strategy 26 staged compiler and full-population audit

The first implementation slice now exists: src/trading_runtime/strategy_initial_ten_percent.py owns the versioned policy and scalar adapter; src/backend/backtest_strategy_initial_ten_percent.py owns the separately typed CertifiedInitialTenPercentPlan. It reuses the existing certified earliest-structural-setup compiler, frozen first indices and strict 10% first/current producer comparisons. There are no additional market queries, MACD computations or Python candidate loops. The policy-specific selection token prevents its authority from being confused with the unchanged 50% refinement. Runtime registration, price-source admission, normalized witnesses and numbered approval have not been changed; the installed price compiler explicitly rejects this staged type.

Validation: 45 focused initiality/native/scalar/strong-momentum checks passed; another 20 existing source-certification and price-authority checks passed. These exercise a first setup passing 20% but not 50%, a weak first setup that cannot be replaced by a later strong one, invalid structural rows, original selection tokens, immutable masks, forged mask/seal rejection and no extra source reads. One obsolete test previously asserted Strategy 20 was uninstalled; it now verifies the current uninstalled boundary, Strategy 26. The initial run preserved that failure; the corrected suite passed.

Full-population research then re-certified the exact Strategy 25 saved definitions and market plans from clean published source bee0dba8cec0527d6586eab99cf4542ebb0dd0d4, loading the two staged modules separately. It checked shared preflight and complete definition authority equality, then compiled original 50% and staged 10% plans over complete PM candidate evidence for all three development dates. Original first indices and episode clocks match exactly; no formerly admitted pre-price row is removed. Exact native scalar restoration was checked for each additional first anchor. Source plans, tokens and compilation timings are retained in strategy26-staged-native-selection-audit-v3.json SHA256 5e3c16bfae4fed4da28d7fdc46bf59cdc7bfd192e4823ccf1a394d0b8b25dd97. One observed compilation per plan/day ranged from approximately 14–67ms; this is a small timing observation, not an end-to-end backtest benchmark.

| PM development day | Complete candidate rows | Additional rows before first price | Additional anchors passing first price | Additional rows after first price |
|---|---:|---:|---:|---:|
| Aug 04 | 4669 | 216 | 3 | 216 |
| Aug 18 | 10878 | 260 | 4 | 188 |
| Aug 19 | 15193 | 174 | 0 | 0 |

All 14 additional native first-anchor keys match the earlier certified price audit one-to-one, including market token and episode identity. strategy26-native-price-reconciliation-v1.json SHA256 d11e62eba5ce3928d0752a3e18f43d13ceb21cc51a88e33e550cd44a25d9fc58 records that reconciliation. These counts are candidate rows, not fills or positions; no alternative portfolio P&L is asserted. AMIX and SLE each have newly eligible source candidates and pass the original first-price check. Holdout dates were not accessed. Installed Strategies 19–25 retain their earlier rules.

Failed full-audit helper V1 attempted cold saved review against edited source; V2 used pinned source but also stopped at cold-definition authority reconstruction. Both failed artifacts/progress receipts are preserved with zero completed comparisons. Separate pinned preflight diagnostics, with and without the staged modules, are ready and match the saved market/V7 tokens exactly. A diagnostic V2 helper also failed before preflight due to a missing datetime import; corrected V3 completed. The successful research audit uses the same mandatory saved-definition/preflight/certified-market contracts directly and makes no financial-prefix claim. The earlier cold-review helper path is not declared repaired; complete numbered execution and cold saved-review validation remain required before approving or claiming a Strategy 26 backtest.

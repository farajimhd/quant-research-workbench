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

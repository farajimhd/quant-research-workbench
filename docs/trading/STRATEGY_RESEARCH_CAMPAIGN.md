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

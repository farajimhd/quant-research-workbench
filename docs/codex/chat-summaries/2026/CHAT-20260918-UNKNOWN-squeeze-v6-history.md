# Early Squeeze candidates 323-328 and reusable V7 preparation

- Chat started: Exact original start unavailable; continued September 18, 2026
- Chat last activity: 2026-09-19 07:39 PDT
- Summary written: 2026-09-19 07:39 PDT
- Chat/task identifier: 01a0b4ac-d46f-7f82-b2f7-1565b42ba926
- Repository: D:/TradingCodes/quant-research-workbench
- Related task-history entries: TASK-0014 and TASK-0211
- Source completeness: Partial. User requirements visible; earlier implementation recovered through compacted context and inspected artifacts; final source, regression and initialization validation performed in this continuation.

## Initial alignment (later superseded)

The user requested an independent Early Squeeze strategy, using Strategy 317's sizing infrastructure without inheriting its trading rules. First Early Squeeze availability controls activation for both a selected ticker and a whole-session run. Levels use filtered V7 history. Initial entries use the current first resistance below prior HOD: a midpoint breakout followed by a completed green one-second candle closing in its top quarter, above the midpoint and VWAP. A later qualifying candle is permitted while the setup remains the current R1. Spread is capped at 2.5%; modest volume and liquidity requirements remain.

Initial entries and reentries use one third of eligible cash. Green completed resistance breaks while holding can add the original cash tranche without the initial candle's top-quarter requirement or MACD. Targets use distinct resistance breaks during the position lifecycle: fewer than four selects the third overhead resistance, four or five the second, and six or more the first. Targets use the resistance midpoint and advance upward; changing the ordinal need not move a target downward. A new position resets the count.

Initial protection is below the broken resistance's lower band. Real-time bid highs raise the stop while preserving the original filled entry-to-stop distance. Stop-out recovery freezes the highest completed close since the breakout. Reentry requires a completed green one-second close above that reference; protection goes below a qualifying swing low above the broken resistance, otherwise at the last completed candle open, offset below bid when necessary without delaying entry. Stop and target movement are exposed in position presentation and decision evidence.

## Corrections and trade review

Version 6 stops a frozen recovery reference from vetoing an independent fresh current-R1 breakout. A candle dipping below and reclaiming the midpoint qualifies. A delayed confirmation cannot use an R1 superseded by another resistance. Only current resistance roles count for additions and lifecycle breaks. Older version contracts retain their behavior.

Candidate 323 is `01399bf7-0aaf-42a3-9f61-701971f4b374`, contract `early-squeeze-r1-fixed-trail-v6`, published release 6 `b7e1a191-ae45-43ff-9427-3573678af01c`. Content hash is `7737487410b722bf793ea62ee4d4ca618390bae81b46b13879b3d3b542272d74`.

Completed JUNS run `da802bf4-8a94-469c-b608-52118ff12792` and SUGP run `250fd129-c952-4630-9277-4445c44d134f` each contain seven positions. All 14 entry audit checks passed, and all first exits were reconciled to effective protection and canonical quotes/trades. JUNS includes recovery at 07:22:17 Eastern; SUGP first entry moved from 04:09:27 to 04:09:08. The disputed original SUGP stop was touched by executable bid even though the trade-price candle did not show it. Detailed evidence is under `D:\TradingML\runtimes\strategy-audits\squeeze-v6`, especially the two `*-after-audit.json` files and `after-exit-quotes.json`.

## Full-market initialization failure

August 18 and 19 had no persisted Early Squeeze records. Empty unverified history incorrectly switched to all-market watchlist materialization, despite native signal activation and `watchlist_policy=not_required`. The unnecessary ClickHouse query scanned billions of rows and exceeded its 1 GiB memory limit. Raising the limit or treating missing history as a signal-free day would not establish correct activation.

The user explicitly approved reconstructing and certifying those dates. The corrected path distinguishes missing recorded coverage, retains source-native preparation even when empty, and reconstructs the exact saved detector using certified canonical SIP events. This source is separate from live records. No additional price ceiling or common-share restriction is applied. Canonically unsupported ticker formats are explicitly recorded as exclusions; aliases are not guessed.

Reconstruction certified 5,302 first signals for August 18 and 5,360 for August 19, covering 04:00–20:00 Eastern. Bounded workers use existing certified per-ticker ordinal ranges, stop at first qualifying occurrence, retain restart checkpoints, and verify request/population/output hashes. Reuse no longer depends on a new reference-data query. Certified empty activation now completes without a market scan or execution. Prepared V7 streams accept both explicitly supported v58 and filtered v59 bar authorities while retaining completeness and SIP-clock checks.

## Validation and remaining work

Focused regression: 103 tests and two subtests passed. Follow-up preparation suite: 13 passed, including frozen cache reuse. Final replay suite: seven passed, including certified empty activation. The broader replay suite had three failures outside this focused acceptance; this work does not claim a globally green test suite.

Live all-ticker diagnostic `f0e42067-af1b-4bb1-b913-1bea1fe46aa1`, August 18 04:00–09:30 Eastern, passed native signal loading, the former failing watchlist stage and V7 coverage. It reused 1,428 of 1,429 requested filtered histories, with one explicitly unavailable, then began 2,856 strategy-frame streams without the memory error. It was deliberately stopped during frame preparation to close the bounded diagnostic. It is not a completed full-day trading/P&L acceptance. Full-session throughput, economics and full-universe coverage remain open under TASK-0211.

Managed QMD History/backend restarts loaded the source changes. Strategy publication is complete locally. Source commit `620c2763` was created, but push to `origin/main` was rejected as non-fast-forward because local and remote histories had already diverged. No force push, unrelated merge or workstation synchronization was performed. Subsequent empty-run handling and this history update are committed separately. Reconcile Git history explicitly before workstation synchronization; preserve prior failed runs and certified artifacts.

## Candidates 324-325: candle clocks and bounded V7 residency

JUNS review exposed late entries and incomplete target audits. Candidate 324 changed admission to completed green 100 ms candles with body at least twice the prior session green-body average, top-quarter close, and five ticks above the resistance upper edge. Gray former resistances became eligible R1 anchors. No MACD gate was added. Completed 100 ms breaks drove additions and lifecycle targets; the stop still followed bid highs. Partial full-position exits now finish liquidation rather than freezing management or permitting additions.

Commit `5335b000` introduced the separate v7 executor. Candidate 324 (`4a06524d-74cf-4889-9f13-7425c9bdca49`) and release 7 (`41df5e51-76e6-4952-9513-f69bb2471026`) matched validated hash `c48e7939f807dfe7b043ea27cc7e020d00fc0e272b7f1dea1203c85ee6dab36b`. Eighty-nine focused tests passed. JUNS `fd6ce0c5-cbeb-47cd-bea4-e410fb20e832` and SUGP `bbbc3d8c-e00d-4999-9e2a-390120a58dcb` covered 14 lifecycles, 990 fills, and 27 target selections. Audits checked candle bodies, causal levels, counts, protection across order IDs, and conservation. Evidence: `D:/TradingML/runtimes/strategy-audits/squeeze-v7`. Historical replay acceptance did not establish complete opportunity capture or profitability.

The user authorized stopping warming run `939a554e` before activation. Its filtered history reused 1,553 tickers with zero builds; strategy frames missed because v58 differed from the current v59-0405-et authority. The 920 finished streams were retained. Revision checks were not bypassed.

SUGP run `29169fc0` exposed the 100 ms path's incorrect dependence on a fresh one-second detector row. The user changed the body multiplier to 1.25 and recovery stops to the latest broken resistance. Commit `1a761783` introduced v8, with freshness from actual V7 cutoff/max-input evidence and a separate frozen recovery trigger anchor. Ninety-nine focused checks passed. Final SUGP `196de38c-0d91-4e6b-a6a5-1ca6f65f5289` completed 53,112 events, four lifecycles and 367 fills; the 04:06:16.700 entry was admitted. Evidence: `D:/TradingML/runtimes/strategy-audits/squeeze-v8`.

Candidate 325 (`2beb54a1-1cd6-4939-b55b-0dd69b85df53`) was published as release 8 (`62fe9dc0-911a-41a9-8f47-3826d2774d60`), hash `2a3bb52e1f94c30f5bd64994a44098a4f4606e0f9d9282fc619962b3d47456bc`, after the user requested activation and repair of `a74e8c0e`. That August 19 run exceeded the 4 GiB RSS budget while warming ticker 1,312/1,553, before execution.

Prepared workers now retain 32 ticker states each and spill complete inactive state losslessly to private local runtime files. Engines, inputs, delta bases and snapshots restore exactly. The RSS guard remains; no source rows are dropped. Release cleans the private spill directory. Actual QMD HTTP preparation of all 1,553 tickers from retained `v9-2227e7dbd1c0b3a4df32cdd8d046287c67385a62d29131c49b868d9df4d3e163.sqlite3`, followed by causal advance to 04:06 ET, passed in 315.8 seconds with sampled worker RSS below 711 MiB. Twenty focused V7 checks passed. This proved bounded preparation, not full-day trading throughput. Managed QMD History activation also restarted Backend/Frontend. Existing Git divergence continued to block remote delivery.

## Midpoint price-rule successor and cash rejection

The user challenged 325 SUGP run b3725498: 04:06:16.700 selected a lower gray band because the nearer resistance upper edge exceeded HOD although its midpoint was below HOD; the 04:10:25.900 candle passed quality but waited for upper-edge/five-tick or frozen-high recovery. The audit had repeated that upper-edge interpretation. The user replaced candle confirmation with an upward price move through R1 midpoint plus 10% of the gap to the next current resistance midpoint, selected R1 solely by midpoint below HOD, and explicitly changed frozen-close-high recovery to a trade-price trigger too.

Candidate 326 (`d501fb74-6252-49b1-b621-0064f8d8cdd0`), contract `early-squeeze-r1-price-gap-v9`, is published as release 9 (`e22ec6fe-630e-4ae5-839d-eb3f2834b5f6`), validated hash `7a1434d5dbe3ed01506180c7a430f0dd7f085a74a988de7b14a2edc0c34cfe45`. Its separate executor removes body, top-quarter and five-tick entry gates; quotes cannot invent price crossings. Recovery uses the frozen highest completed 100ms close, but crossing is event-priced. Same price-gap definition drives additions, latest broken stop anchors and lifecycle counts. Targets follow the existing 3/2/1 midpoint table on trade updates; bid trailing retains initial distance. The presentation records the actual threshold and existing effective protection changes. No MACD/ATR gate was added; older contracts remain separate.

A diagnostic found an additional delay: the 04:10:25.851 crossing was blocked by a one-second VWAP-age limit despite no intervening completed candle. The event adapter now certifies consumption of the latest QMD 100ms sample through the event cutoff, retaining the original sample timestamp and rejecting missing, future or cross-session evidence. Final SUGP run `77d7bd97-c36a-4e15-98c0-843c3101e3fa` completed 53,112 events over 04:00-04:18 ET, eight lifecycles and nine target movements. An independent canonical-event/V7 audit verifies trade timestamps/prices, midpoint selection, thresholds, exact stop geometry, target ranking, monotone effective protection and fill conservation. The expected entry is 04:10:25.851248 with stop 3.49. Earlier price-based entry at 04:05:46.102442 and recovery at 04:06:18.231526 mean the position is already held at 04:09; the qualifying break adds at 04:09:03.934698. These changed sequences are explicit consequences of the new rule, not forced timestamp matches. Artifacts are under `D:\TradingML\runtimes\strategy-audits\squeeze-v9`. Prior diagnostic e835e24a is superseded.

The user also confirmed failure of full-universe run `54e6fdeb`, which used the older v6 contract. DVLT's 254-share addition at the broker-rounded 0.43 limit plus fees required 110.49 with 109.9793 cash. The simulator raised a ValueError that OMS treated as unknown submission and aborted the run. Supported batch funding is now checked before accepting any order; insufficient cash returns an explicit rejection for normal OMS risk/reservation release. OCA alternatives retain per-leg funding semantics. Exact-number reproduction, 30 simulator checks and the OMS end-to-end rejection check passed. Together with 20 price-rule and 99 previous strategy checks, 150 focused checks passed. Full-session cash replay and JUNS v9 behavioral acceptance remain unverified. Managed backend/frontend restart loaded the changes; QMD was preserved. Source push remains blocked by existing main divergence.


## Trade-price stops and reentry-only high gate (327)

User review of 326 run 28325fa2 exposed bid-high trailing (3.45 versus expected 3.48), recovery bypass with an old stop, 199 unfunded add rejections retried repeatedly, and same-level recross entries. Earlier audits checked individual formulas but missed these lifecycle interactions. The user removed recovery entirely and specified trade-price stops. Their final clarification restricts the new-high requirement to reentry: exceed the highest traded price since that resistance first broke. First entries and additions do not have that gate. The gap remains 10%; increasing it cannot repair the bypass.

327 uses a separate v10 contract. It tracks per-resistance highs across exits, prevents entry-resistance additions, consumes each add attempt once per session even when rejected, rechecks current resistance role, and ratchets protection beneath newly broken bands. Eligible trade highs drive the fixed-distance trail; simulator stops trigger on eligible trades while fills use executable liquidity. Prior contracts remain unchanged.

67 focused checks passed. Final SUGP run 223096b4-5724-4014-a326-8fe5146011fc completed 53,112 events with 11 entry intents, eight distinct add attempts, ten target movements, zero portfolio rejections and a flat terminal position. Canonical-price and causal-level audit verified reentry highs and add roles. First stop reached 3.48; the 04:06:18 recovery disappeared. The reentry-only gate moves 04:10:25 admission to 04:10:33 when price exceeds the prior high. Artifacts: D:/TradingML/runtimes/strategy-audits/squeeze-v10. Earlier run 0bdfaea8 is superseded.

Managed Backend/Frontend restart preserved QMD and interrupted no user backtests. 327 was published as release 10, 82fee8c3-76ae-4248-b998-310004479601, hash a20a8603466266b162901c9a4b6ac37693fba1f9a5b9dca88f9bbb7e60923853; running capability and approved APIs verified it. JUNS, full-day and live-broker trigger acceptance remain open. Existing Git divergence constrains remote delivery.


327 subsequently failed on SUGP at 04:10:57: trade 4.1444, ask/reference 4.09, stop 4.11. Initial/add protection now offsets below both trade and ask; trailing remains trade-based and existing stops never loosen. 31 focused tests passed. Saved-configuration replay 41cbbeb8-81df-4478-b0a5-1f1b204c9fbf completed 04:00-04:30, 82,406 events; all 19 buy-intent/profile stops were below entry references, first trail reached 3.48, no recovery or repeated adds, terminal flat. Evidence: D:/TradingML/runtimes/strategy-audits/squeeze-v10-stop-geometry. User authorized stopping 7550bedf and restarting Backend after validation; stopped successfully. Managed Backend/Frontend restart loaded the fix; approved API verified release 10 unchanged.


## Resettable breakout and original whole-position trail (328)

The latest user clarifications supersede the earlier reentry and addition-stop rules: an eligible trade below a resistance lower band resets its breakout, allowing the next fresh breakout without exceeding the previous episode high. Without that reset, reentry still requires a new episode high. One stop protects the entire position; additions retain the original entry-to-stop distance and cannot ratchet the stop to another resistance. Session-single-use addition attempts remain consumed across resets, including rejected attempts. The 10% midpoint-gap trigger and other v10 admission/target rules remain unchanged. The v11 trail anchors to the actual placed stop, including any executable-price offset, rather than later restoring the unadjusted structural stop.

Candidate 328 is published and active: candidate `f1b785b9-f80c-49a2-9813-b179cf305024`, release 11 `b1933859-786a-444b-a5c5-90d6a7f43a3a`, contract `early-squeeze-r1-price-episode-v11`, hash `43e73ab99bec6e6d7d8885f7ef85ae088f3e992771330e7d1c79db3609903cec`. Managed QMD History/Backend/Frontend restart loaded the changes; QMD Live was not restarted.

Final strategy replay `cb9ffee6-6453-43d1-8517-60ca5762922e` completed SUGP August 21 04:00-04:30 ET: 82,406 events, 13 entry intents, six add intents, zero portfolio rejections, 66 stop changes, eight target movements and terminal flat. All 241 effective protection updates were nondecreasing within their lifecycle. The third entry is 04:10:25.851248 with initial stop 3.49 and original distance 0.08. Its later addition at trade 3.66 retains stop 3.58 rather than jumping to 3.61. First trade stop reaches 3.48. Canonical-event/V7 audit verifies resets, reentry highs, single-use/current-role additions, exact trade-peak trails and target selection. Forty-seven strategy regression tests passed, including seven new episode tests.

## Durable V7 preparation reuse and exact transport

Positive catalog certificates, immutable bar arrays and zero-input opening engines now persist locally with dependency identities, checksums, kernel/authority keys and bounded compressed caches (2 GiB payload budget per namespace). Missing publication paths are tracked; changed sources invalidate reuse. Every stream receives independent mutable playback state. Fixed lock stripes coalesce selection misses, SQLite connections close explicitly, and existing RSS/disk guards remain unchanged. UI distinguishes verified coverage from loaded working sets and reports reused bars/opening books.

A four-worker August 19 preparation benchmark verified all 1,553 eligible tickers. The new-process warm pass reused all 1,553 bar arrays and opening engines in 88.031 seconds versus 240.015 seconds for the build pass (2.73x). All identities and 64 sampled 04:06 snapshots matched exactly; maximum sampled worker RSS was 703,176,704 bytes. This is preparation evidence, not full-day trading throughput or P&L acceptance. Fifty-two V7 tests passed; frontend build and 12 visual scenarios passed without objective issues.

HTTP parity exposed one-ULP changes in the Rust worker JSON bridge. It now uses the existing exact numeric parser only for that bridge; the historical certification canonicalizer remains unchanged. All 257 core Rust tests and 119 history tests passed (four ignored). After managed activation, four actual HTTP streams reused both artifacts and exactly matched direct-worker snapshot hashes. The accepted SUGP replay predates this transport correction. A proposed additional replay never started because immutable publication prevented rebuilding its profile; the user then explicitly instructed not to rerun SUGP because they had just tested it.

Evidence is under `D:/TradingML/runtimes/strategy-audits/squeeze-v11`. JUNS, full-day trading and live-broker acceptance remain open. Source delivery was initially blocked by remote divergence, resolved below. Automatic approval review blocked deletion of five stopped diagnostic spill directories with reason "blocked by policy"; they remain in runtime storage. Diagnostic processes were stopped.


## Repository reconciliation and delivery

Commit `989d36f1` delivered v11/cache changes locally; its push initially failed. The user then requested repair of an unfinished merge with nine conflicts: local main was 256 commits ahead and 90 behind. Every incoming conflicted blob exactly matched an older local ancestor; resolving only conflict blocks in favor of the newer local code produced exactly the pre-merge tree. Remote labeler, forming-MACD and R1-ladder implementations were already represented locally. No strategy behavior changed. Twenty-one focused episode, filtered-history and cache tests passed. Merge `63fc842a` was pushed normally; local/remote main matched and the working tree was clean. SUGP was not rerun.

## Durable decisions

- Confirmed: first Early Squeeze activation; filtered causal V7; midpoint R1; price-gap admission; lifecycle 3/2/1 upward midpoint targets; trade-price stops; below-band episode reset; original whole-position trail through additions; consumed add attempts survive resets.
- Architecture: versioned executors and immutable releases; source-verified persistent artifacts with private playback state.
- Rejected/superseded: separate recovery, mandatory candle/body gates, bid-based trailing, addition stop ratchets and repeated rejected-add retries.
- Uncertainty: preparation speed and bounded SUGP correctness do not establish full-day performance or strategy acceptance.

## Delivered outcomes

328/release 11, certified August 18-19 activation histories, bounded reusable V7 preparation, exact worker JSON transport, stop/target presentation and merged source delivery are recorded above. TASK-0014 and TASK-0211 remain in progress.

## Unfinished work

- TASK-0014: JUNS and live-broker acceptance remain unverified. Next owner must reconcile actual executions before widening acceptance. Read the v11 executor, candidate and audit evidence; do not rerun SUGP without a new request.
- TASK-0211: full-day trading/P&L and sustained playback remain unvalidated; the preparation benchmark cannot close these. Next, inspect the user's existing run and agree any additional campaign.
- Workstation synchronization was not performed; the successful push now permits the normal managed synchronization workflow when requested.
- Five diagnostic spill directories remain because automatic cleanup approval was blocked; do not bypass that rejection.

## Unavailable or incomplete source chats

Earlier portions of this same task are partly compacted; retained narrative/evidence supplies their implementation history. Other inventoried tasks were not reviewed for this update. Preserve latest user clarifications over historical rules above.

## Handoff to the next chat

Read TASK-0014/TASK-0211 and the latest v11 section first. Preserve the reset and single-trail rules. Inspect existing user-run evidence before proposing more validation; SUGP reruns remain disallowed. Source is pushed; workstation deployment and broader acceptance remain separate actions.

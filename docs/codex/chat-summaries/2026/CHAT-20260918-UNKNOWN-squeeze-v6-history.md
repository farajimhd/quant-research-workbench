# Early Squeeze v6 and certified missing historical signals

- Chat started: Exact original start unavailable; continued September 18, 2026
- Summary written: September 18, 2026, America/Vancouver
- Chat/task identifier: 01a0b4ac-d46f-7f82-b2f7-1565b42ba926
- Scope: TASK-0014 and TASK-0211
- Source completeness: User requirements visible; earlier implementation recovered through compacted context and inspected artifacts; final source, regression and initialization validation performed in this continuation.

## Aligned strategy

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

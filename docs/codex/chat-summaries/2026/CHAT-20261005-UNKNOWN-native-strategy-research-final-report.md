# Native strategy research: final results and handoff

- Research period: October 5–11, 2026; exact chat start time unavailable.
- Report written: October 11, 2026, UTC.
- Chat identifier: `01a10c88-d342-7690-b11c-a8e90fbc1564`.
- Repository authority: `D:\TradingCodes\quant-research-workbench`.
- Owned source branch: `codex/declared-price-confirmed-risk-exit`.
- Related continuity: TASK-0222 and the September 30 Strategy42/ladder handoff.
- Source completeness: Partial conversation history, current source, and selected saved evidence. This is not an exhaustive audit of every numbered release.
- Final disposition: research stopped at the user's request; goal paused and **not achieved**. No new strategy was created during final app-delivery work.

## 1. Outcome

The best fully completed development result among the comparisons reviewed here is **Strategy117: +$2,891.33 across 26 independent sessions**, with 133 positions. Only **4/26 sessions reached $1,000**. Strategy132, the latest tested participation-quality experiment, completed **4/26 sessions: +$1,170.94**, with 16 positions and one session reaching $1,000. No validation financial result was completed. These results do not meet the objective or establish an out-of-sample edge.

Existing immutable Backtest publications and saved reports were retained. Final app startup succeeded after correcting operational connection references, but the strategy selector and native saved-journal routes failed compatibility checks. **The requested app delivery is incomplete.** All services started during that attempt were stopped.

## 2. Requirements preserved

The initial target was $500 net in every development and untouched validation session. The active goal later specified **$1,000 in every session**. Each PM/AH session starts independently with $10,000; summed P&L is not a compounded account return.

Development dates are August 4, 5, 10, 18, 19, 20, 21, 24, 25, 26, 27, 28 and 31, 2026: 26 PM/AH sessions. LGHL is the sole declared ticker exclusion. Regular-hours financial backtests were excluded. AH requires the prior-day V7 checkpoint and complete regular-session source warmup before decisions.

Other binding requirements:

- Preserve Strategy1/42 sizing, aggregate exposure, costs and immutable releases; never restore withdrawn Strategies43–45 from TASK-0221.
- Historical market events come from certified `market_sip_compact.events_YYYY`, never retained raw flatfiles.
- Use only information available at decision time; no future indicators, fundamentals or split knowledge. Missing certification remains a failure, not permission to fabricate coverage.
- Shared behavior is selected by declared rules, without strategy-number, ticker or date-specific economic branches.
- Portfolio owns cash, sizing, reservations and replacement admission. OMS owns execution, independent protection, OCA ordering and recovery.
- Keep candidate filtering vectorized while preserving sequential cash, liquidity and fills.
- Validation may be backtested and inspected through aggregates/preflight only; its trades, tickers, features, logs and charts must not guide tuning.

The user eventually redirected effort toward shared features preceding major moves, requested deterministic scoring and multi-resolution channels, excluded news, and explicitly prohibited creating another strategy during closure.

## 3. Ideas tested and financial comparisons

### Strategy42 successors: admission and early failure exits

Research examined failed follow-through, resistance rejection, deteriorating participation and profit giveback using saved winners as well as losses. It tested spread relative to original stop risk, completed momentum growth, and price-confirmed early failure exits. Actual financial results—not hypothetical removal of losing trades—determined comparisons.

All rows below cover the same complete 26-session development population. Drawdown is the largest saved **broker-observed session drawdown**, not a continuous or liquidation-value guarantee.

| Strategy | Main tested distinction | Positions | Summed net | Maximum observed drawdown |
|---|---|---:|---:|---:|
| 42 | Starting baseline | 138 | +$1,883.96 | $1,523.08 |
| 57 | Strategy50 exits plus spread ≤ one quarter of original risk | 133 | +$2,851.83 | $1,523.08 |
| 58 | Strategy50 exits plus spread ≤ one half of original risk | 138 | +$2,294.43 | $1,523.08 |
| 59 | Completed 10s histogram growth: first anchor 10%, current candidate 5% | 138 | +$2,663.44 | $1,523.08 |
| 60 | Reversed growth thresholds: first anchor 5%, current candidate 10% | 149 | +$1,270.34 | $1,523.08 |
| 64 | Quarter-risk spread cap on Strategy42, without Strategy50's AH extension | 133 | +$2,474.74 | $1,523.08 |
| 66 | All-held PM/AH quarter-risk failure exit with weak completed 5s MACD | 143 | −$1,823.81 | $1,084.49 |
| 78 | Preserved source/compatibility successor; saved economics matched baseline | 138 | +$1,883.96 | $1,523.08 |
| 117 | Native source/read/recovery successor retaining Strategy115 economic policies | 133 | +$2,891.33 | $1,523.08 |

Strategy57 improved the baseline total by **$967.87**, but still had 13 losing sessions and one zero-trade session. Strategies42/57/64/78/117 each reached $1,000 in only four sessions. Strategy66 reached it once: its lower observed drawdown came with a substantial profitability regression. Broad early exits are therefore not supported merely because they reduce drawdown.

The Strategy42/57 audit reconciled 52 saved reports and 129 matched entries. Five positions with unchanged entry price/quantity but changed exit time were all baseline losers; their actual combined net improvement was $265.16. Other changes also involved admission, sizing and timing, so the total improvement cannot be attributed solely to earlier exits. Strategy117's total is only $39.50 above Strategy57; that difference is not proof of a newly discovered entry edge.

Evidence: [57/58 reconciliation](D:/TradingML/runtimes/strategy-optimization-20261005/strategy57-58-full-development-financial-reconciliation-v1.json), [59 reconciliation](D:/TradingML/runtimes/strategy-optimization-20261005/strategy59-full-development-financial-reconciliation-v1.json), [60 reconciliation](D:/TradingML/runtimes/strategy-optimization-20261005/strategy60-full-development-financial-reconciliation-v1.json), [42/57/64/66 audit](D:/TradingML/runtimes/strategy-optimization-20261005/strategy66-all26-independent-saved-financial-audit-v3.json), [78 complete verification](D:/TradingML/runtimes/strategy-optimization-20261005/strategy78-root-full26-financial-source-verification-v1.json).

### Big-move features and deterministic scoring

The work compared development moves and failures rather than starting with ticker-specific entry rules. A native feature campaign recorded **581 completed packets** across the 13 development dates. The corrected full-session episode comparison reconciled 26 session outcomes without discrepancies and identified 6,242 positive episodes. Warmed momentum covered 2,020; native above-VWAP covered 2,040; their conjunction covered only 1,058. This argued against treating momentum-plus-above-VWAP as a universal veto for major moves.

Those episodes are selected using future outcomes for **development analysis**. Their labels may define a research reward or evaluation target; they cannot enter live decisions. Matched feature associations are not causal treatment effects or executable profit forecasts. Earlier zero-close, quote-only and nonfinite hindsight anchors required correction; the corrected comparisons supersede those initial interpretations.

The requested channel design separates scale-free shape from economically necessary absolute information:

| Feature family | Intended representation and causal boundary |
|---|---|
| Candles | OHLC relative to previous completed close, range, wick fraction, close location and past-volatility normalization |
| Participation | Relative executed volume and trade count; absolute dollar volume; strictly earlier-session time-of-day baselines where certified |
| Timing | Start/end, availability clock, duration, source age, valid constituent count and PM/AH age |
| Structure | Certified squeeze observation; confirmed support/resistance distance in risk units; breakout/retest/reclaim state |
| Absolute economics | Bid/ask, spread, tick size and original stop risk retained alongside return channels |
| Corporate actions | As-of split/reverse-split direction, ratio, effective/known timestamps and comparability masks |
| Fundamentals | As-of float/shares, capitalization, financial/disclosure values and publication age, only with certified historical coverage |

These families were not all implemented as profitable strategy inputs. Producer, publication, typed-reader and recovery infrastructure was built and tested, including private principals, Keeper ownership and SSD placement. The actual installation receipt verified 309 feature rows and four coverage rows. The **financially tested score uses only relative executed volume, relative trade count and upper-wick fraction**; it does not yet consume fundamentals, split events or return momentum. News was excluded.

The deterministic score averages declared 30s and 1m resolutions equally, requiring both known:

`0.5 × log(1 + relative volume) + 0.5 × log(1 + relative trade count) − upper wick / candle range`

It is a participation proxy, not a learned reward model. No trained policy choosing entry/add/reduce/exit was delivered. Decisions remain at the declared 100ms cadence; input resolutions and decision cadence are distinct parameters.

Strategy131 wired ranking into native acquisition. Its August4 and August18 PM results matched Strategy117 exactly. Inspection found just one eligible ticker per boundary: 309/309 and 370/370 candidate boundaries, with no simultaneous ticker competition. Ranking could not reorder competing acquisitions on these pilots.

Strategy132 therefore added a declared **known score ≥1.0** admission condition after mandatory certification. The cutoff is experimental, not an optimized universal value. Scoring/filtering remains columnar; cash and fills remain sequential.

Evidence: [feature proposal and limits](D:/TradingML/runtimes/strategy-optimization-20261005/causal-multires-feature-and-rule-proposal-v1.json), [completed packet census](D:/TradingML/runtimes/strategy-optimization-20261005/native-channel-campaign-current-progress-v2.json), [corrected move comparison](D:/TradingML/runtimes/strategy-optimization-20261005/major-move-native-full26-root-review-v1.json), [real installation](D:/TradingML/runtimes/strategy-optimization-20261005/native-channel-real-installation-delivery-v1.json), [131 candidate competition](D:/TradingML/runtimes/strategy-optimization-20261005/strategy131-native-candidate-competition-v2.json).

## 4. Latest Strategy132 results and audits

| PM session | Positions | Net | Observed drawdown | Saved control comparison |
|---|---:|---:|---:|---|
| August4 | 3 | −$36.42 | $512.86 | +$91.77 vs Strategy131; drawdown lower by $100.67 |
| August5 | 1 | +$361.14 | $197.40 | Position-level comparison not completed |
| August10 | 4 | −$244.06 | $504.26 | +$723.09 vs Strategy117; drawdown lower by $703.07 |
| August18 | 8 | +$1,090.28 | $561.61 | −$141.81 vs Strategy131; drawdown lower by $135.97 |
| **Total** | **16** | **+$1,170.94** | — | **One session ≥$1,000; 22/26 unverified** |

The two matched Strategy131 pilots together lost $50.04 of profit while reducing observed drawdown. The August10 improvement uses Strategy117 as control: there is **no completed Strategy131 August10 result** establishing equivalence there.

Development audits show why the gate is not a simple loss filter. On August4, delayed DXST changed a profitable control episode into a failed follow-through loss; ATPC still failed within roughly 12 seconds. On August18, five control-only winners and two losers disappeared. On August10, several additional AUUD losses, LRHC and ZJYL losses disappeared, but the retained AUUD position still lost $239.36; HUDI outcomes changed and INHD remained profitable. Delays, cash availability and sizing can affect outcomes, so an absent control position is not automatically a direct gate rejection. Saved MFE/MAE fields were null in the inspected evidence; no numerical excursions are claimed.

Evidence: [latest aggregate](D:/TradingML/runtimes/strategy-optimization-20261005/strategy132-verified-financial-progress-v2.json), [August4 audit](D:/TradingML/runtimes/strategy-optimization-20261005/strategy132-vs131-pm04-position-audit-v1.json), [August18 audit](D:/TradingML/runtimes/strategy-optimization-20261005/strategy132-vs131-pm18-position-audit-v1.json), [August10 audit](D:/TradingML/runtimes/strategy-optimization-20261005/strategy132-vs117-pm10-position-audit-v1.json).

### AH failure and committed correction

August4 AH failed before processing financial rows: prepared feature participants were restricted to the AH execution window, while scoring still checked historical PM candidate tickers. A secondary terminal error reported that the evidence clock differed from the checkpoint. There is **no valid AH P&L** from that attempt. The campaign later finished August5 PM, then was stopped at the next AH preflight: four verified sessions, one failed unit, one stopped unit and 20 queued.

Commit `3d2bf3443` fixes the generic scoring-window binding using vectorized `(start,end]` filtering. Original key order, full-length masks and immutable mandatory admission are retained; missing/foreign in-window coverage still fails. Certified empty scopes are handled explicitly. **64 focused tests passed**, but no financial AH retest was completed. The frozen Strategy132 source was not patched. Earlier plans for a new Strategy133 are superseded by the user's final instruction not to create another strategy.

The V7 source review confirmed that regular-session warmup occurs in certified product derivation from prior-day seeds; the runtime consumes certified intervals rather than refitting V7 itself. This was a source-contract review, not independent per-ticker runtime attestation.

## 5. Complete Strategy117 session record

| Date, 2026 | PM net | AH net |
|---|---:|---:|
| August4 | −$128.19 | −$613.81 |
| August5 | −$38.86 | −$174.03 |
| August10 | −$967.15 | −$447.52 |
| August18 | +$1,232.10 | +$719.69 |
| August19 | +$1,320.12 | +$1,095.66 |
| August20 | +$380.12 | −$347.53 |
| August21 | +$502.35 | $0.00 |
| August24 | −$116.42 | +$1,090.94 |
| August25 | −$319.79 | +$34.29 |
| August26 | −$809.11 | −$101.58 |
| August27 | −$539.92 | −$348.00 |
| August28 | +$89.16 | +$870.60 |
| August31 | +$250.14 | +$258.08 |

Total uses unrounded saved values. The PM25 report timeout was recovered through a separately verified, bounded **report-only** read; it was not financially rerun. The latest aggregate supersedes its stale “finish remaining sessions” next-priority text.

Evidence: [complete 117 aggregate](D:/TradingML/runtimes/strategy-optimization-20261005/strategy117-verified-financial-progress-v32.json).

## 6. Ladder branch and validation

The ladder idea remained: certified Early Squeeze adds observation; qualify VWAP/liquidity; enter a causal structural breakout; open multiple independently protected lots with confirmed swing-low fixed protection; enforce one accepted acquisition per ticker/session. Structural versus percentage targets and equal/increasing/decreasing weights were intended comparisons. Price-confirmed trailing and rotation after exits release cash were later stages.

Generic fixed-lot support was previously committed in `768949363`. It is **not a published profitable ladder strategy**. Later ladder probes produced no accepted proposals and no completed financial backtest. Strategy97's saved preflight remained blocked by unavailable causal V7 coverage/market-day seed plan. Target-weight, trailing and rotation comparisons therefore have no verified financial conclusion. Certification was not weakened to manufacture entries.

Validation completed: **zero sessions**. September1/2 and RL research through September18 were exposed. September21/22 remained tentative; no untouched pair was sealed. The October11 metadata refresh found no qualifying native runs, market-stage certificates or V7 coverage, and canonical-day certificate access returned ClickHouse497. Availability alone also cannot establish absence of research exposure. No validation trades, logs, tickers, features or charts were inspected in this closure.

Evidence: [97 preflight](D:/TradingML/runtimes/strategy-optimization-20261005/strategy97-pm04-native-preflight-diagnostic-v2.json), [validation metadata](D:/TradingML/runtimes/strategy-optimization-20261005/validation-availability-refresh-v10.json).

## 7. Performance, publication and final app attempt

Long-running financial work used a 3,600-second soft limit and 4,500-second hard deadline; preflight, publication and reporting used 600-second bounds, without automatic financial retries. A representative Strategy132 August10 execution took **1,115.227 seconds for 2,403 broker rows**. Terminal completion occupied 305.958 seconds, including 277.242 waiting for prior commit work. The underlying CPU/I/O bottleneck was not isolated. Neither this single run nor the older planning microbenchmark establishes full-session speed acceptance.

Strategy132's immutable publication was independently certified, including storage policy/part placement:

- Source: `6fb9c76eb0aeeac44c72910d9eec9ea7d3081361`.
- Revision: `strategy-one-132:b78571a2-e94d-405c-9da9-7081f4df4dfc`.
- Payload SHA256: `77e47aaf5c5f768699a8bf8fe8d7a291abaca95561e0e2aee86d53f9736ff8de`.
- Publication mode: Backtest-only; no live qualification.

Final closure used a clean laptop checkout at that source. QMD initially targeted unavailable localhost, then a stale workstation IP. The established hostname `DESKTOP-SAAI85T:18123` responded; external settings and native credential-file references allowed QMD History/backend/frontend startup. Credentials were not copied into source or artifacts.

Actual API checks then failed:

1. Configuration options returned400, “Unsupported numbered fixed strategy.” Published75/76/78 lacked installed executors in this checkout; withdrawn43–45 were absent and were not restored.
2. Native132 saved-terminal requests initially lacked the native runner credential reference. After that operational correction, both returned409, “Saved review accepts only installed immutable numbered strategies at 100 ms.” The legacy predicate does not recognize this native declaration.

No duplicate publications, new financial campaigns or new strategies were created. App/browser visual verification was not completed; the browser tool timed out. All three services started here were stopped, with unrelated ownership preserved. No subagents remain running; two listed workers ended in usage-limit errors and one completed. Complete historical fan-out counts were not reconstructed. Retained agent history was not deleted.

Evidence: [timing](D:/TradingML/runtimes/strategy-optimization-20261005/strategy132-pm10-full-session-timing-v1.json), [publication](D:/TradingML/runtimes/strategy-optimization-20261005/worker-strategy132-certified-published-identity-v1.json), [journal checks](D:/TradingML/runtimes/strategy-optimization-20261005/tested-app-closure-journal-checks-v1.json), [closure receipt](D:/TradingML/runtimes/strategy-optimization-20261005/tested-app-goal-closure-v1.json).

## 8. Handoff: unfinished work and preservation

Do not resume research or create another strategy without renewed user direction. Remaining work is explicitly incomplete:

1. Design certified app hosting that exposes preserved releases and native journals without changing their trading rules or weakening source checks.
2. Resolve how to qualify the committed AH-window correction under immutable source authority; reconcile the failed AH journal before any retry.
3. Complete a valid development campaign before claiming Strategy132 superiority; audit August5 and retain winner-loss comparisons.
4. Close validation exposure and input certification, then evaluate aggregates only.
5. If research resumes, investigate profitable-move capture versus delayed admission, certify broader feature consumption, and test the ladder/rotation alternatives financially.
6. Establish representative full-session performance acceptance.

Authoritative artifacts remain under `D:\TradingML\runtimes\strategy-optimization-20260930` and `...\strategy-optimization-20261005`, plus the workstation runtime counterpart. Per-run native reports retain source identities, hashes, financials and position evidence. Source commits for the latest feature admission are `a4f575b33`, `6f377da90`, `6fb9c76eb`; the unqualified-for-financial-use window correction is `3d2bf3443`. These owned changes were committed and pushed before closure. Unrelated laptop edits were preserved. This report changes no strategy and does not update the task-history ledger.

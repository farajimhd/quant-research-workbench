# Repair Strategy Freshness, Structural Trailing Protection, and Incorrect CHOCH Exits

- Chat started: 2026-09-02, exact time unavailable (PDT)
- Chat ended or last activity: 2026-09-02, exact time unavailable (PDT)
- Summary written: 2026-09-02, exact time unavailable (PDT)
- Chat/task identifier: unavailable
- Repository or scope: `D:\TradingCodes\quant-research-workbench`; long-momentum strategy, configuration migration, OMS protection, and SUGP Backtest Debug evidence
- Related task-history entries: `TASK-0014`
- Source completeness: Complete for the accessible conversation, repository changes, runtime candidate, and validation; exact chat timestamps and identifier unavailable

### Narrative

The user asked for a causal audit of candidate revision 58 run `19257e4f-c8da-463f-98ae-54a478489844`, focusing on apparently stale confirmation logs, the 04:17:02 ET entry, missing trailing protection, and exits that occurred while MACD remained open. The audit found that `Groups` and `Group Scores` were legitimate separate projections: Boolean rule-set outcomes and numeric condition-pass ratios. The evidence was still incomplete because it dropped operand source timestamps and the evaluator imposed no absolute maximum age on cached values.

The run also proved that `structural-single-target` explicitly configured trailing protection as disabled. Entries therefore emitted an entry limit, structural target, and fixed stop but no broker trail. The prior stop algorithm used a singular support/swing value mixed with ATR and a 6% cap; it did not inspect the complete support book. At 04:17:06.633, the first position exited through `downside_bearish_choch` after crossing a support boundary even though one-second MACD was open. A later entry showed the same structural-exit defect.

Strategy revision 29 and configuration schema 43 implement the requested general repair. Cached condition operands now preserve `observed_at`, calculated age, applicable maximum age, and freshness classification. Missing, malformed, future, or stale time-series operands fail closed before comparison. Session-static references remain explicitly exempt; event sources have a 60-second maximum. Logged stage operators now reflect the executable Boolean expression.

For a long entry, protection orders the complete consolidated support book nearest-first below entry, retains only levels with hold probability strictly greater than 85%, and selects the second. Distance is `min(entry_price * maximum_risk_pct, entry_price - selected_support_price)`, with a configurable default of 15%. If fewer than two supports qualify, the 15% distance is the explicit fail-safe. The distance owns both the fixed stop and an immediately active native broker-amount trail. Audit metadata records the bounded qualified ladder, selected support, cap, fallback reason, stop, and trailing amount. The `downside_bearish_choch` route and dependency were removed; MACD-close and executable-VWAP-loss exits remain.

All 119 strategy/runtime/order-planning tests passed, including the actual `structural-single-target` path producing entry LMT, target LMT, fixed STP, and TRAIL children. Focused configuration default and v42-to-v43 migration tests passed. An isolated 65-test configuration run found one migration compatibility regression, which was fixed and retested; its remaining failure is an unrelated old SEC-label availability expectation. Commits `3ab1bf0f`, `168b13dd`, and `af0a3000` were pushed to `main`. Immutable candidate revision 59, `027a00cb-322e-4c8a-a626-3a598e31e2f2`, pins the repaired authority. No new backtest was run.

### Durable decisions

- Cached time-series and event rule operands require causal timestamps and code-owned maximum ages; invalid freshness fails closed and remains auditable.
- Long protection uses the second-nearest support below entry with hold probability strictly above 85%, capped by the configurable 15% default.
- The realized distance owns both the fixed stop and immediate native broker trail.
- Bearish CHOCH is not an exit authority. Forming one-second MACD closure, executable-VWAP loss, and broker protection remain valid exits.
- SUGP remains the only authorized validation ticker; no ticker-specific rule is permitted.

### Delivered outcomes

- Implemented and pushed strategy revision 29 and configuration schema 43.
- Added fail-closed operand freshness and complete condition evidence.
- Added second-qualified-support fixed and trailing protection with a parameterized cap and explicit fallback.
- Removed the incorrect bearish-CHOCH exit path.
- Added configuration migration and actual OMS/broker-plan regression coverage.
- Created and verified immutable candidate revision 59.

### Unfinished or hanging work

1. Candidate 59 has not been replayed. Run only SUGP from 04:00-04:30 ET on 2026-08-21 and verify the 04:17:02 and later lifecycles against timestamps, selected support, STP, TRAIL, MACD, and VWAP evidence.
2. If the bounded result is accepted, rerun the full August 21 SUGP session and review lifecycle quality before strategy approval.
3. Do not expand tickers or sessions without new user authorization and visual acceptance.

### Handoff to the next chat

Read `TASK-0014`, this summary, candidate 59, and the candidate-58 journal before further strategy changes. Preserve fail-closed freshness, strict greater-than-85% support qualification, second-support protection, the parameterized cap, immediate native trailing protection, and removal of bearish CHOCH as an exit. The next action is the bounded candidate-59 SUGP replay; strategy approval remains open.

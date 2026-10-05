# Strategy42 incremental native research

Status: active research; the per-session financial objective is unmet.
Strategy42 and withdrawn Strategies43–45 must remain immutable. New behavior
uses a fresh number beginning with Strategy46, never a vacant withdrawn slot.

The declared development population is August 4, 5, 10, 18, 19, 20, 21, 24,
25, 26, 27, 28 and 31, 2026, excluding LGHL. Run PM and AH independently with
$10,000 each and inherited sizing, exposure, fees and fill contracts. AH needs
the preceding V7 checkpoint and all regular-session source warmup. No regular
session financial backtest, raw flatfile fallback or future fundamentals.

## First additional failure rule

The first proposed successor retains the exact Strategy42 behavior and adds
one rule after inherited exits: during the first 60 seconds after native first
held quantity in AH, exit on both a completed 5s close and fresh executable bid
at or below original reference ask minus one quarter of original stop risk,
with completed 5s MACD line strictly below its signal. The entire price bucket
must follow first held quantity. Missing, stale or pending-exit observations
do not qualify. Time alone does not exit. PM, later failure rules, entries,
re-entry, adds, sizing, protection, costs and source contracts are inherited.

`EarlyOriginalRiskPolicy` declares separate exact rational PM/AH fractions;
the AH-only candidate declares `premarket_fraction=None`,
`afterhours_fraction=(1,4)`, and `eligibility_ms=60000`. It returns the existing
typed failure witness but grants no persistence or order permission. No old
number imports or selects the new rule. New native witness replay, source
certification, registration and app financial execution remain required.

The development audit reconciles 26 reports and 138 positions. Thirty of forty
follow-through losses exited within 60 seconds: nine AH losses total
-$1,680.06 and twenty-one PM losses total -$3,263.98. FTFT AH28 lost $366.48
early, then a later target winner earned $614.645. Rapid re-entry observations
also include winners; retain re-entry rather than delete it from this ablation.
These groups motivate a test, not counterfactual savings or profitability.
Do not discard low-activity winners or impose a blanket wide-stop veto.

## Ladder integration

Generic `INDEPENDENT_FIXED_LOTS` remains the OMS execution rule. The new
`SessionAcquisitionPolicy.ONCE_PER_EXTENDED_SESSION` is separate: accepted
entry acknowledgements consume a ticker/session even after zero-fill
cancellation; unknown submissions block; never-acknowledged rejected or
cancelled submissions do not consume it. PM and AH locks are independent.

The generic reducer consumes normalized OMS groups and exact caller-verified
strategy/assignment group membership. It never infers strategy ownership from
a protection profile name. The caller still must establish the complete as-of
committed prefix, real Portfolio reservations and actual permissions. It is
not a substitute for those authorities or an installed native ladder writer.

Start the ladder with causal certified squeeze observation, declared VWAP and
liquidity qualification, frozen structural breakout, confirmed swing-low fixed
stop, three equal independently protected lots, no trailing or rotation. Compare
targets and weights only after actual proposals reach a financial run.

## Current evidence and remaining execution

Normalized Strategy42 release is
`strategy-one-42:61d09336-6eb1-4298-bc8e-1b985e97aa78`, payload hash
`048fbd8a27269fcb7c12e1c49d7213e2eb08320a42d86e355d8e55fbdbce37b6`.
The current shared checkout does not satisfy its historical source approval.
Use the frozen `173acaf1ad39be88130258739a002089d3565f79` harness to reproduce
Strategy42; never replace its release or bypass its check to test new behavior.

A full-population August26 PM app run completed on that frozen harness:
`7bfdcdef-b18f-40b7-ae98-918eab8a021a`. Cold saved-result verification reproduced
the prior financial summary: 11 positions, net -$1,205.60377, fees $138.15,
zero open lifecycles, broker-observed drawdown $1,263.98377. Preflight took
25.229s, app launch 4.349s, execution 63.002s for 9,556 processed rows.
This is one full-session baseline measurement, not candidate speed acceptance
or profitability. Asynchronous broker-observed drawdown is not a continuous
atomic equity-curve maximum.

Reports, source receipts, rule tests and benchmark evidence belong under
`D:/TradingML/runtimes/strategy-optimization-20261005`, with the cold report
under the workstation counterpart. The original development campaign manifest
on the workstation has SHA256
`b2567a61ecbe110caadc2100b1ca11e8091c27be64a43ff4d0713d9046150d2e`;
the laptop copy contains only the first run and must not determine campaign
completeness.

Strategy46 now has an installed native Backtest-only contract, exact numbered
witness/journal round-trip, normalized publisher and cold-report reader. Its
additional AH early-risk rule runs after every inherited exit. Strategy42's
normalized release remains unchanged. The new source certificate requires the
complete inherited proof plus exact reviewed successor sources; source mutation
tests reject changed modules. Native financial execution remains required.

Next: publish the fresh immutable release from its clean approved source,
execute saved PM parity and AH comparisons, then all 26 development sessions.
Select two genuinely uninspected later certified validation dates from exposure
metadata and input preflight only. September1–11 includes exposed research;
the live related split trains through September10 and assigns September11 to
validation, unlike the older local receipt. September14–18 requires exposure
reconciliation. Live read-only inventory found no native candidate, entry,
pivot or V7 coverage rows from September14 onward; later validation cannot
currently pass native preflight. No replacement holdouts have been frozen.
Never read
validation trades, tickers, logs, features or charts. Helpers and prepared
release seals are not financial results; the goal remains active until all
declared development and untouched validation PM/AH sessions meet +$500 net.

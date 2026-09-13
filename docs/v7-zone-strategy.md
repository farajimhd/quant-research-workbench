# V7 upper HOD zone candidate

`src.backend.v7_zone_candidate.create()` publishes a separate immutable research
candidate. Candidate 198 uses `v7_zone_enabled=1` and
`v7_center_swing_enabled=1`, profile `v7-center-swing-v2`. Candidate 197 retains
its upper-band entry and resistance-stop policy because the new switch defaults
to zero. This is not a profitability approval.

The entry floor is `VWAP + (1-entry_zone_fraction)*(HOD-VWAP)` (default 30% zone).
At a completed non-red 1s close, select the nearest upcoming resistance boundary
from the preceding causal V7 snapshot within the current zone, and require its
center price to be crossed, with no upper-band offset. The candle must close
strictly above that center; red candles and intrabar updates cannot enter.
Current price and
executable quotes must remain in the zone. HOD must exceed a finite positive VWAP.
There is no HOD-only or delayed-breakout fallback in this candidate.

Historical and current-session origin never changes level eligibility. A V7
transition retains `side=0`, `role=transition`, and its causal `transition_from`.
Only resistance-origin transitions qualify as resistance for entry, additions
and targets. They are separately passed to strategy observations;
ordinary support/resistance consumers do not receive them as confirmed roles.
Unknown transition origins are not guessed. No historical book recalculation is
required; existing role history supplies the origin when available.

Entry requires the existing volume/liquidity gates, current spread <=115 bps,
and causal forming 5s MACD above signal. Initial protection is below the latest
valid confirmed local swing low, minus the configured buffer. Confirmed supports
do not replace that swing. On subsequent completed 1s candles, select the closest
valid swing low below price and bid whose confirmation became available after
entry. Move the stop below its lower bound only if that raises the working stop.
Unconfirmed/future swings cannot affect protection. Resistance breaks and the
initial-risk trailing fallback no longer move the stop in this policy.
Targets outside regular hours use the next resistance above price times
1.10, with the existing band-placement and synthetic-ladder fallback. Regular
hours retain official LULD or explicitly enabled backtest estimation and the
prior-close $0.75 gate. LULD buffer exits remain active, but LULD does not relocate
the swing stop in this policy.

The candidate keeps three cash tranches at 90% allocation, shared protection,
existing episode/rejection management, and filled-exit-gated reentry. Early
three-green stops are disabled. Sessions are 04:00–20:00 New York, with entry
cutoff 19:55 and flatten at 19:59. Halt handling remains an explicit paper-test TODO.

Strategy evidence records HOD, selected resistance and the zone floor, even while
waiting. The chart draws those recorded values at their publication times with
separate strategy-presentation controls for the zone line and label. Rewind never
projects a later floor into earlier candles.
Candidate 198 records and draws the selected resistance center. Exit intent
markers include a short reason from the issued decision, such as `Failed retest`,
`Swing low failed`, `MACD ended`, or `Stop hit`. Missing decision reasons are
explicitly unavailable; the later filled-exit reason is never substituted.

Candidate 198 validation: 183 Python regression tests passed, plus the production
exit-label browser test, frontend build and strict saved-chart review. The SUGP
August 21 04:00–04:30 run `ad4e4a58-ad17-4529-9e99-bff1d96fc31b` completed;
all six entry requests used center thresholds and confirmed swing initial stops,
and all three stop updates matched their recorded confirmed swings. Evidence is
under `D:/TradingML/runtimes/v7-center-swing-validation`.

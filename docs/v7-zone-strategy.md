# V7 upper HOD zone candidate

`src.backend.v7_zone_candidate.create()` publishes a separate immutable research
candidate. It uses the historical/HOD execution policy with `v7_zone_enabled=1`;
older profiles keep that setting disabled. This is not a profitability approval.

The entry floor is `VWAP + (1-entry_zone_fraction)*(HOD-VWAP)` (default 30% zone).
At a completed non-red 1s close, select the nearest upcoming resistance boundary
from the preceding causal V7 snapshot within the current zone, and require its
upper bound plus the configured $0.01 offset to be crossed. Current price and
executable quotes must remain in the zone. HOD must exceed a finite positive VWAP.
There is no HOD-only or delayed-breakout fallback in this candidate.

Historical and current-session origin never changes level eligibility. A V7
transition retains `side=0`, `role=transition`, and its causal `transition_from`.
Only resistance-origin transitions qualify as resistance for entry, additions,
stop advancement and targets. They are separately passed to strategy observations;
ordinary support/resistance consumers do not receive them as confirmed roles.
Unknown transition origins are not guessed. No historical book recalculation is
required; existing role history supplies the origin when available.

Entry requires the existing volume/liquidity gates, current spread <=115 bps,
and causal forming 5s MACD above signal. Initial protection is the closer valid
confirmed local swing low or confirmed support lower band, minus the configured
buffer. Subsequent stop advancement requires a breakout close plus one holding
close. Targets outside regular hours use the next resistance above price times
1.10, with the existing band-placement and synthetic-ladder fallback. Regular
hours retain official LULD or explicitly enabled backtest estimation and the
prior-close $0.75 gate. LULD protection can tighten the structural initial stop.

The candidate keeps three cash tranches at 90% allocation, shared protection,
existing episode/rejection management, and filled-exit-gated reentry. Early
three-green stops are disabled. Sessions are 04:00–20:00 New York, with entry
cutoff 19:55 and flatten at 19:59. Halt handling remains an explicit paper-test TODO.

Strategy evidence records HOD, selected resistance and the zone floor, even while
waiting. The chart draws those recorded values at their publication times with
separate strategy-presentation controls for the zone line and label. Rewind never
projects a later floor into earlier candles.

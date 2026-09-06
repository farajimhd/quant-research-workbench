# Histogram slope exit prototype

This opt-in Long Momentum r47 parameter adds an early full-position exit.
Existing profiles without the parameter retain their existing behavior.
The test candidate must be a separate cloned profile and historical Run Plan;
it does not promote this experiment to Paper or Live.

```json
{"momentum_management": {"histogram_slope_exit": {
  "enabled": true,
  "window_bars": 3,
  "threshold_bps_per_second": 0.0,
  "require_positive_slope_for_same_period_reentry": true
}}}
```

At each completed 1-second bar, calculate histogram = MACD line minus signal
from that bar's direct indicator fields. Fit an ordinary least-squares line to
the most recent three consecutive completed bars, using elapsed seconds.
Normalize slope by the current completed close and multiply by 10,000 to
obtain basis points per second. Exit when this value is at or below the
threshold, even if MACD is still open and the histogram remains positive.
A positive threshold permits a nearly-flat exit. The allowed window is 2–30 bars.

Ticks and other timeframes cannot contribute samples or trigger this exit.
Missing seconds or invalid inputs reset the window; duplicate and out-of-order
closes do not revise consumed history. The bounded sample history survives
strategy checkpoint serialization. Journal exit evidence includes the sample
timestamps and values, slope, normalization price, threshold and contract.

Protective stops and forced exits retain precedence. Existing MACD, VWAP and
other exits remain available. The exit is a condition on the current slope, not
a required positive-to-negative crossing after entry.

The optional re-entry gate defaults to false for existing candidates. When
enabled, an accepted slope exit while MACD is open arms a durable gate. In that
same open period, entry requires a fresh completed slope strictly above zero
(zero and unavailable slopes block). The gate remains armed until entry, so a
positive slope that falls again before entry does not bypass it. Any causal 1s
MACD observation with line <= signal ends the open period and clears the gate;
this uses the existing intrabar MACD authority. Another exit reason does not arm
it. Ordinary entries retain their existing rules, including waiting for pending
exit fills. There is no added time-based cooldown.

Validation covers the real strategy-engine exit/cancel-acquisition path,
unchanged disabled behavior, positive-histogram early exits, normalization,
serialization, missing/invalid inputs and timestamp causality. A saved JUNS
completed-bar audit is only an indicator-condition check, not a portfolio
backtest or evidence of improved returns.

The successor test candidate also enables these parameters (absent parameters
preserve earlier candidates):

```json
{
  "entry_candle_confirmation": {
    "slope_reentry_break_previous_high": true,
    "minimum_reentry_macd_gap_bps": 1.0
  },
  "structural_entry": {"break_above_upper_bound": true},
  "momentum_management": {"resistance_rejection_exit": true}
}
```

Slope-gated re-entry must have a non-red current one-second candle and price
strictly above the previous completed one-second candle high. The high is
carried from the canonical completed bar, not inferred from its close or from
the developing candle. Missing or older-than-two-second candle evidence blocks.
All re-entries additionally require `(MACD line - signal) / price * 10000 > 1`;
initial entries retain their 0.5 bps setting. The positive slope requirement
continues to apply only after a slope exit in the same MACD-open period.

Resistance ordering and quality selection still use the level price. Breakout
acceptance requires a non-red completed close strictly above the selected
resistance's upper band, and current price must remain above it. The point-book
adapter preserves original bounds in `band_lower` and `band_upper`; these are
used before the point-valued `lower` and `upper` fields. Missing/invalid bounds
cannot certify a breakout. Stops and profit-target prices retain their current
contract.

While holding, an observed price entering a qualified resistance band from
below arms a rejection test. The first later price strictly below its lower
bound triggers a full exit unless a price strictly above its upper bound has
already cancelled that test. Equality at the upper bound is a touch. Removed,
role-changed, unqualified or geometrically changed levels clear the old test.
Tests are scoped to the entry lifecycle, survive checkpoint serialization,
and contain touch/rejection timestamps and band geometry for audit. Protective
and session exits retain precedence. No Paper/Live promotion is implied.

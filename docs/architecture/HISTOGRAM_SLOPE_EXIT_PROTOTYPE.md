# Histogram slope exit prototype

This opt-in Long Momentum r47 parameter adds an early full-position exit.
Existing profiles without the parameter retain their existing behavior.
The test candidate must be a separate cloned profile and historical Run Plan;
it does not promote this experiment to Paper or Live.

```json
{"momentum_management": {"histogram_slope_exit": {
  "enabled": true,
  "window_bars": 3,
  "threshold_bps_per_second": 0.0
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
other exits remain available. This does not change entries or add a cooldown;
an otherwise valid immediate re-entry may therefore be followed by another
slope exit. The prototype is a condition on the current slope, not a required
positive-to-negative crossing after entry.

Validation covers the real strategy-engine exit/cancel-acquisition path,
unchanged disabled behavior, positive-histogram early exits, normalization,
serialization, missing/invalid inputs and timestamp causality. A saved JUNS
completed-bar audit is only an indicator-condition check, not a portfolio
backtest or evidence of improved returns.

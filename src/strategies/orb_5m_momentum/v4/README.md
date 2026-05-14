# ORB 5M Momentum v4

This version ports the QuantConnect `v-orb-single-relaxed` implementation from
the saved `2025-05-07 12-45` code into the local provider-backed backtest
engine.

## Design

The strategy is a single-position opening-range breakout:

- build the opening range from completed 1-minute bars 09:31 through 09:35 ET
- rank once at 09:35 ET
- select up to `max_candidates` by opening relative volume
- submit one stop-entry order at a time
- after an exit or canceled entry, try the next ranked candidate if there is
  still enough time
- flatten before the close

This version intentionally does not use 5-minute MACD/TEMA gates. The attached
QuantConnect code only used the 5-minute opening range plus daily ATR, average
daily volume, and previous close.

## Data Requirements

- Event bars: `1m`
- Event feature groups: `core`, `session`
- Daily context: provider-built `1d` bars plus `volatility` features, default
  `daily_lookback_days = 20`

The strategy uses the engine's provider daily context for:

- `avg_daily_volume_14`
- `atr_14`
- `previous_close`

If the requested daily lookback has not been built in the provider store, the
backtest should fail before simulation. For short local smoke tests, reduce
`daily_lookback_days` only as a test override.

## Setup Filters

A symbol must pass:

- price between `min_price` and `max_price`
- average daily volume at least `min_avg_daily_volume`
- ATR at least `min_atr`
- opening relative volume at least `min_opening_relative_volume`
- gap up at least `min_gap_up_pct`
- bullish opening range close
- opening range between configured ATR fractions
- close location at least `min_close_location`
- body-to-range at least `min_body_to_range`
- minimum trade value and planned risk checks

## Orders and Exits

Entry is a buy stop at:

```text
entry = opening_range_high * (1 + entry_buffer_pct)
```

Stop is:

```text
stop = entry - atr_14 * atr_stop_fraction
```

The strategy exits on:

- protective stop cross
- end-of-day flatten
- cancel of unfilled entry near the close

This is meant to match the saved QuantConnect version as closely as the local
engine contract allows.

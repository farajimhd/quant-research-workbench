# ORB 5M Momentum v7

This version starts from v6 and adds a general green-candle entry rule. Scanner
opportunities that print a red completed `1m` candle during entry evaluation are
removed from the pending scanner queue from that point forward.

## Design

The strategy is a single-position opening-range breakout:

- apply the QuantConnect universe prefilter for price, daily dollar volume,
  symbol denylist/suffix denylist, and top 500 daily dollar-volume names
- build the opening range from completed 1-minute bars 09:31 through 09:35 ET
- rank once the opening range is available to the local engine at 09:36 ET
- select up to `max_candidates` by opening relative volume
- submit the same stop-entry structure as v6, but allow the stop to fill only
  when the completed triggering `1m` candle is not red
- remove pending scanner candidates once their current entry-evaluation candle
  is red
- once a position has been open for at least one completed bar, take profit when
  the completed close is at least `take_profit_reentry_pct` above entry
- on the next candle for that ticker, reenter the same symbol with the same size
  only when that candle is green and 1-minute `tema9` is higher than it was on
  the profit-pocket bar
- if that TEMA9 confirmation fails, abandon the reentry and continue with the
  remaining scanner opportunities
- after an exit or canceled entry, try the next ranked candidate if there is
  still enough time
- flatten before the close

This version intentionally does not use 5-minute MACD/TEMA gates. It uses 1m
TEMA9 only for the v6/v7 profit-reentry confirmation.

## Data Requirements

- Event bars: `1m`
- Event feature groups: `core`, `session`, `momentum`
- Daily context: provider-built `1d` bars plus `volatility` features, default
  `daily_lookback_days = 30` calendar days to approximate QuantConnect's
  20 daily-history bars

The strategy uses the engine's provider daily context for:

- `avg_daily_volume_14`
- `atr_14`, computed from the last 14 daily true ranges to match the attached
  QuantConnect `UpdateDailyStats` logic
- `previous_close`

If the requested daily lookback has not been built in the provider store, the
backtest should fail before simulation. For short local smoke tests, reduce
`daily_lookback_days` only as a test override.

## Setup Filters

A symbol first passes the QuantConnect-style universe prefilter:

- previous price between `min_universe_price` and `max_price`
- average daily dollar volume at least `min_daily_dollar_volume`
- denylisted fund/leveraged ETF symbols and warrant/unit/preferred suffixes are
  excluded
- top `max_universe_size` names by average daily dollar volume

A universe symbol must then pass the ORB setup:

- opening-range close at least `min_price`
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

Initial entry is a buy stop at:

```text
entry = opening_range_high * (1 + entry_buffer_pct)
```

Unlike v6, the stop is ignored on completed red `1m` candles. If the bar high
crosses the stop but the bar closes below its open, the order stays pending
instead of filling.

Stop is:

```text
stop = entry - atr_14 * atr_stop_fraction
```

The strategy exits on:

- protective stop cross
- end-of-day flatten
- cancel of unfilled entry near the close

This is meant to match the saved QuantConnect version as closely as the local
engine contract allows, with the v7 green-candle rule layered on top.

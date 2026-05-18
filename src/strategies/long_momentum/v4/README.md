# Long Momentum v4

Long Momentum v4 starts from v2 and changes the entry timing. It keeps the same
extended-hours 1-minute operating model: `current_open` is the actionable bar
open, and all `last_*` fields describe the previous completed bar.

## Setup

At each bar open, v4 first checks the shared setup filters:

- `last_close` between `min_price` and `max_price`, default 1 to 10
- `last_volume >= min_volume`, default 10,000
- `last_transactions >= min_transactions`, default 100
- `long_momentum_spread_ok == true`
- `last_tema_open == true`
- `last_macd_line > 0`
- `last_macd_hist_z_since_open >= min_macd_hist_z_since_open`, default 0.1
- `last_recent_dollar_volume_5 >= min_recent_dollar_volume_5`, default 100,000
- `last_spread_bps_abs <= max_spread_bps_abs`, default 100 bps
- `last_spread_bps_max <= max_spread_bps_max`, default 150 bps
- `last_quote_valid_ratio >= min_quote_valid_ratio`, default 0.8
- `last_locked_or_crossed_count <= max_locked_or_crossed_count`, default 0
- `last_bearish_volume_divergence_score < max_bearish_divergence_entry_score`, default 75

Eligible rows are ranked by `last_recent_volume_5` descending.

## Entry Triggers

Both entry triggers are configurable and enabled by default.

- `enable_entry_trigger_1_earlier_body_break`: Entry Trigger 1, Earlier Body
  Break. This enters when the setup filters pass, the bar is inside one of the
  configured Trigger 1 windows, and `current_open` breaks the active body-break
  threshold by at least `trigger_1_min_break_bps`, default 10 bps. The default
  Trigger 1 windows are 08:00-10:00 ET and 15:00-20:00 ET.
- `enable_entry_trigger_2_pullback_reclaim`: Entry Trigger 2, Pullback/Reclaim.
  When a setup appears, v4 stores the setup body high and setup low for
  `pullback_reclaim_valid_bars`, default 6 bars. If price pulls back below the
  setup body high and later opens back above it while the full setup filters
  remain valid, v4 can enter on that reclaim.

The submitted buy is a same-bar limit at `current_open`. Quantity is the prior
bar `last_quote_ask_size`, capped by available cash and risk-based sizing.
`risk_per_trade_pct`, default 0.5% of account equity, caps quantity by the
initial stop distance.

## Stop Loss

The initial stop is a regular fixed protective stop. v4 uses the setup stop low
when available, calculated from the lowest valid value below entry among:

- `last_3_candle_low_price`
- the previous completed bar body low, `min(last_open, last_close)`

If neither value is valid and below entry, v4 falls back to one cent below entry
using `stop_offset_dollars`, default 0.01. The strategy does not trail this stop.

## Exit

Open positions are exited by:

- the fixed protective stop
- `BEARISH_VOLUME_DIVERGENCE_CLOSE`, when
  `last_bearish_volume_divergence_score >= exit_definite_bearish_divergence_score`,
  default 90
- `BEARISH_VOLUME_DIVERGENCE_WATCH`, when
  `last_bearish_volume_divergence_score >= exit_watch_bearish_divergence_score`,
  default 50; this medium-high warning applies while the score is below the
  definite-close threshold and raises the active stop to the highest watched
  completed close
- `EOD`, at the end of the extended-hours session

## Data Requirements

- Event bars: `1m`
- Event feature groups: `core`, `momentum`, `session`, `volume_liquidity`
- No daily context dependency

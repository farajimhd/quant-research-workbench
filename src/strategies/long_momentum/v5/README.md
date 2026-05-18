# Long Momentum v5

Long Momentum v5 is an early-uptrend lifecycle strategy. It is intentionally
stricter than v4: it removes the loose Pullback/Reclaim entry, enters only when
price, VWAP, TEMA/MACD, volume, and early-move filters agree, and then tries to
hold through ordinary higher-low pullbacks until trend structure or volume
divergence says the move is likely over.

The strategy keeps the same provider-backed 1-minute operating model:
`current_open` is the actionable bar open and all `last_*` fields describe the
previous completed bar.

## Setup

At each bar open, v5 first checks basic liquidity and quote quality:

- `last_close` between `min_price` and `max_price`, default 1 to 10
- `last_volume >= min_volume`, default 10,000
- `last_transactions >= min_transactions`, default 100
- `long_momentum_spread_ok == true`
- `last_recent_dollar_volume_5 >= min_recent_dollar_volume_5`, default 100,000
- `last_spread_bps_abs <= max_spread_bps_abs`, default 100 bps
- `last_spread_bps_max <= max_spread_bps_max`, default 150 bps
- `last_quote_valid_ratio >= min_quote_valid_ratio`, default 0.8
- `last_locked_or_crossed_count <= max_locked_or_crossed_count`, default 0
- `last_bearish_volume_divergence_score < max_bearish_divergence_entry_score`,
  default 50

Then v5 requires actual uptrend quality:

- `current_open > last_vwap` and `last_close > last_vwap`
- `last_tema_open == true`
- `(last_tema9 / last_tema20) - 1 >= min_tema_spread_pct`, default 0.5%
- `last_macd_line > 0`
- `last_macd_hist > 0`
- `last_macd_hist_z_since_open >= min_macd_hist_z_since_open`, default 0.5
- `current_open > last_day_open` and `last_close > last_day_open`

Volume must confirm the move:

- `last_volume / last_avg_volume_so_far >= min_volume_vs_avg_so_far`, default
  1.5
- `last_volume / last_volume_avg_3 >= min_volume_vs_recent_3`, default 0.75

## Early-Move Filter

v5 rejects entries that look late, vertical, or directly under a tired high:

- `current_open` must be no more than `max_distance_above_vwap_pct`, default
  8%, above VWAP.
- `current_open` must be no more than `max_distance_from_day_low_pct`, default
  35%, above the day low.
- `current_open` must be no more than `max_open_above_last_close_pct`, default
  3%, above the previous close.
- The previous completed candle range must be no more than
  `max_last_bar_range_pct`, default 12%, of close.
- `last_close_location >= min_close_location`, default 0.55.
- Price must not be too far below the day high, but if it is already within
  `near_day_high_chase_pct`, default 0.5%, of the high then it must break to a
  fresh high by at least `fresh_day_high_break_bps`, default 5 bps.

## Entry

v5 has one entry trigger:

```text
setup_open
AND inside one of the configured entry windows
AND current_open > active_body_break_threshold by min_body_break_bps
AND current_open >= last_close
```

The default entry windows are 08:00-10:00 ET and 15:00-20:00 ET.

The active body-break threshold is the larger of the previous completed bar body
high and any still-active setup body high. Setup highs remain active for
`setup_valid_bars`, default 3 bars.

The submitted buy is a same-bar limit at `current_open`. Quantity is capped by
prior-bar ask size, available cash, and risk-based sizing. `risk_per_trade_pct`,
default 0.5% of account equity, caps quantity by the initial stop distance.

## Stop Loss

The initial stop is structural. v5 uses the lowest valid support below entry
among:

- active setup stop low
- `last_3_candle_low_price`
- previous completed bar body low, `min(last_open, last_close)`
- `last_vwap` minus `vwap_stop_buffer_pct`, default 0.3%

If no structural value is valid, v5 falls back to `entry - stop_offset_dollars`.
The trade is skipped when initial risk is above `max_initial_risk_pct`, default
8% of entry.

## Holding And Exit

v5 is designed to hold through ordinary pullbacks. It does not exit on one small
red candle while structure remains valid.

The stop is managed in phases:

- New trade: use the initial structural stop.
- After `breakeven_activation_r`, default 1R, stop cannot remain below entry.
- After `structural_trail_activation_r`, default 1.5R, stop trails the strongest
  valid structural support below the last close: last three-candle low, TEMA20,
  or buffered VWAP.
- If bearish volume divergence is between the watch and definite-close
  thresholds, v5 raises the active stop to the strongest watched close.

Definite exits:

- `last_bearish_volume_divergence_score >= exit_definite_bearish_divergence_score`,
  default 90
- profitable VWAP trend failure after the trade has reached structural-trail
  activation
- profitable TEMA9/TEMA20 trend failure
- MACD histogram below zero while price is at or below VWAP
- hard stop
- EOD flatten

## Scanner Diagnostics

The scanner exposes v5-specific columns including:

- `long_momentum_v5_price_above_vwap`
- `long_momentum_v5_trend_quality_ok`
- `long_momentum_v5_volume_expansion_ok`
- `long_momentum_v5_day_high_position_ok`
- `long_momentum_v5_early_move_ok`
- `long_momentum_v5_setup_open`
- `long_momentum_v5_entry_time_ok`
- `long_momentum_v5_entry_threshold`
- `long_momentum_v5_early_uptrend_entry_open`
- `long_momentum_v5_entry_open`

## Data Requirements

- Event bars: `1m`
- Event feature groups: `core`, `momentum`, `session`, `volume_liquidity`
- No daily context dependency

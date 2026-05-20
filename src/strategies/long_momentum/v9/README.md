# Long Momentum v9

Long Momentum v9 is a live-safe day momentum watchlist strategy. It uses only
current/open fields and completed `last_*` bar features from the provider-built
strategy-time rows. It does not use lookahead returns, oracle supervision, or
future bars.

## Scanner And Day Watchlist

The scanner is the raw source of rows. The strategy keeps its own day momentum
watchlist.

A ticker is eligible for the watchlist when:

- `min_price <= last_close <= max_price`
- `last_5m_return >= min_last_5m_return`
- `last_volume >= min_watchlist_add_volume`
- `last_transactions >= min_first_entry_transactions`

The default `min_watchlist_add_volume` is `8000`.

Passing the watchlist filters does not submit an entry by itself. The ticker
can later enter through either High Break Hold or VWAP Reclaim.

For 1-minute bars:

```text
last_5m_return = last_return_5
```

`last_return_5` is provider-built from the same ticker/session only. Until a
true five-bar lookback exists, it uses the first completed session close as the
placeholder baseline, so the first completed bar is neutral at `0.0`. After
that it uses the close from five bars earlier. Because strategy-time rows use
completed-bar inputs, `last_5m_return` at the current open is always based on
the previous completed candle. It never uses prior-session prices or future
bars.

## High Break Hold Entry

Each day-watchlist ticker is eligible for one High Break Hold entry. High Break
Hold has the highest priority, but it does not enter immediately when the day
high is touched. It first adds the ticker to a separate high-break watchlist
when:

- the ticker is already in the day momentum watchlist
- the current minute is inside the configured trading window
- `current_open >= last_day_high_so_far`
- the ticker has not already submitted or filled its High Break Hold entry

`last_day_high_so_far` is the session high known before the current actionable
bar, so the breakout check does not use the current bar high or any future bar.

After a ticker enters the high-break watchlist, v9 waits for later bars to hold
the breakout level:

```text
hold_threshold = breakout_level * (1 - high_break_hold_tolerance_ratio)
hold_ok = last_close >= hold_threshold
          and (last_close >= last_open or last_close >= breakout_level)
```

The entry can submit only when `hold_ok` has been true for
`high_break_hold_confirmation_bars` consecutive later bars. The default is `1`.

If High Break Hold candidates appear while cash is tied up in lower-priority
VWAP Reclaim positions, v9 submits same-bar sell orders for enough of those
lower-priority positions to fund the High Break Hold target size. Existing High
Break Hold positions are not rotated out by this rule. If multiple High Break
Hold candidates appear on the same bar, v9 splits available cash equally across
them, respecting the configured maximum entry order size.

High Break Hold uses the tighter valid stop from the VWAP stop and breakout
stop:

```text
limit_price = current_open
entry_price = filled limit_price
vwap_stop = last_vwap - (last_vwap * vwap_stop_offset_pct / 100)
breakout_stop = breakout_level * (1 - high_break_stop_offset_ratio)
stop_price = max(vwap_stop, breakout_stop)
```

The stop is active immediately and trails upward with VWAP while the position
is open.

For the first `first_entry_soft_exit_wait_bars` completed bars after a High
Break Hold fill, the soft exits are disabled:

- TEMA close
- 2xBVD
- pocketing

The protective VWAP stop remains active during this wait. The default wait is
`3` bars.

After that fixed wait, High Break Hold keeps soft exits disabled while the
position keeps making new highs or staying close to the highest high since
entry. This is controlled by `first_entry_high_lifecycle_exit_enabled`, which
is enabled by default.

On each completed bar after the High Break Hold fill, v9 calculates:

```text
highest_high_since_entry = max(highest_high_since_entry, last_high)
near_high_threshold = highest_high_since_entry * (1 - first_entry_high_near_tolerance_ratio)

if last_high > previous_highest_high:
    no_new_high_count = 0
elif last_high >= near_high_threshold:
    no_new_high_count = 0
else:
    no_new_high_count += 1
```

Soft exits become eligible only when:

```text
no_new_high_count >= first_entry_high_stall_bars
```

Defaults are `first_entry_high_near_tolerance_ratio = 0.003`, equal to 0.3%,
and `first_entry_high_stall_bars = 6`. This gives the first entry room to pause
near the high without allowing TEMA, 2xBVD, or pocketing to close the position
too early. The stop remains active the whole time.

The older green-body lifecycle values are still calculated and shown in debug,
but body lifecycle exit gating is off by default. If
`first_entry_body_lifecycle_exit_enabled = true`, High Break Hold also keeps
soft exits disabled until the green-body lifecycle contracts from its peak.

On each completed bar after the High Break Hold fill, v9 calculates:

```text
green_body = max(last_close - last_open, 0)
green_body_pct = green_body / last_open
green_body_ema_fast = EMA(green_body_pct, first_entry_body_fast_ema_bars)
green_body_ema_slow = EMA(green_body_pct, first_entry_body_slow_ema_bars)
peak_green_body_ema_fast = max(green_body_ema_fast since High Break Hold entry)
body_strength_ratio = green_body_ema_fast / peak_green_body_ema_fast
```

Soft exits become eligible only when:

```text
body_strength_ratio <= first_entry_body_contraction_ratio
for first_entry_body_contraction_bars consecutive completed bars
```

Body lifecycle defaults are `first_entry_body_fast_ema_bars = 3`,
`first_entry_body_slow_ema_bars = 8`,
`first_entry_body_contraction_ratio = 0.65`, and
`first_entry_body_contraction_bars = 2`.

## VWAP Reclaim Entry

VWAP Reclaim is the second entry method. It does not require High Break Hold to
have happened. A ticker can enter when all VWAP Reclaim rules are true:

- the ticker was added to the watchlist on a prior bar, not the current bar
- there is no open position or pending order for the ticker
- the current minute is inside the configured trading window
- `min_price <= last_close <= max_price`
- `last_close >= last_vwap * (1 + reentry_vwap_buffer_pct / 100)`
- the completed VWAP reclaim bar is not red: `last_close >= last_open`
- last completed candle TEMA is open by the configured buffer:
  `last_tema9 >= last_tema20 * (1 + tema9_open_buffer_pct)`

The 5-minute return, volume, and transaction thresholds are used to add the
ticker to the watchlist. For VWAP Reclaim, the gate is the VWAP/body-break
rule, and the first possible VWAP Reclaim entry is the next bar after
the ticker entered the day momentum watchlist.

If multiple watchlist VWAP entry candidates appear on the same bar, v9 splits
available cash equally across them and submits them at the same current open.

VWAP Reclaim also blocks bearish exhaustion on the last completed
candle:

```text
last_bearish_volume_divergence_score <= max_reentry_bvd_score
```

The default `max_reentry_bvd_score` is `80.0`, so a 1-minute BVD score above
80 blocks VWAP Reclaim. This does not block High Break Hold.

VWAP Reclaim requires the last completed candle to close above VWAP
by the configured buffer:

```text
last_close >= last_vwap * (1 + reentry_vwap_buffer_pct / 100)
```

The default `reentry_vwap_buffer_pct` is `2.0`.

VWAP Reclaim also requires the last completed candle TEMA stack to be
open:

```text
last_tema9 >= last_tema20 * (1 + tema9_open_buffer_pct)
```

The default `tema9_open_buffer_pct` is `0.002`, which is a ratio equal to
`+0.2%`, so VWAP Reclaim requires the completed-bar TEMA9 to reach 100.2%
of completed-bar TEMA20.

VWAP Reclaim also requires the current bar open to break the highest
body high of the last two completed bars:

```text
current_open > max(
  max(last_open, last_close),
  max(second_last_open, second_last_close)
)
```

This VWAP Reclaim body-break rule does not use MACD.

## Entry Sizing And Stop

VWAP Reclaim uses a stop slightly below VWAP:

```text
limit_price = current_open
entry_price = filled limit_price
stop_price = last_vwap - (last_vwap * vwap_stop_offset_pct / 100)
```

Legacy immediate transaction-impulse entry is disabled in current v9. The
`min_first_entry_transactions_vs_prior_3` parameter may still appear in older
debug views, but it no longer opens a same-bar entry.

If `risk_per_share <= 0`, the entry is skipped.

For each candidate cash slice:

```text
max_risk_cash = cash_slice * max_risk_fraction_of_cash
risk_size = max_risk_cash / risk_per_share
cash_size = cash_slice / limit_price
quantity = floor(min(risk_size, cash_size, max_entry_order_quantity))
```

The default `max_entry_order_quantity` is `3000`, so v9 does not submit a BUY
order larger than 3000 shares to the backtest.

The default `limit_order_offset_dollars` is `0.01`. For liquid-limit execution,
v9 treats the bar open as the executable ask, so buys submit at `current_open`.
Sells submit at `current_open - 0.01` as a bid estimate. The backtest fills
matched limit orders at the submitted limit price.

While the position remains open, the stop trails upward with VWAP:

```text
stop_price = max(previous_stop_price, last_vwap - (last_vwap * vwap_stop_offset_pct / 100))
```

## Partial Fill Remainders

When the backtest partially fills a v9 order, v9 submits the remaining quantity
on the next strategy step as an aggressive limit order:

```text
BUY remainder:  limit_price = current_open
SELL remainder: limit_price = current_open - limit_order_offset_dollars
```

BUY remainder orders are also capped by `max_entry_order_quantity`; v9 does not
submit an oversized BUY remainder just because the original desired position was
larger.

## Exit

Main exit has priority:

```text
last_double_timeframe_bearish_volume_divergence_score > double_bvd_exit_score
and last_close <= last_open
```

On 1-minute data this is 2-minute BVD. When it triggers on the last completed
red or flat bar, v9 exits immediately at the current open.

Pocketing:

```text
estimated_bid = current_open
estimated_ask = current_open

if adaptive_pocket_enabled:
    raw_pocket_pct = last_true_range_ema5_pct * adaptive_pocket_vol_multiplier
    active_pocket_pct = clamp(
        raw_pocket_pct,
        adaptive_pocket_min_profit_pct,
        adaptive_pocket_max_profit_pct,
    )
else:
    active_pocket_pct = pocket_profit_pct

if estimated_bid >= entry_price * (1 + active_pocket_pct):
    sell current position at estimated_bid
```

Adaptive pocketing is enabled by default. Its default parameters are
`adaptive_pocket_vol_multiplier = 1.25`, `adaptive_pocket_min_profit_pct = 0.025`,
and `adaptive_pocket_max_profit_pct = 0.06`. The fixed `pocket_profit_pct`
default remains `0.03`; it is used when `adaptive_pocket_enabled = false`, and
as an explicit fallback if adaptive mode cannot read provider-built short
volatility. Pocketing uses the actionable `current_open` directly as the
estimated bid, instead of the prior completed close or the general sell-order
offset. The debug scanner rows expose the pocket mode, volatility input,
calculated pocket percent, current open, trigger price, estimated bid, and
remaining distance to the trigger on every evaluated position bar.

Pocketing only exits the current position. v9 does not reenter on the pocket
candle; after the fill is reported back to the strategy, the ticker remains on
the day momentum watchlist and can enter again on a later bar only through the
normal VWAP Reclaim gates.

Emergency exit:

```text
current_open_tema20 >= current_open_tema9 * (1 + tema9_exit_buffer_pct)
```

The default `tema9_exit_buffer_pct` is `-0.002`, which is a ratio equal to
`-0.2%`, so the TEMA emergency exit triggers when the current-open TEMA20
estimate reaches 99.8% of the current-open TEMA9 estimate. Normal `tema9` and
`tema20` remain close-of-bar indicators; only the active decision bar also has
`current_open_tema9` and `current_open_tema20`. If no main exit is active and
TEMA is closed, v9 exits at the current open.

After exit, the ticker stays in the day momentum watchlist and the same VWAP
entry rule can open another position later in the session.

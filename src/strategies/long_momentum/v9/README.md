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

If the same bar also has:

- `last_transactions_vs_prior_3 >= min_first_entry_transactions_vs_prior_3`

then v9 can enter immediately on that current bar without waiting for the next
bar VWAP entry rule.

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

## Watchlist VWAP Entry

If a ticker only passes the watchlist-add conditions, it waits in the day
momentum watchlist. It can enter later when all VWAP entry rules are true:

- the ticker was added to the watchlist on a prior bar, not the current bar
- there is no open position or pending order for the ticker
- the current minute is inside the configured trading window
- `min_price <= last_close <= max_price`
- `last_close >= last_vwap * (1 + reentry_vwap_buffer_pct / 100)`
- the completed VWAP reclaim bar is not red: `last_close >= last_open`
- last completed candle TEMA is open: `last_tema9 > last_tema20`

The 5-minute return and transaction threshold are used only to add the ticker to
the watchlist unless the same bar also passes the transaction-impulse threshold.
For watchlist-only names, the entry gate is the VWAP cross, and the first
possible VWAP entry is the next bar after the watchlist add.

If multiple watchlist VWAP entry candidates appear on the same bar, v9 splits
available cash equally across them and submits them at the same current open.

Watchlist VWAP reentry also blocks bearish exhaustion on the last completed
candle:

```text
last_bearish_volume_divergence_score <= max_reentry_bvd_score
```

The default `max_reentry_bvd_score` is `80.0`, so a 1-minute BVD score above
80 blocks watchlist reentry. This does not block same-bar immediate First Entry.

Watchlist VWAP reentry requires the last completed candle to close above VWAP
by the configured buffer:

```text
last_close >= last_vwap * (1 + reentry_vwap_buffer_pct / 100)
```

The default `reentry_vwap_buffer_pct` is `2.0`.

Watchlist VWAP reentry also requires the last completed candle TEMA stack to be
open:

```text
last_tema9 > last_tema20
```

Watchlist VWAP reentry also requires the current bar open to break the highest
body high of the last two completed bars:

```text
current_open > max(
  max(last_open, last_close),
  max(second_last_open, second_last_close)
)
```

This reentry body-break rule does not use MACD. `tema9_exit_buffer_pct` is only
used by the emergency TEMA exit; reentry TEMA-open uses the completed candle's
plain `last_tema9 > last_tema20` comparison.

## Entry Sizing And Stop

Immediate entry uses the previous candle open as the stop reference:

```text
limit_price = current_open + limit_order_offset_dollars
entry_price = filled limit_price
stop_price = last_open
```

Watchlist VWAP entry uses a stop slightly below VWAP:

```text
limit_price = current_open + limit_order_offset_dollars
entry_price = filled limit_price
stop_price = last_vwap - (last_vwap * vwap_stop_offset_pct / 100)
```

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
v9 submits buys at `current_open + 0.01` as an ask estimate and sells at
`current_open - 0.01` as a bid estimate. The backtest fills matched limit orders
at the submitted limit price.

While the position remains open, the stop trails upward with VWAP:

```text
stop_price = max(previous_stop_price, last_vwap - (last_vwap * vwap_stop_offset_pct / 100))
```

## Partial Fill Remainders

When the backtest partially fills a v9 order, v9 submits the remaining quantity
on the next strategy step as an aggressive limit order:

```text
BUY remainder:  limit_price = current_open + limit_order_offset_dollars
SELL remainder: limit_price = current_open - limit_order_offset_dollars
```

BUY remainder orders are also capped by `max_entry_order_quantity`; v9 does not
submit an oversized BUY remainder just because the original desired position was
larger.

## Exit

Main exit has priority:

```text
last_double_timeframe_bearish_volume_divergence_score > double_bvd_exit_score
and last_close < last_open
```

On 1-minute data this is 2-minute BVD. When it triggers on the last completed
red bar, v9 exits immediately at the current open.

Pocketing:

```text
estimated_bid = current_open - limit_order_offset_dollars
estimated_ask = current_open + limit_order_offset_dollars

if estimated_bid >= entry_price * (1 + pocket_profit_pct):
    sell current position at estimated_bid
```

The default `pocket_profit_pct` is `0.03`. Pocketing does not check the scanner
or watchlist reentry gates for the pocket sell itself. By default
`pocket_immediate_reentry_enabled` is `False`, so after pocketing v9 waits for a
later candle and uses the normal watchlist reentry gates. If
`pocket_immediate_reentry_enabled` is set to `True`, v9 immediately buys back at
`estimated_ask` on the same bar without checking reentry gates.

Emergency exit:

```text
current_open_tema20 >= current_open_tema9 * (1 + tema9_exit_buffer_pct)
```

The default `tema9_exit_buffer_pct` is `-0.01`, so the TEMA emergency exit
triggers when the current-open TEMA20 estimate reaches 99% of the current-open
TEMA9 estimate. Normal `tema9` and `tema20` remain close-of-bar indicators;
only the active decision bar also has `current_open_tema9` and
`current_open_tema20`. If no main exit is active and TEMA is closed, v9 exits
at the current open.

After exit, the ticker stays in the day momentum watchlist and the same VWAP
entry rule can open another position later in the session.

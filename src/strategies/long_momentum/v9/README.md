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
- `last_transactions >= min_first_entry_transactions`

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
- `last_close > last_vwap`
- the completed VWAP reclaim bar is not red: `last_close >= last_open`

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

Watchlist VWAP reentry also requires the current-open TEMA stack to remain
constructive using the same buffer as the TEMA exit, but with the inverse
condition:

```text
current_open_tema20 < current_open_tema9 * (1 + tema9_exit_buffer_pct)
```

With the default `tema9_exit_buffer_pct = -0.01`, reentry is allowed only while
current-open TEMA20 is still below 99% of current-open TEMA9.

## Entry Sizing And Stop

Immediate entry uses the previous candle open as the stop reference:

```text
entry_price = current_open
stop_price = last_open
```

Watchlist VWAP entry uses a stop slightly below VWAP:

```text
entry_price = current_open
stop_price = last_vwap - (last_vwap * vwap_stop_offset_pct / 100)
```

If `risk_per_share <= 0`, the entry is skipped.

For each candidate cash slice:

```text
max_risk_cash = cash_slice * max_risk_fraction_of_cash
risk_size = max_risk_cash / risk_per_share
cash_size = cash_slice / current_open
quantity = floor(min(risk_size, cash_size, max_entry_order_quantity))
```

The default `max_entry_order_quantity` is `3000`, so v9 does not submit a BUY
order larger than 3000 shares to the backtest.

While the position remains open, the stop trails upward with VWAP:

```text
stop_price = max(previous_stop_price, last_vwap - (last_vwap * vwap_stop_offset_pct / 100))
```

## Partial Fill Remainders

When the backtest partially fills a v9 order, v9 submits the remaining quantity
on the next strategy step as an aggressive limit order:

```text
BUY remainder:  limit_price = max(current_open, bid) + partial_fill_reprice_offset
SELL remainder: limit_price = min(current_open, ask) - partial_fill_reprice_offset
```

The default `partial_fill_reprice_offset` is `0.01`. BUY remainder orders are
also capped by `max_entry_order_quantity`; v9 does not submit an oversized BUY
remainder just because the original desired position was larger.

## Exit

Main exit has priority:

```text
last_double_timeframe_bearish_volume_divergence_score > double_bvd_exit_score
```

On 1-minute data this is 2-minute BVD. When it triggers on the last completed
bar, v9 exits immediately at the current open.

Secondary main exit:

```text
peak_completed_close_pnl > 0
and (peak_completed_close_pnl - current_completed_bar_pnl) / peak_completed_close_pnl > profit_giveback_exit_pct
```

The default `profit_giveback_exit_pct` is `0.15`, so v9 exits at the current
open when the completed-bar P/L has given back more than 15% of the best
completed-close P/L seen so far. This intentionally ignores candle highs for
profit giveback; highs still update trade MFE and stop-loss simulation still
uses intrabar lows.

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

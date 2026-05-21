# Long Momentum v11

Long Momentum v11 is a price-pop continuation strategy.

## Watchlist Add

At a strategy decision bar, v11 treats the prior completed bar as the pop bar.
A ticker is added to the pop watchlist only when all are true:

- `min_price <= last_close <= max_price`, default `$1` to `$10`
- `last_return_5 >= min_last_5m_return`, default `0.08`
- `last_volume >= min_watchlist_add_volume`, default `8000`
- `last_transactions_avg_prior_3 > 0`
- `last_transactions / last_transactions_avg_prior_3 >= min_pop_transaction_ratio`,
  default `20`
- `last_vwap > 0`

The watchlist stores the pop high, close, VWAP, transactions, prior-three
transaction average, and pop transaction ratio.

## Entry

V11 does not buy the pop directly. For the first entry after the pop, it
requires the entry bar's completed-bar transactions to still be liquid versus
the same pre-pop baseline:

```text
entry_transaction_ratio = last_transactions / pop_prior_3_avg_transactions
entry_transaction_ratio >= min_entry_transaction_ratio
```

Default `min_entry_transaction_ratio` is `10`.

If the entry is still inside `entry_expire_bars`, default `3`, v11 submits a
BUY STOP:

```text
buy_stop = pop_high + pop_entry_stop_offset_dollars
buy_limit = buy_stop + pop_entry_limit_offset_dollars
```

The current backtest fill model defers BUY STOP fills to the next bar open
after the stop is crossed.

The initial stop is VWAP based:

```text
initial_stop = pop_vwap * (1 - vwap_stop_offset_pct / 100)
```

Default `vwap_stop_offset_pct` is `1.0`. If pop VWAP is unavailable, v11
rejects the entry rather than falling back to the pop low.

## Management

After fill, v11 lets the position run while price remains extended above VWAP
and VWAP is not turning down.

It tracks:

- current distance above VWAP
- maximum distance above VWAP since entry
- VWAP slope versus the previous observed VWAP
- consecutive VWAP-down bars

Exits:

- hard/protective stop under VWAP
- VWAP slope down for `vwap_slope_down_bars`, default `2`
- VWAP-distance giveback after the trade expanded by at least
  `min_vwap_distance_for_giveback_pct`, default `0.04`

The giveback rule exits when:

```text
current_distance_above_vwap <= max_distance_above_vwap * (1 - vwap_distance_giveback_pct)
```

Default `vwap_distance_giveback_pct` is `0.40`.

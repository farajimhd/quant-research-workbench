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

## First Entry

First Entry has higher priority than watchlist reentry. A ticker that is already
in the day watchlist and has not had its first entry submitted today can enter
when all main entry rules are true:

- `last_5m_return >= min_last_5m_return`
- `last_transactions >= min_first_entry_transactions`
- `last_transactions_vs_prior_3 >= min_first_entry_transactions_vs_prior_3`

The price range is an eligibility parameter, not a main entry trigger.
`min_first_entry_transactions` and
`min_first_entry_transactions_vs_prior_3` are calibrated for 1-minute bars; they
may not hold for other timeframes without retuning.

If multiple First Entry candidates appear on the same bar, v9 splits available
cash equally across them and submits them at the same current open.

## First Entry Sizing And Stop

First Entry uses the previous candle open as the stop reference:

```text
entry_price = current_open
stop_price = last_open
risk_per_share = current_open - last_open
```

If `risk_per_share <= 0`, the entry is skipped.

For each candidate cash slice:

```text
max_risk_cash = cash_slice * max_risk_fraction_of_cash
risk_size = max_risk_cash / risk_per_share
cash_size = cash_slice / current_open
quantity = floor(min(risk_size, cash_size))
```

## Rotation

If First Entry candidates appear while another position is already open:

- if there is cash for the new First Entry group, keep the existing position and
  enter the new candidates
- if there is no cash for the new First Entry group, close existing positions at
  the current open and enter the new First Entry candidates at the current open

Watchlist reentries never force rotation.

## Exit

Main exit has priority:

```text
last_double_timeframe_bearish_volume_divergence_score > double_bvd_exit_score
```

On 1-minute data this is 2-minute BVD. When it triggers on the last completed
bar, v9 exits immediately at the current open.

Emergency exit:

```text
TEMA close
```

If no main exit is active and TEMA is closed, v9 exits at the current open.

## Watchlist Reentry

After exit, the ticker stays in the day momentum watchlist. The watchlist tracks
`max_vwap`, the highest completed-bar VWAP seen since the ticker joined the
watchlist.

Reentry is lower priority than First Entry and is allowed when:

- the ticker had its first entry submitted today
- there is no open position for the ticker
- `last_close > max_vwap`
- `last_tema_open` is true

Reentry stop:

```text
stop_price = last_vwap * (1 - vwap_stop_buffer_pct)
```

While a reentry position remains open, the stop trails upward with VWAP:

```text
stop_price = max(previous_stop_price, last_vwap * (1 - vwap_stop_buffer_pct))
```

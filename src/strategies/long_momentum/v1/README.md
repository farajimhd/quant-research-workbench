# Long Momentum v1

Long Momentum v1 is an extended-hours scanner strategy that trades long from
04:00 ET through 20:00 ET. It evaluates each 1-minute event at the current bar
open. The strategy row exposes `current_open` for the actionable bar's open and
uses `last_*` columns for the previous completed bar, such as `last_high`,
`last_low`, `last_close`, `last_volume`, `last_spread`, and indicator values.

## Scanner

At each bar open the strategy filters the current cross-section using the
strategy-time row:

- `last_close` between `min_price` and `max_price`, default 1 to 10
- `last_volume >= min_volume`, default 10,000
- `last_transactions >= min_transactions`, default 100
- `last_is_red == false`
- `last_return_1 > 0`
- `current_open_above_last_body_high == true`, meaning `current_open >= last_high`
- `last_tema9 > last_tema20`
- `last_macd_line > 0`
- `last_macd_hist_z_since_open >= min_macd_hist_z_since_open`, default 0.1
- `last_max_fill_qty` is available from the provider as the known entry
  capacity. When quote size is present, it is based on the prior bar's
  `quote_ask_size`;
  otherwise it falls back to a 3-bar volume/transaction estimate.
- spread gate:
  - price from 1 through 4.9999: `last_spread <= 0.02`
  - price from 5 through 10: `last_spread <= 0.05`

Eligible rows are ranked by `last_return_1` descending. The top eligible candidate is
the entry candidate.

## Entry And Rotation

The strategy keeps one long position. If there is no open position, it deploys
available cash into the top scanner candidate while reserving enough for the
configured slippage and per-share fee estimate. If the top candidate is already
held, it is ignored.

An eligible scanner row is only an intent. The strategy submits a one-bar-valid
buy stop at `max(current_open, last_close)` of the strategy-time row. The
initial stop is one
cent below the actual entry fill by default. The stop-entry fills when the
current execution bar trades through the trigger. If the trigger is not touched
on that bar, the order expires and the scanner is evaluated again on the next
bar open.

If a different candidate appears while a position is open, the strategy compares
the candidate's one-bar return with the open position's total unrealized return.
When the new one-bar return is stronger, the strategy fully rotates: it exits
the current position and enters the new candidate. There is no partial rotation
in v1.

If quote capacity is zero, the strategy skips the entry before submitting an
order. Otherwise it lets the backtest engine apply execution-bar liquidity.
Entries use ask-side quote size as capacity and exits use bid-side quote size.
If either side partially fills, Long Momentum waits for the next bar open and
submits the remaining quantity as a same-bar limit order at `current_open`.
Later buy fills are averaged into the open position price.

## Stop And Exits

After entry, the active stop and take-profit target are submitted as
one-bar-valid protective orders. If both stop and target are touched inside the
same OHLC bar, the stop is evaluated first as the conservative assumption.
Exits are not evaluated on the same 1-minute bar that just filled the entry,
because OHLC bars do not reveal whether the low happened before or after the
entry trigger.

Additional exits:

- `TEMA_CLOSE`: `last_tema9 < last_tema20 + offset`
- `TAKE_PROFIT`: target exit when the current bar high reaches at least
  `take_profit_pct`, default 10%, above the entry price
- `EOD`: flatten at the end of the available extended-hours session

## Data Requirements

- Event bars: `1m`
- Event feature groups: `core`, `momentum`, `session`, `volume_liquidity`
- No daily context dependency

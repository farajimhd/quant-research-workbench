# Long Momentum v1

Long Momentum v1 is an extended-hours scanner strategy that trades long from
04:00 ET through 20:00 ET. It evaluates each 1-minute event at the current bar
open. The strategy row keeps the current bar's `open` and timestamp, while
`high`, `low`, `close`, volume, transactions, spread, and indicators come from
the previous completed bar.

## Scanner

At each bar open the strategy filters the current cross-section using the
strategy-time row:

- `close` between `min_price` and `max_price`, default 1 to 10
- `volume >= min_volume`, default 10,000
- `transactions >= min_transactions`, default 100
- `is_red == false`
- current close is above the previous completed candle close
- `tema9 > tema20`
- `macd_line > 0`
- `macd_hist_z_since_open >= min_macd_hist_z_since_open`, default 0.1
- `max_fill_qty` is available from the provider as a 3-bar liquidity estimate
  based on prior completed bars
- spread gate:
  - price from 1 through 4.9999: `spread <= 0.02`
  - price from 5 through 10: `spread <= 0.05`

Eligible rows are ranked by `return_1` descending. The top eligible candidate is
the entry candidate.

## Entry And Rotation

The strategy keeps one long position. If there is no open position, it deploys
available cash into the top scanner candidate while reserving enough for the
configured slippage and per-share fee estimate. If the top candidate is already
held, it is ignored.

An eligible scanner row is only an intent. The strategy submits a one-bar-valid
buy stop at `max(open, close)` of the strategy-time row, which means the current
open or previous completed close, whichever is higher. The initial stop is one
cent below the actual entry fill by default. The stop-entry fills when the
current execution bar trades through the trigger. If the trigger is not touched
on that bar, the order expires and the scanner is evaluated again on the next
bar open.

If a different candidate appears while a position is open, the strategy compares
the candidate's one-bar return with the open position's total unrealized return.
When the new one-bar return is stronger, the strategy fully rotates: it exits
the current position and enters the new candidate. There is no partial rotation
in v1.

If the all-cash entry size is larger than provider-estimated `max_fill_qty`, the
strategy skips the entry before submitting an order. The backtest engine still
applies its execution-bar liquidity guard at fill time.

## Stop And Exits

After entry, the active stop and take-profit target are submitted as
one-bar-valid protective orders. If both stop and target are touched inside the
same OHLC bar, the stop is evaluated first as the conservative assumption.
Exits are not evaluated on the same 1-minute bar that just filled the entry,
because OHLC bars do not reveal whether the low happened before or after the
entry trigger.

Additional exits:

- `TEMA_CLOSE`: `tema9 < tema20 + offset`
- `TAKE_PROFIT`: target exit when the current bar high reaches at least
  `take_profit_pct`, default 10%, above the entry price
- `EOD`: flatten at the end of the available extended-hours session

## Data Requirements

- Event bars: `1m`
- Event feature groups: `core`, `momentum`, `session`, `volume_liquidity`
- No daily context dependency

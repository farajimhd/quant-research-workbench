# Long Momentum v1

Long Momentum v1 is an extended-hours scanner strategy that trades long from
04:00 ET through 20:00 ET. It uses completed 1-minute bars as the actionable
event stream.

## Scanner

At each completed bar the strategy filters the current cross-section:

- `close` between `min_price` and `max_price`, default 1 to 10
- `volume >= min_volume`, default 10,000
- `transactions >= min_transactions`, default 100
- `is_red == false`
- current close is above the previous completed candle close
- `tema9 > tema20`
- `macd_line > 0`
- `macd_hist_z_since_open >= min_macd_hist_z_since_open`, default 0.1
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

If a different candidate appears while a position is open, the strategy compares
the candidate's one-bar return with the open position's total unrealized return.
When the new one-bar return is stronger, the strategy fully rotates: it exits
the current position and enters the new candidate. There is no partial rotation
in v1.

## Stop And Exits

The initial stop is structure based:

- if the entry candle is green, use the midpoint of the entry candle body
- also consider the most recent red-candle low for that ticker
- cap maximum risk with `max_initial_stop_pct`, default 2%

After entry, the active stop is evaluated on completed 1-minute closes. The
stop that existed before the just-completed bar is the only stop that can
trigger on that bar. If a new red candle creates a higher structural stop, that
stop becomes active from the next bar forward.

Additional exits:

- `TEMA_CLOSE`: `tema9 < tema20 + offset`
- `STRUCTURE_STOP`: close breaks the current raised structural stop
- `VELOCITY_TAKE_PROFIT`: unusually large fast green move after profit
- `GREEN_BODY_CONTRACTION`: consecutive green candle bodies shrink after profit
- `SMALL_RED_TOP`: small red candle appears near the best price after profit
- `EOD`: flatten at the end of the available extended-hours session

## Data Requirements

- Event bars: `1m`
- Event feature groups: `core`, `momentum`, `session`, `volume_liquidity`
- No daily context dependency

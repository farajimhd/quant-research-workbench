# Long Momentum v2

Long Momentum v2 is an extended-hours long scanner that evaluates each 1-minute
strategy-time row from 04:00 ET through 20:00 ET. It uses `current_open` as the
actionable bar open and all `last_*` columns as the previous completed bar.

## Scanner

At each bar open, v2 filters the cross-section with only these gates:

- `last_close` between `min_price` and `max_price`, default 1 to 10
- `current_open_above_last_body_high == true`
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

Eligible rows are ranked by `last_recent_volume_5` descending.

## Entry

The strategy walks ranked candidates from top to bottom and submits buy limits
at `current_open` until available cash is exhausted. The requested quantity is
the prior bar `last_quote_ask_size`, capped by available cash when the quote
size is larger than the account can buy.

The initial stop is the previous completed bar body floor:
`min(last_open, last_close)`. If that floor is not below the entry price, v2
falls back to one cent below the entry. After the position has seen at least
`trailing_activation_r_multiple`, default 0.1R, the stop trails up to newer
completed body floors. The distance from entry is recorded in strategy metadata
for the trailing stop. Entry orders carry an attached same-bar protective stop,
so if the execution candle trades through that stop after entry, the engine
closes the actually filled quantity.

If an entry or exit partially fills, v2 handles the residual first on the next
bar open with a same-bar limit at `current_open`, then it continues scanning the
same bar. Entry residuals are not refilled when TEMA is already closed at that
bar open.

## Exit

The strategy has no profit-taking, no rotation, and no extra momentum exits.
Open positions are exited only by:

- the one-cent protective stop
- `TEMA_CLOSE`, when `last_tema9 < last_tema20 + offset`
- `EOD`, at the end of the extended-hours session

## Data Requirements

- Event bars: `1m`
- Event feature groups: `core`, `momentum`, `session`, `volume_liquidity`
- No daily context dependency

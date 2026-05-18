# Long Momentum v7

Long Momentum v7 is a normal, live-safe strategy. It does not read future
returns, future prices, oracle labels, `best_exit_*` values, or provider
supervision artifacts during backtests.

May 1, 2024 was used only as an offline learning session. The comparison across
normal Long Momentum versions showed the v3 rule family was the most stable on
that day, so v7 preserves the v3 live-safe entry and exit structure and makes it
available as the post-oracle normal strategy version.

## Runtime Data Contract

v7 uses only:

- `bars` on `1m`
- `features_core`
- `features_momentum`
- `features_session`
- `features_volume_liquidity`

Its `DataRequirements.supervision_groups` is empty.

## Entry

v7 enters when the current open breaks above the prior completed bar high and
the completed prior bar passes live-safe filters:

- price between `min_price` and `max_price`
- minimum volume and transactions
- spread and quote quality are acceptable
- TEMA is open
- MACD line is positive
- MACD histogram z-score is strong enough
- close location is high in the candle
- recent dollar volume is sufficient

Rows are ranked by recent five-bar volume and recent dollar volume. Only
`max_entries_per_bar` entries can be submitted per bar.

## Exit

v7 uses the v3 live-safe exit model:

- initial stop from recent candle structure
- profit lock after the configured R and percent activation
- TEMA-close exit
- end-of-day flatten

This keeps v7 separated from v6. v6 is the explicit oracle benchmark; v7 is the
normal strategy you can backtest without lookahead columns.

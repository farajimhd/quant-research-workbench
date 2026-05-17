# Long Momentum

Long Momentum is an extended-hours long-only strategy family. It searches for
liquid upward momentum from 04:00 ET through 20:00 ET using provider-built
strategy-time rows: `current_open` is the actionable bar open and `last_*`
columns are the previous completed bar.

Version details live in each version folder:

- `v1`: adaptive scanner with rotation and profit-protection exits.
- `v2`: quote-liquidity scanner, five-bar volume ranking, ask-size entries,
  one-cent stop, and TEMA-close trend exit.

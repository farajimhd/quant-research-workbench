# Long Momentum

Long Momentum is an extended-hours long-only strategy family. It searches for
liquid upward momentum from 04:00 ET through 20:00 ET using provider-built
strategy-time rows: `current_open` is the actionable bar open and `last_*`
columns are the previous completed bar.

Version details live in each version folder:

- `v1`: adaptive scanner with rotation and profit-protection exits.
- `v2`: quote-liquidity scanner, five-bar volume ranking, ask-size entries,
  one-cent stop, and TEMA-close trend exit.
- `v3`: v2-derived tighter momentum selection with a $5 price floor, one new
  entry per bar, and profit-lock exits.
- `v4`: two-trigger version with earlier body-break and Pullback/Reclaim
  entries plus bearish volume-divergence exits.
- `v5`: early-uptrend lifecycle version that removes loose Pullback/Reclaim,
  requires VWAP/TEMA/MACD/volume/early-move agreement, and holds with
  structural stops until divergence or trend structure fails.
- `v6`: oracle-supervised benchmark version for supervision validation.
- `v7`: live-safe May 1 learned version based on the v3 rule family.
- `v8`: news-shock continuation version using provider shock features.
- `v9`: day momentum watchlist version. A completed-bar 5-minute return adds a
  ticker to the strategy watchlist, strict 1-minute transaction impulse rules
  trigger First Entry, simultaneous First Entries split cash, no-cash First
  Entries rotate out existing positions, lower-priority VWAP/two-bar
  body-break reentries remain available after exits, and in-position pocketing
  captures configured gains before immediately rebuying.

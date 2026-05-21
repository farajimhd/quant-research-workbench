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
- `v9`: day momentum watchlist version. A completed-bar 5-minute return,
  volume, and transactions add a ticker to the strategy watchlist. The
  high-priority High Break Hold entry waits for a day-high break to hold for
  later bars before entering, while the lower-priority VWAP Reclaim entry can
  enter whenever the watchlist ticker reclaims VWAP and breaks the last two
  bodies. High Break Hold candidates can rotate out lower-priority positions,
  and in-position pocketing captures configured or adaptive gains.
- `v10`: v9-derived longer High Break Hold version with switchable High Break
  Hold and VWAP Reclaim entry methods.
- `v11`: price-pop continuation version. A ticker enters the pop watchlist only
  after a 5-minute return and transaction shock versus the three pre-pop bars;
  entry uses a buy stop above the pop high, VWAP-based risk, and VWAP
  slope/distance exits.

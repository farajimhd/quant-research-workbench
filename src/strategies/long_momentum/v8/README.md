# Long Momentum v8

Long Momentum v8 is a live-safe news-shock continuation strategy. It is built
for the premarket pattern where a news bar creates a price/volume shock, spreads
and liquidity improve over the next few minutes, and the stock then starts an
uptrend.

v8 does not use oracle labels, future returns, best-exit labels, or supervision
artifacts. It uses current/prior bars and provider-built feature groups only.

## Runtime Data Contract

v8 requires:

- `bars` on `1m`
- `features_core`
- `features_momentum`
- `features_session`
- `features_volume_liquidity`
- `features_shock`

Its `DataRequirements.supervision_groups` is empty.

## Setup: News Shock Watch

A completed bar can start a watch when:

- price shock or combined price-volume shock score is high enough
- the shock candle closes in the upper part of its range
- the candle is bullish or provider marks it as a price shock
- by default, the shock happens near `:00` or `:30`

The setup does not enter immediately. It creates a temporary watch with the
shock high, low, body high, midpoint, and best shock scores.

## Confirmation

The following bars must show that the name became tradable:

- provider confirmation that volume arrived after the shock
- retained volume and combined shock scores
- volume is above average volume so far
- volume is not collapsing versus the recent three-bar average
- spread, spread bps, quote validity, and locked/crossed quote checks pass

## Entry

v8 enters only after the watch has aged by at least the configured delay, then:

- price holds above VWAP
- price holds above the shock midpoint acceptance level
- TEMA and MACD remain constructive
- bearish volume divergence is below the entry warning level
- current open reclaims the shock body or breaks the post-shock structure
- initial structural risk is not too wide
- the combined v8 score clears `min_entry_score`

By default entries are premarket-only, from 04:00 through 09:29 ET. v8 also
limits each symbol to one entry per day and caps total daily entries so a single
news-shock watch cannot become repeated churn after the first exit.

The entry reason is `LONG_MOMENTUM_V8` and the trigger label is
`NEWS_SHOCK_LIQUIDITY_RECLAIM`.

## Stop And Exit

The initial stop is structural and uses the best available support from:

- post-shock low
- shock midpoint
- last three-candle low
- VWAP with buffer

The inherited v3 exit model then manages the position with profit lock,
TEMA-close exit, initial stop, and end-of-day flattening.

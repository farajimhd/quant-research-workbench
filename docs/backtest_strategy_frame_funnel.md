# Backtest strategy-frame computational funnel

Backtest preparation groups required strategy timeframes by ticker. For each ticker, QMD History
reads the causal canonical event window once and advances the `100ms`, `1s`, `5s`, `10s`, and
`30s` bar/indicator states together. Tickers are processed concurrently under the configured
gateway build budget; timeframes for one ticker are not separate replay jobs.

## Admission before intraday work

Strategies that declare `momentum_price_policy.prior_close_maximum` resolve prior-session closes
through one batch QMD request before building intraday frames. The authority is the completed QMD
daily-session-bar population as of session start. Symbols at or above the configured maximum are
excluded from frame preparation and market-event playback. An unavailable prior close is not
silently accepted: it remains in the causal strategy path, where entry fails closed.

The resolved batch also seeds the backtest LULD prior-close cache, avoiding a later scalar QMD
request for each ticker.

## Projection and persistence

Strategy 349/350 requests only its bar identity, MACD, VWAP, prior-close, ATR, and LULD columns.
Level Book V7 remains the separate structural authority. QMD emits bounded frame batches, and the
backend persists large batches into the run-owned SQLite spool. Completion markers for every
timeframe in a ticker bundle are committed together; an interruption deletes and rebuilds the
whole incomplete bundle, so partial streams are never advertised as reusable.

Prepared-cache reuse attaches a donor database once and copies all compatible completed streams
in one transaction. Cache identity still pins the full request window, source revision, projection,
and calculation contract. Exact event-time ordering across tickers is restored by the SQLite index
before strategy execution.

## Causality boundary

The optimization fuses bar and indicator preparation, not strategy decisions. Stateful decisions,
Portfolio, OMS, and broker simulation still consume the globally ordered causal tape. They are not
vectorized across time because doing so would change position state, order timing, or equal-time
ordering semantics.

## Operational evidence

Preparation progress counts logical `(ticker, timeframe)` streams even though one ticker bundle is
one physical QMD build. Authority records identify `qmd_history_derived_bundle`, list all bundled
timeframes and per-timeframe frame counts, and assert `single_event_traversal=true`.

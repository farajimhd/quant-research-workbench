# Multi-resolution causal decision channels

`multi_resolution_decisions` aligns the v4 native candle channels to one declared
native decision interval. Supply ordered `(resolution_ms, maximum_age_ms)` pairs;
each output row contains one typed channel struct per declared resolution.
Completed source boundaries use milliseconds since New York source midnight.
Forming, missing, stale, and foreign-attempt candles cannot supply channels.
Absolute OHLC, volume, notional and trade counts remain alongside relative
returns, prior-only relative participation and volatility-normalized shapes.

Alignment uses Polars cross/as-of joins and grouped expressions, bounded to two
million expanded decisions. Timeframe expressions iterate over the small declared
schema, never over market rows. Cash, liquidity, fills and OCA ordering remain in
the sequential native execution path. This research projection neither publishes
a feature product nor certifies a strategy. No news, splits or fundamentals are
included until their point-in-time source authority is established. Full-session
financial throughput and profitability remain unverified.

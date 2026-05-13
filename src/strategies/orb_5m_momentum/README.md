# ORB 5-Minute Momentum Strategy

This strategy family studies opening-range momentum. It looks for liquid tickers with strong early-session price and volume behavior, builds an opening range, and then evaluates whether later bars confirm continuation strongly enough to enter a long trade.

The root folder describes the shared idea only. Exact rules, indicators, data requirements, default config, and behavior live under version folders so each backtest can be tied to the strategy definition that produced it.

## Versions

- `v1`: First provider-backed ORB momentum implementation.

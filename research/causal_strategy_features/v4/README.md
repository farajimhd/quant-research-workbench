# Native causal feature research v4

Extends v3's completed native-bar projection with candle body/wicks, volatility
units, relative trade count and source-day timing. Absolute prices, volume,
source identity, validity and completed-end availability remain present.
Volatility uses only the preceding declared number of close returns; the
current return is excluded. Gaps, invalid prior returns and zero volatility
produce missing normalized values. Baselines cannot cross source identities.

All calculations use Polars expressions. Source resolutions and strategy
decision interval are separate parameters: a faster decision interval must
reuse the latest completed features, never the unfinished bar. Timing is local
source-day timing, not elapsed Backtest time or an assumed session VWAP.

Callers must certify source products using the v3 reader before projecting.
This research module does not publish a feature product, install a strategy,
or establish financial results or full-session speed acceptance. Split and
fundamental availability remain separate requirements; neither is inferred.
Generated outputs belong under the operational runtime root.

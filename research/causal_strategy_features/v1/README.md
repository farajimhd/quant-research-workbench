# Causal strategy feature research v1

Pure Polars construction of completed multi-resolution research channels from
canonical-derived one-minute observations. This is not a certified producer
product or a strategy admission capability. Each call contains one session
and a complete ticker partition; never mix sessions or split a ticker's history
between calls. The caller retains source build/attempt identity.

`completed_channels` accepts explicit timeframe and trailing-lookback parameters.
It excludes current volume from trailing baselines, requires every constituent
minute of a higher timeframe, and exposes the completed bar availability clock.
Price, volume and trade channels retain absolute context alongside ratios.
Quote-only/incomplete minutes do not become candles. Future label columns are
excluded by projection. Missing prior bars yield null ratios.

News is excluded. Opens, split/reverse-split events, fundamentals, native AH VWAP
and sub-minute products are not present in this input contract; do not infer
them. Their future channels require producer identity, effective/available
timestamps and coverage. AH strategy decisions still require prior-day V7 and
all regular-session warming. Runtime decisions require a separately sealed
strategy interval and certified products.

Artifacts and benchmarks belong under `D:\TradingML\runtimes`. Feature benchmarks
do not establish full-session Backtest speed. Cash, liquidity, fills, protection
and OCA remain in their existing sequential coordinator.

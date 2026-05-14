# Break of VWAP v1

Break of VWAP v1 is a low-turnover long strategy for liquid stocks that reclaim
VWAP from below on a completed 1-minute bar.

## Data Requirements

- Event bars: `1m`
- Event feature groups: `core`, `momentum`, `session`, `volume_liquidity`,
  `price_action`, `shock`, `market_structure`
- Daily context: none

The strategy consumes provider-built bars and features. It does not calculate
bars, VWAP, MACD, TEMA, or liquidity features inside the backtest.

## Scanner

Every completed 1-minute bar, the scanner checks only fresh rows. A row can be
eligible when:

- price is between `$3` and `$80`
- 20-bar average dollar volume is at least `$500k`
- relative dollar volume is at least `1.1`
- the prior completed bar was below VWAP
- the current completed bar closes at least `3 bps` above VWAP
- the VWAP break is not too extended above VWAP
- the current bar has a strong close location and positive return
- MACD pressure is improving
- TEMA spread is not deteriorating materially
- day and short-term returns are controlled
- scanner score and estimated gross edge exceed their thresholds

The strategy excludes common leveraged, inverse, commodity, crypto, sector, and
index ETF symbols by default because it is meant for individual stocks.

## Entries

Eligible rows are ranked by `scanner_score`. The strategy opens at most one new
position per bar, holds at most two positions, and opens at most four positions
per day by default.

## Exits

Each entry receives an initial risk unit `R`. The initial stop is the tighter
reasonable level between a volatility-style price stop and a VWAP-failure stop
below VWAP. The strategy exits on:

- stop/trailing stop cross
- VWAP failure after the minimum hold period
- weak reversal failure after the minimum hold period
- max hold time
- end of day

Stops are submitted as same-bar stop exits when the completed bar shows the
stop was crossed.

## Observability

The strategy records scanner rows, entry intents, skips, exits, and state
snapshots. Important scanner fields:

- `scanner_score`
- `estimated_edge_bps`
- `prior_vwap_bps`
- `vwap_bps`
- `vwap_break_bps`
- `macd_hist_delta_bps`
- `tema_spread_delta_bps`
- `dollar_volume_sma20`
- `relative_dollar_volume20`

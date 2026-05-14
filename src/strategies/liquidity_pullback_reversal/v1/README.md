# Liquidity Pullback Reversal v1

Liquidity Pullback Reversal v1 is a low-turnover long strategy derived from the
1-minute discovery pass. The discovery did not show enough net edge in simple
breakout momentum states after realistic fees. The stronger pattern was a
controlled liquid pullback that starts to recover.

## Data Requirements

- Event bars: `1m`
- Event feature groups: `core`, `momentum`, `session`, `volume_liquidity`,
  `price_action`, `shock`, `market_structure`
- Daily context: none

The strategy expects provider-built bars and features. It does not calculate
bars or indicators inside the backtest.

## Scanner

Every completed 1-minute bar, the strategy scores fresh ticker rows only. A row
must pass these default gates before entry:

- price between `$3` and `$80`
- 20-bar average dollar volume at least `$500k`
- relative dollar volume at least `1.0`
- regular-session window from 10:30 to 15:30 ET
- common leveraged, inverse, commodity, crypto, sector, and index ETF symbols
  are excluded by default because the strategy is meant for individual stocks
- price no more than `25 bps` above VWAP and no more than `250 bps` below VWAP
- day return between `-500 bps` and `300 bps`
- recent 15-minute return between `-350 bps` and `100 bps`
- TEMA and MACD are still weak or neutral, not already extended
- the current 1-minute bar is a reversal bar with a strong close location
- MACD pressure is improving
- scanner score and estimated gross edge exceed their thresholds

The scanner score favors liquidity, a controlled VWAP pullback, a strong
reversal candle, improving MACD pressure, and controlled intraday damage.

## Entries

The strategy ranks eligible rows by scanner score. It opens at most one new
position per bar, holds at most two positions, and limits default daily entries
to three. Each symbol also has a cooldown after an exit or rejected entry to
reduce churn.

Sizing is capped by cash, notional exposure, and risk per trade. Small notional
positions are rejected because fees can dominate them.

## Exits

Each position receives an initial `R` risk unit and a trailing stop. The
strategy exits when:

- the current bar trades through the active trailing stop
- the reversal fails after the minimum hold period
- the position reaches the maximum hold period
- the backtest reaches end of day

Stops are submitted as same-bar stop exits when the completed bar shows the stop
was crossed. This is more faithful to an active stop than waiting for the close.

## Observability

The strategy records scanner rows, entry intents, skipped candidates, exit
intents, and compact strategy state snapshots. Important scanner columns:

- `scanner_score`
- `estimated_edge_bps`
- `vwap_bps`
- `day_return_bps`
- `ret15_bps`
- `macd_hist_bps`
- `macd_hist_delta_bps`
- `tema_spread_bps`
- `tema_spread_delta_bps`
- `dollar_volume_sma20`
- `relative_dollar_volume20`

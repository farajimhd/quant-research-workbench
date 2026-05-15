# ORB 5-Minute Momentum Strategy

This strategy family studies opening-range momentum. It looks for liquid tickers with strong early-session price and volume behavior, builds an opening range, and then evaluates whether later bars confirm continuation strongly enough to enter a long trade.

The root folder describes the shared idea only. Exact rules, indicators, data requirements, default config, and behavior live under version folders so each backtest can be tied to the strategy definition that produced it.

## Versions

- `v1`: Baseline provider-backed ORB momentum implementation with daily context,
  opening-range setup scoring, 5-minute momentum confirmation, and multi-position
  portfolio rotation.
- `v2`: Removes the 60-day daily setup dependency from v1, keeps same-session
  opening-range logic, trades on completed `1m` closes, and uses 5-minute
  momentum confirmation.
- `v3`: Simplifies the scanner toward liquidity and opening-box strength, then
  keeps the v2-style entry, exit, and stop behavior.
- `v4`: Rebuilds the family around the saved QuantConnect ORB implementation:
  one active position, QuantConnect-style universe filters, opening relative
  volume ranking, daily ATR/volume context, and ORB stop/flatten exits.
- `v5`: Starts from v4 and adds profit-pocket behavior: once a position is open,
  a configured favorable move exits the trade and schedules a same-size reentry
  attempt on the next bar.
- `v6`: Starts from v5 and adds reentry confirmation: after pocketing profit,
  the next bar must have `1m` TEMA9 above the TEMA9 value at the profit-pocket
  exit, otherwise the reentry is abandoned.
- `v7`: Starts from v6 and adds a general green-candle entry rule: initial
  entries and profit reentries are not allowed on red completed `1m` candles,
  and scanner candidates that turn red during entry evaluation are removed from
  consideration from that point forward.
- `v8`: Starts from v7 and removes the immediate profit reentry. When a
  position reaches the pocketing threshold, it exits with `POCKETING` and lets
  the scanner choose the next opportunity.

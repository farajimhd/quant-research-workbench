# Long Momentum v10

Long Momentum v10 starts from v9 and keeps the same provider-built features,
day momentum watchlist, High Break Hold entry, sizing, execution assumptions,
and VWAP Reclaim path.

The difference is High Break Hold exits. V10 is designed to reduce churn and
hold strong day-high breaks longer:

- enter High Break Hold exactly like v9 after the day-high break is detected
  and the configured hold confirmation passes
- while the High Break Hold position is open, do not use the v9 TEMA, 2xBVD,
  body-cycle, or pocket exits for that position
- trail the High Break Hold stop to the maximum VWAP seen so far that day
- exit when price touches back to that day max VWAP stop
- exit for profit when the current open is greater than
  `entry_price * (1 + high_break_take_profit_pct)`
- after a High Break Hold exit, do not re-enter from the old High Break watch;
  the ticker must break the day high again and pass confirmation again

The default `high_break_take_profit_pct` is `0.15`, meaning `+15%`.

VWAP Reclaim remains the v9 implementation for now.

# ORB 5-Minute Momentum Strategy v3

Version 3 simplifies the setup scanner, but keeps the v2 entry, exit, stop, sizing, cancellation, and rotation rules.

## Data Requirements

- `1m` bars for each requested session
- `1m` feature groups: `core`, `session`
- `5m` context feature groups: `momentum`

It does not request prior daily context.

## Behavior

The strategy builds the opening box from the 09:30-09:35 one-minute bars. The scanner only filters for tradability using price, opening share volume, opening dollar volume, and a valid positive opening box range.

Passing tickers are ranked by the completed `5m` MACD pressure available at the setup scan:

```text
macd_pressure_bps = sum((macd_line - macd_signal) / close * 10000)
```

After the opening range is complete, live decisions wait for completed `1m` closes. The first possible entry/skip action is 09:36. From that point onward, entry/exit/stop behavior is inherited from v2:

- Entry requires the completed `1m` close to break the buffered opening-range high.
- Entry also requires `5m` MACD to be open and `5m` TEMA9 to be above TEMA20 plus the configured buffer.
- The initial stop uses the same opening-box pullback formula as v2.
- Normal exits use the same v2 breakout-failure, TEMA close, rotation, and end-of-day flatten rules.

# ORB 5-Minute Momentum Strategy v3

Version 3 simplifies the setup scanner, but keeps the v2 entry, exit, stop, sizing, cancellation, and rotation rules.

## Data Requirements

- `1m` bars for each requested session
- `1m` feature groups: `core`, `session`, `momentum`
- `5m` context feature groups: `momentum`

It does not request prior daily context.

## Behavior

The strategy builds the opening box from the 09:30-09:35 one-minute bars. The scanner only filters for tradability using price, opening share volume, opening dollar volume, and a valid positive opening box range.

The opening setup rank uses completed `1m` MACD pressure available at the setup scan:

```text
macd_pressure_bps = sum((macd_line - macd_signal) / close * 10000)
```

During the session, the live scanner is recalculated every minute from completed `1m` bars:

```text
cumulative_macd_pressure_bps = sum(1m macd_hist / close * 10000 from session start through the current bar)
price_trend_bps = (current_close / opening_box_close - 1) * 10000
trend_score = trend_macd_weight * cumulative_macd_pressure_bps + trend_price_weight * price_trend_bps
```

The live rank uses this dynamic `trend_score` as the scanner base, so a candidate can move up or down as the morning trend improves or fades.

After the opening range is complete, live decisions wait for completed `1m` closes. The first possible entry/skip action is 09:36. From that point onward, entry/exit/stop behavior is inherited from v2:

- Entry requires the completed `1m` close to break the buffered opening-range high.
- Entry also requires `5m` MACD to be open and `5m` TEMA9 to be above TEMA20 plus the configured buffer.
- The initial stop uses the same opening-box pullback formula as v2.
- Normal exits use the same v2 breakout-failure, TEMA close, rotation, and end-of-day flatten rules.

# ORB 5-Minute Momentum Strategy v3

Version 3 simplifies the setup scanner and removes MACD/TEMA confirmation from the trading decision.

## Data Requirements

- `1m` bars for each requested session
- `1m` feature groups: `core`, `session`

It does not request same-session `5m` momentum indicators or prior daily context.

## Behavior

The strategy builds the opening box from the 09:30-09:35 one-minute bars. The scanner only filters for tradability using price, opening share volume, and opening dollar volume.

Passing tickers are ranked by opening-box strength:

```text
box_strength = (box_high - box_low) / box_low
```

After the opening range is complete, live decisions wait for completed `1m` closes. The first possible entry/skip action is 09:36. A ticker becomes eligible when the completed `1m` close breaks above the buffered opening range high:

```text
close > box_high * (1 + entry_buffer_pct)
```

Entries use market orders on the completed `1m` close. The initial stop is the opening-box midpoint. Normal exits are price-only: breakout failure at the stop, trailing giveback after the configured hold time and R-multiple activation, and end-of-day flattening.

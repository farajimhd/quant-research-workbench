# ORB 5-Minute Momentum Strategy v2

This version removes the prior-daily-context dependency from v1. It only requires provider-built `1m` bars for the requested backtest sessions and same-session `5m` momentum indicators.

## Data Requirements

- `1m` bars for each requested session
- `1m` feature groups: `core`, `session`
- `5m` context feature groups: `momentum`

It does not request 60 calendar days of prior `1d` bars and does not request daily feature groups.

## Behavior

The strategy builds the opening box from the 09:30-09:35 one-minute bars, ranks candidates using same-session price, range, and opening-volume quality, then evaluates live continuation on each later `1m` close.

Entries and normal exits are submitted as market orders on the current `1m` bar close. The `5m` MACD and TEMA indicators are used only as confirmation columns joined into the `1m` event stream.

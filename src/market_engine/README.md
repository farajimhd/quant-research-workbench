# Event-Based Market Engine

This package defines the shared contracts for the new quote/trade regime.

The active system should adapt all sources into canonical quote/trade events:

- Massive live websocket events
- historical ClickHouse quote/trade rows
- persisted live-session replay rows

Derived components then consume the same contracts:

- bars and chart candles
- scanner snapshots and presets
- live trading market state
- event replay
- event-based backtests
- simulated broker fills and portfolio state

The legacy one-minute-bar backtest and data-builder code remains outside this
package as archived/reference implementation. New active app features should
depend on this package instead.

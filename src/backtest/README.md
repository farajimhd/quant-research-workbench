# Backtest Architecture

The backtest package is responsible for simulation, not market-data preparation.
All bars, features, indicators, supervision labels, and multi-timeframe context
must be built by the data provider before a backtest starts.

## Data Contract

The backtest engine loads provider-built data only. It should not read raw market
data directly, consolidate bars, calculate indicators, or create fallback feature
columns during a run.

The expected flow is:

1. The strategy declares its data requirements.
2. The engine asks the data provider for the required date range, event
   timeframe, feature groups, and columns.
3. The data provider validates that the requested artifacts already exist.
4. If any required session, timeframe, feature group, or column is missing, the
   backtest fails before simulation starts.
5. The strategy receives provider-backed frames and decides how to filter
   sessions and interpret features.

This preserves a strict boundary: the provider owns bars, features, indicators,
and supervision data; the backtest owns ordering, fills, portfolio state,
metrics, and artifacts.

## Event Model

The standard simulation loop is timestamp driven. The engine should process one
market-clock timestamp at a time, where each timestamp slice contains all ticker
rows with fresh updates at that time.

The session frame should be normalized into event order before iteration:

```python
frame = frame.sort(["bar_time_market", "ticker"])
```

Sorting by ticker first is useful for provider feature construction and
per-symbol windows, but timestamp-first ordering is the natural shape for a
backtest event loop.

## Fresh And Stale Bars

Ticker updates can be sparse. If a ticker has no new bar at the current
timestamp, the engine must not silently treat an older row as a fresh update.

The event context should distinguish:

- `updates`: rows whose bar timestamp equals the current engine timestamp.
- `latest`: the latest known row per ticker at or before the current engine
  timestamp.

Fresh updates are required for order crossing logic because stop and limit fills
depend on the current bar high and low. Latest rows may be used for marking open
positions, display, or ranking only when the strategy explicitly allows stale
data and checks staleness.

Useful freshness fields include:

```text
is_fresh_bar
last_bar_time
stale_minutes
```

## Strategy Execution

Strategies should operate on Polars tables, not per-row Python callbacks. The
engine sends a timestamp slice containing all current ticker updates, and the
strategy uses Polars expressions for filtering, scoring, ranking, and selecting
candidates.

The strategy may return a small number of order requests as Python objects. It
is acceptable to convert only selected candidates or held positions to Python
dicts; avoid iterating every ticker row in Python.

## Backtest Responsibilities

The backtest engine is responsible for:

- validating that required provider data is available before simulation
- iterating timestamp slices in market-clock order
- maintaining pending orders and open positions
- applying fill models
- tracking cash, equity, exposure, and position snapshots
- writing run artifacts and metrics
- exposing progress to jobs and the frontend

The engine should remain strategy agnostic. Strategy-specific setup filters,
entry rules, ranking formulas, exit rules, and session preferences belong in the
strategy package.

## Vectorization Path

The timestamp-slice model is the common execution standard because it preserves
stateful behavior such as orders, fills, stops, position limits, and future
quote/trade execution models.

For faster hyperparameterization and strategy discovery, strategies should also
keep their signal logic expression-oriented. That allows a later discovery mode
to run broader Polars pipelines over full sessions or parameter grids while the
standard simulator continues to provide the safer event-driven confirmation
path.

The intended split is:

- event simulation: accurate portfolio and order state over timestamp slices
- vectorized discovery: fast signal and trade approximation for broad searches

Both modes should consume the same provider-built data and reuse the same
strategy expressions where practical.

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

## Portfolio And Order State

Strategies should have read access to portfolio and order state while the
backtest engine owns all mutations.

The strategy may inspect:

- current positions
- cash, equity, buying power, and exposure when those constraints are enabled
- open entry and exit orders
- recent fills or order status when needed for strategy rules
- available position slots
- per-symbol state such as held, pending, blocked, or recently exited

The strategy should not directly open positions, close positions, mutate cash,
fill orders, or create trades. It observes state and emits order requests; the
engine or broker simulator accepts, rejects, cancels, fills, and records the
result.

## Orders, Fills, And Trades

Orders and trades are execution-layer records, not provider data and not
strategy-owned state.

The standard lifecycle is:

```text
Strategy signal
  -> OrderRequest
  -> Order accepted into the simulated order book
  -> Fill model checks fresh bar, quote, or trade data
  -> Fill event
  -> Portfolio update
  -> Trade record when a position is closed or reduced
```

An order represents intent and execution history. A trade represents realized
position history. A single round-trip trade usually contains an entry fill and
an exit fill, while the underlying orders remain separate audit records.

Example:

```text
BUY STOP CADL 100 @ 3.20
  order created at 09:41
  filled at 09:44

SELL STOP CADL 100 @ 2.95
  order created at 09:44
  filled at 10:03

Trade:
  CADL entry 09:44 @ 3.20
  exit 10:03 @ 2.95
  pnl = realized exit value minus entry cost
```

For the timestamp-slice simulator, order crossing must use fresh updates. If a
symbol has no new row at the current timestamp, the engine cannot claim that the
symbol's high or low crossed a stop or limit at that time. Latest-known rows can
mark positions, but they should not trigger fills unless a later execution model
explicitly supports that behavior.

The order book should be indexed so the engine checks pending orders only for
symbols with fresh updates at the current timestamp. This keeps the event loop
small even when the provider frame contains many symbols.

## Run Artifacts

Backtests should persist execution state as structured artifacts so every signal,
order, fill, position, and realized trade can be audited after the run.

Core artifacts:

- `orders.parquet`: every accepted, filled, canceled, or rejected order
- `fills.parquet`: each execution event produced by the fill model
- `trades.parquet`: realized position close or reduce records
- `positions.parquet`: point-in-time position snapshots
- `portfolio.parquet`: cash, mark-to-market equity, realized P/L, open
  unrealized P/L, gross exposure, peak equity, and drawdown snapshots
- `portfolio_candles.parquet`: mark-to-market P/L and equity OHLC candles plus
  open-unrealized and drawdown OHLC fields for `1m`, `1h`, `2h`, `4h`, and
  `1d`
- `signal_events.parquet`: strategy signal events
- `rejection_events.parquet`: rejected strategy candidates or invalid signals
- `candidate_rankings.parquet`: setup rankings
- `live_rankings.parquet`: timestamp-level live rankings

Run artifacts are written through a single background artifact writer thread.
The simulation loop queues JSON, text, parquet, and P/L candle writes and keeps
processing bars while disk I/O completes. Live chart artifacts are refreshed
during the run at the active P/L candle cadence; the full artifact set is
flushed before the run is marked complete. Job progress and cancellation files
remain synchronous control-plane state so the UI can observe subprocess
progress deterministically.

Long-running jobs can be stopped by writing `cancel.requested` in the job
directory through the backtest cancel endpoint. The worker and engine check that
file between bar events, mark the job `cancelled`, and flush the partial run
artifacts so the visible result can still be reviewed.

Live progress events also publish a summary snapshot with mark-to-market P/L,
return, Sharpe, drawdown, trade counts, win rate, profit factor, and unrealized
P/L. The result page should use that live summary for metric cards during the
run instead of repeating submitted run or strategy parameters.

As execution modeling becomes more realistic, the backtest should also write or
extend:

- `order_events.parquet`: order lifecycle events such as accepted, canceled,
  expired, rejected, partially filled, and filled
- `fills.parquet`: partial fills, quote/trade execution references, spread
  checks, and liquidity diagnostics

Separating orders, fills, and trades is important for Phase 2 quote/trade
execution. Partial fills, spread checks, liquidity limits, and extended-hours
execution rules are much easier to debug when every lifecycle event is recorded
directly instead of being collapsed into a single order row.

## Backtest Responsibilities

The backtest engine is responsible for:

- validating that required provider data is available before simulation
- iterating timestamp slices in market-clock order
- exposing read-only portfolio and order views to strategies
- accepting, rejecting, canceling, and filling orders
- maintaining pending orders and open positions
- applying fill models
- creating trade records from realized position changes
- tracking cash, equity, exposure, position snapshots, and execution artifacts
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

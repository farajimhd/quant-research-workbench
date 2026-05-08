# QQ Momentum Trading

This project is moving to a local-first research workflow. The goal is to develop, inspect, and improve momentum strategies on local historical data before translating anything to QuantConnect or live execution.

## Development Workflow

The strategy development process has four phases.

1. **Phase 1: local minute-bar research**
   - Develop strategies in plain Python.
   - Backtest against local 1-minute historical bars using Polars.
   - Iterate scanner logic, entries, exits, sizing, and risk rules until results are acceptable.
   - Use rich saved outputs and a frontend to understand every trade and missed trade.

2. **Phase 2: realistic quote/trade backtesting**
   - Reuse the same strategy modules where possible.
   - Replace or extend the fill model with quote and trade data.
   - Simulate order matching more realistically using bid/ask, trades, spread, liquidity, and partial fills.
   - Use this phase before live deployment when execution quality matters.

3. **Phase 3: direct live trading through IBKR**
   - Run approved strategies directly using an IBKR client library as the brokerage layer.
   - Use Massive for live market data where needed.
   - Reuse the same scanner, signal, sizing, risk, and portfolio modules from backtesting.
   - Keep brokerage, data feed, and execution adapters modular so live trading is not tied to QuantConnect.

4. **Phase 4: optional QuantConnect translation**
   - Translate only approved local strategies into QuantConnect.
   - Use QuantConnect backtests to validate platform-specific execution behavior.
   - Iterate only on execution mismatches, data differences, and platform constraints.

## Data Sources

Minute-bar historical data is expected at:

```text
D:/TradingData/massive_flatfiles/us_stock_sip/minutes_agg_v1/[yyyy]/[mm]/[yyyy-mm-dd].csv.gz
```

The current available dataset includes May 2024. More months can be added later without changing the strategy code.

Phase 2 will also use quote and trade data when available. The code should treat minute bars, quotes, and trades as different data adapters feeding the same strategy engine.

## Output Storage

Backtest and research outputs should be saved under:

```text
D:/TradingData/qq-momentum-trading/runs/
```

Each run should have its own timestamped folder:

```text
D:/TradingData/qq-momentum-trading/runs/
  2026-05-08_001_opening_range_momentum/
    config.json
    summary.json
    daily_summary.parquet
    orders.parquet
    trades.parquet
    positions.parquet
    portfolio.parquet
    scanner_snapshots.parquet
    candidate_rankings.parquet
    signal_events.parquet
    rejection_events.parquet
    logs.txt
```

Every run must be reproducible from its saved `config.json`.

## Code Organization

The intended source layout is:

```text
src/
  backtest/
    data/
      minute_bars.py
      quote_trade.py
      adapters.py
    indicators.py
    engine.py
    fills.py
    portfolio.py
    results.py
    plotting.py
  strategies/
    opening_range_momentum/
      config.py
      scanner.py
      signals.py
      sizing.py
      exits.py
      strategy.py
  live/
    ibkr_adapter.py
    massive_adapter.py
    execution.py
  frontend/
    streamlit_app.py
```

Large strategy deviations can live in separate folders under `src/strategies`.

## Backtest Design

The backtest engine should be parameterized and strategy-agnostic.

At day-load time:

- load the 1-minute Polars dataframe for the day
- build a second 5-minute dataframe by consolidating every five 1-minute bars
- calculate indicators on both 1-minute and 5-minute data using Polars expressions
- avoid recalculating indicators inside the event loop

The engine should simulate the trading day over time and record:

- scanner state
- candidate rankings
- signals
- rejected signals and reasons
- orders
- fills
- positions
- cash, equity, realized PnL, unrealized PnL
- daily and run-level summaries

The Phase 1 fill model can approximate fills from bars. Phase 2 should swap in a quote/trade fill model without changing strategy rules.

## Strategy Configuration

Every strategy must be easy to hyperparameterize. Parameters should be supplied through a config object or dictionary, not hidden globals.

Typical parameters include:

- date range
- initial cash
- max active positions
- scanner thresholds
- opening box window
- indicator periods
- entry rules
- exit rules
- position sizing rules
- risk rules
- slippage and spread assumptions
- data paths
- output paths

## Frontend

The frontend can be a Streamlit app.

Run it from the repository root with:

```powershell
streamlit run src/frontend/streamlit_app.py
```

Install the local Phase 1 dependencies first if needed:

```powershell
pip install -r requirements.txt
```

Sidebar:

- strategy selector
- backtest run selector, newest first
- run/new-run controls

Run panel:

- editable strategy parameters
- editable backtest parameters
- run button
- live progress while the run is executing

Results view:

- summary metrics
- equity curve
- daily PnL
- orders
- trades
- positions
- scanner snapshots
- candidate rankings
- rejected signals

Per-day inspection:

- one tab or container per trading day
- timestamp navigation
- scanner status at selected time
- active positions at selected time
- ranked candidates at selected time
- selected ticker candlestick chart

Chart requirements:

- 1-minute candlesticks
- optional 5-minute indicators
- opening box high, mid, and low
- VWAP and strategy indicators
- entry markers
- profit exits
- stop exits
- other relevant signal markers

## Core Principle

The local system should be a research and debugging environment, not only a PnL calculator. Every trade, missed trade, rejected candidate, and exit should be explainable from saved structured data.

The same strategy logic should be reusable across:

- minute-bar backtesting
- quote/trade backtesting
- IBKR live execution
- QuantConnect translation

## Current Phase 1 Implementation

The first complete local strategy is `orb_5m_momentum`.

It lives under:

```text
src/strategies/orb_5m_momentum/
```

The reusable backtest modules live under:

```text
src/backtest/
```

Current capabilities:

- loads Massive/Polygon 1-minute bars from local gzipped CSV files
- filters to regular market hours
- consolidates completed 5-minute bars
- precomputes VWAP, MACD, TEMA 9, and TEMA 20 with Polars
- builds a 09:30-09:35 opening box
- ranks the top opening candidates
- reranks live candidates every minute
- simulates stop, limit, and market fills from OHLC bars
- tracks cash, equity, positions, orders, trades, scanner snapshots, candidate rankings, signals, and rejections
- saves every run under `D:/TradingData/qq-momentum-trading/runs/`
- exposes completed runs through the Streamlit frontend

The frontend now treats the sidebar as strategy navigation only. Each strategy opens a main-page workspace with:

- app-created run history sorted by recency
- required run names
- named run folders with `metadata.json`
- new-run form grouped by dataset, portfolio, fill model, scanner, entry, exit, and risk parameters
- a resolved session preview before launch so the selected start/end dates show exactly which local files will run
- live daily progress while a run is executing
- the same run-detail dashboard while a run is executing, refreshed from partial artifacts after each completed session
- run detail pages with overview, daily results, trades, orders, scanner candidates, rejected signals, positions, chart inspector, config, and logs
- a parameters dialog for viewing the saved config and launching a copied run with edits
- cached Polars artifact loading so selected-day/range filtering and chart pulls avoid repeated disk reads
- scanner debugging for both ranking systems: the opening setup ranking and the minute-by-minute live ranking snapshots

Runs without app metadata are not listed by the frontend. Local runs created by the app or engine include:

```text
metadata.json
config.json
summary.json
daily_summary.parquet
orders.parquet
trades.parquet
positions.parquet
portfolio.parquet
scanner_snapshots.parquet
candidate_rankings.parquet
live_rankings.parquet
signal_events.parquet
rejection_events.parquet
logs.txt
```

The summary file follows the QuantConnect-style structure where supported:

- `tradeStatistics`
- `portfolioStatistics`
- `runtimeStatistics`
- flat dashboard fields such as return, PnL, win rate, profit factor, drawdown, Sharpe, Sortino, and turnover

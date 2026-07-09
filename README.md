# Quant Research Workbench

This project is moving to a local-first research workflow. The goal is to develop, inspect, and improve momentum strategies on local historical data before translating anything to QuantConnect or live execution.

## Coding And Training Workflow Rules

The laptop repo is the source of truth:

```text
D:/TradingCodes/quant-research-workbench
```

The workstation is for heavier training and long-running jobs through the shared
drive:

```text
//DESKTOP-SAAI85T/Workstation-D
```

Make code changes in the laptop repo first. Validate locally when practical,
commit, push, and then sync the required runtime code to the correct workstation
runtime root. Do not treat workstation runtime folders as the primary source of
truth.

Research/model versions belong under `research/<model_family>/vN/`. A
training-capable version should normally include `config.py`, `model.py`,
`data.py`, `train.py`, `run_train.py`, job-specific launchers, useful notebooks,
and a `README.md` describing purpose, defaults, output roots, and assumptions.

Shared research utilities belong in `research/mlops/`. Keep only stable
cross-version infrastructure there, such as environment loading, secret
redaction, W&B setup, checkpoint helpers, path conventions, manifests, metrics,
ClickHouse helpers, seed/device helpers, and shared data-provider helpers.
Operational workflows belong under `pipelines/`, not `research/mlops/`.

Workstation sync is subsystem-specific. If a task changes
`research/<model_family>/vN/`, sync that model version and its required shared
utilities to the corresponding model runtime. If a task changes `pipelines/...`,
sync it to a pipeline runtime/code root, not inside a model-version folder. If a
task changes shared `research/mlops` utilities, sync them only to the runtimes
that depend on those utilities. Docs-only changes usually do not need
workstation sync unless the document is needed for an active workstation run.

Runnable jobs should prefer Python launchers over PowerShell-only workflows. A
launcher should be runnable with `python run_train.py`, show its effective CLI
command, expose simple overrides, resolve paths correctly from either the laptop
repo or workstation runtime copy, and keep progress output clear.

Each training or pipeline run should write logs, metrics, checkpoints, W&B
files, config snapshots, artifacts, and run manifests under one run directory.
Run manifests should include enough information to reproduce the run: model
family/version, job type, run name, git commit, command args, resolved data and
output roots, checkpoint paths, W&B project/run IDs when applicable, and secret
presence status only.

Never copy `.env` files or secret values into runtime folders, notebooks,
checkpoints, logs, manifests, or W&B configs. Load secrets from environment
variables or env-file discovery and redact values for keys such as `*_KEY`,
`*_TOKEN`, `*_SECRET`, and `*_PASSWORD`.

Before finishing code changes, review the modified files, run compile/smoke
checks when possible, sync workstation runtime code to the right subsystem root
when the change affects workstation execution, stage only relevant files, commit
with a meaningful conventional-style message, and push to the configured remote
branch. Do not commit temporary files, caches, logs, secrets, or unrelated dirty
files.

If a task starts a server or long-running helper, stop it gracefully before
finishing. For current operational state and runbooks, start with
`docs/current_state.md` and `docs/README.md`.

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
D:/TradingData/quant-research-workbench/runs/
```

Each run should have its own timestamped folder:

```text
D:/TradingData/quant-research-workbench/runs/
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
  backend/
    app.py
  frontend/
    React/Vite operator UI
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

The frontend is a React/Vite operator UI served by the FastAPI backend. Streamlit has been removed so the UI can use the same design stance and component model as the larger trading dashboard.

Install Python dependencies:

```powershell
pip install -r requirements.txt
```

Install frontend dependencies:

```powershell
npm --prefix frontend install
```

Run the backend API:

```powershell
uvicorn src.backend.app:app --reload --reload-dir src
```

Run the React development server:

```powershell
npm --prefix frontend run dev
```

For a production-style local build:

```powershell
npm --prefix frontend run build
uvicorn src.backend.app:app
```

Sidebar:

- strategy selector
- Market Data Build
- Market Data Review

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
- saves every run under `D:/TradingData/quant-research-workbench/runs/`
- exposes completed runs through the FastAPI/React frontend

The frontend treats backend services as the authority. Each strategy opens a main-page workspace with:

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

The Market Data workspace exposes:

- Build Data: force-rebuilds the canonical provider store, submits durable backend worker jobs, polls backend-owned progress, and renders active/completed session cards without page reloads.
- Review Data: summarizes the saved manifest, artifact coverage, schemas, sampled Parquet rows, and chart-ready bars/features/supervision markers.
- Chart review: uses saved provider artifacts, centralized chart colors, extended-hours shading, timeframe buttons, settings, fit controls, and fullscreen mode.

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

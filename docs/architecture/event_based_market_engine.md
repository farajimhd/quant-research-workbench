# Event-Based Market Engine

## Goal

The active application should move away from the legacy regime built around
prebuilt one-minute bars. The new regime uses quotes and trades as the source
of truth. Bars, indicators, scanner rows, charts, replay, and backtests are
derived views over the same normalized event stream.

## Active Regimes

### Archived Legacy Bar Regime

The old backtest, market-data build, market-data review, and semi-auto pages
are removed from the active UI. Their code remains in the repository as
reference implementation until the event-based replacement covers the useful
debug and review workflows.

### Quote/Trade Regime

The quote/trade regime is the active app path. Its source data can come from:

- Massive websocket trades and quotes
- historical `market_sip_compact.events_YYYY` through the Rust historical QMD gateway

All sources must be adapted into `src.market_engine` contracts before scanner,
chart, strategy, replay, or backtest code consumes them.

## Massive Subscription Policy

The live gateway subscribes to the full stock tape by default:

- `T.*` for trades
- `Q.*` for quotes

The gateway still loads the ClickHouse reference universe for conid, listings,
logos, float/short enrichment, and scanner metadata. That universe is used for
order routing and scanner setup, not as a hard ingestion limit. If provider
entitlements or throughput require it, the gateway can fall back to targeted
subscriptions through config.

## Data Paths

### Live Fast Path

```text
Massive websocket
  -> Rust qmd-gateway normalize event
  -> update qmd-gateway memory
  -> publish compact scanner/chart state to app over local HTTP/WebSocket
```

This path must not wait for ClickHouse.

### Persistence Path

```text
Massive websocket
  -> normalize event
  -> enqueue raw event
  -> batch insert into q_live ClickHouse tables
```

ClickHouse may lag under load without slowing live trading decisions.

## QMD Gateway Process Model

`services/qmd-gateway` is one OS process. It does not create multiple worker
processes in the first implementation. It uses Tokio async tasks inside that
single process:

- Massive websocket ingest task
- ClickHouse writer task
- maintenance/gap-fill task
- local HTTP/WebSocket API server
- in-memory market-state updates

This keeps deployment simple while allowing high concurrency. If throughput
requires more parallelism later, ticker-sharded worker tasks can be added inside
the same process before moving to multiple OS processes.

## Gap Fill And Maintenance

The gateway treats 04:00-20:00 New York time on weekdays as the active extended
trading window. During that window, live websocket ingest is the priority. The
maintenance worker waits.

Outside the active window, the maintenance worker can:

- detect symbols captured in the app-owned live tables
- compare their latest persisted quote/trade timestamps with the current time
- call Massive REST historical trades and quotes for missing windows
- insert recovered rows into the same `q_live` raw tables
- record each cycle in `qmd_gap_fill_runs`

This keeps crash recovery and rest-data backfill out of the live decision path.

### Historical Path

```text
market_sip_compact.events_YYYY
  -> Rust qmd-history-gateway
  -> shared qmd_core compact decoder and enriched bars
  -> replay / backtest / debug
```

The historical service is configured through `QMD_HISTORY_*` variables. It is
read-only and does not share live QMD memory or writers.

## Shared Components

`src/market_engine/events.py`
: canonical quote and trade events.

`src/market_engine/bars.py`
: streaming and batch-compatible bar contracts.

`src/market_engine/scanner.py`
: scanner preset contract and backend sort/limit definitions.

`src/market_engine/broker.py`
: live IBKR and simulated backtest broker-facing account, order, fill, and
portfolio schemas.

`src/market_engine/sources.py`
: common event source contract for live, historical, and replay.

`src/market_engine/storage.py`
: ClickHouse configuration boundaries for historical event access.

## Broker And Portfolio Rule

Live trading uses IBKR for execution and account state. Event-based backtests
must use a simulated broker adapter that exposes the same order, fill, account,
and portfolio schema shape. The simulator owns fill decisions, partial fills,
average fill price, commissions, and portfolio mutation, but the UI should be
able to consume it similarly to IBKR.

## Backtest Scanner Rule

Live scanner setup:

```text
reference universe
+ Massive REST snapshot
+ float/short enrichment
= scanner setup
```

Historical backtest scanner setup:

```text
reference universe
+ synthetic snapshot from historical quotes/trades at the backtest start time
+ historical/as-of float/short enrichment
= scanner setup
```

The synthetic snapshot should mimic Massive snapshot columns closely enough
that scanner presets can be shared by live and backtest.

## Offline Derived Data

An offline process should materialize historical event-derived bars and
indicators. Derived rows must carry version fields such as:

- `feature_set_id`
- `bar_spec_id`
- `source_date`
- `build_version`

This keeps live trading context and backtests fast while preserving the ability
to rebuild historical features when indicator definitions change.

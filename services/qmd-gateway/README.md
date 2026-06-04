# QMD Gateway

Standalone Rust market-data gateway for the quote/trade regime.

The gateway runs as one OS process. Inside that process, Tokio runs async tasks
for websocket ingest, ClickHouse persistence, and local API/WebSocket serving.

Current responsibilities:

- subscribe to Massive stock websocket channels
- default subscription scope is full tape: `T.*` and `Q.*`
- normalize quote/trade events
- maintain in-memory live market state
- publish compact local snapshots/streams to the quant app
- batch-write raw events to the app-owned ClickHouse database

The gateway keeps two paths separate:

```text
fast path: Massive -> memory -> local app stream
persistence path: Massive -> queue -> ClickHouse batch insert
```

ClickHouse writes must never block the live trading decision path.

## Configuration

Environment variables:

- `MASSIVE_API_KEY`
- `QMD_GATEWAY_BIND`, default `127.0.0.1:8795`
- `QMD_MASSIVE_WS_URL`, default `wss://socket.massive.com/stocks`
- `QMD_SUBSCRIBE_ALL_SYMBOLS`, default `true`
- `QMD_SUBSCRIBE_TRADES`, default `true`
- `QMD_SUBSCRIBE_QUOTES`, default `true`
- `QMD_CLICKHOUSE_URL`
- `QMD_CLICKHOUSE_DATABASE`, default `q_live`
- `QMD_CLICKHOUSE_USER`, default `default`
- `QMD_CLICKHOUSE_PASSWORD`
- `QMD_CLICKHOUSE_MAX_BATCH`, default `10000`
- `QMD_CLICKHOUSE_FLUSH_INTERVAL_MS`, default `1000`
- `QMD_EVENT_CHANNEL_CAPACITY`, default `250000`
- `QMD_SCANNER_BROADCAST_MS`, default `1000`
- `QMD_TICKER_BROADCAST_MS`, default `250`
- `QMD_GAP_FILL_ENABLED`, default `true`
- `QMD_GAP_FILL_INTERVAL_MS`, default `300000`
- `QMD_GAP_FILL_LOOKBACK_MINUTES`, default `120`
- `QMD_GAP_FILL_MIN_GAP_SECONDS`, default `60`
- `QMD_GAP_FILL_MAX_PAGES_PER_SYMBOL`, default `5`
- `QMD_GAP_FILL_SYMBOLS`, optional comma-separated priority symbols

The service writes to:

- `live_massive_trades`
- `live_massive_quotes`
- `qmd_gap_fill_runs`

## Session Lifecycle

The gateway keeps the Massive websocket ingest task running for live capture.
It treats 04:00-20:00 New York time on weekdays as the active streaming window:

- 04:00-09:29 ET: premarket
- 09:30-15:59 ET: regular
- 16:00-19:59 ET: aftermarket

Outside that window, the maintenance worker runs gap-fill cycles. Gap fill uses
Massive REST historical trades and quotes:

- `/v3/trades/{stockTicker}`
- `/v3/quotes/{stockTicker}`

If `QMD_GAP_FILL_SYMBOLS` is set, only those symbols are checked. Otherwise the
worker discovers symbols already present in `live_massive_trades` and
`live_massive_quotes` for the current date, then fills from each symbol's latest
stored timestamp to now. This is meant for crash/restart recovery without
blocking the live ingest fast path.

## Install Rust On Windows

From the repo root:

```powershell
.\scripts\install_rust_windows.ps1
```

Then open a new PowerShell window and verify:

```powershell
rustc --version
cargo --version
```

## Run

```powershell
.\scripts\run_qmd_gateway.ps1
```

Check only:

```powershell
.\scripts\run_qmd_gateway.ps1 -CheckOnly
```

Health endpoint:

```text
GET http://127.0.0.1:8795/health
```

Snapshot endpoints:

```text
GET http://127.0.0.1:8795/snapshot/scanner?limit=250
GET http://127.0.0.1:8795/snapshot/ticker/AAPL
```

Local websocket endpoints:

```text
ws://127.0.0.1:8795/stream/scanner
ws://127.0.0.1:8795/stream/ticker/AAPL
ws://127.0.0.1:8795/stream/events
```

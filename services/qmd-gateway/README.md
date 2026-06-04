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

The service writes to:

- `live_massive_trades`
- `live_massive_quotes`

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

# QMD Gateway

Standalone Rust market-data gateway for the quote/trade regime.

Current responsibilities defined by this service boundary:

- subscribe to Massive stock websocket channels
- default subscription scope is full tape: `T.*` and `Q.*`
- normalize quote/trade events into app contracts
- maintain in-memory live market state
- publish compact local streams/snapshots to the quant app
- batch-write raw events to the app-owned ClickHouse database

The gateway must keep two paths separate:

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

## Run

```powershell
cargo run --manifest-path services/qmd-gateway/Cargo.toml
```

Health endpoint:

```text
GET http://127.0.0.1:8795/health
```

# QMD Gateway Configuration

All settings are read from environment variables at process start. Changing a value requires restarting the gateway.

## Required For Live Use

| Env Var | Default | Meaning | Tuning Note |
|---|---:|---|---|
| `MASSIVE_API_KEY` | empty | Massive websocket and REST API key. | Without it the API can run, but live ingest and REST gap fill cannot work. |
| `QMD_CLICKHOUSE_URL` | `http://localhost:8123` | ClickHouse HTTP endpoint. | Use a reachable LAN URL if ClickHouse runs on another machine or WSL host. |
| `QMD_CLICKHOUSE_DATABASE` | `q_live` | App-owned database for gateway writes. | Keep separate from read-only external databases. |
| `QMD_CLICKHOUSE_USER` | `default` | ClickHouse user. | Use a user with write access only to the app-owned database. |
| `QMD_CLICKHOUSE_PASSWORD` | empty | ClickHouse password. | Never commit this value. |

## API And Massive Connection

| Env Var | Default | Meaning | Tuning Note |
|---|---:|---|---|
| `QMD_GATEWAY_BIND` | `127.0.0.1:8795` | Local API bind address. | Use LAN bind only if the gateway must be reached from another machine. |
| `QMD_MASSIVE_WS_URL` | `wss://socket.massive.com/stocks` | Massive stock websocket URL. | Change only if Massive endpoint changes. |
| `QMD_SUBSCRIBE_ALL_SYMBOLS` | `true` | Subscribe to wildcard channels. | Current design expects full-market `T.*` and `Q.*`. |
| `QMD_SUBSCRIBE_TRADES` | `true` | Subscribe to Massive trades. | Disable only for quote-only testing. |
| `QMD_SUBSCRIBE_QUOTES` | `true` | Subscribe to Massive quotes. | Disabling quotes removes spread, midpoint, and NBBO-derived fields. |
| `QMD_SCANNER_BROADCAST_MS` | `1000` | Interval for legacy scanner websocket snapshots. | Lower values increase API work. |
| `QMD_TICKER_BROADCAST_MS` | `250` | Interval for ticker, bar, and indicator websocket snapshots. | Lower values increase API work. |

## Queue And Backpressure

| Env Var | Default | Meaning | Tuning Note |
|---|---:|---|---|
| `QMD_EVENT_CHANNEL_CAPACITY` | `250000` | Queue size for raw ClickHouse persistence. | If drops occur, raise this or improve writer throughput. |
| `QMD_BAR_CHANNEL_CAPACITY` | `250000` | Queue size for bar aggregation shards. | If drops occur, raise this or increase bar shards. |
| `QMD_INDICATOR_CHANNEL_CAPACITY` | `250000` | Queue size for tick-indicator event shards. | If drops occur, raise this or increase indicator shards. |
| `QMD_INDICATOR_BAR_CHANNEL_CAPACITY` | `250000` | Queue size for closed bars sent to indicator engine. | Relevant when many timeframes close at once. |
| `QMD_SCANNER_PRIMITIVE_CHANNEL_CAPACITY` | `250000` | Queue size for closed bars sent to scanner primitive engine. | Relevant when scanner primitive evaluation lags. |

The gateway uses non-blocking queue sends. A full queue means the downstream item is dropped and a metric counter is incremented. Massive ingest is not blocked.

## Bars

| Env Var | Default | Meaning | Tuning Note |
|---|---:|---|---|
| `QMD_BAR_TIMEFRAMES` | `1s,10s,30s,1m,5m,1h` | Timeframes built from quotes/trades. | Timeframes are aligned to the top of their interval. |
| `QMD_BAR_HISTORY_LIMIT` | `1000` | In-memory closed bars retained per ticker/timeframe. | Deeper history should come from ClickHouse. |
| `QMD_BAR_SHARD_COUNT` | `8` | Number of bar worker shards. | Increase if bar queue drops or latency rises. |

## Indicators

| Env Var | Default | Meaning | Tuning Note |
|---|---:|---|---|
| `QMD_TICK_INDICATOR_WINDOW_SECONDS` | `300` | Rolling quote/trade sample window for tick indicators. | Minimum is 60 because 60-second fields require it. |
| `QMD_INDICATOR_HISTORY_BY_TIMEFRAME` | `1s:900,10s:360,30s:480,1m:960,5m:192,1h:32` | Closed indicator rows retained per ticker/timeframe. | If a timeframe is missing, fallback is `QMD_INDICATOR_HISTORY_LIMIT`. |
| `QMD_INDICATOR_HISTORY_LIMIT` | `1000` | Fallback indicator history limit. | Used only for unlisted timeframes. |
| `QMD_INDICATOR_SHARD_COUNT` | `8` | Number of indicator worker shards. | Increase if indicator queue drops. |
| `QMD_PERSIST_INDICATORS` | `false` | Persist closed indicator rows to ClickHouse. | Keep false until the durable indicator set is finalized. |

## ClickHouse Batch Writes

| Env Var | Default | Meaning | Tuning Note |
|---|---:|---|---|
| `QMD_CLICKHOUSE_MAX_BATCH` | `10000` | Max rows per ClickHouse insert batch. | Larger batches reduce HTTP overhead but increase memory per batch. |
| `QMD_CLICKHOUSE_FLUSH_INTERVAL_MS` | `1000` | Max time before flushing partial ClickHouse batches. | Lower values reduce persistence delay but increase insert frequency. |

## Gap Fill

| Env Var | Default | Meaning | Tuning Note |
|---|---:|---|---|
| `QMD_GAP_FILL_ENABLED` | `true` | Enable Massive REST gap fill. | Disable for isolated websocket tests. |
| `QMD_GAP_FILL_MODE` | `auto` | Gap-fill mode: `auto`, `session`, `session_catch_up`, `after_hours`, or `repair`. | `auto` does startup catch-up during streaming and repair after hours. |
| `QMD_GAP_FILL_INTERVAL_MS` | `300000` | After-hours repair interval. | Default is 5 minutes. |
| `QMD_GAP_FILL_LOOKBACK_MINUTES` | `120` | Lookback when no latest timestamp exists. | Session catch-up uses this to warm recent memory. |
| `QMD_GAP_FILL_MIN_GAP_SECONDS` | `60` | Ignore gaps shorter than this. | Prevents excessive REST calls for tiny gaps. |
| `QMD_GAP_FILL_MAX_PAGES_PER_SYMBOL` | `5` | Max Massive REST pages per symbol per cycle. | Rate-limit control. |
| `QMD_GAP_FILL_SYMBOLS` | empty | Optional comma-separated symbol list. | If empty, symbols are discovered from existing `q_live` raw rows for the date. |

Current limitation: session catch-up writes recovered rows to ClickHouse but does not yet feed recovered REST rows through the in-memory bar/indicator/scanner pipeline. That is the next gap-fill improvement if scanner warm-up must be immediate for all tickers.

## Scanner Primitives

| Env Var | Default | Meaning | Tuning Note |
|---|---:|---|---|
| `QMD_SCANNER_PRIMITIVE_HISTORY_LIMIT` | `10000` | Number of primitive events retained in memory. | Snapshot uses latest primitive by ticker/timeframe/key. |
| `QMD_SCANNER_PRIMITIVE_CHANNEL_CAPACITY` | `250000` | Queue size for closed bars entering scanner primitive engine. | Raise if `bar_rows_scanner_dropped` increases. |

## Replay

| Env Var | Default | Meaning | Tuning Note |
|---|---:|---|---|
| `QMD_REPLAY_ENABLED` | `false` | Enable one-shot replay on process start. | Use for deterministic validation, not normal live trading. |
| `QMD_REPLAY_DATE` | empty | Date to replay. Empty means today UTC. | Use `YYYY-MM-DD`. |
| `QMD_REPLAY_SYMBOLS` | empty | Optional comma-separated tickers. | Empty means all symbols for the replay date. |
| `QMD_REPLAY_MAX_ROWS` | `1000000` | Max raw rows read during replay. | Raise carefully; replay feeds live in-memory queues. |

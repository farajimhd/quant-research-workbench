# QMD Gateway Configuration

Settings are read from environment variables at process start. The gateway also loads discovered `.env` files without overwriting variables already set in the shell. Changing a value requires restarting the gateway.

## Required For Live Use

| Env Var | Default | Meaning | Tuning Note |
|---|---:|---|---|
| `MASSIVE_API_KEY` | empty | Massive websocket and REST API key. | Without it the API can run, but live ingest and REST gap fill cannot work. |
| `QMD_CLICKHOUSE_URL` | `REAL_LIVE_CLICKHOUSE_WRITE_URL`, then `http://localhost:8123` | ClickHouse HTTP endpoint. | Use a reachable LAN URL if ClickHouse runs on another machine or WSL host. |
| `QMD_CLICKHOUSE_DATABASE` | `REAL_LIVE_CLICKHOUSE_WRITE_DATABASE`, then `q_live` | App-owned database for gateway writes. | Keep separate from read-only external databases. |
| `QMD_CLICKHOUSE_USER` | `REAL_LIVE_CLICKHOUSE_WRITE_USER`, then shared ClickHouse user fallbacks, then `default` | ClickHouse user. | Use a user with write access only to the app-owned database. |
| `QMD_CLICKHOUSE_PASSWORD` | `REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD`, then shared ClickHouse password fallbacks, then empty | ClickHouse password. | Never commit this value. |
| `QMD_CLICKHOUSE_STORAGE_POLICY` | `CLICKHOUSE_LIVE_STORAGE_POLICY` or empty | Optional storage policy for gateway-created compact tables. | Use the live SSD policy when available. |

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
| `QMD_EVENT_CHANNEL_CAPACITY` | `250000` | Queue size for optional raw ClickHouse persistence. | Relevant only when `QMD_PERSIST_RAW_EVENTS=true`. |
| `QMD_COMPACT_EVENTS_ENABLED` | `true` | Enable compact unified event conversion and websocket streaming. | Keep enabled for live ML consumers. |
| `QMD_COMPACT_EVENT_CHANNEL_CAPACITY` | `250000` | Queue size for compact event conversion/persistence. | If downstream work cannot keep up, live ingest backpressures rather than discarding compact events. |
| `QMD_COMPACT_EVENT_TABLE` | `live_market_events_v2` | ClickHouse table for compact live events. | Version the table name when the durable live event contract changes. |
| `QMD_COMPACT_EVENT_CONTINUITY_TABLE` | `live_event_ordinal_continuity` | Append-only live ordinal continuity snapshots. | Used to audit and bootstrap ticker-local live ordinals. |
| `QMD_COMPACT_EVENT_LIVE_BUFFER_EVENTS_PER_TICKER` | `512` | Recent compact events retained in memory per ticker for ML/app snapshots. | Must be at least the largest live inference context, e.g. 128. |
| `QMD_COMPACT_EVENT_REORDER_LAG_MS` | `500` | Per-ticker persistence reorder watermark lag before assigning final DB ordinals. | Higher values improve late-arrival ordering but delay durable writes. |
| `QMD_COMPACT_EVENT_REORDER_FORCE_FLUSH_MS` | `2000` | Maximum persistence wait before flushing reorder buffers. | Keeps DB lag bounded. |
| `QMD_COMPACT_EVENT_REORDER_MAX_EVENTS_PER_TICKER` | `4096` | Per-ticker persistence reorder buffer cap. | Protects memory during liquid bursts. |
| `QMD_PERSIST_COMPACT_EVENTS` | `true` | Persist compact live events to ClickHouse. | Disable only for stream-only tests. |
| `QMD_PERSIST_RAW_EVENTS` | `false` | Persist raw quote/trade rows. | Enable only for debug/replay/gap-fill workflows. |
| `QMD_REFERENCE_DIR` | repo `research/market_references/massive` | Massive reference files used for condition packing. | Must contain `conditions_indicators_glossary.json`. |
| `QMD_BAR_CHANNEL_CAPACITY` | `250000` | Queue size for bar aggregation shards. | If latency rises, raise this or increase bar shards. |
| `QMD_INDICATOR_CHANNEL_CAPACITY` | `250000` | Queue size for tick-indicator event shards. | If latency rises, raise this or increase indicator shards. |
| `QMD_INDICATOR_BAR_CHANNEL_CAPACITY` | `250000` | Queue size for closed bars sent to indicator engine. | Relevant when many timeframes close at once. |
| `QMD_SCANNER_PRIMITIVE_CHANNEL_CAPACITY` | `250000` | Queue size for closed bars sent to scanner primitive engine. | Relevant when scanner primitive evaluation lags. |

Required data-path queues use awaited sends. A full queue applies backpressure instead of dropping canonical quote/trade work. Compact live inference does not wait for DB ordinals: the ML/app path reads the in-memory per-ticker buffer, while final ticker-local ordinals are assigned only when sorted rows are flushed to `q_live`.

## Bars

| Env Var | Default | Meaning | Tuning Note |
|---|---:|---|---|
| `QMD_BAR_TIMEFRAMES` | `1s,10s,30s,1m,5m,1h` | Timeframes built from quotes/trades. | Timeframes are aligned to the top of their interval. |
| `QMD_BAR_HISTORY_LIMIT` | `1000` | In-memory closed bars retained per ticker/timeframe. | Deeper history should come from ClickHouse. |
| `QMD_BAR_SHARD_COUNT` | `8` | Number of bar worker shards. | Increase if bar latency rises. |

## Indicators

| Env Var | Default | Meaning | Tuning Note |
|---|---:|---|---|
| `QMD_TICK_INDICATOR_WINDOW_SECONDS` | `300` | Rolling quote/trade sample window for tick indicators. | Minimum is 60 because 60-second fields require it. |
| `QMD_INDICATOR_HISTORY_BY_TIMEFRAME` | `1s:900,10s:360,30s:480,1m:960,5m:192,1h:32` | Closed indicator rows retained per ticker/timeframe. | If a timeframe is missing, fallback is `QMD_INDICATOR_HISTORY_LIMIT`. |
| `QMD_INDICATOR_HISTORY_LIMIT` | `1000` | Fallback indicator history limit. | Used only for unlisted timeframes. |
| `QMD_INDICATOR_SHARD_COUNT` | `8` | Number of indicator worker shards. | Increase if indicator latency rises. |
| `QMD_PERSIST_INDICATORS` | `false` | Persist closed bar-level indicator rows to ClickHouse. | Keep false by default because these indicators can be recomputed from `live_market_bars`. |

## ClickHouse Batch Writes

| Env Var | Default | Meaning | Tuning Note |
|---|---:|---|---|
| `QMD_CLICKHOUSE_MAX_BATCH` | `10000` | Max rows per ClickHouse insert batch. | Larger batches reduce HTTP overhead but increase memory per batch. |
| `QMD_CLICKHOUSE_FLUSH_INTERVAL_MS` | `5000` | Max time before flushing partial ClickHouse batches. | Writes are background-batched; lower values reduce persistence delay but increase insert frequency. |

## Gap Fill

| Env Var | Default | Meaning | Tuning Note |
|---|---:|---|---|
| `QMD_GAP_FILL_ENABLED` | `true` | Enable Massive REST gap fill. | Disable for isolated websocket tests. |
| `QMD_GAP_FILL_MODE` | `auto` | Gap-fill mode: `auto`, `session`, `session_catch_up`, `after_hours`, or `repair`. | `auto` does startup catch-up during streaming and repair after hours. |
| `QMD_GAP_FILL_INTERVAL_MS` | `300000` | After-hours repair interval. | Default is 5 minutes. |
| `QMD_GAP_FILL_AWAITING_SYMBOLS_RETRY_MS` | `10000` | Short retry interval while streaming is active and repair is waiting for live websocket symbols. | Lets a clean-slate run start REST repair quickly as soon as websocket symbols arrive. |
| `QMD_GAP_FILL_LOOKBACK_MINUTES` | `120` | Legacy warmup lookback for focused tests. | Recent live repair now uses market-day coverage. |
| `QMD_GAP_FILL_MAX_LOOKBACK_DAYS` | `3` | Recent structural audit lookback in calendar days. | The REST repair window is controlled by `QMD_RECENT_LIVE_PRIOR_MARKET_DAYS`. |
| `QMD_GAP_FILL_MIN_GAP_SECONDS` | `1` | Ignore gaps shorter than this. | Keep at `1` for no intentional market-session holes. |
| `QMD_GAP_FILL_MAX_PAGES_PER_SYMBOL` | `5` | Legacy REST page cap for focused/manual repair paths. | Recent live coverage repair uses `QMD_RECENT_LIVE_MAX_PAGES_PER_INTERVAL`. |
| `QMD_RECENT_LIVE_MAX_PAGES_PER_INTERVAL` | `1000` | Max Massive REST pages per ticker/kind/repair interval for current-day plus 3-day q_live coverage repair. | Keep high enough that liquid tickers do not stop at `partial_page_limit`. |
| `QMD_RECENT_LIVE_PRIOR_MARKET_DAYS` | `3` | Number of prior US market sessions, plus the current market day, that q_live REST repair must keep covered. | Skips weekends and common US equity market holidays. |
| `QMD_RECENT_LIVE_REPAIR_CONCURRENCY` | `8` | Max concurrent ticker repair workers for recent q_live REST repair. | Keeps full-market session-head repair from crawling symbol by symbol. |
| `QMD_STARTUP_MAINTENANCE_ENABLED` | `true` | Audit and repair recent `q_live` event coverage before live websocket ingest starts. | Disable only for isolated tests. |
| `QMD_COVERAGE_TABLE` | `qmd_market_coverage_manifest_v1` | Coarse run-level coverage manifest table in `q_live`. | Records startup audits, recent live repairs, and historical flatfile update plans; not used as the fine-grained source of truth for live holes. |
| `QMD_LIVE_EVENT_COVERAGE_TABLE` | `qmd_live_event_coverage_v1` | Durable q_live compact-event coverage intervals. | Recent gap detection subtracts these intervals from required market sessions. |
| `QMD_FLATFILE_EVENT_COVERAGE_TABLE` | `qmd_flatfile_event_coverage_v1` | Historical flatfile coverage intervals. | First startup bootstraps one 2019-forward row from `market_sip_compact.events_ordinal_continuity`. |
| `QMD_GAP_FILL_SYMBOL_UNIVERSE_TABLE` | `qmd_gap_fill_symbol_universe_v1` | Durable ticker queue used by recent q_live REST repair. | Seeded from recent flatfile symbols and extended by websocket-discovered tickers. |
| `QMD_GAP_FILL_UNIVERSE_MARKET_DAYS` | `5` | Number of latest historical market sessions used to seed the symbol universe when the queue is empty. | Keeps startup repair broad without depending on broker/reference tables. |
| `QMD_RUN_ID` | generated | Optional stable id for one gateway run. | Normally leave generated; used as the live coverage row id suffix. |
| `QMD_RUN_STARTED_AT_UTC` | generated | Optional run start timestamp. | Normally leave generated; used to open the live coverage row. |
| `QMD_HOST_ROLE` | `auto` | Host role for historical update planning. | Override with `workstation` or `laptop` if auto-detection is wrong. |
| `QMD_HISTORICAL_CLICKHOUSE_DATABASE` | `market_sip_compact` | Read-only historical event database name. | QMD never writes live rows into this database. |
| `QMD_HISTORICAL_CLICKHOUSE_URL` | falls back to q_live ClickHouse URL | Historical ClickHouse endpoint. | Use when historical data is on a different endpoint. |
| `QMD_HISTORICAL_CLICKHOUSE_USER` | `CLICKHOUSE_WORKSTATION_USER`, then q_live user fallbacks | Historical ClickHouse read user. | Should have read access to `market_sip_compact.events_ordinal_continuity`. |
| `QMD_HISTORICAL_CLICKHOUSE_PASSWORD` | `CLICKHOUSE_WORKSTATION_PASSWORD`, then q_live password fallbacks | Historical ClickHouse read password. | Secret presence only is exposed in `/config`. |
| `QMD_HISTORICAL_FLATFILE_UPDATE_ENABLED` | `true` | Plan after-hours flatfile event updates for historical gaps. | Keeps historical update work away from websocket peak time. |
| `QMD_HISTORICAL_FLATFILE_AUTORUN` | `false` | Launch the flatfile update command automatically on workstation hosts. | Keep false if you want the command printed but not started. |
| `QMD_HISTORICAL_FLATFILE_SAFE_LAG_DAYS` | `1` | Latest historical day considered safe for flatfile update planning. | Massive flatfiles arrive after the trading day; keep at least one day lag. |
| `QMD_HISTORICAL_KNOWN_COVERAGE_END_DATE` | `2026-06-05` | Fallback historical coverage date if the continuity table query fails. | Coarse seed only; normal operation reads `events_ordinal_continuity`. |
| `QMD_HISTORICAL_PIPELINE_CODE_ROOT` | `D:\TradingML\codes\quant_research_workbench_pipelines` | Workstation path used to build the flatfile update command. | Must point to the synced pipeline code that updates historical `events` and qmd-compatible `live_market_bars`. |

Recent live repair converts Massive REST rows to the same normalized
`MarketEvent` type used by the websocket path, then feeds the same state,
stream, bar, indicator, and compact-event queues. Raw `live_massive_trades` and
`live_massive_quotes` are not part of the default repair contract. The repair
loads `qmd_live_event_coverage_v1`, materializes covered intervals from the
intersection of `compact_persisted` and `bars_persisted` rows plus explicit
`repair_completed` rows, subtracts those intervals from the current New York
market day plus the configured prior sessions, and fills every remaining
04:00-20:00 ET session gap. Symbols come from the durable
`qmd_gap_fill_symbol_universe_v1` queue. When that queue is empty, QMD seeds it
from the latest configured historical flatfile market sessions. During
streaming hours, new tickers observed in the in-memory live websocket compact
buffer are added to the queue as `not_gap_filled`. Each repair attempt updates
the symbol row status, and later runs reuse the queue instead of rediscovering
symbols from scratch.

## Scanner Primitives

| Env Var | Default | Meaning | Tuning Note |
|---|---:|---|---|
| `QMD_SCANNER_PRIMITIVE_HISTORY_LIMIT` | `10000` | Number of primitive events retained in memory. | Snapshot uses latest primitive by ticker/timeframe/key. |
| `QMD_SCANNER_PRIMITIVE_CHANNEL_CAPACITY` | `250000` | Queue size for closed bars entering scanner primitive engine. | Raise if scanner primitive latency rises. |

## Replay

| Env Var | Default | Meaning | Tuning Note |
|---|---:|---|---|
| `QMD_REPLAY_ENABLED` | `false` | Enable one-shot replay on process start. | Use for deterministic validation, not normal live trading. |
| `QMD_REPLAY_DATE` | empty | Date to replay. Empty means today UTC. | Use `YYYY-MM-DD`. |
| `QMD_REPLAY_SYMBOLS` | empty | Optional comma-separated tickers. | Empty means all symbols for the replay date. |
| `QMD_REPLAY_MAX_ROWS` | `1000000` | Max raw rows read during replay. | Raise carefully; replay feeds live in-memory queues. |

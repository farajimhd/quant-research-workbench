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
| `QMD_COMPACT_EVENT_TABLE` | `events` | Singular ClickHouse table for compact live events. | Encoding stays aligned with historical `market_sip_compact.events_YYYY`; the physical live table is not yearly. |
| `QMD_COMPACT_EVENT_LIVE_BUFFER_EVENTS_PER_TICKER` | `512` | Recent compact events retained in memory per ticker for ML/app snapshots. | Must be at least the largest live inference context, e.g. 128. |
| `QMD_COMPACT_EVENT_REORDER_LAG_MS` | `500` | Per-ticker persistence reorder watermark lag. | Higher values improve late-arrival ordering but delay durable writes. |
| `QMD_COMPACT_EVENT_REORDER_FORCE_FLUSH_MS` | `500` | Maximum event-time reorder wait before routing sparse events to live bars. | Runs on an independent 100 ms clock; ClickHouse inserts retain their separate batch interval. |
| `QMD_COMPACT_EVENT_REORDER_MAX_EVENTS_PER_TICKER` | `4096` | Per-ticker persistence reorder buffer cap. | Protects memory during liquid bursts. |
| `QMD_PERSIST_COMPACT_EVENTS` | `true` | Persist compact live events to ClickHouse. | Disable only for stream-only tests. |
| `QMD_PERSIST_RAW_EVENTS` | `false` | Persist raw quote/trade rows. | Enable only for debug/replay/gap-fill workflows. |
| Canonical references | historical ClickHouse | Condition, indicator, and tape encoding tables. | Startup fails if the DB reference contract is missing or disagrees with the updater. |
| `QMD_BAR_CHANNEL_CAPACITY` | `250000` | Queue size for bar aggregation shards. | If latency rises, raise this or increase bar shards. |
| `QMD_INDICATOR_CHANNEL_CAPACITY` | `250000` | Queue size for tick-indicator event shards. | If latency rises, raise this or increase indicator shards. |
| `QMD_INDICATOR_BAR_CHANNEL_CAPACITY` | `250000` | Queue size for closed bars sent to indicator engine. | Relevant when many timeframes close at once. |
| `QMD_SCANNER_PRIMITIVE_CHANNEL_CAPACITY` | `250000` | Queue size for closed bars sent to scanner primitive engine. | Relevant when scanner primitive evaluation lags. |
| `QMD_LIVE_MARKET_STATE_CHANNEL_CAPACITY` | `250000` | Queue size for quote/trade events and closed bars entering the abnormal market-state overlay. | Required path; if full, live ingest/bar finalization backpressures rather than dropping state evaluation. |

Required data-path queues use awaited sends. A full queue applies backpressure instead of dropping canonical quote/trade work. Compact live inference reads the in-memory per-ticker buffer; both memory and the ordinal-free live table use the timestamp/sequence/type/arrival ordering contract.

## Live Abnormal Market State

| Env Var | Default | Meaning | Tuning Note |
|---|---:|---|---|
| `QMD_LIVE_MARKET_STATE_ENABLED` | `true` | Persist abnormal live market-state transitions to ClickHouse. | Keep enabled for order/scanner audit. Ordinary normal rows are never persisted. |
| `QMD_LIVE_MARKET_STATE_TABLE` | `live_symbol_market_event_v1` | ClickHouse table for abnormal market-state transition rows. | The table stores sparse open/close rows only. Repeated active-state observations refresh memory but are not persisted. |
| `QMD_LIVE_MARKET_STATE_HISTORY_LIMIT` | `5000` | In-memory recent abnormal-state events retained for API snapshots. | Does not affect durable storage. |
| `QMD_LIVE_MARKET_STATE_TRADE_HALT_CONDITIONS` | empty | Comma-separated Massive trade condition ids that open `condition_halt`. | Leave empty until condition ids are validated against the reference glossary. |
| `QMD_LIVE_MARKET_STATE_TRADE_RESUME_CONDITIONS` | empty | Comma-separated Massive trade condition ids that close `condition_halt`. | Must be validated before production use. |
| `QMD_LIVE_MARKET_STATE_QUOTE_HALT_CONDITIONS` | empty | Comma-separated Massive quote condition ids that open `condition_halt`. | Optional; trade conditions are usually the first candidate. |
| `QMD_LIVE_MARKET_STATE_QUOTE_RESUME_CONDITIONS` | empty | Comma-separated Massive quote condition ids that close `condition_halt`. | Optional; use only after validation. |

When all-symbol live subscription is enabled, the gateway subscribes to
`LULD.*`; official indicator 17 opens `condition_halt` and indicator 18 closes
it. Massive currently documents halt/resumption indicators as NASDAQ-listed
coverage, so validated condition mappings may still be configured as a
secondary authority for other venues.

The gateway also opens/closes abnormal states from bar-derived conditions:

- `estimated_luld_near_upper`
- `estimated_luld_near_lower`
- `estimated_luld_breach_upper`
- `estimated_luld_breach_lower`
- `locked_crossed_quote`

Fields and event families prefixed `estimated_luld_` remain QMD estimates and
must not be confused with official `LULD.*` messages. Near-band rows are warning context. Breach rows and locked/crossed quote rows
are live-tradability blocking until their close transition is observed.

## Bars

| Env Var | Default | Meaning | Tuning Note |
|---|---:|---|---|
| `QMD_BAR_TIMEFRAMES` | `100ms,1s,5s,10s,30s,1m,5m,1h` | Enriched timeframes built from quotes/trades for indicators and downstream state. | Timeframes are aligned to the top of their interval. |
| `QMD_BAR_HISTORY_LIMIT` | `4` | Current-tail closed bars retained per ticker/timeframe across the all-market store. | Full chart windows come from QMD History/ClickHouse; raising this multiplies full Structure snapshots by every ticker and timeframe. |
| `QMD_BAR_SHARD_COUNT` | `8` | Number of bar worker shards. | Increase if bar latency rises. |
| `QMD_PRODUCT_CACHE_MAX_BYTES` | `536870912` | Service-wide estimated byte ceiling for canonical family and condition rows. | Limits are divided across shards. |
| `QMD_PRODUCT_CACHE_MAX_ROWS` | `2000000` | Service-wide canonical product row ceiling. | Eviction removes complete ticker-day partitions. |
| `QMD_PRODUCT_CACHE_MAX_PARTITIONS` | `8192` | Service-wide ticker-day partition ceiling. | Prevents unbounded symbol/day keys. |

Canonical intraday bars are always enabled and require compact-event
persistence. `QMD_INTRADAY_BAR_TIMEFRAMES` defaults to
`100ms,1s,5s,10s,30s,1m,5m,1h`, `QMD_INTRADAY_BAR_TABLE` defaults to
`intraday_family_bars_v3`, and the channel/shard controls are
`QMD_INTRADAY_BAR_CHANNEL_CAPACITY` and `QMD_INTRADAY_BAR_SHARD_COUNT`.
`QMD_INTRADAY_BAR_BOOTSTRAP_ON_START` defaults to `false`; use the managed
launcher `-BootstrapBars` only for the reviewed one-time v3 migration.
`QMD_INTRADAY_BAR_BOOTSTRAP_SYMBOL_BATCH` defaults to `128` and bounds each
restart-safe date/ticker batch. Keep it unchanged until an active migration
finishes; the checkpoint also fingerprints the exact ordered ticker set so
source-universe changes cannot make a different batch look complete.

`QMD_DERIVED_RECONCILIATION_ENABLED` defaults to `true`. It automatically
reconciles the retained Live event window into canonical bars outside market
hours and performs scoped indicator reconstruction when computation demand is
activated. `QMD_DERIVED_RECONCILIATION_INTERVAL_MS` defaults to `300000`, and
`QMD_DERIVED_COVERAGE_TABLE` defaults to `qmd_derived_coverage_v1`.
`-BootstrapBars` is an emergency foreground recovery control, not normal
operation.

## Indicators

| Env Var | Default | Meaning | Tuning Note |
|---|---:|---|---|
| `QMD_TICK_INDICATOR_WINDOW_SECONDS` | `300` | Rolling quote/trade sample window for tick indicators. | Minimum is 60 because 60-second fields require it. |
| `QMD_INDICATOR_HISTORY_BY_TIMEFRAME` | `1s:900,10s:360,30s:480,1m:960,5m:192,1h:32` | Closed indicator rows retained per ticker/timeframe. | If a timeframe is missing, fallback is `QMD_INDICATOR_HISTORY_LIMIT`. |
| `QMD_INDICATOR_HISTORY_LIMIT` | `1000` | Fallback indicator history limit. | Used only for unlisted timeframes. |
| `QMD_INDICATOR_SHARD_COUNT` | `8` | Number of indicator worker shards. | Increase if indicator latency rises. |
| `QMD_INDICATOR_TABLE` | `qmd_indicator_rows_v1` | Versioned durable closed-bar indicator table. | Change only as a coordinated schema migration. |
| `QMD_PERSIST_INDICATORS` | `true` | Persist closed scoped bar-level indicator rows to ClickHouse. | Required for low-latency Live chart hydration; Replay/Backtest/Debug still use QMD History. |
| `QMD_PERSIST_STRUCTURE_EVENTS` | `true` | Persist confirmed generic-structure events plus the latest full versioned engine checkpoint. | Keep enabled for strategy history and exact restart continuity; changed symbols are coalesced per writer flush and this does not require full bar-indicator persistence. |

## ClickHouse Batch Writes

| Env Var | Default | Meaning | Tuning Note |
|---|---:|---|---|
| `QMD_CLICKHOUSE_MAX_BATCH` | `10000` | Max rows per ClickHouse insert batch. | Larger batches reduce HTTP overhead but increase memory per batch. |
| `QMD_COMPACT_EVENT_MAX_CLICKHOUSE_BATCH` | `50000` | Max ordered compact-event rows per authoritative ClickHouse insert. | Kept separate from derived writers so the full-market source lane amortizes HTTP overhead without inflating every service batch. |
| `QMD_CLICKHOUSE_FLUSH_INTERVAL_MS` | `5000` | Max time before flushing partial ClickHouse batches. | Writes are background-batched; lower values reduce persistence delay but increase insert frequency. |

## Gap Fill

| Env Var | Default | Meaning | Tuning Note |
|---|---:|---|---|
| `QMD_GAP_FILL_ENABLED` | `true` | Enable Massive REST gap fill. | Disable for isolated websocket tests. |
| `QMD_GAP_FILL_MODE` | `auto` | Gap-fill mode: `auto`, `session`, `session_catch_up`, `after_hours`, or `repair`. | `auto` does startup catch-up during streaming and repair after hours. |
| `QMD_GAP_FILL_INTERVAL_MS` | `300000` | After-hours repair interval. | Default is 5 minutes. |
| `QMD_GAP_FILL_AWAITING_SYMBOLS_RETRY_MS` | `10000` | Short retry interval while streaming is active and repair is waiting for live websocket symbols. | Lets a clean-slate run start REST repair quickly as soon as websocket symbols arrive. |
| `QMD_GAP_FILL_MIN_GAP_SECONDS` | `1` | Ignore gaps shorter than this. | Keep at `1` for no intentional market-session holes. |
| `QMD_RECENT_LIVE_MAX_PAGES_PER_INTERVAL` | `1000` | Max Massive REST pages per ticker/kind/repair interval for current-day plus 3-day q_live coverage repair. | Keep high enough that liquid tickers do not stop at `partial_page_limit`. |
| `QMD_RECENT_LIVE_PRIOR_MARKET_DAYS` | `3` | Number of prior US market sessions, plus the current market day, that q_live REST repair must keep covered. | Skips weekends and common US equity market holidays. |
| `QMD_RECENT_LIVE_REPAIR_CONCURRENCY` | `8` | Max concurrent ticker repair workers for recent q_live REST repair. | Keeps full-market session-head repair from crawling symbol by symbol. |
| `QMD_STARTUP_MAINTENANCE_ENABLED` | `true` | Audit and repair recent `q_live` event coverage before live websocket ingest starts. | Disable only for isolated tests. |
| `QMD_COVERAGE_TABLE` | `qmd_market_coverage_manifest_v1` | Coarse run-level coverage manifest table in `q_live`. | Records startup audits, recent live repairs, and historical flatfile update plans; not used as the fine-grained source of truth for live holes. |
| `QMD_LIVE_EVENT_COVERAGE_TABLE` | `qmd_live_event_coverage_v1` | Durable q_live compact-event coverage intervals. | Recent gap detection subtracts these intervals from required market sessions. |
| `QMD_FLATFILE_COVERAGE_TABLE` | `qmd_flatfile_coverage_v2` | Per-session quote/trade remote object and historical coverage. | The obsolete v1 bootstrap table is dropped at startup. |
| `QMD_GAP_FILL_SYMBOL_UNIVERSE_TABLE` | `qmd_gap_fill_symbol_universe_v1` | Durable ticker queue used by recent q_live REST repair. | Seeded from recent flatfile symbols and extended by websocket-discovered tickers. |
| `QMD_GAP_FILL_UNIVERSE_MARKET_DAYS` | `5` | Number of latest historical market sessions used to seed the symbol universe when the queue is empty. | Keeps startup repair broad without depending on broker/reference tables. |
| `QMD_RUN_ID` | generated | Optional stable id for one gateway run. | Normally leave generated; used as the live coverage row id suffix. |
| `QMD_RUN_STARTED_AT_UTC` | generated | Optional run start timestamp. | Normally leave generated; used to open the live coverage row. |
| `QMD_HOST_ROLE` | `laptop` | Explicit host authority for historical update planning. | Accepts only `laptop` or `workstation`; workstation-only historical autorun is never inferred from paths or machine names. |
| `QMD_HISTORICAL_CLICKHOUSE_DATABASE` | `market_sip_compact` | Read-only historical event database name. | QMD never writes live rows into this database. |
| `QMD_HISTORICAL_DAILY_SESSION_BARS_TABLE` | `daily_session_bars_by_symbol_time_v1` | Three-session SIP daily-bar authority for historical reference levels. | Only fully available premarket, regular, and after-hours session sets are aggregated. |
| `QMD_HISTORICAL_CLICKHOUSE_URL` | falls back to q_live ClickHouse URL | Historical ClickHouse endpoint. | Use when historical data is on a different endpoint. |
| `QMD_HISTORICAL_CLICKHOUSE_USER` | `CLICKHOUSE_WORKSTATION_USER`, then q_live user fallbacks | Historical ClickHouse read user. | Should have read access to `market_sip_compact.events_ordinal_continuity`. |
| `QMD_HISTORICAL_CLICKHOUSE_PASSWORD` | `CLICKHOUSE_WORKSTATION_PASSWORD`, then q_live password fallbacks | Historical ClickHouse read password. | Secret presence only is exposed in `/config`. |
| `QMD_HISTORICAL_FLATFILE_UPDATE_ENABLED` | `true` | Plan after-hours flatfile event updates for historical gaps. | Keeps historical update work away from websocket peak time. |
| `QMD_HISTORICAL_FLATFILE_AUTORUN` | `true` | Launch the unchanged updater on a workstation after collection closes. | Laptop hosts always record the exact manual command. |
| `QMD_MARKET_STATUS_URL` | Massive `/v1/marketstatus/now` | Cached current exchange state. | Used with holidays for close/early-close decisions. |
| `QMD_MARKET_HOLIDAYS_URL` | Massive `/v1/marketstatus/upcoming` | Cached full-holiday and early-close calendar. | Local New York schedule is fallback only. |
| `QMD_FLATFILE_ENDPOINT_URL` | `https://files.massive.com` | Massive S3-compatible flatfile endpoint. | Signed metadata discovery starts after 08:00 ET. |
| `QMD_FLATFILE_ACCESS_KEY_ID` / `QMD_FLATFILE_SECRET_ACCESS_KEY` | shared AWS env fallbacks | Credentials for metadata-only remote discovery. | Secret values are never serialized by `/config`. |
| `QMD_HISTORICAL_PIPELINE_CODE_ROOT` | `D:\TradingML\codes\quant_research_workbench_pipelines` | Workstation path used to build the flatfile update command. | Must point to the synced pipeline code that updates read-only historical `events_YYYY`. |
| `QMD_HISTORICAL_PIPELINE_PYTHON` | empty | Exact Python executable used by workstation historical autorun. | The managed launcher sets this to its resolved Python path; workstation autorun fails preflight if it is missing. |
| `QMD_HISTORICAL_UPDATE_RUNTIME_ROOT` | `D:\TradingML\runtimes\qmd_gateway\historical_flatfile_update` | Runtime-owned updater state and stdout/stderr logs. | Must remain outside the repository. |

Recent live repair converts Massive REST rows to the same normalized
`MarketEvent` type used by the websocket path, then feeds the same state,
stream, bar, indicator, and compact-event queues. Raw `live_massive_trades` and
`live_massive_quotes` are not part of the default repair contract. The repair
filters full normalized and in-response exact replays before fan-out,
serializes concurrent focused activations, and records completion only after
compact-event and 100 ms bar coverage overlap durably. The startup structural
audit validates the exact table engine/key contract; physical
ReplacingMergeTree versions are not canonical conflicts, and matching provider
timestamp/sequence tuples with different payloads remain distinct events.
The repair
loads `qmd_live_event_coverage_v1`, materializes covered intervals from the
intersection of `compact_persisted` and `intraday_bars_persisted` rows plus explicit
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

## Historical runtime

The live QMD binary has no replay configuration. Replay and backtest configure
`services/qmd_history_gateway`, which depends on QMD's shared Rust library but
uses only read-only `market_sip_compact.events_YYYY` sources.

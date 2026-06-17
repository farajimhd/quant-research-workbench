# Python News Gateway

This service is the production Benzinga news gateway for the app. It polls the
Massive-served Benzinga REST endpoint, saves raw payloads, normalizes each item
through the shared Benzinga item pipeline, writes canonical ClickHouse rows, and
serves recent news to the app over HTTP and websocket endpoints.

The old Rust news gateway was removed. News is a lower-rate REST/text workflow,
so keeping it in Python avoids duplicating normalization, URL policy, ticker-link
creation, and historical/live contracts.

## Runtime Flow

```text
start service
-> load .env files
-> resolve workstation-first data root
-> construct provider client, normalizer, ClickHouse target, in-memory state
-> run dependency preflight
-> read latest persisted published_at_utc from ClickHouse
-> decide startup gap action
-> start live polling loop
-> optionally start Rich terminal dashboard
-> expose HTTP/websocket endpoints
```

Each poll cycle:

```text
compute UTC window from lookback
-> fetch Benzinga news from Massive REST
-> save every raw payload under workstation market-data
-> normalize with pipelines.news.benzinga.news_pipeline
-> write q_live.benzinga_news_normalized_v1
-> write q_live.benzinga_news_ticker_v1
-> update in-memory recent-news cache
-> update metrics
```

The service skips existing `canonical_news_id` rows by default through the shared
batch writer. Overlapping lookbacks and restarts are expected.

## Dependency Preflight

The gateway is fail-fast. It does not start the live polling loop, startup gap
fill, raw payload writes, or ClickHouse batch writes until preflight succeeds.

Preflight checks:

| Check | What It Verifies |
| --- | --- |
| Configuration | `MASSIVE_API_KEY`, ClickHouse URL, user, and password are present. |
| Artifact storage | Raw and prepared roots can be created and written. |
| ClickHouse | `SELECT 1` works and the normalized/news ticker tables exist with the expected columns. |
| Benzinga provider | The Massive-served Benzinga endpoint accepts the API key and returns a valid JSON response. |

`.\scripts\run_news_gateway.ps1 -CheckOnly` runs this same preflight and exits.
Use it before leaving the gateway running.

## Storage Rule

All app service data is written to the workstation market-data root first:

- On the workstation: `D:/market-data`
- From the laptop: `\\DESKTOP-SAAI85T\Workstation-D\market-data`

The service does not silently write service data to laptop-local
`D:/market-data`. If the workstation path is not available, startup fails with a
clear prompt to run on the workstation, mount the share, or set
`NEWS_GATEWAY_DATA_ROOT_WIN`.

Default folders:

```text
raw payloads: <data-root>/news-benzinga/raw/YYYY/MM/DD/benzinga_<id>.json
prepared output: <data-root>/prepared
gateway output: <data-root>/prepared/benzinga_news_gateway
```

## Polling Schedule

Polling is based on Eastern Time:

| Session | Time ET | Default |
| --- | ---: | ---: |
| Premarket | 04:00-09:30 | 10 sec |
| Market | 09:30-16:00 | 5 sec |
| After-hours | 16:00-20:00 | 15 sec |
| Closed | 20:00-04:00 | 60 sec |

The live poll window is `now - NEWS_BENZINGA_LOOKBACK_MINUTES` to `now`.
Default lookback is 15 minutes. Existing rows are skipped, so overlapping windows
are safe.

## Gap Handling

On startup the service reads:

```sql
SELECT max(published_at_utc)
FROM q_live.benzinga_news_normalized_v1
```

Then it subtracts `NEWS_BENZINGA_POLL_OVERLAP_SECONDS` from that timestamp and
compares the resulting gap against current UTC time.

Behavior:

| Situation | Action |
| --- | --- |
| No ClickHouse watermark | Live polling starts with normal lookback. |
| Gap fits inside normal lookback | Live polling covers it. |
| Gap is larger than lookback and <= 3 days | Service starts background gap fill. |
| Gap is > 3 days and running on workstation | Service starts background gap fill automatically. |
| Gap is > 3 days and not running on workstation | Service prints the exact manual historical fill command and continues live polling. |

Large non-workstation gaps are not auto-filled because the workstation has the
correct storage root and compute. The printed command uses:

```powershell
python -m pipelines.news.benzinga.news_benzinga_provider_gap_fill --start-utc <start> --end-utc <end> --raw-root-win D:/market-data/news-benzinga/raw --bucket-minutes 90 --workers 4 --batch-size 1000 --progress-interval 10 --execute
```

## ClickHouse Writes

The live service writes canonical rows to:

```text
q_live.benzinga_news_normalized_v1
q_live.benzinga_news_ticker_v1
```

It does not write the legacy split tables:

```text
benzinga_news_event_v1
benzinga_news_text_v1
benzinga_news_url_v1
benzinga_news_attachment_v1
```

Raw JSON remains on disk. The database stores compact normalized fields and
ticker links. This keeps historical and live data aligned.

## Terminal Dashboard

When stdout is interactive, the service starts a Rich dashboard. It is a separate
async task and only reads in-memory metrics plus recent-news state. It does not
call Massive, ClickHouse, or disk, and it does not run in the polling/write path.

Dashboard content:

- dependency preflight status and timing
- service status and current poll interval
- total and last-cycle provider rows
- processed rows, written rows, skipped existing rows
- raw payload save count
- failures and last error
- startup gap status and manual command when needed
- recent news table with time, tickers, title, and quality flags

Controls:

```powershell
$env:NEWS_TERMINAL_RICH_ENABLED="auto"   # default
$env:NEWS_TERMINAL_RICH_ENABLED="true"   # force on
$env:NEWS_TERMINAL_RICH_ENABLED="false"  # disable
$env:NEWS_TERMINAL_REFRESH_SECONDS="1"
$env:NEWS_TERMINAL_NEWS_LIMIT="12"
```

## Run

Preferred wrapper:

```powershell
.\scripts\run_news_gateway.ps1
```

The wrapper runs with `conda run --no-capture-output -n ml4t` by default so the
service uses the same workstation environment regardless of the shell's active
Python. It uses plain `python` directly only when the current conda environment
is already `ml4t`.

Check configuration only:

```powershell
.\scripts\run_news_gateway.ps1 -CheckOnly
```

This is now a full dependency preflight, not only config parsing.

Overrides:

```powershell
.\scripts\run_news_gateway.ps1 -CondaEnv ml4t
.\scripts\run_news_gateway.ps1 -PythonExe C:/Users/g835l/miniconda3/envs/ml4t/python.exe
.\scripts\run_news_gateway.ps1 -Bind 127.0.0.1:8796
```

Direct module run:

```powershell
python -m services.news_gateway.main --check-only
python -m services.news_gateway.main
```

## API

```text
GET /health
GET /config
GET /metrics
GET /snapshot/news/recent?limit=250
GET /snapshot/news/scanner?limit=250
GET /snapshot/news/ticker/AAPL?limit=100

WS /stream/news
WS /stream/news/scanner
WS /stream/news/ticker/AAPL
```

Example checks:

```powershell
curl.exe http://127.0.0.1:8796/health
curl.exe http://127.0.0.1:8796/metrics
curl.exe "http://127.0.0.1:8796/snapshot/news/recent?limit=20"
```

## Configuration

Required:

```text
MASSIVE_API_KEY
NEWS_CLICKHOUSE_URL or QMD_CLICKHOUSE_URL or REAL_LIVE_CLICKHOUSE_WRITE_URL
```

Recommended:

```text
NEWS_CLICKHOUSE_USER
NEWS_CLICKHOUSE_PASSWORD
CLICKHOUSE_WORKSTATION_USER
CLICKHOUSE_WORKSTATION_PASSWORD
CLICKHOUSE_USER
CLICKHOUSE_PASSWORD
NEWS_BENZINGA_CLICKHOUSE_DATABASE=q_live
NEWS_BENZINGA_NORMALIZED_TABLE=benzinga_news_normalized_v1
NEWS_BENZINGA_TICKER_TABLE=benzinga_news_ticker_v1
```

Credential fallback order is `NEWS_*`, `QLIVE_MIGRATION_*`, `QMD_*`,
`REAL_LIVE_*`, `CLICKHOUSE_WORKSTATION_*`, then plain `CLICKHOUSE_*`.

Storage:

```text
NEWS_GATEWAY_DATA_ROOT_WIN=<optional explicit root>
NEWS_BENZINGA_RAW_ROOT_WIN=<optional explicit raw root>
NEWS_BENZINGA_PREPARED_ROOT_WIN=<optional explicit prepared root>
```

Provider:

```text
NEWS_BENZINGA_URL=https://api.massive.com/benzinga/v2/news
NEWS_BENZINGA_PAGE_LIMIT=1000
NEWS_BENZINGA_MAX_PAGES=1000
```

Polling:

```text
NEWS_BENZINGA_MARKET_POLL_SECONDS=5
NEWS_BENZINGA_PREMARKET_POLL_SECONDS=10
NEWS_BENZINGA_AFTERHOURS_POLL_SECONDS=15
NEWS_BENZINGA_CLOSED_POLL_SECONDS=60
NEWS_BENZINGA_LOOKBACK_MINUTES=15
NEWS_BENZINGA_POLL_OVERLAP_SECONDS=120
NEWS_BENZINGA_RESTART_GAP_MAX_DAYS=3
```

Writes and memory:

```text
NEWS_BENZINGA_EXECUTE=true
NEWS_CLICKHOUSE_MAX_BATCH=1000
NEWS_RECENT_HISTORY_LIMIT=5000
NEWS_BENZINGA_TEXT_LIMIT_CHARS=50000
NEWS_BENZINGA_URL_DOMAIN_POLICY_JSON=<optional policy file>
```

Terminal:

```text
NEWS_TERMINAL_RICH_ENABLED=auto
NEWS_TERMINAL_REFRESH_SECONDS=1
NEWS_TERMINAL_NEWS_LIMIT=12
```

## Behavior In Common Situations

### Workstation share is unavailable

Startup fails before polling. Fix by running on the workstation, mounting
`\\DESKTOP-SAAI85T\Workstation-D\market-data`, or setting
`NEWS_GATEWAY_DATA_ROOT_WIN` to an existing path.

### Massive API key is missing

Startup fails while constructing the provider client. Set `MASSIVE_API_KEY`.

### ClickHouse latest-watermark query fails

Startup fails before polling. The latest-watermark query uses the same
ClickHouse dependency that preflight validated; if it fails, the service does
not treat the failure as `no_watermark` and does not start batch work.

### ClickHouse write fails during a poll

The poll cycle is marked failed, `poll_failures` increments, and `last_error`
records the exception. The service continues and retries on the next scheduled
poll.

### A raw item cannot be normalized

That item increments the failed-row count for the cycle. Other items in the same
provider response continue processing.

### Existing news is seen again

The batch writer skips existing `canonical_news_id` rows. This is expected during
overlapping lookbacks and restarts.

### Provider returns multiple pages

The provider client follows `next_url` until there are no more pages or
`NEWS_BENZINGA_MAX_PAGES` is reached. Saturation is visible in cycle summaries
and metrics.

### The service is stopped with Ctrl+C

Uvicorn stops the app, the gateway sets its stop event, and the polling,
gap-fill, and terminal dashboard tasks are cancelled.

## Historical Gap Fill

Use this when the service prints a manual large-gap command or when you want to
backfill a known period:

```powershell
python -m pipelines.news.benzinga.news_benzinga_provider_gap_fill --start-utc 2026-06-01T00:00:00Z --end-utc 2026-06-04T00:00:00Z --raw-root-win D:/market-data/news-benzinga/raw --bucket-minutes 90 --workers 4 --batch-size 1000 --progress-interval 10 --execute
```

This script downloads provider data, saves raw payloads, normalizes items, and
writes the same canonical tables as live.

For already downloaded raw files:

```powershell
python -m pipelines.news.benzinga.news_benzinga_package_gap_fill --raw-root-win D:/market-data/news-benzinga/raw --start-utc 2026-06-01 --end-utc 2026-06-02 --processes 8 --batch-size 1000 --execute
```

## Current Limitations

- The service keeps recent news in memory only for live API snapshots. The source
  of truth is ClickHouse plus raw payload files.
- Live external URL/PDF enrichment is not performed in the gateway hot path.
  The item pipeline records URL tasks and quality flags; enrichment remains a
  separate workflow.
- Websocket streams currently emit periodic snapshots, not per-row delta events.

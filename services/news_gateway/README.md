# Python News Gateway

This service is the production Benzinga news gateway for the app. It replaces the
old Rust news gateway because news ingestion is REST polling plus text
normalization, URL policy, optional enrichment, ClickHouse writes, and websocket
fanout. Those tasks are better kept in Python so historical gap fill and live
ingestion use the same item-level pipeline.

## Storage Rule

All app service data is written to the workstation market-data root first:

- On the workstation: `D:/market-data`
- From the laptop: `\\DESKTOP-SAAI85T\Workstation-D\market-data`

The service does not silently write service data to a laptop-local
`D:/market-data`. If the workstation path is not available, startup fails with a
clear prompt to run on the workstation, mount the share, or set
`NEWS_GATEWAY_DATA_ROOT_WIN`.

## Polling Schedule

Defaults are Eastern Time:

- Premarket, 04:00-09:30: every 10 seconds
- Market, 09:30-16:00: every 5 seconds
- After-hours, 16:00-20:00: every 15 seconds
- Closed, 20:00-04:00: every 60 seconds

The service polls the Massive-served Benzinga endpoint only. Massive is the API
transport; the canonical provider remains `benzinga`.

## Gap Handling

At startup the service reads the latest `published_at_utc` already persisted in
ClickHouse.

- If the gap is covered by the normal live lookback, live polling handles it.
- If the gap is larger than the live lookback but <= 3 days, the service fills it
  in the background.
- If the gap is > 3 days and the service is running on the workstation, the
  service fills it automatically in the background.
- If the gap is > 3 days and the service is not running on the workstation, the
  service prints the exact historical fill command to run manually on the
  workstation and continues live polling.

The manual command uses the provider-backed gap-fill runner:

```powershell
python -m pipelines.news.benzinga.news_benzinga_provider_gap_fill --start-utc 2026-06-01T00:00:00Z --end-utc 2026-06-04T00:00:00Z --raw-root-win D:/market-data/news-benzinga/raw --bucket-minutes 90 --workers 4 --batch-size 1000 --progress-interval 10 --execute
```

## Important Environment Variables

```text
MASSIVE_API_KEY
NEWS_GATEWAY_BIND=127.0.0.1:8796
NEWS_GATEWAY_DATA_ROOT_WIN=<optional override>
NEWS_BENZINGA_MARKET_POLL_SECONDS=5
NEWS_BENZINGA_PREMARKET_POLL_SECONDS=10
NEWS_BENZINGA_AFTERHOURS_POLL_SECONDS=15
NEWS_BENZINGA_CLOSED_POLL_SECONDS=60
NEWS_BENZINGA_LOOKBACK_MINUTES=15
NEWS_BENZINGA_RESTART_GAP_MAX_DAYS=3
NEWS_BENZINGA_PAGE_LIMIT=1000
NEWS_BENZINGA_MAX_PAGES=1000
NEWS_BENZINGA_EXECUTE=true
NEWS_TERMINAL_RICH_ENABLED=auto
NEWS_TERMINAL_REFRESH_SECONDS=1
NEWS_TERMINAL_NEWS_LIMIT=12
NEWS_CLICKHOUSE_URL
NEWS_CLICKHOUSE_USER
NEWS_CLICKHOUSE_PASSWORD
NEWS_BENZINGA_CLICKHOUSE_DATABASE=q_live
NEWS_BENZINGA_NORMALIZED_TABLE=benzinga_news_normalized_v1
NEWS_BENZINGA_TICKER_TABLE=benzinga_news_ticker_v1
```

## Run

Check configuration only:

```powershell
python -m services.news_gateway.main --check-only
```

Run the service:

```powershell
python -m services.news_gateway.main
```

PowerShell wrapper:

```powershell
.\scripts\run_news_gateway.ps1
```

The wrapper runs with `conda run -n ml4t` by default so the service uses the same
environment on the workstation regardless of the shell's active Python. Override
when needed:

```powershell
.\scripts\run_news_gateway.ps1 -CondaEnv ml4t
.\scripts\run_news_gateway.ps1 -PythonExe C:/path/to/python.exe
```

The service renders a Rich terminal dashboard when stdout is interactive. The
dashboard is a separate async task that reads in-memory metrics and recent-news
state; it does not run inside the provider polling or ClickHouse write path.
Disable it with:

```powershell
$env:NEWS_TERMINAL_RICH_ENABLED="false"
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

# News Gateway

Standalone Rust gateway for Benzinga news served through the Massive REST API.

The service polls only:

- Benzinga news: `/benzinga/v2/news`

It writes every valid article to one ClickHouse table, `live_news_articles`, and classifies rows for live scanner and research use. It does not drop crypto, macro, politics, war, ETF, no-ticker, or title-only rows. Those are persisted and labeled because they can matter for model training and broad market context.

## Responsibilities

- Poll Massive REST endpoints incrementally.
- Save provider timestamps with high precision.
- Save `gateway_seen_at` so provider delay can be measured live.
- Normalize Benzinga news into the canonical news schema.
- Persist raw JSON, article text, tickers, channels, tags, and normalized metadata.
- Clean HTML into text.
- Optionally fetch article URLs when body text is short.
- Optionally discover/download PDF links and extract text with `pdftotext`.
- Maintain in-memory recent and per-ticker news state.
- Expose local REST and websocket endpoints to the app.

## Environment Variables

Required:

- `MASSIVE_API_KEY`
- `NEWS_CLICKHOUSE_URL`, defaults to `QMD_CLICKHOUSE_URL`, then `http://localhost:8123`
- `NEWS_CLICKHOUSE_DATABASE`, defaults to `QMD_CLICKHOUSE_DATABASE`, then `q_live`
- `NEWS_CLICKHOUSE_USER`, defaults to `QMD_CLICKHOUSE_USER`, then `default`
- `NEWS_CLICKHOUSE_PASSWORD`, defaults to `QMD_CLICKHOUSE_PASSWORD`
- `NEWS_CLICKHOUSE_STORAGE_POLICY`, defaults to `CLICKHOUSE_LIVE_STORAGE_POLICY`

Common settings:

- `NEWS_GATEWAY_BIND`, default `127.0.0.1:8796`
- `NEWS_BENZINGA_URL`, default `https://api.massive.com/benzinga/v2/news`
- `NEWS_BENZINGA_ENABLED`, default `true`
- `NEWS_BENZINGA_POLL_INTERVAL_MS`, default `5000`
- `NEWS_POLL_LIMIT`, default `1000`
- `NEWS_MAX_PAGES_PER_POLL`, default `5`
- `NEWS_LIVE_LOOKBACK_MINUTES`, default `30`
- `NEWS_POLL_OVERLAP_SECONDS`, default `120`
- `NEWS_EXTRACTION_ENABLED`, default `true`
- `NEWS_EXTRACTION_MIN_BODY_CHARS`, default `300`
- `NEWS_EXTRACTION_TIMEOUT_MS`, default `2500`
- `NEWS_PDF_EXTRACTION_ENABLED`, default `true`
- `NEWS_PDF_MAX_BYTES`, default `10000000`
- `NEWS_CLICKHOUSE_MAX_BATCH`, default `1000`
- `NEWS_CLICKHOUSE_FLUSH_INTERVAL_MS`, default `1000`
- `NEWS_WRITER_CHANNEL_CAPACITY`, default `100000`
- `NEWS_RECENT_HISTORY_LIMIT`, default `5000`
- `NEWS_INTELLIGENCE_ENABLED`, default `true`
- `NEWS_INTELLIGENCE_URL`, default `http://127.0.0.1:8797`
- `NEWS_INTELLIGENCE_TIMEOUT_MS`, default `1500`

PDF text extraction uses the external `pdftotext` command. If it is not installed, PDF rows are still persisted with metadata and `extraction_error`.

## Run

```powershell
.\scripts\run_news_gateway.ps1
```

Check only:

```powershell
.\scripts\run_news_gateway.ps1 -CheckOnly
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

The snapshot and websocket payloads are compact summaries. When the optional
news-intelligence service is reachable, those summaries also include sentiment,
event, materiality, urgency, ticker-impact, and model-version labels. Full text,
raw JSON, and full intelligence outputs are in ClickHouse.

## Persistence Rule

The gateway persists every valid article. It only skips malformed rows that lack a stable provider id/title/time. Duplicates are handled by `ReplacingMergeTree(gateway_seen_at)` using:

```text
ORDER BY (session_date, source, provider_article_id)
```

The canonical source value is `benzinga`. The derivative Massive general news endpoint is intentionally excluded from this service.

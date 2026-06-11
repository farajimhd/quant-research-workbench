# News Gateway

Standalone Rust gateway for Benzinga news served through Massive REST.

The service polls only:

- Benzinga news: `/benzinga/v2/news`

It does not use the separate Massive news endpoint for canonical persistence. Benzinga is the provider; Massive is only the transport/API host for this subscription path.

## Responsibilities

- Poll Benzinga incrementally.
- Save each raw Benzinga provider payload under the same artifact layout used by historical scripts.
- Preserve provider timestamps at the highest precision returned by the API.
- Save `gateway_seen_at` so live provider delay can be measured.
- Normalize every valid Benzinga article, including macro, crypto, no-ticker, title-only, PDF-backed, and link-only articles.
- Keep the legacy live stream table, `live_news_articles`, for current UI compatibility.
- Persist canonical compact rows into split Benzinga tables:
  - `benzinga_news_event_v1`
  - `benzinga_news_text_v1`
  - `benzinga_news_url_v1`
  - `benzinga_news_attachment_v1`
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
- `NEWS_BENZINGA_ARTIFACT_ROOT_WIN`, default `D:/market-data/benzinga_news_canonical`
- `NEWS_BENZINGA_CANONICAL_ENABLED`, default `true`
- `NEWS_BENZINGA_EVENT_TABLE`, default `benzinga_news_event_v1`
- `NEWS_BENZINGA_TEXT_TABLE`, default `benzinga_news_text_v1`
- `NEWS_BENZINGA_URL_TABLE`, default `benzinga_news_url_v1`
- `NEWS_BENZINGA_ATTACHMENT_TABLE`, default `benzinga_news_attachment_v1`
- `NEWS_BENZINGA_CANONICAL_TABLE`, backward-compatible alias for the event table default
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

PDF text extraction uses the external `pdftotext` command. If it is not installed, PDF metadata rows can still be persisted with extraction status.

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

The snapshot and websocket payloads are compact summaries. When the optional news-intelligence service is reachable, those summaries also include sentiment, event, materiality, urgency, ticker-impact, and model-version labels. Full text and raw provider payload references are in ClickHouse and the artifact store.

## Persistence Rule

The gateway first saves the raw provider payload to:

```text
NEWS_BENZINGA_ARTIFACT_ROOT_WIN/raw/YYYY/MM/DD/benzinga_<id>.json
```

It then normalizes the article and queues it for asynchronous batch persistence.

`live_news_articles` remains a compatibility table for the current UI stream. It should not be treated as the training source of truth.

Canonical Benzinga data is split:

- `benzinga_news_event_v1`: one compact metadata/event row per article.
- `benzinga_news_text_v1`: body, external, and PDF text rows keyed by `canonical_news_id`.
- `benzinga_news_url_v1`: article, source, PDF, SEC, social, and other URL rows with policy/extraction metadata.
- `benzinga_news_attachment_v1`: downloaded attachment metadata and extraction quality.

The canonical source value is `benzinga`.

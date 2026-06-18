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
run logs: <data-root>/prepared/news_gateway/logs/<run_id>/news_gateway_events.jsonl
```

## Run Logs

Every gateway run writes an async JSONL status log unless
`NEWS_GATEWAY_RUN_LOG_ENABLED=false` is set. The log is not a copy of the
terminal output. It is structured for debugging and later processing.

Default path:

```text
<data-root>/prepared/news_gateway/logs/<run_id>/news_gateway_events.jsonl
```

Each row includes `ts_utc`, `run_id`, `event`, and event-specific status fields.
The gateway logs operational state only:

- startup, shutdown, and dependency preflight status
- phase changes and timing context
- provider fetch windows, row counts, page counts, and saturation status
- item processing status keyed by provider article id and canonical news id
- raw artifact path/hash, ticker count, warning count, and quality flags
- ClickHouse write summaries
- skipped-existing counts with sampled canonical ids and reason
  `canonical_news_id_exists`
- input duplicate id samples
- coverage bootstrap, compaction, gap planning, gap fill, and live coverage
  writes
- error type/message for provider, processing, write, and startup failures

The logger deliberately does not write titles, body text, extracted text, raw API
payloads, or secret values. Long strings are truncated and keys that look like
credentials are redacted.

Controls:

```text
NEWS_GATEWAY_RUN_LOG_ENABLED=true
NEWS_GATEWAY_LOG_ROOT_WIN=<data-root>/prepared/news_gateway/logs
NEWS_GATEWAY_RUN_LOG_QUEUE_SIZE=10000
NEWS_GATEWAY_RUN_LOG_SKIP_SAMPLE_SIZE=100
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

The gateway uses a coverage manifest, not only the newest news timestamp. The
manifest table records time windows that were successfully fetched and written.
This is required because `max(published_at_utc)` can hide internal holes.

Coverage is stored in:

```text
q_live.benzinga_news_coverage_manifest_v1
```

The table is a `ReplacingMergeTree` ordered by `(source, coverage_start_utc,
coverage_id)`. The gateway inserts replacement rows instead of using ClickHouse
mutations. Query it with `FINAL` when the latest state of a coverage segment
matters.

On startup:

1. Preflight creates the coverage table if it does not already exist.
2. If the coverage table is empty, the gateway discovers historical coverage
   from the existing normalized news table. The default bootstrap treats
   `2010-01-01T00:00:00Z` through `2026-06-01T00:00:00Z` as trusted historical
   coverage because that range was fully downloaded in the historical Benzinga
   backfill. This creates one coverage interval for the trusted range instead
   of thousands of rows split by quiet news hours.
   This historical discovery is not repeated after the manifest has rows unless
   `NEWS_BENZINGA_REBUILD_COVERAGE_MANIFEST=true` is set.
3. For data after the trusted historical end, the gateway splits the normalized
   table range into `NEWS_BENZINGA_COVERAGE_DISCOVERY_CHUNK_SECONDS` buckets,
   currently 300 seconds. Adjacent non-empty buckets become coverage candidates.
4. Empty bucket runs after `NEWS_BENZINGA_BOOTSTRAP_VERIFY_GAPS_AFTER_UTC` are
   checked with a cheap Benzinga provider probe that requests only one row. If
   the provider returns zero rows, the interval is marked covered-empty and is
   merged into neighboring coverage. If the provider returns at least one row,
   the interval remains a real gap so normal startup gap fill can download and
   insert the missing rows.
5. Buckets with zero existing news that are outside the trusted range and are
   not provider-verified remain unknown gaps.
6. The gateway reads all coverage intervals from the manifest.
7. Adjacent or overlapping intervals are merged using
   `NEWS_BENZINGA_POLL_OVERLAP_SECONDS` as tolerance.
8. Gaps between merged coverage intervals are identified.
9. The trailing gap from the last coverage timestamp to current UTC is always
   included in startup planning. That catch-up creates both news rows and
   coverage rows before normal polling continues.

Behavior:

| Situation | Action |
| --- | --- |
| No coverage intervals | Live polling starts with normal lookback. |
| One or more coverage gaps and total gap time is <= 30 days | Service starts concurrent background gap fill for all gaps during startup. |
| One or more coverage gaps, total gap time is > 30 days, and running on the workstation | Service writes and runs the workstation PowerShell gap-fill package automatically. |
| One or more coverage gaps, total gap time is > 30 days, and not running on the workstation | Service writes workstation-ready PowerShell gap-fill scripts and a manifest, prints their paths, and continues live polling. |

During live operation the gateway opens a live coverage segment. It extends that
segment only after a provider window is fetched, normalized, and written without
row-level normalization failures. If provider calls fail long enough that the
next successful window no longer overlaps the prior segment, the gateway closes
the old segment and opens a new one. That prevents a long-running but failing
process from pretending that the failed interval was covered.

Graceful shutdown writes a final replacement row for the live segment with
`status=completed`. If the process is killed, the latest replacement row still
contains the last successfully written `coverage_end_utc`, so the next startup
can see the missing tail.

Manual and automatic provider gap fills fetch provider windows in bounded
chunks, but the coverage manifest is compacted. Automatic startup fills run
multiple chunks concurrently, controlled by
`NEWS_BENZINGA_STARTUP_GAP_FILL_WORKERS`. Coverage is still advanced in
chronological order: the service writes a running coverage row for each
contiguous successful fill run and updates the same `coverage_id` as earlier
chunks are confirmed. If the process crashes, the manifest still records the
latest successfully covered end time. When the run finishes, the same row is
closed as `completed`. This includes provider windows that return zero news
rows. A zero-row covered range means "provider checked this interval and it was
empty", so the gateway will not retry that interval on the next startup.

On startup the gateway also compacts active coverage rows in the table itself.
It does not rely only on read-time merging. Existing active rows are written back
as `superseded`, and merged replacement rows are inserted with source
`coverage_compacted`. The compaction tolerance defaults to the coverage discovery
bucket size, currently 300 seconds. The tolerance is stored in `metadata_json` so a
future audit can tell exactly why two neighboring intervals were treated as one
continuous coverage interval.

Controls:

```text
NEWS_BENZINGA_COVERAGE_COMPACT_ON_STARTUP=true
NEWS_BENZINGA_COVERAGE_COMPACT_TOLERANCE_SECONDS=300
```

Large non-workstation gaps are not auto-filled because the workstation has the
correct storage root and compute. The generated manifest is written under
`market-data`:

```text
<data-root>/prepared/news_gateway_manual_gap_fill/<run_id>/<run_id>_manifest.json
```

Generated PowerShell code is written under the TradingML runtime/code tree:

```text
D:/TradingML/codes/quant_research_workbench_pipelines/generated/news_gateway_manual_gap_fill/<run_id>/<run_id>_run_all.ps1
```

The gateway writes one master `*_run_all.ps1` script plus one child script per
interval. This lets the same mechanism handle non-contiguous gap plans without
asking the user to manually assemble commands.

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
- startup gap status, generated workstation script, manifest, and first command
  when a manual fill is needed
- current operation phase and message, including bootstrap, provider fetch,
  processing, writing, and gap-fill chunks
- recent news table with time, tickers, title, and quality flags

Controls:

```powershell
$env:NEWS_TERMINAL_RICH_ENABLED="auto"   # default
$env:NEWS_TERMINAL_RICH_ENABLED="true"   # force on
$env:NEWS_TERMINAL_RICH_ENABLED="false"  # disable
$env:NEWS_TERMINAL_SCREEN_ENABLED="true" # default; render in alternate screen to reduce flicker
$env:NEWS_TERMINAL_REFRESH_SECONDS="1"
$env:NEWS_TERMINAL_NEWS_LIMIT="12"
```

When Rich is enabled, routine gateway status messages are written to the JSONL
run log and shown in the dashboard instead of being printed directly to stdout.
This prevents normal log lines from fighting Rich's live render.

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
NEWS_BENZINGA_COVERAGE_TABLE=benzinga_news_coverage_manifest_v1
```

Credential fallback order is `NEWS_*`, `QLIVE_MIGRATION_*`, `QMD_*`,
`REAL_LIVE_*`, `CLICKHOUSE_WORKSTATION_*`, then plain `CLICKHOUSE_*`.

Storage:

```text
NEWS_GATEWAY_DATA_ROOT_WIN=<optional explicit root>
NEWS_BENZINGA_RAW_ROOT_WIN=<optional explicit raw root>
NEWS_BENZINGA_PREPARED_ROOT_WIN=<optional explicit prepared root>
NEWS_BENZINGA_MANUAL_GAP_MANIFEST_ROOT_WIN=<optional explicit manifest root>
NEWS_BENZINGA_MANUAL_GAP_SCRIPT_ROOT_WIN=<optional explicit script/code root>
NEWS_GATEWAY_WORKSTATION_CODE_ROOT_WIN=D:/TradingML/codes/quant_research_workbench_pipelines
NEWS_GATEWAY_WORKSTATION_CONDA_ENV=ml4t
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
NEWS_BENZINGA_STARTUP_AUTO_FILL_MAX_GAP_DAYS=30
NEWS_BENZINGA_COVERAGE_DISCOVERY_CHUNK_SECONDS=300
NEWS_BENZINGA_REBUILD_COVERAGE_MANIFEST=false
NEWS_BENZINGA_BOOTSTRAP_TRUSTED_COVERAGE_START_UTC=2010-01-01T00:00:00Z
NEWS_BENZINGA_BOOTSTRAP_TRUSTED_COVERAGE_END_UTC=2026-06-01T00:00:00Z
NEWS_BENZINGA_BOOTSTRAP_VERIFY_GAPS_AFTER_UTC=2024-01-01T00:00:00Z
NEWS_BENZINGA_BOOTSTRAP_PROBE_RECENT_GAPS=true
NEWS_BENZINGA_BOOTSTRAP_PROBE_PROGRESS_INTERVAL=25
NEWS_BENZINGA_GAP_FILL_CHUNK_MINUTES=90
NEWS_BENZINGA_STARTUP_GAP_FILL_WORKERS=4
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
NEWS_TERMINAL_SCREEN_ENABLED=true
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

Startup fails before polling. The coverage-manifest query uses the same
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

The launcher resolves the target conda environment's `python.exe` and runs it
directly when possible, instead of wrapping the service in `conda run`. This
keeps Ctrl+C delivery reliable in PowerShell. Uvicorn then stops the app, the
gateway sets its stop event, and the polling, gap-fill, and terminal dashboard
tasks are cancelled with a bounded graceful shutdown timeout.

## Historical Gap Fill

Use this when the service prints a manual large-gap command or when you want to
backfill a known period:

```powershell
python -m pipelines.news.benzinga.news_benzinga_provider_gap_fill --start-utc 2026-06-01T00:00:00Z --end-utc 2026-06-04T00:00:00Z --raw-root-win D:/market-data/news-benzinga/raw --bucket-minutes 90 --workers 4 --batch-size 1000 --progress-interval 10 --execute
```

For service-detected large gaps from a laptop run, prefer the generated `.ps1`
script shown in `/metrics` and the terminal dashboard. It runs from the
workstation code root, uses `conda run --no-capture-output -n ml4t`, writes raw
payloads under `D:/market-data/news-benzinga/raw`, and inserts normalized rows
into ClickHouse with `--execute`.

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

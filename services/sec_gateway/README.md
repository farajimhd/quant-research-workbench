# Python SEC Gateway

The SEC gateway is the live service layer for SEC filings. It is intentionally
Python because SEC ingestion is network, text parsing, ClickHouse, and audit
heavy rather than low-latency tick processing.

## Runtime Flow

```text
start service
-> load .env files
-> run dependency preflight
-> validate q_live as the SEC source-of-truth read database
-> create/validate the configured SEC write database
-> clone SEC write-table schemas from q_live when the write database is empty
-> create/validate the write database sec_coverage_manifest_v1
-> bootstrap write-database coverage from existing q_live SEC tables when the manifest is empty
-> detect filing/text/XBRL coverage gaps from the manifest, falling back to q_live recency only when needed
-> write workstation historical-fill command for old gaps, including repair and audit steps
-> poll SEC current Atom feed
-> enqueue new accessions into a bounded live worker pool
-> fetch SEC submissions JSON for each discovered CIK/accession
-> canonicalize parent filing metadata from submissions.recent
-> download new accession .txt filings
-> parse SGML documents with the shared SEC text normalizer
-> fetch SEC companyfacts for filings that expose XBRL or inline-XBRL documents
-> write sec_filing_v2/document_v2/text_v2/skip rows to the configured write database
-> write sec_xbrl_* rows to the configured write database when matching companyfacts are available
-> audit the write database for duplicate and orphan SEC rows
-> keep one live-run coverage row current, including empty/all-duplicate polls
-> show Rich terminal status and expose HTTP/websocket snapshots
```

The gateway does not own global ticker/reference mappings. It writes raw SEC
filing, document, text, skip, and XBRL rows only. `reference_gateway` maintains
`q_live.id_sec_market_bridge_v1`, and `text_embed_gateway` reads that bridge to
refresh ticker-aligned SEC text context before token and embedding writes.

## Run

From the workstation runtime:

```powershell
Set-Location D:\TradingML\codes\quant_research_workbench_pipelines
.\scripts\run_sec_gateway.ps1 -CheckOnly
```

```powershell
Set-Location D:\TradingML\codes\quant_research_workbench_pipelines
.\scripts\run_sec_gateway.ps1
```

Direct Python command:

```powershell
Set-Location D:\TradingML\codes\quant_research_workbench_pipelines
python -m services.sec_gateway.main --check-only
```

## Important Environment Variables

```text
SEC_USER_AGENT
REAL_LIVE_CLICKHOUSE_WRITE_URL
REAL_LIVE_CLICKHOUSE_WRITE_USER
REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD
CLICKHOUSE_LIVE_STORAGE_POLICY
SEC_CLICKHOUSE_READ_DATABASE=q_live
SEC_CLICKHOUSE_WRITE_DATABASE=q_live
SEC_GATEWAY_BIND=127.0.0.1:8797
SEC_GATEWAY_DATA_ROOT_WIN=D:/market-data
SEC_GATEWAY_POLL_SECONDS=30
SEC_GATEWAY_CLOSED_POLL_SECONDS=300
SEC_GATEWAY_LIVE_WORKERS=4
SEC_GATEWAY_LIVE_QUEUE_MAX_ITEMS=500
SEC_GATEWAY_SUBMISSIONS_CACHE_ENTRIES=512
SEC_GATEWAY_SUBMISSIONS_CACHE_MAX_AGE_SECONDS=3600
SEC_GATEWAY_XBRL_PAYLOAD_CACHE_ENTRIES=32
SEC_GATEWAY_XBRL_PAYLOAD_CACHE_MAX_AGE_SECONDS=3600
SEC_GATEWAY_XBRL_MISSING_CIK_CACHE_ENTRIES=5000
SEC_GATEWAY_RECENT_METADATA_RETENTION_HOURS=24
SEC_GATEWAY_FULL_AUDIT_ON_STARTUP=true
SEC_GATEWAY_FULL_AUDIT_AFTER_WRITE_BATCHES=0
SEC_GATEWAY_COLLECTION_START_ET=04:00
SEC_GATEWAY_COLLECTION_END_ET=20:00
SEC_MARKET_STATUS_URL=https://api.massive.com/v1/marketstatus/now
SEC_MARKET_HOLIDAYS_URL=https://api.massive.com/v1/marketstatus/upcoming
SEC_MARKET_STATUS_ENABLED=true
SEC_MARKET_STATUS_REFRESH_SECONDS=10
SEC_MARKET_HOLIDAYS_REFRESH_SECONDS=3600
SEC_REQUEST_MIN_INTERVAL_SECONDS=0.12
SEC_REQUEST_TRANSIENT_ERROR_COOLDOWN_SECONDS=60
SEC_REQUEST_RATE_LIMIT_COOLDOWN_SECONDS=300
SEC_GATEWAY_AUTO_RUN_HISTORICAL_ON_WORKSTATION=true
```

The default gateway mode is production write-through:

```text
read database:  q_live
write database: q_live
```

That means live SEC feed rows, coverage rows, write-audit checks, and generated
historical gap-fill scripts use `q_live` unless explicitly overridden. For a
temp smoke test, override only the write database:

```text
SEC_CLICKHOUSE_READ_DATABASE=q_live
SEC_CLICKHOUSE_WRITE_DATABASE=q_sec_tmp
```

## Memory Bounds

The live gateway caches SEC submissions JSON and SEC companyfacts JSON by CIK to
avoid repeated requests when multiple filings arrive for the same company. These
caches are bounded by both count and age because companyfacts payloads can be
large:

```text
SEC_GATEWAY_SUBMISSIONS_CACHE_ENTRIES=512
SEC_GATEWAY_SUBMISSIONS_CACHE_MAX_AGE_SECONDS=3600
SEC_GATEWAY_XBRL_PAYLOAD_CACHE_ENTRIES=32
SEC_GATEWAY_XBRL_PAYLOAD_CACHE_MAX_AGE_SECONDS=3600
SEC_GATEWAY_XBRL_MISSING_CIK_CACHE_ENTRIES=5000
SEC_GATEWAY_RECENT_METADATA_RETENTION_HOURS=24
```

The Rich terminal and `/health` metrics expose the current cache counts and
limits. Filing text and extracted text are not retained in memory after a live
job is written. The longer recent metadata window is for the trading app and
terminal display only. A running gateway must be restarted to pick up changed
limits.

When the gateway is started on the workstation and historical gaps are found,
it writes the exact historical-fill PowerShell script under:

```text
D:/TradingML/codes/quant_research_workbench_pipelines/generated/sec_gateway_manual_gap_fill/
```

When the gateway is started from another machine, it writes the same script
through the workstation share:

```text
\\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\quant_research_workbench_pipelines\generated\sec_gateway_manual_gap_fill\
```

The terminal still reports the workstation-local `D:/TradingML/...` path, so
the command can be copied into a workstation PowerShell session without editing.

Before writing the script, the gateway syncs the historical-fill runtime
dependencies from the current repo into the workstation code root:

```text
pipelines/sec/edgar/
research/mlops/
pipelines/__init__.py
pipelines/sec/__init__.py
research/__init__.py
```

This keeps generated gap-fill scripts tied to the code that generated them and
prevents stale workstation runtime copies from running old SEC ingest logic. The
sync is targeted and non-destructive: it overwrites those dependency files, but
does not delete generated scripts, data, logs, secrets, or unrelated runtime
folders.

With `SEC_GATEWAY_AUTO_RUN_HISTORICAL_ON_WORKSTATION=true`, the gateway starts
that script automatically only when it is running on the workstation outside the
active collection window, which defaults to `04:00-20:00 ET`. During the active
collection window it still generates the script, but it defers auto-run so live
collection is not competing with historical backfill. From a laptop or other
remote host, it only writes the script and reports the command in the Rich
terminal and HTTP metrics.

The generated script runs the unified historical gap-fill command and then
appends one final explicit audit command:

```text
sec_historical_gap_fill.py
sec_integrity_audit.py
```

The script is intentionally self-contained. The gateway writes the resolved
read database, write database, coverage table, workstation data roots, output
roots, worker counts, SEC request pacing, retry policy, text limits, and
`--resume-from-coverage` into the command. Operators should not add missing
arguments by hand; changing the generated command can make the coverage
manifest disagree with the files and tables produced by the run.

The unified fill command first refreshes SEC bulk `submissions` and
`companyfacts`, mirrors those bulk files into `sec_core`, derives canonical
filing parents and XBRL rows from that mirror, then performs archive download,
validation, text extraction, ClickHouse insert, API fallback for missing recent
XBRL, XBRL relationship repair, audit, and coverage writes. The final audit
command gives the operator a short post-run verification surface before the
result is trusted.

## Coverage

Coverage is stored in:

```text
<SEC_CLICKHOUSE_WRITE_DATABASE>.sec_coverage_manifest_v1
```

Coverage kinds include:

- `sec_live_feed`
- `sec_daily_archive`
- `sec_bulk_submissions`
- `sec_bulk_companyfacts`
- `sec_text_extraction`
- `sec_integrity_audit`
- `sec_stage_<stage_name>` for resumable historical gap-fill stages

The gateway updates one live coverage row for the whole service run. The row is
opened on the first successful poll and its end time is updated after every
successful feed fetch, including empty polls and polls where every accession is
already present. This avoids repeatedly rechecking a window that was already
observed. Graceful shutdown marks the row `completed`.

Historical coverage is interval based. SEC filings are sparse, so the gateway
does not infer a gap just because a short time bucket has zero filings. It uses
coverage rows written by historical/live jobs first and only falls back to
source-table recency when a coverage kind has no manifest rows yet.

Historical gap-fill also writes stage-level coverage after each successful
stage. If a later stage fails, the next run can skip the successful completed
stages for the same date range and continue from the failed stage. Final
semantic coverage rows such as `sec_text_extraction` and
`sec_bulk_companyfacts` are written only after the full unified gap-fill command
finishes without failed stages.

## After-Hours Maintenance

The SEC Gateway owns its own coverage checks, current feed repair, and
historical gap-fill planning. It audits `q_live.sec_coverage_manifest_v1` with
the same SEC coverage planner used by live startup. If filing, text, or XBRL
coverage is stale, it generates the unified historical fill command:

```text
pipelines/sec/edgar/sec_historical_gap_fill.py
```

That command is the canonical historical repair path. It downloads or reuses SEC
daily archives, extracts filing parents and normalized filing text, refreshes
companyfacts/XBRL, repairs XBRL relationships, runs the integrity audit, and
writes coverage.

When the coverage manifest is empty, the gateway bootstraps one compact
`sec_historical_baseline` row from the existing source-of-truth SEC tables. That
baseline starts at `2019-01-01` and ends at the conservative latest timestamp
supported by filing parents, filing text, and XBRL companyfacts. The gateway does
not plan historical backfills before `2019-01-01`.

## Write Database Audit

Preflight creates the write database if needed, clones these schemas from the
read database, and validates them before polling starts:

```text
sec_filing_v2
sec_filing_document_v2
sec_filing_text_v2
sec_filing_document_skip_v1
sec_xbrl_concept_v1
sec_xbrl_company_fact_v1
sec_xbrl_frame_v1
sec_xbrl_frame_observation_v1
```

The gateway write audit checks:

- duplicate `(cik, accession_number)` filing parents
- document rows without filing parents
- text rows without matching document rows
- text rows without filing parents
- XBRL company facts without filing parents
- XBRL frame observations without matching company facts
- XBRL frame observations without a frame parent, matched by the natural frame
  key `(taxonomy, tag, unit_code, calendar_period_code)`

The latest audit status appears in `/metrics` under `audit_status` and
`audit_message`.

Full audits can be expensive against `q_live`. Keep startup audits enabled, but
use `SEC_GATEWAY_FULL_AUDIT_AFTER_WRITE_BATCHES=0` unless you explicitly want
periodic full-table audits after live writes.

## Live Worker Queue

The feed poller is intentionally lightweight. It fetches the Atom feed,
identifies accessions already present in the write database, and queues only new
accessions. Worker tasks then download accession text, fetch submissions and
companyfacts, parse documents, extract text, write ClickHouse rows, and update
in-memory metrics.

Shutdown is graceful: the service stops polling, waits for queued workers to
finish up to `SEC_GATEWAY_GRACEFUL_SHUTDOWN_SECONDS`, writes final coverage, and
then exits. If the timeout is exceeded, the event is logged in the run JSONL log.

The submissions and companyfacts SEC API responses are cached per gateway run by
CIK. This reduces duplicate SEC requests when a feed poll contains multiple
filings for the same company.

All SEC network calls share one process-local limiter. Normal requests are
spaced by `SEC_REQUEST_MIN_INTERVAL_SECONDS`; transient provider failures add a
global cooldown before any thread can make another SEC request:

```text
timeout, connection reset, DNS/URL transient, SEC 5xx:
  SEC_REQUEST_TRANSIENT_ERROR_COOLDOWN_SECONDS

SEC 403 or 429:
  SEC Retry-After header if present, otherwise SEC_REQUEST_RATE_LIMIT_COOLDOWN_SECONDS
```

During cooldown the poll loop reports `provider_cooldown`, does not start a new
feed poll, and live workers also wait before their next SEC request. This avoids
turning a timeout into a tight retry loop that could violate SEC pacing.

## Poll Cadence

The SEC gateway uses the shared service market-hours policy when
`SEC_MARKET_STATUS_ENABLED=true`. Massive market status
(`/v1/marketstatus/now`) supplies the current active/closed state, and Massive
market holidays (`/v1/marketstatus/upcoming`) supply full closures and early
closes. Premarket and after-hours are treated as active trading sessions:

```text
active/premarket/after-hours: SEC_GATEWAY_POLL_SECONDS
closed:                       SEC_GATEWAY_CLOSED_POLL_SECONDS
```

If Massive market status is unavailable, the gateway falls back to the local New
York extended-hours clock, using 04:00-20:00 ET as active.

## Live XBRL

The SEC Atom feed is only the low-latency discovery source. It does not contain
canonical filing metadata or companyfacts rows. For each feed accession, the
gateway first fetches:

```text
https://data.sec.gov/submissions/CIK##########.json
```

It finds the accession in `filings.recent` and uses that row to canonicalize:

- form type
- filing date
- report date
- accepted timestamp
- primary document
- filing size
- filing items
- XBRL / inline-XBRL flags

For filings that submissions or the accession document set identifies as
XBRL-bearing, the gateway then fetches:

```text
https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json
```

It filters facts to the exact accession and writes:

- `sec_xbrl_concept_v1`
- `sec_xbrl_company_fact_v1`
- `sec_xbrl_frame_v1`
- `sec_xbrl_frame_observation_v1`

Ownership XML filings such as Forms 3/4/5 are still recorded as structured
documents and skip rows, but they do not create companyfacts XBRL rows unless SEC
companyfacts exposes matching financial facts for that accession.

SEC does not expose `companyfacts` JSON for every CIK. A companyfacts `404` is
treated as `missing_404`, cached for that CIK during the gateway run, and does
not fail live filing ingestion. The filing, document, text and skip rows are
still written when the accession itself can be downloaded and parsed.

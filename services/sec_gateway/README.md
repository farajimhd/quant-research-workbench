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
-> detect filing/text/XBRL freshness gaps from q_live
-> write workstation historical-fill command for old gaps
-> poll SEC current Atom feed
-> download new accession .txt filings
-> parse SGML documents with the shared SEC text normalizer
-> fetch SEC companyfacts for filings that expose XBRL or inline-XBRL documents
-> write sec_filing_v2/document_v2/text_v2/skip rows to the configured write database
-> write sec_xbrl_* rows to the configured write database when matching companyfacts are available
-> audit the write database for duplicate and orphan SEC rows
-> update live feed coverage
-> show Rich terminal status and expose HTTP/websocket snapshots
```

The gateway does not own global ticker/reference mappings. SEC-sourced ticker
mapping files belong to the future reference-data service.

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
SEC_CLICKHOUSE_WRITE_DATABASE=q_sec_tmp
SEC_GATEWAY_BIND=127.0.0.1:8797
SEC_GATEWAY_DATA_ROOT_WIN=D:/market-data
SEC_GATEWAY_POLL_SECONDS=30
SEC_GATEWAY_CLOSED_POLL_SECONDS=300
SEC_MARKET_STATUS_URL=https://api.massive.com/v1/marketstatus/now
SEC_MARKET_STATUS_ENABLED=true
SEC_MARKET_STATUS_REFRESH_SECONDS=10
SEC_REQUEST_MIN_INTERVAL_SECONDS=0.12
SEC_GATEWAY_AUTO_RUN_HISTORICAL_ON_WORKSTATION=true
```

The default gateway mode is a sandbox:

```text
read database:  q_live
write database: q_sec_tmp
```

That keeps the migrated `q_live` tables as the reference source while new live
gateway rows, coverage rows, and write-audit checks land in `q_sec_tmp`. After
the temp data has been audited and accepted, production mode is only a config
change:

```text
SEC_CLICKHOUSE_READ_DATABASE=q_live
SEC_CLICKHOUSE_WRITE_DATABASE=q_live
```

When the gateway is started on the workstation and historical gaps are found,
it writes the exact historical-fill PowerShell script under:

```text
D:/TradingML/codes/quant_research_workbench_pipelines/generated/sec_gateway_manual_gap_fill/
```

With `SEC_GATEWAY_AUTO_RUN_HISTORICAL_ON_WORKSTATION=true`, the gateway starts
that script automatically. From a laptop or other remote host, it only writes
the script and reports the command in the Rich terminal and HTTP metrics.

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

The gateway updates live coverage only after a poll window is fetched and rows
are written or confirmed as existing.

## Temp Database Audit

Preflight creates the temp database if needed, clones these schemas from the
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

The latest audit status appears in `/metrics` under `audit_status` and
`audit_message`.

## Poll Cadence

The SEC gateway uses Massive market status when `SEC_MARKET_STATUS_ENABLED=true`.
Premarket and after-hours are treated as active trading sessions:

```text
active/premarket/after-hours: SEC_GATEWAY_POLL_SECONDS
closed:                       SEC_GATEWAY_CLOSED_POLL_SECONDS
```

If Massive market status is unavailable, the gateway falls back to the local New
York extended-hours clock, using 04:00-20:00 ET as active.

## Live XBRL

The SEC Atom feed itself does not contain companyfacts rows. For a feed item that
contains XBRL sidecars or inline-XBRL content, the gateway fetches:

```text
https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json
```

It then filters facts to the feed accession and writes:

- `sec_xbrl_concept_v1`
- `sec_xbrl_company_fact_v1`
- `sec_xbrl_frame_v1`
- `sec_xbrl_frame_observation_v1`

Ownership XML filings such as Forms 3/4/5 are still recorded as structured
documents and skip rows, but they do not create companyfacts XBRL rows unless SEC
companyfacts exposes matching financial facts for that accession.

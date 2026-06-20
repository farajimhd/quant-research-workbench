# Python SEC Gateway

The SEC gateway is the live service layer for SEC filings. It is intentionally
Python because SEC ingestion is network, text parsing, ClickHouse, and audit
heavy rather than low-latency tick processing.

## Runtime Flow

```text
start service
-> load .env files
-> run dependency preflight
-> create/validate q_live.sec_coverage_manifest_v1
-> bootstrap coverage from existing SEC tables when the manifest is empty
-> detect filing/text/XBRL freshness gaps
-> write workstation historical-fill command for old gaps
-> poll SEC current Atom feed
-> download new accession .txt filings
-> parse SGML documents with the shared SEC text normalizer
-> write q_live sec_filing_v2/document_v2/text_v2/skip rows
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
SEC_GATEWAY_BIND=127.0.0.1:8797
SEC_GATEWAY_DATA_ROOT_WIN=D:/market-data
SEC_GATEWAY_POLL_SECONDS=30
SEC_GATEWAY_CLOSED_POLL_SECONDS=300
SEC_REQUEST_MIN_INTERVAL_SECONDS=0.12
SEC_GATEWAY_AUTO_RUN_HISTORICAL_ON_WORKSTATION=true
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
q_live.sec_coverage_manifest_v1
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

# Benzinga Historical Gap-Fill Orchestrator

This runbook records the successful manual flow used to load historical Benzinga news into `q_live.benzinga_news_normalized_v1`, then wraps that flow in one orchestrator.

The source of truth for the working manual commands was:

```text
\\DESKTOP-SAAI85T\Workstation-D\market-data\prepared\workstation_powershell_history_sec_q_live_news.txt
```

The saved history contained the successful URL download, normalized-row build, and ClickHouse ingest commands. The earlier raw-download, URL-inventory, and URL-fetch-plan stages were reconstructed from the current pipeline guides and script defaults.

## Orchestrator

Script:

```text
pipelines/news/benzinga/news_benzinga_historical_gap_fill.py
```

Plan only:

```powershell
python //DESKTOP-SAAI85T/Workstation-D/TradingML/codes/quant_research_workbench_pipelines/pipelines/news/benzinga/news_benzinga_historical_gap_fill.py --start-utc 2026-06-01 --end-utc 2026-06-02
```

Run the full historical gap fill and write to ClickHouse:

```powershell
python //DESKTOP-SAAI85T/Workstation-D/TradingML/codes/quant_research_workbench_pipelines/pipelines/news/benzinga/news_benzinga_historical_gap_fill.py --start-utc 2026-06-01 --end-utc 2026-06-02 --execute-db --yes
```

Run only through normalization, without database writes:

```powershell
python //DESKTOP-SAAI85T/Workstation-D/TradingML/codes/quant_research_workbench_pipelines/pipelines/news/benzinga/news_benzinga_historical_gap_fill.py --start-utc 2026-06-01 --end-utc 2026-06-02 --to-stage build_normalized_rows --yes
```

Resume from URL download:

```powershell
python //DESKTOP-SAAI85T/Workstation-D/TradingML/codes/quant_research_workbench_pipelines/pipelines/news/benzinga/news_benzinga_historical_gap_fill.py --start-utc 2026-06-01 --end-utc 2026-06-02 --from-stage url_download --execute-db --yes
```

Each orchestrator run writes:

```text
D:/market-data/prepared/benzinga_news_historical_gap_fill/<run_id>/
```

Files:

- `benzinga_news_historical_gap_fill_manifest.json`: arguments, planned commands, stage results.
- `benzinga_news_historical_gap_fill_stages.jsonl`: one status row per completed stage.

## Stage Succession

### 1. Raw Benzinga Download

Downloads raw Benzinga provider JSON into:

```text
D:/market-data/news-benzinga/raw/YYYY/MM/DD/
```

The raw downloader only accepts `--start-utc`, `--end-utc`, and `--download-processes`, so the orchestrator sets `NEWS_BENZINGA_ARTIFACT_ROOT_WIN=D:/market-data/news-benzinga` in the child environment.

Equivalent command shape:

```powershell
python .../news_benzinga_raw_download.py --start-utc <START> --end-utc <END> --download-processes 32
```

### 2. Scope Raw Files

The manual historical run processed the full raw corpus. A gap-fill run must not do that. The orchestrator therefore creates a run-local scoped raw tree before URL inventory:

```text
D:/market-data/prepared/benzinga_news_historical_gap_fill/<run_id>/raw_scope/raw/YYYY/MM/DD/
```

It hardlinks raw JSON files whose `published` timestamp is inside `[start_utc, end_utc)`. If hardlinks are unavailable, it copies the file. This keeps the central raw archive intact while ensuring later stages only see the requested date range.

### 3. URL Inventory

Scans raw news files and builds URL occurrence rows.

Equivalent command:

```powershell
python .../news_benzinga_url_inventory.py --raw-root-win D:/market-data/prepared/benzinga_news_historical_gap_fill/<run_id>/raw_scope --output-root-win D:/market-data/prepared/benzinga_news_url_inventory --processes 32 --chunk-size 1000
```

### 4. URL Fetch Plan

Deduplicates actionable URLs and applies the deterministic domain policy.

Equivalent command:

```powershell
python .../news_benzinga_url_fetch_plan.py --inventory-root-win D:/market-data/prepared/benzinga_news_url_inventory --output-root-win D:/market-data/prepared/benzinga_news_url_fetch_plan --shards 256 --progress-interval 1000000
```

### 5. URL Download

This command came from the successful workstation history.

Equivalent command:

```powershell
python .../news_benzinga_url_download.py --fetch-plan-root-win D:/market-data/prepared/benzinga_news_url_fetch_plan --output-root-win D:/market-data/prepared/benzinga_news_url_download --artifact-root-win D:/market-data/news_benzinga_url_download_artifacts --network-concurrency 128 --max-pending-futures 512 --per-domain-min-interval-seconds 0.02 --timeout-seconds 5 --max-retries 0 --progress-interval 5000 --heartbeat-seconds 15 --flush-interval 500 --resume
```

### 6. Build Normalized Rows

This command came from the successful workstation history. The final successful version used `32` normalization workers and `32` inline extraction workers.

Equivalent command:

```powershell
python .../news_benzinga_build_normalized_rows.py --raw-root-win D:/market-data/prepared/benzinga_news_historical_gap_fill/<run_id>/raw_scope/raw --fetch-plan-root-win D:/market-data/prepared/benzinga_news_url_fetch_plan --download-root-win D:/market-data/prepared/benzinga_news_url_download --extraction-root-win D:/market-data/prepared/benzinga_news_url_extraction --output-root-win D:/market-data/prepared/benzinga_news_normalized_rows --processes 32 --max-pending-futures 96 --inline-extraction-processes 32 --text-limit-chars 50000 --max-enriched-text-chars-per-url 24000 --max-enriched-urls-per-article 5 --rows-per-file 100000 --max-output-file-bytes 268435456 --progress-interval 25000 --inline-extraction-progress-interval 5000 --flush-interval 1000
```

### 7. ClickHouse Preflight

This command came from the successful workstation history.

Equivalent command:

```powershell
python .../news_benzinga_clickhouse_file_ingest.py --manifest-root-win D:/market-data/prepared/benzinga_news_normalized_rows --parts-root-win D:/market-data --parts-root-ch /mnt/d/market-data --preflight-only
```

### 8. ClickHouse Ingest

This command came from the successful workstation history. The orchestrator uses `--manifest-root-win` so it loads the latest normalized run created by the current gap-fill run.

Equivalent command:

```powershell
python .../news_benzinga_clickhouse_file_ingest.py --manifest-root-win D:/market-data/prepared/benzinga_news_normalized_rows --parts-root-win D:/market-data --parts-root-ch /mnt/d/market-data --execute
```

### 9. Ticker Link Rebuild

After inserting normalized rows, rebuild the ticker/time join table from the normalized source table.

Equivalent command:

```powershell
python .../news_benzinga_ticker_links.py --execute --rebuild
```

This is intentionally simple for now. The first historical backfill rebuilt `4,309,119` ticker rows in about six seconds, so a full rebuild is acceptable until we implement period-scoped ticker-link maintenance.

## Notes

- Use `--execute-db` only when the date range is intentionally ready to insert into ClickHouse.
- Without `--execute-db`, the ClickHouse ingest stage prints a dry-run insert and the ticker-link stage runs as an audit/dry-run.
- The orchestrator is a faithful runner around the known-good scripts. The next redesign should extract shared modules so historical gap fill and live ingestion use the same provider, normalization, URL policy, extraction, and ClickHouse code paths directly.

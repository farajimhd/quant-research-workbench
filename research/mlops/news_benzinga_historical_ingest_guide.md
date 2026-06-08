# Benzinga Historical News Ingest Guide

This guide runs the canonical Benzinga historical news pipeline:

1. Download Benzinga news from the Massive-hosted Benzinga endpoint.
2. Save raw provider payloads and optional PDF artifacts to disk.
3. Queue downloaded raw files for normalization.
4. Queue normalized rows for batched ClickHouse insertion.

Normalized rows are not saved as local files. They are inserted into ClickHouse only.

## Script

Laptop repo path:

```powershell
python D:\TradingCodes\quant-research-workbench\research\mlops\news_benzinga_historical_ingest.py
```

Workstation runtime path:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\news_benzinga_historical_ingest.py
```

Use the workstation path for the full redownload because it can use the workstation CPU, network, and disk layout.

## What Gets Written

Raw downloaded provider payloads:

```text
--artifact-root-win\raw\YYYY\MM\DD\benzinga_<id>.json
```

Optional PDF artifacts:

```text
--artifact-root-win\pdfs\YYYY\MM\DD\<id>\*.pdf
```

Run reports:

```text
--output-root-win\benzinga_historical_ingest_<run_id>.jsonl
```

The report includes compact `extraction_event` records for external article/PDF work. These records are for audit/debug only and are not inserted into the normalized news table.

ClickHouse tables:

```text
<database>.benzinga_news_normalized_v1
<database>.benzinga_news_ingest_manifest_v1
```

The live `services/news-gateway` writes to the same normalized table by default. Historical and live rows are deduplicated by `(published_date, provider_article_id)` through `ReplacingMergeTree(updated_at_utc)`.

PDF downloads never decide whether a news row is kept. Every valid Benzinga news row is normalized even when a linked PDF is too large, low-value, unavailable, or queued for offline handling. PDF decisions are stored in `pdf_metadata_json`.

## Required Environment

The script loads `.env` through the shared MLOps environment discovery. Required values:

```text
MASSIVE_API_KEY
REAL_LIVE_CLICKHOUSE_WRITE_URL
REAL_LIVE_CLICKHOUSE_WRITE_DATABASE
REAL_LIVE_CLICKHOUSE_WRITE_USER
REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD
CLICKHOUSE_LIVE_STORAGE_POLICY
```

Recommended for SEC PDF fetching:

```text
SEC_EDGAR_USER_AGENT
```

Set this to a descriptive application name and contact email, for example `QuantResearchWorkbench/1.0 your_email@example.com`. The script falls back to the normal browser user-agent when it is absent, but full SEC-heavy runs should set it explicitly.

If ClickHouse does not require a user/password in the current environment, those values can be absent.

Install the Python dependencies in the workstation environment before a PDF-enabled run:

```powershell
python -m pip install -r \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\requirements.txt
```

## Recommended First Run

Dry-run only. This validates env loading, date parsing, bucket construction, target database, and storage policy. It does not download or insert.

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\news_benzinga_historical_ingest.py --dry-run --start-utc 2026-01-01T00:00:00Z --end-utc 2026-01-02T00:00:00Z --bucket-minutes 90 --limit 1000 --max-pages 1000
```

Small API smoke test. This downloads and normalizes one tiny bucket, writes raw payloads if rows exist, but does not insert into ClickHouse.

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\news_benzinga_historical_ingest.py --no-insert --start-utc 2026-01-01T00:00:00Z --end-utc 2026-01-01T00:05:00Z --bucket-minutes 90 --limit 1000 --max-pages 1000 --limit-buckets 1 --download-processes 1 --no-fetch-external --no-extract-pdfs
```

Small insert test. This creates tables and inserts normalized rows for a short period. Use a tiny insert batch to validate the staged path.

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\news_benzinga_historical_ingest.py --start-utc 2026-01-01T00:00:00Z --end-utc 2026-01-01T01:00:00Z --bucket-minutes 90 --limit 1000 --max-pages 1000 --limit-buckets 4 --download-processes 2 --normalize-processes 2 --insert-concurrency 2 --insert-batch-rows 25 --manifest-batch-rows 25 --no-fetch-external --no-extract-pdfs
```

## Full Historical Run

The script uses a base-first asynchronous enrichment pipeline in one invocation:

```text
download raw payloads
base-normalize rows without external/PDF fetches
insert base rows quickly
queue only rows needing external/PDF enrichment
insert enriched replacement rows later in the same run
```

The provider pass is still one pass over Massive/Benzinga. External article/PDF rate limits affect only the enrichment lane, not the base row insertion lane.

Fast one-pass run:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\news_benzinga_historical_ingest.py --start-utc 2010-01-01T00:00:00Z --end-utc 2026-06-08T00:00:00Z --bucket-minutes 90 --limit 1000 --max-pages 1000 --download-processes 24 --normalize-processes 12 --enrichment-processes 4 --insert-concurrency 8 --insert-batch-rows 10000 --manifest-batch-rows 2000 --external-request-min-interval-seconds 1.0 --benzinga-request-min-interval-seconds 1.25 --sec-request-min-interval-seconds 0.25 --external-max-retries 4 --external-retry-base-seconds 1.5
```

If Massive and ClickHouse remain stable, increase the base lane while keeping enrichment controlled:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\news_benzinga_historical_ingest.py --start-utc 2010-01-01T00:00:00Z --end-utc 2026-06-08T00:00:00Z --bucket-minutes 90 --limit 1000 --max-pages 1000 --download-processes 32 --normalize-processes 16 --enrichment-processes 4 --insert-concurrency 8 --insert-batch-rows 10000 --manifest-batch-rows 2000 --external-request-min-interval-seconds 1.0 --benzinga-request-min-interval-seconds 1.25 --sec-request-min-interval-seconds 0.25 --external-max-retries 4 --external-retry-base-seconds 1.5
```

If any provider starts returning rate limits, keep the base lane high and reduce only enrichment pressure:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\news_benzinga_historical_ingest.py --start-utc 2010-01-01T00:00:00Z --end-utc 2026-06-08T00:00:00Z --bucket-minutes 90 --limit 1000 --max-pages 1000 --download-processes 24 --normalize-processes 12 --enrichment-processes 2 --insert-concurrency 8 --insert-batch-rows 10000 --manifest-batch-rows 2000 --external-request-min-interval-seconds 2.0 --benzinga-request-min-interval-seconds 3.0 --sec-request-min-interval-seconds 0.5 --external-max-retries 5 --external-retry-base-seconds 2.0
```

## Argument Reference

### ClickHouse Arguments

`--clickhouse-url`

ClickHouse HTTP endpoint. Default resolution:

```text
REAL_LIVE_CLICKHOUSE_WRITE_URL
NEWS_CLICKHOUSE_URL
QMD_CLICKHOUSE_URL
CLICKHOUSE_URL
TD__DATABASE__CLICKHOUSE__ENDPOINT_URL
http://localhost:8123
```

`--user`

ClickHouse user. Default resolution:

```text
REAL_LIVE_CLICKHOUSE_WRITE_USER
NEWS_CLICKHOUSE_USER
QMD_CLICKHOUSE_USER
CLICKHOUSE_WORKSTATION_USER
CLICKHOUSE_USER
TD__DATABASE__CLICKHOUSE__USER
default
```

`--password`

ClickHouse password. Default resolution:

```text
REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD
NEWS_CLICKHOUSE_PASSWORD
QMD_CLICKHOUSE_PASSWORD
CLICKHOUSE_WORKSTATION_PASSWORD
CLICKHOUSE_PASSWORD
TD__DATABASE__CLICKHOUSE__PASSWORD
empty
```

`--database`

Target database. Default resolution:

```text
REAL_LIVE_CLICKHOUSE_WRITE_DATABASE
NEWS_CLICKHOUSE_DATABASE
QMD_CLICKHOUSE_DATABASE
q_live
```

`--news-table`

Normalized news target table. Default:

```text
benzinga_news_normalized_v1
```

`--manifest-table`

Bucket/run manifest table. Default:

```text
benzinga_news_ingest_manifest_v1
```

`--storage-policy`

MergeTree storage policy for both news and manifest tables. Default:

```text
CLICKHOUSE_LIVE_STORAGE_POLICY
NEWS_CLICKHOUSE_STORAGE_POLICY
empty
```

Use `CLICKHOUSE_LIVE_STORAGE_POLICY` for this pipeline so news is stored on the SSD policy reserved for the live database.

### Provider Arguments

`--api-key`

Massive API key used to call the Benzinga endpoint. Default:

```text
MASSIVE_API_KEY
```

`--endpoint-url`

Benzinga endpoint served through Massive. Default:

```text
NEWS_BENZINGA_URL
NEWS_MASSIVE_BENZINGA_URL
https://api.massive.com/benzinga/v2/news
```

`NEWS_MASSIVE_BENZINGA_URL` is only a backward-compatible variable name. The canonical source is Benzinga.

### Date and Bucket Arguments

`--start-utc`

Inclusive start timestamp. Default:

```text
NEWS_BENZINGA_HISTORICAL_START_UTC
2024-01-01T00:00:00Z
```

`--end-utc`

Exclusive end timestamp. Default:

```text
NEWS_BENZINGA_HISTORICAL_END_UTC
2026-01-01T00:00:00Z
```

`--bucket-minutes`

Fixed UTC bucket size. Default:

```text
NEWS_BENZINGA_BUCKET_MINUTES
90
```

Buckets are half-open UTC ranges: `published.gte` at the bucket start and `published.lt` at the bucket end. A news item exactly on a 90-minute boundary belongs to the next bucket. This avoids both missed rows and duplicate boundary downloads.

Smaller buckets reduce the chance that a dense period saturates the provider page limit. Larger buckets reduce scheduling overhead.

`--limit`

Provider page size. Default:

```text
NEWS_BENZINGA_POLL_LIMIT
1000
```

`--max-pages`

Maximum provider pages per bucket. Default:

```text
NEWS_BENZINGA_MAX_PAGES
1000
```

If a bucket still has a next page after this limit, the bucket is marked saturated/partial in the manifest.

`--limit-buckets`

Debug limit after bucket construction. Default:

```text
0
```

`0` means all buckets.

### Concurrency Arguments

`--download-processes`

Worker processes for API download and raw-payload disk writes. Default:

```text
NEWS_BENZINGA_DOWNLOAD_PROCESSES
8
```

This controls Massive request pressure and raw JSON write pressure. Start with 16 on the workstation, then increase only if Massive, network, and disk remain stable.

The script keeps only a bounded download backlog in memory, currently `--download-processes * 4`, so a full historical run does not create one pending future per time bucket.

Massive page downloads retry transient HTTP failures, including `429 Too Many Requests`; if the provider sends `Retry-After`, the worker waits for that value before retrying.

`--normalize-processes`

Worker processes for fast base normalization. Base normalization does not fetch external article pages or PDFs; it extracts provider fields, body text already in the payload, links, PDF URLs, and enrichment eligibility. Default:

```text
NEWS_BENZINGA_NORMALIZE_PROCESSES
0
```

`0` means half of `--download-processes`, with a minimum of 1.

`--enrichment-processes`

Worker processes for external article/PDF enrichment. Default:

```text
NEWS_BENZINGA_ENRICHMENT_PROCESSES
0
```

`0` means `min(4, --normalize-processes)`. Keep this lower than the base lane because these workers are the ones that hit Benzinga article pages, SEC, and other external domains. The script keeps a bounded enrichment backlog of `--enrichment-processes * 4`, so a fast base lane does not create unlimited pending enrichment futures.

`--insert-concurrency`

Concurrent ClickHouse insert workers. Default:

```text
NEWS_BENZINGA_INSERT_CONCURRENCY
4
```

Start with 6 on the workstation. Increase only if ClickHouse insert latency and memory stay stable.

`--insert-batch-rows`

Minimum normalized rows accumulated before a ClickHouse insert batch is submitted. Default:

```text
NEWS_BENZINGA_INSERT_BATCH_ROWS
5000
```

Rows are batched across buckets. Larger batches reduce ClickHouse overhead, but they also make a failed insert affect more buckets. Start with 5,000 to 10,000 for the full run.

`--manifest-batch-rows`

Minimum manifest/status rows accumulated before a ClickHouse manifest insert batch is submitted. Default:

```text
NEWS_BENZINGA_MANIFEST_BATCH_ROWS
1000
```

Manifest writes are queued through the same bounded ClickHouse writer pool as news inserts. The main orchestration loop does not open separate synchronous ClickHouse connections for status updates.

### Artifact and Report Arguments

`--artifact-root-win`

Root for raw provider payloads and optional PDF artifacts. Default:

```text
NEWS_BENZINGA_ARTIFACT_ROOT_WIN
D:/market-data/benzinga_news_canonical
```

This is the raw download location.

`--output-root-win`

Root for run reports. Default:

```text
NEWS_BENZINGA_OUTPUT_ROOT_WIN
D:/market-data/prepared/benzinga_news_ingest
```

This is not the raw download location.

## Live Gateway Integration

The Rust live news gateway uses the same raw artifact root and canonical ClickHouse table:

```text
NEWS_BENZINGA_ARTIFACT_ROOT_WIN
NEWS_BENZINGA_CANONICAL_ENABLED
NEWS_BENZINGA_CANONICAL_TABLE
NEWS_CLICKHOUSE_STORAGE_POLICY
```

Default live behavior:

```text
NEWS_BENZINGA_CANONICAL_ENABLED=true
NEWS_BENZINGA_CANONICAL_TABLE=benzinga_news_normalized_v1
NEWS_BENZINGA_ARTIFACT_ROOT_WIN=D:/market-data/benzinga_news_canonical
```

The live process saves the raw provider payload first, then normalizes and batch-inserts into ClickHouse asynchronously through the gateway writer task. The live UI stream still uses the gateway's in-memory state and existing `live_news_articles` write path.

### Extraction Arguments

`--external-min-body-chars`

If Benzinga body text is shorter than this threshold, the normalizer may fetch the article URL when external fetching is enabled. Default:

```text
NEWS_EXTRACTION_MIN_BODY_CHARS
300
```

`--extraction-timeout-seconds`

Timeout for external URL and PDF requests. Default:

```text
NEWS_EXTRACTION_TIMEOUT_SECONDS
8
```

`--external-request-min-interval-seconds`

Minimum seconds between external HTTP requests to the same host for domains that do not have a provider-specific override. This is shared across normalization worker processes through a lock directory under `--artifact-root-win`. Default:

```text
NEWS_EXTERNAL_REQUEST_MIN_INTERVAL_SECONDS
0.5
```

This protects miscellaneous external websites during one-pass external/PDF extraction.

`--benzinga-request-min-interval-seconds`

Minimum seconds between Benzinga article-page requests to the same host. Default:

```text
NEWS_BENZINGA_REQUEST_MIN_INTERVAL_SECONDS
1.0
```

This is intentionally slower than the generic default because Benzinga article-page fetches can return `429 Too Many Requests` when many workers hit the same host concurrently.

`--sec-request-min-interval-seconds`

Minimum seconds between SEC/EDGAR requests to the same host. Default:

```text
NEWS_SEC_REQUEST_MIN_INTERVAL_SECONDS
0.13
```

The SEC publishes a 10 requests/second ceiling. `0.13` seconds is deliberately below that ceiling once process scheduling jitter is included.

`--external-max-retries`

Maximum retries after the first external/PDF HTTP attempt for transient failures such as 408, 429, and 5xx responses. Default:

```text
NEWS_EXTERNAL_MAX_RETRIES
3
```

`--external-retry-base-seconds`

Base backoff used when the server does not provide `Retry-After`. If a response includes `Retry-After`, the script honors it. Default:

```text
NEWS_EXTERNAL_RETRY_BASE_SECONDS
1.0
```

`--external-user-agent`

Default user-agent for non-SEC external article/PDF requests. Default:

```text
NEWS_EXTERNAL_USER_AGENT
Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125 Safari/537.36
```

`--sec-user-agent`

SEC-specific user-agent for SEC/EDGAR requests. Default resolution:

```text
NEWS_SEC_USER_AGENT
SEC_EDGAR_USER_AGENT
empty
```

Set this before SEC-heavy runs so SEC sees a descriptive app/contact identity.

`--max-pdf-bytes`

Maximum PDF size to download/extract. Default:

```text
NEWS_PDF_MAX_BYTES
12000000
```

Before downloading a PDF, the normalizer records deterministic metadata where available: URL, domain, HTTP `Content-Type`, HTTP `Content-Length`, max byte cap, importance score, importance tier, reasons, and policy. The policy values are:

```text
download_now
metadata_only
offline_queue
```

Large PDFs above `--max-pdf-bytes` are not downloaded in the hot path. If the deterministic score is medium-or-better, they are marked `offline_queue`; otherwise they are marked `metadata_only`. In both cases, the news row is still inserted.

`--text-limit-chars`

Maximum characters stored per normalized text field. Default:

```text
NEWS_NORMALIZED_TEXT_LIMIT_CHARS
24000
```

This keeps ClickHouse rows compact while preserving enough text for training and later keyword discovery.

`--no-fetch-external`

Disable article URL fetching. This is useful for fast first-pass ingestion.

`--no-extract-pdfs`

Disable PDF download and extraction. This is useful for fast first-pass ingestion.

### Resume and Safety Arguments

`--retry-inserted`

Reprocess buckets whose latest manifest status is `inserted`. Leave this off for normal resume behavior.

`--retry-partial`

Reprocess buckets whose latest manifest status is `partial`. Use this after increasing `--max-pages`; reduce `--bucket-minutes` only if a bucket remains saturated after the 1000-page cap.

`--no-insert`

Download and normalize only. Raw artifacts and run reports can still be written, but ClickHouse tables are not created and rows are not inserted.

`--dry-run`

Print configuration and bucket previews only. No download, raw artifact write, table creation, or insert.

## Resume Behavior

The script reads the latest status from:

```text
<database>.benzinga_news_ingest_manifest_v1
```

Normal resume skips buckets whose latest status is `inserted`. Buckets marked `partial` are also skipped unless `--retry-partial` is provided.

Recommended retry flow for saturated buckets after increasing the page cap:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\news_benzinga_historical_ingest.py --start-utc 2010-01-01T00:00:00Z --end-utc 2026-06-08T00:00:00Z --bucket-minutes 90 --limit 1000 --max-pages 1000 --retry-partial --download-processes 16 --normalize-processes 8 --insert-concurrency 6 --insert-batch-rows 5000 --manifest-batch-rows 1000
```

If any bucket is still marked `partial`, rerun that affected date range with a smaller `--bucket-minutes` value.

## Monitoring

The script prints:

```text
pending_buckets
completed
failed
downloaded_rows
normalized_rows
enrichment_required_rows
enrichment_pending_rows
enrichment_completed_rows
enriched_rows
inserted_rows
insert_buffer
elapsed_min
eta_min
```

Each run also writes a JSONL report under `--output-root-win`. Use that report to inspect bucket-level exceptions without searching terminal history.

If a specific raw payload fails during raw-file write or normalization, the report writes a separate record:

```text
type=file_error
```

That record includes the stage, bucket id, raw artifact path when available, raw payload hash when available, provider article id when available, provider timestamp, exception, and traceback. The stage summary rows only include `file_error_count` so the report does not duplicate tracebacks and the normalized ClickHouse table stays compact.

Useful ClickHouse checks:

```sql
SELECT count()
FROM q_live.benzinga_news_normalized_v1;
```

```sql
SELECT
    status,
    count()
FROM q_live.benzinga_news_ingest_manifest_v1
GROUP BY status
ORDER BY status;
```

```sql
SELECT
    min(published_at_utc),
    max(published_at_utc),
    count()
FROM q_live.benzinga_news_normalized_v1;
```

## Practical Starting Recommendation

For the first real canonical pass, keep enrichment enabled but let only the enrichment lane run under controlled external request pressure:

```powershell
python \\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\masked_event_model\v4\research\mlops\news_benzinga_historical_ingest.py --start-utc 2010-01-01T00:00:00Z --end-utc 2026-06-08T00:00:00Z --bucket-minutes 90 --limit 1000 --max-pages 1000 --download-processes 24 --normalize-processes 12 --enrichment-processes 4 --insert-concurrency 8 --insert-batch-rows 10000 --manifest-batch-rows 2000 --external-request-min-interval-seconds 1.0 --benzinga-request-min-interval-seconds 1.25 --sec-request-min-interval-seconds 0.25 --external-max-retries 4 --external-retry-base-seconds 1.5
```

Install `PyMuPDF` in the workstation environment before running with PDF extraction enabled. The repo `requirements.txt` includes it.

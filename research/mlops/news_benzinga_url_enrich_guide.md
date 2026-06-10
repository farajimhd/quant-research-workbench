# Benzinga URL Enrichment

This script fetches URLs from the deduplicated Benzinga URL fetch plan and saves clean extracted text plus metadata. Raw HTML/PDF bytes are not saved unless explicitly requested.

## Script

`research/mlops/news_benzinga_url_enrich.py`

## Inputs

- `news_url_fetch_plan.jsonl`: one row per unique actionable URL.
- `news_url_fetch_plan_attachments.jsonl`: many-to-many mapping from original Benzinga news rows to URL hashes. This script does not need the attachment file while fetching, but downstream joins use it.

If `--fetch-plan-jsonl` is omitted, the latest fetch-plan manifest under `--fetch-plan-root-win` is used.

## Outputs

Each run writes to:

`D:/market-data/prepared/benzinga_news_url_enrichment/<run_id>/`

Files:

- `news_url_enrichment_result.jsonl`: successful, empty, metadata-only, unsupported, and deferred rows.
- `news_url_enrichment_errors.jsonl`: failed or transient-failed rows.
- `news_url_enrichment_manifest.json`: counts, settings, output paths, and timing.
- `raw_artifacts/`: only present when `--save-raw-artifacts` is used.

The result is one row per `url_hash`. It joins back to original news rows through `news_url_fetch_plan_attachments.jsonl` on `url_hash`.

## Stop Behavior

Press `Ctrl+C` once to request a graceful stop. The script cancels queued URLs, writes a partial manifest with `interrupted=true`, and exits after active network requests release. Active requests cannot be killed safely inside Python threads, so the remaining delay is bounded mainly by `--timeout-seconds`.

## Text Extraction

HTML extraction uses the best available method in this order:

1. `trafilatura`, if installed.
2. `readability-lxml`, if installed.
3. `beautifulsoup4`, if installed.
4. Built-in HTML parser fallback.

PDF extraction uses the repo's in-memory PDF path:

1. `pymupdf` / `fitz`, if installed.
2. `pypdf`, if available through the existing fallback.

The default output stores extracted text and metadata only. This keeps the enrichment dataset compact and avoids saving raw web pages unless debugging is needed.

## Result Fields

Important fields in `news_url_enrichment_result.jsonl`:

- `url_hash`
- `normalized_url`
- `final_url`
- `final_url_hash`
- `final_action`
- `resolved_action`
- `status`
- `status_reason`
- `http_status`
- `content_type`
- `content_length`
- `fetched_at_utc`
- `redirect_chain_json`
- `title`
- `canonical_url`
- `extracted_text`
- `extracted_text_chars`
- `extracted_text_hash`
- `extraction_method`
- `extraction_quality`
- `quality_flags`
- `pdf_page_count`
- `pdf_metadata_json`
- `error_type`
- `error_message`

Common statuses:

- `success`
- `empty_text`
- `unsupported_content_type`
- `deferred_sec_handler`
- `failed`
- `transient_failed`

## One-Line Commands

Laptop smoke test against latest fetch plan:

```powershell
python D:/TradingCodes/quant-research-workbench/research/mlops/news_benzinga_url_enrich.py --fetch-plan-root-win D:/market-data/prepared/benzinga_news_url_fetch_plan --output-root-win D:/market-data/prepared/benzinga_news_url_enrichment --limit-urls 1000 --network-concurrency 8 --max-pending-futures 32 --per-domain-min-interval-seconds 0.2 --progress-interval 100 --heartbeat-seconds 15 --load-progress-interval 100000
```

Workstation smoke test:

```powershell
python //DESKTOP-SAAI85T/Workstation-D/TradingML/codes/masked_event_model/v4/research/mlops/news_benzinga_url_enrich.py --fetch-plan-root-win D:/market-data/prepared/benzinga_news_url_fetch_plan --output-root-win D:/market-data/prepared/benzinga_news_url_enrichment --limit-urls 1000 --network-concurrency 8 --max-pending-futures 32 --per-domain-min-interval-seconds 0.2 --progress-interval 100 --heartbeat-seconds 15 --load-progress-interval 100000
```

Workstation medium run:

```powershell
python //DESKTOP-SAAI85T/Workstation-D/TradingML/codes/masked_event_model/v4/research/mlops/news_benzinga_url_enrich.py --fetch-plan-root-win D:/market-data/prepared/benzinga_news_url_fetch_plan --output-root-win D:/market-data/prepared/benzinga_news_url_enrichment --limit-urls 50000 --network-concurrency 12 --max-pending-futures 48 --per-domain-min-interval-seconds 0.2 --progress-interval 1000 --heartbeat-seconds 15 --load-progress-interval 100000 --resume
```

Workstation full run:

```powershell
python //DESKTOP-SAAI85T/Workstation-D/TradingML/codes/masked_event_model/v4/research/mlops/news_benzinga_url_enrich.py --fetch-plan-root-win D:/market-data/prepared/benzinga_news_url_fetch_plan --output-root-win D:/market-data/prepared/benzinga_news_url_enrichment --network-concurrency 12 --max-pending-futures 48 --per-domain-min-interval-seconds 0.2 --progress-interval 1000 --heartbeat-seconds 15 --load-progress-interval 100000 --resume
```

Debug run with raw artifacts:

```powershell
python //DESKTOP-SAAI85T/Workstation-D/TradingML/codes/masked_event_model/v4/research/mlops/news_benzinga_url_enrich.py --fetch-plan-root-win D:/market-data/prepared/benzinga_news_url_fetch_plan --output-root-win D:/market-data/prepared/benzinga_news_url_enrichment --limit-urls 100 --network-concurrency 4 --save-raw-artifacts
```

## Arguments

- `--fetch-plan-jsonl`: exact fetch-plan JSONL path.
- `--fetch-plan-root-win`: root containing fetch-plan runs.
- `--output-root-win`: root where the enrichment run folder is created.
- `--limit-urls`: optional cap for smoke tests.
- `--network-concurrency`: number of concurrent fetch workers.
- `--max-pending-futures`: maximum queued/in-flight URL jobs. Defaults to `4 * --network-concurrency`.
- `--per-domain-min-interval-seconds`: minimum time between requests to the same host.
- `--timeout-seconds`: request timeout.
- `--max-html-bytes`: hot-path byte cap for HTML/text content.
- `--max-pdf-bytes`: hot-path byte cap for PDFs.
- `--max-text-chars`: maximum extracted text stored per URL.
- `--max-retries`: retry count for transient failures.
- `--progress-interval`: completed URLs between progress prints.
- `--load-progress-interval`: fetch-plan rows loaded between startup load-progress prints.
- `--heartbeat-seconds`: maximum silence while URL workers are still pending.
- `--resume`: skip URL hashes already present in previous successful result files under the output root.
- `--save-raw-artifacts`: debug-only flag to save fetched bytes.

Environment-variable defaults:

- `NEWS_BENZINGA_URL_FETCH_PLAN_JSONL`
- `NEWS_BENZINGA_URL_FETCH_PLAN_ROOT_WIN`
- `NEWS_BENZINGA_URL_ENRICHMENT_OUTPUT_ROOT_WIN`
- `NEWS_BENZINGA_URL_ENRICH_LIMIT_URLS`
- `NEWS_BENZINGA_URL_ENRICH_NETWORK_CONCURRENCY`
- `NEWS_BENZINGA_URL_ENRICH_PER_DOMAIN_SECONDS`
- `NEWS_BENZINGA_URL_ENRICH_TIMEOUT_SECONDS`
- `NEWS_BENZINGA_URL_ENRICH_MAX_HTML_BYTES`
- `NEWS_BENZINGA_URL_ENRICH_MAX_PDF_BYTES`
- `NEWS_BENZINGA_URL_ENRICH_MAX_TEXT_CHARS`
- `NEWS_BENZINGA_URL_ENRICH_MAX_RETRIES`

## Recommended Optional Packages

For best HTML extraction quality in the `ml4t` environment:

```powershell
conda run -n ml4t python -m pip install trafilatura readability-lxml beautifulsoup4
```

The script still runs without these packages, but extraction quality will rely on the built-in fallback.

# Benzinga URL Download and Extraction Pipeline

This replaces the old one-step enrichment script for large runs.

## Stage 1: Download

The downloader reads the deduplicated fetch plan, skips already completed rows when `--resume` is used, interleaves URLs by domain, and saves compressed raw artifacts to disk.

Recommended workstation command:

```powershell
python //DESKTOP-SAAI85T/Workstation-D/TradingML/codes/masked_event_model/v4/research/mlops/news_benzinga_url_download.py --fetch-plan-root-win D:/market-data/prepared/benzinga_news_url_fetch_plan --output-root-win D:/market-data/prepared/benzinga_news_url_download --artifact-root-win D:/market-data/news_benzinga_url_download_artifacts --network-concurrency 128 --max-pending-futures 512 --per-domain-min-interval-seconds 0.02 --timeout-seconds 5 --max-retries 0 --progress-interval 5000 --heartbeat-seconds 15 --flush-interval 500 --resume
```

Expected startup marker:

```text
submitted_initial=512 max_pending_futures=512
```

The downloader writes:

- `news_url_download_result.jsonl`: downloaded artifacts and deferred rows.
- `news_url_download_errors.jsonl`: failed or transient-failed downloads.
- `news_url_download_manifest.json`: run settings and summary.
- compressed artifacts under `--artifact-root-win`.

## Stage 2: Extract

The extractor reads downloaded artifact metadata and uses a process pool to extract clean text. It does not make network requests.

Recommended workstation command:

```powershell
python //DESKTOP-SAAI85T/Workstation-D/TradingML/codes/masked_event_model/v4/research/mlops/news_benzinga_url_extract.py --download-root-win D:/market-data/prepared/benzinga_news_url_download --output-root-win D:/market-data/prepared/benzinga_news_url_extraction --processes 16 --max-pending-futures 64 --progress-interval 1000 --heartbeat-seconds 15 --flush-interval 100 --resume
```

The extractor writes:

- `news_url_extraction_result.jsonl`: extracted text rows.
- `news_url_extraction_errors.jsonl`: artifact/read/extraction failures.
- `news_url_extraction_manifest.json`: run settings and summary.

## Resume

Downloader resume:

- skips prior `downloaded` rows
- skips prior permanent failures by default
- retries transient failures such as timeouts
- pass `--retry-permanent-failures` only when intentionally retesting blocked/dead URLs

Extractor resume:

- skips prior non-failed extraction rows
- retries prior extraction failures

## Why This Is Faster

- URLs are domain-interleaved before download.
- Download and extraction are separate, so CPU-heavy parsing cannot block network fetching.
- Raw downloaded content is saved once and can be reprocessed without another network request.
- The downloader has a bounded future queue and high network concurrency.

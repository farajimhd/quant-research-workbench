# Benzinga URL Download, Extraction, and Normalization Pipeline

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

The standalone extractor reads downloaded artifact metadata and uses a process pool to extract clean text. It does not make network requests. This stage is optional now because Stage 3 can extract downloaded URL artifacts inline before building normalized rows.

Recommended workstation command:

```powershell
python //DESKTOP-SAAI85T/Workstation-D/TradingML/codes/masked_event_model/v4/research/mlops/news_benzinga_url_extract.py --download-root-win D:/market-data/prepared/benzinga_news_url_download --output-root-win D:/market-data/prepared/benzinga_news_url_extraction --processes 16 --max-pending-futures 64 --progress-interval 1000 --heartbeat-seconds 15 --flush-interval 100 --resume
```

The extractor writes:

- `news_url_extraction_result.jsonl`: extracted text rows.
- `news_url_extraction_errors.jsonl`: artifact/read/extraction failures.
- `news_url_extraction_manifest.json`: run settings and summary.

## Stage 3: Build Normalized News Rows

The row builder reads the original raw Benzinga article JSON, extracts downloaded URL artifacts when needed, attaches clean source text, and writes compact DB-ready datasets. It does not make network requests. The main event table stays lean; body, external, PDF, URL, and artifact details are written to separate part sets.

Recommended workstation command after URL download finishes:

```powershell
python //DESKTOP-SAAI85T/Workstation-D/TradingML/codes/masked_event_model/v4/research/mlops/news_benzinga_build_normalized_rows.py --raw-root-win D:/market-data/news-benzinga/raw --fetch-plan-root-win D:/market-data/prepared/benzinga_news_url_fetch_plan --download-root-win D:/market-data/prepared/benzinga_news_url_download --extraction-root-win D:/market-data/prepared/benzinga_news_url_extraction --output-root-win D:/market-data/prepared/benzinga_news_normalized_rows --processes 24 --max-pending-futures 96 --inline-extraction-processes 24 --text-limit-chars 24000 --max-enriched-text-chars-per-url 12000 --max-enriched-urls-per-article 5 --rows-per-file 100000 --max-output-file-bytes 268435456 --progress-interval 25000 --inline-extraction-progress-interval 5000 --flush-interval 1000
```

Smoke-test command:

```powershell
python //DESKTOP-SAAI85T/Workstation-D/TradingML/codes/masked_event_model/v4/research/mlops/news_benzinga_build_normalized_rows.py --raw-root-win D:/market-data/news-benzinga/raw --fetch-plan-root-win D:/market-data/prepared/benzinga_news_url_fetch_plan --download-root-win D:/market-data/prepared/benzinga_news_url_download --output-root-win D:/market-data/prepared/benzinga_news_normalized_rows_smoke --limit-articles 1000 --limit-attachment-rows 10000 --processes 8 --max-pending-futures 32 --inline-extraction-processes 8 --rows-per-file 10000 --progress-interval 100 --inline-extraction-progress-interval 500
```

The row builder writes:

- `normalized_parts/event_parts/benzinga_news_event_part_*.jsonl`: one compact event row per Benzinga article for `q_live.benzinga_news_event_v1`.
- `normalized_parts/text_parts/benzinga_news_text_part_*.jsonl`: body, external, and PDF text rows for `q_live.benzinga_news_text_v1`.
- `normalized_parts/url_parts/benzinga_news_url_part_*.jsonl`: persisted article/source/PDF/SEC/social URLs for `q_live.benzinga_news_url_v1`.
- `normalized_parts/attachment_parts/benzinga_news_attachment_part_*.jsonl`: downloaded artifact and extraction metadata for `q_live.benzinga_news_attachment_v1`.
- `benzinga_news_normalized_errors.jsonl`: raw files that could not be normalized.
- `benzinga_news_normalized_attachment_summary.jsonl`: sidecar metadata showing which extracted URLs were attached to each article.
- `benzinga_news_inline_extraction_result.jsonl`: clean text extracted from downloaded URL artifacts during Stage 3.
- `benzinga_news_inline_extraction_errors.jsonl`: URL artifacts that could not be extracted during Stage 3.
- `benzinga_news_normalized_manifest.json`: run paths, dataset part-file lists, ClickHouse column structures, insert templates, counts, quality flags, and timing.

Important behavior:

- The builder includes raw articles even when URL enrichment is missing or failed.
- Event rows do not store `body_text`, `external_text`, `pdf_text`, `normalized_full_text`, raw `links`, or `pdf_urls`.
- Text rows store the original text components separately. The training/export layer should assemble full model input from title, teaser, and text rows.
- URL rows skip Benzinga quote, stock, navigation, tracking, and image URLs. Those links are redundant with ticker/image fields or are not useful news sources.
- Part files are rotated by `--rows-per-file` and `--max-output-file-bytes` so the next ClickHouse load script can pass a manageable file glob to the `file()` table function.
- If a standalone Stage 2 extraction result exists, the builder reuses it. Otherwise `--inline-extract` extracts downloaded URL artifacts inside Stage 3.
- `--no-inline-extract` disables inline extraction and only uses existing extraction results.
- Unicode text is normalized with NFKC and control characters are removed before DB part rows are written. Valid non-English letters, names, and punctuation are preserved.
- `text_hash`, `has_external_text`, `has_pdf`, and `content_quality_flags` are recomputed after URL text is attached.
- `--require-extraction-result` can be added when a run must fail if Stage 2 output is unavailable.
- `--processes` controls article normalization workers.
- `--inline-extraction-processes` controls URL artifact extraction workers; if omitted or `0`, it uses `--processes`.
- `--path-prefix-map FROM=TO` can map workstation paths to a share path for laptop-side validation, for example `--path-prefix-map D:/=//DESKTOP-SAAI85T/Workstation-D/`.

## Stage 4: Push Normalized Parts to ClickHouse

The ClickHouse loader reads the Stage 3 manifest, validates each dataset part file through the server-side `file()` table function, creates the four news tables and a part-level ingest manifest table when `--execute` is used, then inserts each part. Reruns skip parts already marked `ok` for that dataset unless `--force` is passed.

Preflight only:

```powershell
python //DESKTOP-SAAI85T/Workstation-D/TradingML/codes/masked_event_model/v4/research/mlops/news_benzinga_clickhouse_file_ingest.py --manifest-root-win D:/market-data/prepared/benzinga_news_normalized_rows --parts-root-win D:/market-data --parts-root-ch /mnt/d/market-data --preflight-only
```

Execute:

```powershell
python //DESKTOP-SAAI85T/Workstation-D/TradingML/codes/masked_event_model/v4/research/mlops/news_benzinga_clickhouse_file_ingest.py --manifest-root-win D:/market-data/prepared/benzinga_news_normalized_rows --parts-root-win D:/market-data --parts-root-ch /mnt/d/market-data --execute
```

## Resume

Downloader resume:

- skips prior `downloaded` rows
- skips prior permanent failures by default
- retries transient failures such as timeouts
- pass `--retry-permanent-failures` only when intentionally retesting blocked/dead URLs

Extractor resume:

- skips prior non-failed extraction rows
- retries prior extraction failures

Normalizer resume:

- The row builder is deterministic and cheap compared with downloading. Rerun it into a new output folder when extraction results change.

## Why This Is Faster

- URLs are domain-interleaved before download.
- Download and extraction are separate, so CPU-heavy parsing cannot block network fetching.
- Raw downloaded content is saved once and can be reprocessed without another network request.
- The downloader has a bounded future queue and high network concurrency.
- Final normalization is offline, so DB-ready rows can be rebuilt after extraction policy changes without re-downloading URLs.

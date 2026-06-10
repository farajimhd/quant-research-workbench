# Benzinga URL Inventory Builder

This script scans already-downloaded raw Benzinga JSON files and creates the URL inventory needed before enrichment. It does not call the network and does not write to ClickHouse.

## Script

`research/mlops/news_benzinga_url_inventory.py`

## Why It Exists

The raw Benzinga articles can contain provider article URLs, quote-page links, image links, PDF links, SEC links, and external source links. Before enrichment, we need one deterministic inventory that tells us:

- which article each URL came from,
- where inside the article payload the URL was found,
- whether the URL should be ignored, fetched as HTML, fetched as PDF, sent to a SEC handler, or kept as metadata only,
- which domain policy should be reviewed before network enrichment,
- which stable keys can attach future extracted text back to the original raw article.

## Outputs

Each run writes to:

`D:/market-data/prepared/benzinga_news_url_inventory/<run_id>/`

Files:

- `news_url_inventory.jsonl`: one URL occurrence per row.
- `news_url_inventory_errors.jsonl`: one parse/read error per failed raw JSON file.
- `news_domain_summary.csv`: one domain summary row with counts and suggested policy.
- `news_url_policy_seed.json`: a generated starting point for domain-level enrichment policy review.
- `news_url_inventory_manifest.json`: run metadata and output paths.

Important attachment keys in `news_url_inventory.jsonl`:

- `provider_article_id`: Benzinga article id.
- `canonical_news_id`: stable hash for this raw news item.
- `raw_artifact_path`: exact raw JSON file path.
- `raw_payload_hash`: stable hash of the raw payload.
- `url_row_id`: stable id for this URL occurrence.
- `url_source`: where the URL was found, such as `body_link`, `provider_article_url`, `body_pdf_regex`, `image_url`, or `raw_json_url_string`.
- `url_ordinal`: occurrence order inside the article inventory.
- `url_hash`: stable normalized URL hash.

## One-Line Commands

Laptop smoke test:

```powershell
python D:/TradingCodes/quant-research-workbench/research/mlops/news_benzinga_url_inventory.py --raw-root-win D:/market-data/benzinga_news_canonical/raw --output-root-win D:/market-data/prepared/benzinga_news_url_inventory --limit-files 100 --processes 2 --chunk-size 25
```

Laptop full inventory:

```powershell
python D:/TradingCodes/quant-research-workbench/research/mlops/news_benzinga_url_inventory.py --raw-root-win D:/market-data/benzinga_news_canonical/raw --output-root-win D:/market-data/prepared/benzinga_news_url_inventory --processes 16 --chunk-size 1000
```

Workstation full inventory after sync:

```powershell
python //DESKTOP-SAAI85T/Workstation-D/TradingML/codes/masked_event_model/v4/research/mlops/news_benzinga_url_inventory.py --raw-root-win G:/market-data/benzinga_news_canonical/raw --output-root-win G:/market-data/prepared/benzinga_news_url_inventory --processes 32 --chunk-size 1000
```

## Arguments

- `--raw-root-win`: root folder containing raw downloaded Benzinga JSON files.
- `--output-root-win`: folder where the run output directory is created.
- `--processes`: number of worker processes used to parse raw JSON files.
- `--chunk-size`: number of raw JSON files sent to each worker task.
- `--limit-files`: optional cap for smoke tests. Use `0` or omit for all files.

Environment-variable defaults:

- `NEWS_BENZINGA_RAW_ROOT_WIN`
- `NEWS_BENZINGA_URL_INVENTORY_OUTPUT_ROOT_WIN`
- `NEWS_BENZINGA_URL_INVENTORY_PROCESSES`
- `NEWS_BENZINGA_URL_INVENTORY_CHUNK_SIZE`

## URL Action Labels

- `ignore`: do not fetch this URL during enrichment. Typical examples are Benzinga article pages, Benzinga quote pages, and provider images.
- `fetch_html`: candidate external source page to fetch and parse as HTML.
- `fetch_pdf`: candidate PDF to download under the PDF size and metadata policy.
- `sec_handler`: SEC URL that should be handled by the SEC-specific pipeline.
- `metadata_only`: keep the URL metadata, but do not fetch it in the normal enrichment path.

The inventory is intentionally conservative. It keeps the news row even if linked artifacts are ignored, skipped, or deferred.

# Benzinga URL Fetch Plan Builder

This script converts the raw URL inventory into an enrichment-ready fetch plan.

It does not call the network and does not write to ClickHouse.

## Script

`research/mlops/news_benzinga_url_fetch_plan.py`

## Purpose

The URL inventory is occurrence-level and can contain millions of duplicate or low-value rows. The fetch plan applies a domain policy layer, removes non-actionable URLs, and deduplicates by URL so enrichment fetches each URL once.

The script also writes an attachment file so a fetched URL can still be joined back to every original Benzinga article that referenced it.

## Outputs

Each run writes to:

`D:/market-data/prepared/benzinga_news_url_fetch_plan/<run_id>/`

Files:

- `news_url_fetch_plan.jsonl`: one deduplicated actionable URL per row.
- `news_url_fetch_plan_attachments.jsonl`: article-to-URL attachment rows for actionable URLs.
- `news_url_fetch_plan_domain_summary.csv`: domain-level before/after policy summary.
- `news_url_domain_policy_effective.json`: the policy actually used for this run.
- `news_url_fetch_plan_manifest.json`: run metadata, counts, and output paths.

## Default Policy

The default policy keeps true external content as `fetch_html`, keeps PDFs as `fetch_pdf`, keeps SEC URLs as `sec_handler`, and moves questionable HTML domains out of direct fetch:

- `resolve_redirect`: `t.co`, `bit.ly`, `c212.net`, `feedburner.com`, `lnkd.in`, and similar redirectors.
- `metadata_only`: social/media/photo/video/webinar/product-store/market-page domains such as `facebook.com`, `youtube.com`, `twitter.com`, `x.com`, `flickr.com`, `pixabay.com`, `unsplash.com`, `media-server.com`, `on24.com`, `coingecko.com`, `coinmarketcap.com`, and `opensea.io`.
- `ignore`: Benzinga internal/product pages and obvious affiliate/tracking domains such as `benzinga.help`, `benzingapro.com`, `grsm.io`, and ad/tracking hosts. Exact subdomain rules can be used without blocking the registered domain; for example, `register.zacks.com` is ignored while normal `zacks.com` article URLs remain fetchable.

News, press-release, financial-media, and official/regulator domains are intentionally left as `fetch_html` unless they are known redirectors, media hosts, or internal utility pages. The policy decision is for enrichment work only; it does not drop the original news row.

You can override or extend this policy with `--policy-json`.

Example override:

```json
{
  "version": "benzinga-url-domain-policy-v1-custom",
  "exact_domain_actions": {
    "subdomain.example.com": "metadata_only"
  },
  "registered_domain_actions": {
    "short.example": "resolve_redirect"
  }
}
```

`exact_domain_actions` applies only to the exact host. `registered_domain_actions` applies to the registered domain and its subdomains. The older `domain_actions` key is still accepted as a compatibility override.

Valid actions:

- `fetch_html`
- `fetch_pdf`
- `resolve_redirect`
- `sec_handler`
- `metadata_only`
- `ignore`
- `review`

Only `fetch_html`, `fetch_pdf`, `resolve_redirect`, and `sec_handler` are written to the deduped fetch plan.

## One-Line Commands

Laptop smoke test against the latest inventory:

```powershell
python D:/TradingCodes/quant-research-workbench/research/mlops/news_benzinga_url_fetch_plan.py --inventory-root-win D:/market-data/prepared/benzinga_news_url_inventory --output-root-win D:/market-data/prepared/benzinga_news_url_fetch_plan --limit-rows 200000 --shards 32 --progress-interval 50000
```

Workstation smoke test:

```powershell
python //DESKTOP-SAAI85T/Workstation-D/TradingML/codes/masked_event_model/v4/research/mlops/news_benzinga_url_fetch_plan.py --inventory-root-win D:/market-data/prepared/benzinga_news_url_inventory --output-root-win D:/market-data/prepared/benzinga_news_url_fetch_plan --limit-rows 200000 --shards 32 --progress-interval 50000
```

Workstation full run:

```powershell
python //DESKTOP-SAAI85T/Workstation-D/TradingML/codes/masked_event_model/v4/research/mlops/news_benzinga_url_fetch_plan.py --inventory-root-win D:/market-data/prepared/benzinga_news_url_inventory --output-root-win D:/market-data/prepared/benzinga_news_url_fetch_plan --shards 256 --progress-interval 1000000
```

Workstation full run with a custom policy:

```powershell
python //DESKTOP-SAAI85T/Workstation-D/TradingML/codes/masked_event_model/v4/research/mlops/news_benzinga_url_fetch_plan.py --inventory-root-win D:/market-data/prepared/benzinga_news_url_inventory --output-root-win D:/market-data/prepared/benzinga_news_url_fetch_plan --policy-json D:/market-data/prepared/benzinga_news_url_fetch_plan/domain_policy_override.json --shards 256 --progress-interval 1000000
```

## Arguments

- `--inventory-jsonl`: exact inventory JSONL path. If omitted, the latest inventory manifest under `--inventory-root-win` is used.
- `--inventory-root-win`: root containing URL inventory runs.
- `--output-root-win`: root where the fetch-plan run folder is created.
- `--policy-json`: optional policy override JSON.
- `--shards`: number of temporary shard files. More shards reduce peak memory during dedupe.
- `--limit-rows`: optional smoke-test cap over inventory rows.
- `--attachment-sample-limit`: number of article ids/titles retained inside each plan row as samples. The attachment JSONL still keeps all actionable mappings.
- `--progress-interval`: inventory rows between progress prints.
- `--keep-shards`: keep temporary candidate shard files for debugging.

Environment-variable defaults:

- `NEWS_BENZINGA_URL_INVENTORY_JSONL`
- `NEWS_BENZINGA_URL_INVENTORY_ROOT_WIN`
- `NEWS_BENZINGA_URL_FETCH_PLAN_OUTPUT_ROOT_WIN`
- `NEWS_BENZINGA_URL_DOMAIN_POLICY_JSON`
- `NEWS_BENZINGA_URL_FETCH_PLAN_SHARDS`

## Efficiency

The script is designed for the large inventory file:

1. Pass 1 streams the inventory once, applies policy, skips non-actionable rows, and writes compact actionable rows into hash shards.
2. Pass 2 deduplicates one shard at a time, so memory is bounded by one shard rather than the full URL corpus.
3. The attachment file is streamed during pass 1, preserving all article-to-URL relationships without keeping them in memory.

The temporary shards are removed by default after a successful run.

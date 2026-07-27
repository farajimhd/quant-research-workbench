# Benzinga News Normalization Pipeline

## Purpose

Benzinga is the canonical news provider for historical training data and live production news state. Massive is the API host for the Benzinga subscription endpoint; the separate Massive news endpoint is not part of the canonical corpus.

The pipeline has one goal: make historical and live news produce the same compact rows so models see the same representation in training and production.

## Source Scope

Accepted:

- Benzinga REST rows from `/benzinga/v2/news`.
- Articles with no ticker.
- Macro, geopolitical, crypto, ETF, sector, PDF-backed, link-only, and title-only articles.
- Provider updates to prior articles.

Rejected:

- Rows that cannot be parsed as Benzinga provider payloads.
- Duplicate provider rows where the replacement key already has a newer version.

No topic is rejected as junk during normalization. Filtering for trading relevance happens later in keyword, cheap-model, or LLM stages.

## Artifact Rule

Raw provider payloads are saved to disk first. ClickHouse stores compact data, hashes, timestamps, quality flags, and artifact references. Large raw JSON blobs and redundant full-text copies should not be persisted into the event table.

Live and historical paths must both use:

- deterministic HTML cleanup;
- deterministic text normalization;
- stable URL extraction and URL policy labels;
- the same normalizer version convention;
- the same ClickHouse table contract.

## Canonical Tables

The operational canonical contract is now structured rendering v2, documented
in `docs/data_contracts/benzinga_news_rendered_v2.md`. The v1 design below is
retained as historical rationale; `benzinga_news_normalized_v1` remains the
non-destructive rebuild source until downstream migration is complete.

The canonical normalized output is split into four tables.

### `benzinga_news_event_v1`

One row per provider article and update version.

Important fields:

- `provider`: always `benzinga`.
- `provider_article_id`: Benzinga id.
- `canonical_news_id`: stable app id, usually provider plus provider id.
- `published_at_utc`: provider publication timestamp as `DateTime64(9, 'UTC')`.
- `last_updated_at_utc`: nullable provider update timestamp.
- `downloaded_at_utc`: gateway or historical downloader observation time.
- `provider_delay_ns`: `downloaded_at_utc - published_at_utc`, when measurable.
- `title`, `normalized_title`, `teaser`.
- `text_hash`: hash of normalized title, teaser, body, external text, and PDF text.
- `article_url`, `article_url_domain`, `author`.
- `tickers`, `channels`, `provider_tags`, `image_urls`.
- `has_body`, `is_title_only`, `has_external_text`, `has_pdf`.
- `content_quality_flags`.
- `external_fetch_status`, `external_fetch_error`.
- `pdf_extract_status`, `pdf_extract_error`.
- `raw_artifact_path`, `raw_payload_hash`.
- `normalizer_version`, `updated_at_utc`.

The event table intentionally does not store `body_text`, `external_text`, `pdf_text`, raw links, or raw PDF URL arrays.

### `benzinga_news_text_v1`

One row per text part:

- `text_kind`: `body`, `external`, or `pdf`.
- `text`: normalized text capped by the configured limit.
- `text_hash`, `text_chars`, `text_bytes`.
- `source_count`: number of source fragments used.

This table is the retrieval source for model prompts and training text.

### `benzinga_news_url_v1`

One row per URL discovered from the provider row or extracted content.

Important fields:

- `url_hash`, `url`, `registered_domain`.
- `url_kind`: article, source, PDF, SEC, social, image, or other policy category.
- `url_source`: where the URL came from.
- `final_action` and `resolved_action`: deterministic policy action.
- HTTP and content metadata when fetched.
- attachment and extraction references.

This table lets enrichment attach downloaded text back to the original news row without reopening every raw JSON file.

### `benzinga_news_attachment_v1`

One row per downloaded attachment or source artifact.

Important fields:

- URL identity and domain.
- artifact path and hash.
- content type, content length, HTTP status.
- extraction method and quality.
- extracted text hash and character count.
- PDF page count when known.

## Live Gateway Contract

`services/news_gateway` is the Python live gateway. It uses the same item-level
structured renderer as the historical v2 rebuild, writes raw payloads under the
workstation market-data root, and persists v2 event, source, block, rendered,
and revision-bound ticker rows. Startup is gated by the certified v2 authority.

The old Rust gateway and its split-table canonical write path have been removed.
Historical and live news now share one structured v2 contract plus the ticker
link table.

## Historical Pipeline

The v2 rebuild reads the legacy authority by bounded UTC day, recovers each
provider raw JSON artifact, resolves available PDF artifacts, and renders in a
bounded worker pool. It writes versioned rows directly to ClickHouse in batches.
Restart certification checks every daily product—not merely the event count—so
a crash between event, source, block, rendered, and ticker inserts causes the
day to be safely replayed.

Historical external/PDF text whose original source artifact was never retained
cannot be structurally reconstructed. The renderer preserves that legacy text
with explicit provenance flags rather than presenting it as raw or silently
dropping it.

## Downstream Order

1. Stop the live news gateway.
2. Run the full structured v2 rebuild.
3. Validate whole-corpus relationships and visually inspect stratified
   original-versus-rendered Markdown samples.
4. Restart the gateway only after the v2 authority is `ready`.
5. Build one article embedding and link it to every ticker relationship.
6. Build semantic labels and reaction targets from the certified identity and
   text authorities.

SEC filings should follow the same pattern later: raw artifact first, compact event/document text tables second, market-reaction labels last.

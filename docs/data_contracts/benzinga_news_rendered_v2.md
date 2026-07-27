# Benzinga structured rendering v2

## Authority

`benzinga_structured_renderer_v2+sec_packed_text_renderer_v9` is the shared
historical/live rendering authority. It uses the current SEC packed-text
renderer for HTML structure and adds news-specific source boundaries, image
metadata, provenance, revision identity, and article/ticker separation.

The v1 normalized table remains source evidence during migration. V2 is
non-destructive and becomes writable by the live news gateway only after the
full rebuild records a `ready` authority row with zero audit errors.

## Tables

- `q_live.benzinga_news_event_v2`: current compact provider event and revision.
- `q_live.benzinga_news_source_v2`: provider body, external page, and PDF source
  parts with source hashes and provenance.
- `q_live.benzinga_news_block_v2`: ordered headings, paragraphs, lists, table
  captions/columns/rows, and image descriptions.
- `q_live.benzinga_news_rendered_v2`: one complete model/display text per
  article. This canonical value is not silently truncated.
- `q_live.benzinga_news_ticker_v2`: revision-bound article/ticker links.
- `q_live.benzinga_news_render_authority_v2`: rebuild and certification state.

Historical enrichment created before raw external artifacts were retained is
not falsely reconstructed. Its available flattened text is preserved and
marked with `legacy_flattened_enrichment` and an artifact-unavailable flag.
New live HTML enrichment retains raw HTML for the shared renderer.

## Rebuild, audit, and cutover

Stop the news gateway, then run:

```powershell
python -m pipelines.news.benzinga.run_news_rendered_v2_rebuild
```

The launcher:

1. creates the versioned tables;
2. reads the v1 source by bounded UTC day;
3. resolves raw artifacts and renders articles concurrently;
4. inserts idempotent daily products;
5. audits whole-corpus event/render cardinality, empty output, and orphans;
6. writes stratified original-versus-rendered Markdown samples;
7. marks the authority `ready` only after a complete, error-free run.

The run root and `AUDIT.md` are printed at completion. A limited run can never
certify the authority. Restarting skips days whose certified renderer-version
event count already equals the source count.

After certification, restart the news gateway. Its preflight refuses to start
against missing or failed v2 authority state, and live writes use the same
renderer and v2 tables.

## Article-level embeddings

Run:

```powershell
python -m research.news_reaction_model.openai_embeddings_v2.run_build --execute
```

The embedding pipeline reads every rendered article, including zero-ticker and
multi-ticker news, exactly once. It stores one vector per article and creates
lightweight ticker links. It never submits a duplicate embedding request for
each ticker.

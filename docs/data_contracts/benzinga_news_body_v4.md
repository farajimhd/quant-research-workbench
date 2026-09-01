# Benzinga canonical body authority V4

## Purpose

V4 is the immutable successor to Body V3. V3 remains retained evidence because
part of its historical population was rendered by an earlier implementation
under the same V3 version identifier. V4 fixes that contract-versioning defect
and adds structural related-content rejection plus an independent output purity
gate. Rebuilds never write gold, News Synthesis, operator-label, group-decision,
lesson, or note tables.

The deterministic contract is:

- source selection: `benzinga_body_source_selection_v1`;
- cleaner: `benzinga_body_cleaner_v4`;
- renderer: `benzinga_body_renderer_v4`;
- text contract: `benzinga_canonical_body_only_v2`.

## Body purity

Classification removes publisher chrome after normalizing structural prefixes
used only for classification. This catches list-prefixed forms such as
`- READ MORE:`, `* Read Also:`, bullets, and ordered-list markers while retaining
the original block as evidence. It also removes bare/concatenated CTAs such as
`Read more...`, `Read morehttps://...`, `Continue reading`, and explicit
`To read more ... click here` forms. A standalone marker excludes its associated
link headline and then resumes at the next article paragraph. A strong uppercase
terminal marker embedded in an otherwise valid paragraph is trimmed without
discarding the preceding news body.

The rendered-output purity detector is independent of the classification branch.
Certification fails if wrappers, binary/data URIs, related-content markers,
promotions, disclosures, control characters, or encoding artifacts survive.
Legitimate prose containing a lowercase phrase such as “read more detail” is
preserved.

## Physical authority

- `q_live.benzinga_news_event_v4`
- `q_live.benzinga_news_source_v4`
- `q_live.benzinga_news_block_v4`
- `q_live.benzinga_news_rendered_v4`
- `q_live.benzinga_news_ticker_v4`
- `q_live.benzinga_news_body_lineage_v2`
- shared control table `q_live.benzinga_news_body_authority_v1`

Historical and live rows use the same renderer and writer path. V2 remains the
model-input compatibility authority until downstream News Synthesis, TF-IDF,
DeepFM, and embedding successors are measured and promoted. Body V4 is the
display/search authority for the audit labeler and application news detail.
The V4 lineage row records the immediate V3 `body_hash` and renderer version;
the earlier V2-to-V3 lineage remains available in the retained V1 lineage table.

## Lifecycle

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
python -m pipelines.news.benzinga.run_news_body_v4_rebuild rebuild --execute
python -m pipelines.news.benzinga.run_news_body_v4_rebuild certify --execute
python -m pipelines.news.benzinga.run_news_body_v4_rebuild promote --execute
```

Runtime reports are written under
`D:\TradingML\runtimes\news\benzinga_news_body_v4`. The rebuild is partitioned,
idempotent, and restart-safe. Live compatibility writes require both
`NEWS_BENZINGA_BODY_V4_SHADOW_ENABLED=1` and a future
`NEWS_BENZINGA_BODY_V4_SHADOW_END_UTC`; gateway preflight requires certified V4
before the writer starts.

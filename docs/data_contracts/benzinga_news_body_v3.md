# Benzinga canonical body authority v3

## Scope and authority

This is a versioned successor to the packed-source V2 rendering tables. It
defines news body text only. It does not change the current News Synthesis,
TF-IDF/DeepFM, embedding, hypothesis, audit-review, or application read paths.
Those consumers remain bound to their existing rendered-text hashes until a
separate drift assessment and controlled migration.

The body contract has three deterministic versions:

- source selection: `benzinga_body_source_selection_v1`;
- cleaner: `benzinga_body_cleaner_v3`;
- renderer: `benzinga_body_renderer_v3`;
- text contract: `benzinga_canonical_body_only_v1`.

Raw provider and enrichment artifacts remain immutable evidence. A body rebuild
never mutates canonical gold, V61, operator labels, group decisions, lessons,
or notes.

## Source selection

Provider body is the default and normally the only `primary_body`. External
HTML and PDFs are retained as `supporting_document` sources but do not enter
`canonical_body_text`. A supporting source can be promoted only when the
provider body is absent or non-substantive, a durable non-legacy source
artifact exists, at least 120 cleaned characters remain, and deterministic
title identity is at least 0.72. Legacy flattened enrichment without URL or
artifact provenance is never promotable.

Every source records `source_role`, `disposition`, `disposition_reason`, source
identity score, original/cleaned hashes, provenance, and contract versions.
Every block records its body role, inclusion disposition and reason, original
and cleaned text hashes, and structural order. Excluded material remains
auditable in the block table but cannot appear in canonical body text.

## Deterministic cleaning

The cleaner removes empty blocks, duplicated paragraphs, source wrappers,
image/base64 metadata, navigation clusters, promotions, disclosures, and
inline related-content blocks such as `Read Also` plus the immediately
associated headline. It resumes at the following article paragraph. It
preserves article headings, paragraphs, lists, tables, quotations, and
transcripts. It does not use an LLM to delete text by semantic relevance.

## Physical tables

- `q_live.benzinga_news_event_v3`: provider event and body contract revision.
- `q_live.benzinga_news_source_v3`: all source candidates and dispositions.
- `q_live.benzinga_news_block_v3`: included and excluded ordered blocks.
- `q_live.benzinga_news_rendered_v3`: one body-only row per article.
- `q_live.benzinga_news_ticker_v3`: body-revision-bound ticker links.
- `q_live.benzinga_news_body_lineage_v1`: old packed hash to new body hash,
  with `label_mutation_status=not_mutated`.
- `q_live.benzinga_news_body_authority_v1`: build, certification, promotion,
  and rollback state.

`body_status` is `complete`, `partial`, or `missing`. Missing bodies remain in
the population; title-only records are not fabricated into article bodies.
Source, block, ticker, and lineage rows retain revision keys for audit. Current
authority checks join those products to the revision selected by the current
rendered row; superseded source revisions are evidence, not current body parts.

## Eight-phase operation

1. Contract and version the source, block, body, and lineage fields.
2. Select exactly one primary body or explicitly record a missing body.
3. Clean blocks deterministically and retain every exclusion reason.
4. Rebuild a new physical table family from current V2 event/source rows and
   retained raw artifacts. The legacy normalized table is not the rebuild
   authority because its current coverage ends before the live V2 population.
5. Certify cardinality, one-primary semantics, body purity, lineage, and
   relational integrity.
6. Enable temporary live shadow writes after certification.
7. Promote or roll back only the body authority state; downstream consumers
   are intentionally not repointed in this stage.
8. Preserve old/new hash lineage while leaving labels and derived outputs
   untouched for the later drift migration.

## Commands and lifecycle

One-day read-only validation is the launcher default:

```powershell
python -m pipelines.news.benzinga.run_news_body_v3_rebuild
```

Run a complete historical rebuild and certification:

```powershell
python -m pipelines.news.benzinga.run_news_body_v3_rebuild rebuild --execute
```

Generated status, error, control, and certification artifacts are written only
under `D:\TradingML\runtimes\news\benzinga_news_body_v3`. Daily rows use
deterministic ReplacingMergeTree identities and bounded synchronous inserts, so
an interrupted rebuild can be restarted. The default 14-day processing window
reduces ClickHouse round trips while preserving daily partitions; it can be
reduced with `--window-days` if a host needs a smaller memory bound. A limited
date run cannot certify.

After a full certified rebuild, enable temporary live comparison with:

```text
NEWS_BENZINGA_BODY_V3_SHADOW_ENABLED=1
NEWS_BENZINGA_BODY_V3_SHADOW_END_UTC=2026-09-14T00:00:00Z
```

The end timestamp is mandatory: the shared writer stops shadow writes after it,
and gateway preflight rejects an absent, invalid, or expired end time. This
prevents permanent dual storage. The News Gateway preflight also requires a
certified body authority whenever the flag is enabled. V2 remains production authority. Shadow failures are emitted
as `body_v3_shadow_failed:*` publish warnings and do not reinterpret an already
acknowledged V2 write as failed.

Record an explicit promotion or rollback state only after comparison review:

```powershell
python -m pipelines.news.benzinga.run_news_body_v3_rebuild promote --execute
python -m pipelines.news.benzinga.run_news_body_v3_rebuild rollback --execute
```

These commands do not repoint downstream readers. Repointing, recomputation,
TF-IDF/DeepFM drift measurement, News Synthesis regression review, embeddings,
and application cutover belong to the subsequent downstream migration.

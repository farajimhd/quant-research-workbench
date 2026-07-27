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
4. inserts idempotent daily products through one persistent, synchronously
   acknowledged ClickHouse session with row- and byte-bounded requests;
5. audits whole-corpus event/render cardinality, empty output, and orphans;
6. writes stratified original-versus-rendered Markdown samples;
7. marks the authority `ready` only after a complete, error-free run.

Generated status, errors, audit samples, and `AUDIT.md` are written below
`D:\TradingML\runtimes\news\benzinga_news_rendered_v2`; the final run root is
printed at completion. A limited run can never certify the authority.

Each request has a finite 180-second socket deadline. The rebuild and live
gateway explicitly disable asynchronous inserts and wait for query completion;
the rebuild reuses one HTTP connection instead of opening a new workstation-to-
WSL connection for every product batch. Transient disconnects,
connection timeouts, and HTTP 408, 425, 429, 502, 503, or 504 responses receive
up to 20 total attempts with capped exponential backoff. The retry schedule
provides eight minutes of bounded backoff, in addition to the finite duration of
the individual attempts, without allowing one socket call to hang forever.

Insert batches close at either 500 rows or a 4 MiB encoded JSONEachRow body.
A single structural row may exceed the soft batch target but may not exceed the
hard 8 MiB row contract. The renderer never truncates article content to satisfy
these limits: an anomalously large product fails before insertion and reports
only its safe article/source/block identity and byte size. Each batch has a
deterministic query ID derived from its complete, untruncated JSON content. If
the response is lost, the rebuild checks the active-query and query-log
authorities for that ID before deciding whether a retry is safe. Batch start,
completion, reconciliation, retry, row count, body bytes, maximum row bytes,
table, UTC timestamp, and UTC day are appended to `status.jsonl`; article text
is never copied into operational logs.

Retries reuse the identical bounded payload and query identity. A finished
insert is accepted without resending; an explicitly failed or still-ambiguous
insert stops instead of guessing. Deterministic `ReplacingMergeTree` row
identities remain the final idempotency boundary. Operators can override the
defaults with
`--insert-batch-size`, `--insert-target-bytes`,
`--insert-max-row-bytes`, `--clickhouse-timeout-seconds`, and
`--clickhouse-attempts`, but increasing the hard row limit is not a substitute
for auditing an anomalously large structural product.

If the process still exits, run the same command again. Restarting verifies all
five products and skips complete days. A partially inserted day is rebuilt
idempotently from the retained v1 source and raw artifacts, including its
complete rendered article text. It is not considered complete merely because
its event, source, or block rows exist. No table deletion or full restart is
required. Query/schema errors are not retried because they require a code or
data-contract correction.

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

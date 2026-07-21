# SEC Rendered Text v3 Rebuild

This is the only full-corpus renderer rebuild path. It reads the complete raw
text authority from `q_live.sec_filing_text_v3`; it does not reacquire SEC
archives or rebuild filing, document, entity, XBRL, bridge, token, or embedding
tables.

The active historical and live producers both call
`sec_packed_text_renderer_v8`. A repository search found no executable v1-v7
renderer or extractor-local v1 normalizer path. The older
`sec_filing_text_repair_rebuild.py` remains intentionally because it repairs
selected source rows from archives; it is not a full-corpus rendered-table
builder.

The renderer authority lives in
`pipelines/sec/edgar/sec_pipeline/text_renderer.py`, which is inside the tree
that `sec_gateway` synchronizes to the workstation. Historical, live,
market-SIP, embedding, repair, and audit consumers import that one module.
There is no minimum rendered length and no rendered-text cap argument.

Keep `sec_gateway` and `text_embed_gateway` stopped for the complete run. The
script captures the source watermark and refuses validation or cutover if the
source changes. The watermark includes a full logical-row metadata hash, not
only row counts. A file-root probe also fails before table creation when Python
and ClickHouse do not share the workstation `D:\market-data` mount.

## Dry Run

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_filing_text_rendered_v3_rebuild.py
```

## Full Rebuild And Cutover

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_filing_text_rendered_v3_rebuild.py `
  --workers 4 `
  --max-concurrent-inserts 2 `
  --execute `
  --cutover `
  --confirm-sec-gateway-stopped
```

Four workers are intentional. A monthly partition can contain more than 200
billion source characters and an individual source row can exceed 600 million
characters. ClickHouse exports exactly one monthly partition at a time while up
to four Python workers render already exported partitions. This overlaps
database I/O with CPU rendering without allowing large source scans to compete
for server memory. Increase the renderer workers to eight only after observing
stable RAM, temporary disk, and ClickHouse merge pressure.

Render concurrency and ClickHouse insert concurrency are independent. Keep
`--max-concurrent-inserts 2` even when increasing `--workers`: Parquet decoding
and insertion are the memory-heavy database stage. A production run with 16
unbounded insert lanes reached 212 GiB resident memory on a 226 GiB ClickHouse
server and triggered the global overcommit tracker after 96 bundles had already
committed. The shared cross-process insert gate keeps all renderer workers busy
while allowing only two inserts to decode and merge at once. Waiting, active,
and completed insert states are printed per partition, bundle, and part.

The rebuild work table is monthly partitioned even though the final rendered
table is CIK-hash partitioned. Writing every small bundle directly into the 64
CIK partitions created 64 part families per insert and eventually drove dozens
of concurrent background merges to the ClickHouse memory ceiling. On resume,
the script detects the old hash-partitioned staging table, copies its validated
logical rows server-side into a monthly work table, atomically swaps the work
table into the existing staging name, and stops merges on the retained legacy
copy. Existing renderer and bundle checkpoints remain valid; no source text is
rerendered during this migration.

After all bundle and corpus validations pass, cutover creates a fresh canonical
CIK-hash table and streams one hash partition at a time from the monthly work
table. The transfer intentionally has neither source `FINAL` nor a global
`ORDER BY`: both materialize all text in a CIK partition and the original
bucket-7 transfer exceeded its 32 GiB query limit while executing
`ReplacingSorted`. A later fixed 2,048-row stream exposed the other side of the
same defect: row counts do not bound variable-width SEC text, and bucket 9
requested an 8 GiB text-column allocation. Cutover now reads the largest stored
`text_byte_count` before transfer and derives
`max_block_size=floor(1 GiB / max_text_byte_count)`, bounded to 1-256 rows. The
current 254,521,551-byte maximum therefore uses four-row source blocks.
Destination blocks are independently formed between 512 MiB and 1 GiB under an
8 GiB query ceiling. New rendered tables explicitly enable adaptive 10 MiB
MergeTree granules. If source revisions produce more physical than logical
rows, a synchronous destination-partition `OPTIMIZE ... FINAL` selects the
authoritative `source_revision_rank` winner before validation.
Every partition must match the source logical row count and checksum. A partial
partition is dropped and rebuilt on resume with insert deduplication disabled;
completed partitions are reused. Only after all 64 partitions match does the
script exchange the new table with `sec_filing_text_rendered_v3`, retain the
previous target backup, and remove the temporary work and legacy staging
tables. This gives live reads the required same-CIK revision locality without
creating a merge storm during the historical build.

The exchange and cleanup are separate durable phases. After bounded
target/staging validation, the script atomically records `cutover.json` with the
run identity, backup name, row count, and checksum before deleting staging. If
cleanup is interrupted, resume detects the run-specific backup and absent
final-hash table, verifies the immutable source and filing watermarks, reruns
the bounded target/staging audit, and performs cleanup without rebuilding or
exchanging data. ClickHouse's 50 GiB drop guard is overridden only for table
names matching the renderer's run-scoped staging convention; canonical and
backup names are rejected by construction.

The exact previously failing bucket was exercised against the production
staging corpus in a temporary table: 375,581 rows copied and matched checksum
`14158962764992567782` in 568 seconds. Observed query memory remained below
1 GiB, compared with the former 32 GiB failure. The temporary table was dropped
after validation.

The subsequent bucket-9 failure was also reproduced and retested against the
exact production staging rows. Its 407,229 logical and physical rows matched
checksum `1437036227408557971`; observed memory peaked near 3.25 GiB instead of
requesting an 8 GiB text-column allocation. The independent post-query audit
passed and removed the temporary table.

Corpus validation is bounded in the same way. Eight validation lanes recalculate
text hashes, UTF-8 character counts, byte counts, and renderer-version fields one
monthly partition at a time. Cross-month logical-key identity is then checked in
the 64 CIK buckets used by the final layout. Each query uses one ClickHouse
thread and an 8 GiB limit, so validation has a 64 GiB aggregate ceiling instead
of a whole-corpus `FINAL` query. Final source/target checksums are also summed
from those 64 bounded buckets.

Before rendering resumes, the script verifies that `sec_filing_text_v3` uses
`ReplacingMergeTree(source_revision_rank)`. An older deployed table used
`ReplacingMergeTree(inserted_at)`, which allowed a later database insert to
override a newer SEC archive revision. The repair creates the canonical table,
attaches each monthly partition from the old table without retransmitting or
recompressing source text, validates physical row/byte/hash totals, repairs
child `filing_id` values against the canonical `(CIK, accession)` parent, and
atomically exchanges the tables. The insertion-ranked table is retained as
`sec_filing_text_v3_inserted_at_engine_backup` with merges stopped.

The migration report records every month where insertion-time and SEC-revision
authority differed. On the same-run resume, only those rendered staging
partitions, bundle checkpoints, exports, and lookup rows are invalidated. The
run manifest is rebased to the revision-ranked source watermark. Unaffected
completed months remain reusable, while no stale authority row can survive the
final validation or cutover.

The parent process owns each bounded monthly export. A renderer worker processes
that export as deterministic bundles of eight Parquet row groups. Every bundle
is rendered, inserted, checkpointed in
`sec_filing_text_rendered_rebuild_bundle_manifest_v3`, and cleaned before the
next bundle starts. Completed bundles are skipped on same-run resume. Stable
ClickHouse insert-deduplication tokens and a 100,000-block non-replicated
deduplication window make a retry idempotent even if the process stopped after
the insert committed but before its checkpoint was written. A failed bundle
atomically writes `STOP_REQUESTED.json`; other workers stop at their next bundle
boundary instead of draining an entire multi-hour month. Each run owns a
separate staging table. The worker resolves each source `filing_id` through the
run's compact form map so structured XML classification receives the
authoritative parent form type. Only explicitly classified structured fund XML
is omitted from the rendered text table; an empty result for any other source
row fails loudly.

The first production attempt failed because every monthly worker joined the
entire `sec_filing_v3 FINAL` relation while exporting large source text. Each
query read roughly 8.2 million rows and 26-30 GiB before reaching the 32 GiB
query limit. Removing that join exposed a second issue: selecting source text
with `FINAL` performs cross-partition revision reconciliation and again exceeds
32 GiB. The corrected path exports compact filing and source-authority metadata
once into an indexed local SQLite map. Workers then stream physical rows from
only one monthly partition without `FINAL`, retain exactly the authoritative
cross-partition source version locally, and perform no large-text sort or join.
Do not resume a run created by the pre-fix implementation; start a new run.

On Windows, each PyArrow reader is explicitly closed before its temporary
Parquet export is deleted. If interruption occurs after the complete SQLite
lookup has been committed but before its atomic rename, resuming the same run
validates and promotes `render_lookup.sqlite.tmp` instead of repeating the
multi-million-row export and import.

The source transport checks Parquet page size after every row, targets 256 MiB
row groups by bytes, and disables unnecessary parallel encoding and bloom
filters. This is required because grouping 1,024 unusually large SEC text rows
can exceed Parquet's uncompressed page limit even though the largest individual
source row is about 601 MB. Partition submission is bounded to the renderer
worker count. The first export, render, or insert failure stops new exports,
requests cooperative cancellation of active workers, and writes the exact
partition, bundle, stage, and exception to the ClickHouse manifests. Bundle
progress reports row-group bounds, source/rendered rows, source-character
throughput, and wall time so CPU rendering speed is visible separately from
ClickHouse export and insert time.

Each completed source export now receives an atomic `source_export.json`
receipt bound to the immutable run, source table, partition, expected logical
counts, Parquet filename and size, physical row count, and exact column
contract. Resume validates that receipt and the Parquet footer before reusing
the export. Complete exports from runs created immediately before receipts were
introduced are adopted only after the same structural and row-count checks.
A Parquet read failure removes that partition's export and receipt after the
reader closes. Before re-export, the next resume resets only that month's staged
rows and bundle checkpoints because a replacement Parquet file is not assumed
to preserve physical row-group boundaries. Renderer/content failures retain
the valid source export and every completed bundle, avoiding both ClickHouse
transport and repeated rendering.

Image-only HTML is not treated as an empty render. The canonical renderer
preserves the HTML title plus every non-tracking image source, alt/title label,
and declared dimension as a compact image inventory. It explicitly flags that
the referenced image content was not OCR-extracted.

Structurally empty submitted documents are distinct from renderer loss. Empty
HTML wrappers such as `<html><body></body></html>`, empty XML roots, and
zero-byte source payloads produce a deterministic presence-only record with
document metadata, source character count, `document_presence_only`, and
`no_renderable_content`. No source text or image content is fabricated. If the
HTML parser observes visible substantive characters but produces no blocks,
the result remains empty and the rebuild still fails the partition. This keeps
parser loss fatal while allowing genuine SEC-submitted placeholders to remain
visible to downstream filing models.

Substantive XML comments are model-visible source content. This matters for
`ABS-EE` `EX-103` asset-related documents whose otherwise empty `<assetdata>`
root contains the complete explanatory narrative in comments. The renderer
preserves those comments in document order and flags
`xml_comments_preserved`. For malformed SEC HTML, `<head>` is explicit parser
state and the opening `<body>` ends it even when the submitter placed the
closing `</head>` after the body. This prevents legal opinions and similar
exhibits from being discarded as header metadata.

Legacy SEC fixed-width HTML tables are also model-visible. Historical filings
may use `<S>` and `<C>` column markers without `<TR>/<TD>` and may omit the
closing `</CAPTION>`. The renderer uses those explicit SEC markers to separate
caption/header lines from the body, removes only separator rules, and emits
header-labelled rows. Accession `0001445546-20-000575`, document
`exhibit_e2.txt`, now renders all `SERIES` and `EFFECTIVE DATE` pairs instead of
producing an empty fatal result.

## Resume

Use the `run_id` printed by the interrupted run:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_filing_text_rendered_v3_rebuild.py `
  --run-id sec_render_v8_20260716_151718 `
  --workers 4 `
  --max-concurrent-inserts 2 `
  --execute `
  --cutover `
  --confirm-sec-gateway-stopped
```

The cutover is forbidden for limited test runs. A successful cutover retains
the prior table as `sec_filing_text_rendered_pre_v8_<timestamp>_v3`; remove it
only after the v8 corpus and downstream token audit have been accepted.

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

The parent process owns each bounded monthly export. A renderer worker then
owns that exported partition through v8 rendering, Parquet insertion,
ClickHouse checkpoint, and temporary-file cleanup. A failed partition leaves
the staging table and durable successful checkpoints intact. Each run owns a
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
drains only already active workers, and writes the failed stage and exception to
the ClickHouse rebuild manifest.

Each completed source export now receives an atomic `source_export.json`
receipt bound to the immutable run, source table, partition, expected logical
counts, Parquet filename and size, physical row count, and exact column
contract. Resume validates that receipt and the Parquet footer before reusing
the export. Complete exports from runs created immediately before receipts were
introduced are adopted only after the same structural and row-count checks.
A Parquet read failure removes that partition's export and receipt after the
reader closes, so only the damaged partition is re-exported. Renderer/content
failures retain the valid source export and avoid repeating hours of ClickHouse
transport.

Image-only HTML is not treated as an empty render. The canonical renderer
preserves the HTML title plus every non-tracking image source, alt/title label,
and declared dimension as a compact image inventory. It explicitly flags that
the referenced image content was not OCR-extracted. Truly empty non-structured
documents still fail the partition instead of disappearing silently.

Substantive XML comments are model-visible source content. This matters for
`ABS-EE` `EX-103` asset-related documents whose otherwise empty `<assetdata>`
root contains the complete explanatory narrative in comments. The renderer
preserves those comments in document order and flags
`xml_comments_preserved`. For malformed SEC HTML, `<head>` is explicit parser
state and the opening `<body>` ends it even when the submitter placed the
closing `</head>` after the body. This prevents legal opinions and similar
exhibits from being discarded as header metadata.

## Resume

Use the `run_id` printed by the interrupted run:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_filing_text_rendered_v3_rebuild.py `
  --run-id sec_render_v8_YYYYMMDD_HHMMSS `
  --workers 4 `
  --execute `
  --cutover `
  --confirm-sec-gateway-stopped
```

The cutover is forbidden for limited test runs. A successful cutover retains
the prior table as `sec_filing_text_rendered_pre_v8_<timestamp>_v3`; remove it
only after the v8 corpus and downstream token audit have been accepted.

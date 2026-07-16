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
characters. Increase to eight only after observing stable RAM, temporary disk,
and ClickHouse merge pressure.

Each worker owns one monthly source partition through export, v8 rendering,
Parquet insertion, ClickHouse checkpoint, and temporary-file cleanup. A failed
partition leaves the staging table and durable successful checkpoints intact.
Each run owns a separate staging table. The worker resolves each source
`filing_id` through the run's compact form map so structured XML classification
receives the authoritative parent form type. Only explicitly classified structured fund XML is omitted from the
rendered text table; an empty result for any other source row fails loudly.

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

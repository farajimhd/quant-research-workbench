# SEC Filing Archive Rebuild Guide

`sec_filing_archive_rebuild.py` is the full-history filing text path. It keeps
the original SEC `.nc.tar.gz` archives and bounds temporary storage by making
each daily archive an independent extraction and insertion transaction.

Each fixed worker lane performs:

1. extract and parse one assigned daily archive;
2. render every supported submitted text document without a text cap;
3. write gzip-compressed v3 JSONEachRow parts;
4. preflight every non-empty part through ClickHouse `file()`;
5. insert filing, document, source text, rendered text, and skip rows;
6. verify successful part-manifest status;
7. record `sec_filing_archive_ingest_manifest_v3` completion;
8. delete that archive's temporary parts;
9. advance to the next archive assigned to the same lane.

The Rich historical-fill terminal shows one stable row per lane with Extract,
Preflight, Insert, Verify, Cleanup, lane progress, row count, and current
temporary size columns. An overall archive progress bar includes archives
already completed by an earlier run.

The rebuild fails fast on the first archive error. The terminal retains a red
failure panel with the archive, lane, and exception; all lanes stop taking new
archives, active extractors stop at the next filing-member boundary, and fully
written parts remain eligible for resume. Peer-cancelled archives are reported
separately from the original failure.

Complete SEC source text can produce JSONEachRow records far wider than
ClickHouse's parallel parser chunk. The archive path therefore defaults to
`--no-input-format-parallel-parsing --input-max-block-rows 16` and permits only
two concurrent source/rendered-text inserts. Extraction still uses all 32
workers; only the memory-sensitive ClickHouse text inserts are serialized by
`--text-insert-concurrency 2`.

Run through the unified historical fill:

```powershell
Set-Location D:\TradingML\codes\quant_research_workbench_pipelines
python pipelines\sec\edgar\sec_historical_gap_fill.py --execute
```

Direct focused run:

```powershell
python pipelines\sec\edgar\sec_filing_archive_rebuild.py `
  --start-date 2019-01-01 `
  --end-date 2026-07-12 `
  --workers 32 `
  --no-input-format-parallel-parsing `
  --input-max-block-rows 16 `
  --text-insert-concurrency 2 `
  --execute
```

Resume is automatic. The archive manifest skips fully inserted archives. A
state journal reuses compressed parts left after an interruption between
extraction and insertion. For legacy failed extractor runs, recovery only uses
archive dates explicitly logged with `status=ok`; files from active or failed
archives are not inserted. Once the selected range completes, obsolete
unmanifested temporary parts for that range are removed.

The original files under `D:/market-data/sec_core/daily_archives` are never
deleted. They remain the source for future parser audits and repairs.

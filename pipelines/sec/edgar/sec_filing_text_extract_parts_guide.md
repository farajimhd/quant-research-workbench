# SEC Filing Text Extract Parts Guide

This script parses SEC daily `.nc.tar.gz` archives and writes DB-ready,
byte-bounded Parquet shards. It does not insert anything into ClickHouse.

Output datasets:

- `sec_filing_v3_parts`: archive-derived parent rows for filings missing from `q_live.sec_filing_v3`.
- `sec_filing_document_v3_parts`: real archive `<DOCUMENT>` metadata.
- `sec_filing_text_v3_parts`: submitted text-source documents.
- `sec_filing_text_rendered_v3_parts`: packed renderer/normalizer output for useful text documents.
- `sec_filing_document_skip_v3_parts`: explicit skip records for structured XML/XBRL, images, PDFs without extraction, and low-signal documents.

The extractor uses `q_live.sec_filing_v3` as the filing parent table. It stores
submitted source text and deterministic renderer output; training jobs should add
prompt headers later by joining to filing/document metadata.

## Workstation Smoke

Run this first. It processes 25 filings from one known archive and writes a small output under `D:/market-data/prepared/sec_filing_text_parts_smoke`.

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_filing_text_extract_parts.py --archive-root-win D:/market-data/sec_core/daily_archives --output-root-win D:/market-data/prepared/sec_filing_text_parts_smoke --start-date 2025-01-02 --end-date 2025-01-03 --archive-workers 1 --max-filings-per-archive 25 --sample-limit 20 --progress-every 1
```

Expected shape from the laptop smoke:

```text
archives=1/1
missing parent rows written=4
documents=62
text=12
skips=50
errors=0
```

Parent-missing filings are not dropped. They are written as `sec_filing_v3` parent parts first, then document/text/skip rows are written against those generated parents.

## Full Historical Rebuild

Do not use this standalone extractor to stage the full archive history. Full
source text can require several terabytes. Use the bounded archive rebuild,
which writes 256 MiB row groups into 1 GiB Parquet files, inserts, verifies, and
deletes temporary shards one archive at a time:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_filing_archive_rebuild.py --start-date 2019-01-01 --end-date 2026-07-12 --execute
```

The unified historical fill invokes this path automatically:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_historical_gap_fill.py --execute
```

## Important Arguments

- `--start-date`, `--end-date`: archive date range; end is exclusive.
- `--archive-workers`: number of archives processed concurrently. Use `2` conservatively and `4` when memory/IO look stable.
- `--max-filings-per-archive`: smoke/testing cap only. Do not use for final extraction.
- `--sample-limit`: number of text samples retained in the run output for manual review.
- Every nonempty supported render is persisted. There is no minimum-length
  filter and no rendered-text cap. Only explicit non-text formats and the
  structured-fund-XML policy produce skip rows.
- `--parent-window-days-before`, `--parent-window-days-after`: accepted timestamp lookup window around each archive date.
- `--parquet-row-group-mb`: logical byte target for each row group; default `256`.
- `--parquet-file-mb`: logical byte target for each file; default `1024`.
- `--parquet-compression-level`: ZSTD staging compression level; default `1`.

## Output

Each run writes:

```text
sec_filing_text_extract_manifest.json
sec_filing_text_extract_summary.md
sec_filing_text_extract_errors.jsonl
sec_filing_text_extract_samples.jsonl
parts/sec_filing_document_v3_parts/*.parquet
parts/sec_filing_v3_parts/*.parquet
parts/sec_filing_text_v3_parts/*.parquet
parts/sec_filing_text_rendered_v3_parts/*.parquet
parts/sec_filing_document_skip_v3_parts/*.parquet
```

Use `sec_filing_text_extract_manifest.json` as the input to `sec_filing_text_clickhouse_file_ingest.py`.

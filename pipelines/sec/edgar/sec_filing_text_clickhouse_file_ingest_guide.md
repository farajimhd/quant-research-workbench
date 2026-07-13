# SEC Filing Text ClickHouse File Ingest Guide

This script loads typed Parquet shards into ClickHouse through the server-side
`file()` table function and native Parquet v3 reader.

Targets:

- `q_live.sec_filing_v3`
- `q_live.sec_filing_document_v3`
- `q_live.sec_filing_text_v3`
- `q_live.sec_filing_text_rendered_v3`
- `q_live.sec_filing_document_skip_v3`
- `q_live.sec_filing_text_file_ingest_manifest_v3`

The daily archive files stay on disk. The database receives document metadata,
submitted text-source rows, rendered/normalized text rows, and skip records only.

The preferred historical path is `sec_filing_archive_rebuild.py`, which invokes
the same preflight/insert manifest contract per archive and removes temporary
parts only after successful verification. Use this standalone loader for smoke,
repair, and legacy manifest workflows.

## Smoke Preflight

Use the manifest from the smoke extractor run.

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_filing_text_clickhouse_file_ingest.py --manifest-json D:/market-data/prepared/sec_filing_text_parts_smoke/<run_id>/sec_filing_text_extract_manifest.json --parts-root-win D:/market-data --parts-root-ch /mnt/d/market-data --preflight-only
```

Expected laptop smoke result:

```text
preflight_part=1/5 dataset=filing rows=4
preflight_part=2/5 dataset=document rows=62
preflight_part=3/5 dataset=text_source rows=12
preflight_part=4/5 dataset=text rows=12
preflight_part=5/5 dataset=skip rows=50
preflight=done
```

## Full Preflight

Run this after the full extractor completes.

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_filing_text_clickhouse_file_ingest.py --manifest-json D:/market-data/prepared/sec_filing_text_parts/<run_id>/sec_filing_text_extract_manifest.json --parts-root-win D:/market-data --parts-root-ch /mnt/d/market-data --preflight-only
```

## Execute Load

Only run this after preflight succeeds.

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_filing_text_clickhouse_file_ingest.py --manifest-json D:/market-data/prepared/sec_filing_text_parts/<run_id>/sec_filing_text_extract_manifest.json --parts-root-win D:/market-data --parts-root-ch /mnt/d/market-data --execute --skip-preflight
```

Resume a partially failed load:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_filing_text_clickhouse_file_ingest.py --manifest-json D:/market-data/prepared/sec_filing_text_parts/<run_id>/sec_filing_text_extract_manifest.json --parts-root-win D:/market-data --parts-root-ch /mnt/d/market-data --execute --skip-preflight --retry-failed
```

## Important Arguments

- `--parts-root-win`: Windows path prefix for the part files.
- `--parts-root-ch`: matching ClickHouse server path prefix. On the workstation this is `/mnt/d/market-data`.
- `--dataset`: optional `filing`, `document`, `text`, or `skip` subset for debugging.
- `--limit-parts`: optional cap for smoke/debug only.
- `--force`: insert even if the part manifest says the part already loaded. Use only for deliberate reprocessing.
- `--retry-failed`: retry parts whose latest manifest status is `failed`.
- `--skip-preflight`: skip local Parquet footer validation during execute. Use this only after `--preflight-only` succeeded for the same manifest.

## Safety Checks

- The loader validates target v3 tables are readable before inserting.
- The loader validates Parquet schema and row counts from file footers without decoding text columns.
- ClickHouse verifies Parquet page checksums and reads files and row groups in parallel.
- Successful parts are recorded in `q_live.sec_filing_text_file_ingest_manifest_v3`.
- Re-running without `--force` skips parts already marked `ok` for the same source run.

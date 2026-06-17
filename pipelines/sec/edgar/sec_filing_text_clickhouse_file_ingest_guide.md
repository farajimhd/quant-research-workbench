# SEC Filing Text ClickHouse File Ingest Guide

This script loads the extractor part files into ClickHouse through the server-side `file()` table function.

Targets:

- `q_live.sec_filing_document_v2`
- `q_live.sec_filing_text_v2`
- `q_live.sec_filing_document_skip_v1`
- `q_live.sec_filing_text_file_ingest_manifest_v1`

The daily archive files stay on disk. The database receives document metadata, clean normalized text, and skip records only.

## Smoke Preflight

Use the manifest from the smoke extractor run.

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_filing_text_clickhouse_file_ingest.py --manifest-json D:/market-data/prepared/sec_filing_text_parts_smoke/<run_id>/sec_filing_text_extract_manifest.json --parts-root-win D:/market-data --parts-root-ch /mnt/d/market-data --preflight-only
```

Expected laptop smoke result:

```text
preflight_part=1/3 dataset=document rows=40
preflight_part=2/3 dataset=text rows=8
preflight_part=3/3 dataset=skip rows=32
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
- `--dataset`: optional `document`, `text`, or `skip` subset for debugging.
- `--limit-parts`: optional cap for smoke/debug only.
- `--force`: insert even if the part manifest says the part already loaded. Use only for deliberate reprocessing.
- `--retry-failed`: retry parts whose latest manifest status is `failed`.
- `--skip-preflight`: skip the file row-count scan during execute. Use this only after `--preflight-only` succeeded for the same manifest.

## Safety Checks

- The loader validates target v2 tables are readable before inserting.
- The loader validates every part through `file()` and row counts before insert.
- For large loads, run `--preflight-only` once, then `--execute --skip-preflight` to avoid reading the full part set twice.
- Successful parts are recorded in `q_live.sec_filing_text_file_ingest_manifest_v1`.
- Re-running without `--force` skips parts already marked `ok` for the same source run.

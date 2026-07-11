# SEC Text v3 Schema Guide

Use this script to create the archive-derived SEC document/text targets:

- `q_live.sec_filing_document_v3`
- `q_live.sec_filing_text_v3`
- `q_live.sec_filing_text_rendered_v3`
- `q_live.sec_filing_document_skip_v3`

These tables are the current archive-derived SEC document/text targets.
`sec_filing_text_v3` stores submitted text-source documents before packed
renderer output is stored in `sec_filing_text_rendered_v3`.

## Local Dry Run

```powershell
python D:\TradingCodes\quant-research-workbench\pipelines\sec\edgar\sec_text_v3_schema.py
```

The dry run renders SQL and writes a manifest without touching ClickHouse.

## Local Execute

```powershell
python D:\TradingCodes\quant-research-workbench\pipelines\sec\edgar\sec_text_v3_schema.py --execute
```

## Workstation Runtime Dry Run

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_text_v3_schema.py
```

## Workstation Runtime Execute

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_text_v3_schema.py --execute
```

## Required Environment

The script loads env files through the repo's shared env discovery. Required values:

```text
REAL_LIVE_CLICKHOUSE_WRITE_URL or SEC_CLICKHOUSE_URL or QMD_CLICKHOUSE_URL
REAL_LIVE_CLICKHOUSE_WRITE_USER or SEC_CLICKHOUSE_USER or QMD_CLICKHOUSE_USER
REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD or SEC_CLICKHOUSE_PASSWORD or QMD_CLICKHOUSE_PASSWORD
CLICKHOUSE_LIVE_STORAGE_POLICY
```

`CLICKHOUSE_LIVE_STORAGE_POLICY` is required so SEC text tables are created on the live SSD storage policy.

## Output

Each run writes:

```text
D:/market-data/prepared/sec_text_v3_schema/<run_id>/rendered_sec_text_v3_schema.sql
D:/market-data/prepared/sec_text_v3_schema/<run_id>/sec_text_v3_schema_manifest.json
D:/market-data/prepared/sec_text_v3_schema/<run_id>/sec_text_v3_schema_execution.jsonl
```

After execution, rerun the integrity audit with `--require-v3-tables`.

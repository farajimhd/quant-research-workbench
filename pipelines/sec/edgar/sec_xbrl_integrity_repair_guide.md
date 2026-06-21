# SEC XBRL Integrity Repair

Use this script after SEC filing/text/XBRL loads when the integrity audit reports
XBRL relationship failures.

The script is idempotent and dry-run by default. It handles three repair stages:

- `drop-legacy`: drops stale `sec_filing_document_v1` and `sec_filing_text_v1`.
- `filing-parents`: inserts missing `sec_filing_v2` parent rows for 2019+
  companyfacts accessions that do not currently join to `sec_filing_v2`.
- `frame-parents`: inserts missing `sec_xbrl_frame_v1` rows derived from
  `sec_xbrl_frame_observation_v1`.

It does not call SEC APIs. It repairs relationships from data that is already in
ClickHouse.

## Dry Run

Run this first on the workstation:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_xbrl_integrity_repair.py --database q_live --scope-start-date 2019-01-01
```

The dry run writes SQL and summaries under:

```text
D:/market-data/prepared/sec_xbrl_integrity_repair/<run_id>/
```

## Execute Full Repair

This drops the two stale v1 tables and repairs both XBRL relationship failures:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_xbrl_integrity_repair.py --database q_live --scope-start-date 2019-01-01 --execute
```

## Execute Only One Stage

Drop only legacy tables:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_xbrl_integrity_repair.py --database q_live --stages drop-legacy --execute
```

Repair only missing `sec_filing_v2` parents:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_xbrl_integrity_repair.py --database q_live --scope-start-date 2019-01-01 --stages filing-parents --execute
```

Repair only missing frame parents:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_xbrl_integrity_repair.py --database q_live --scope-start-date 2019-01-01 --stages frame-parents --execute
```

## Progress Report

The terminal prints:

- before snapshot: legacy v1 presence, XBRL orphan rows/accessions, frame orphan
  rows/frames;
- stage progress and query elapsed time;
- after snapshot with remaining orphan counts;
- paths to JSON and Markdown summaries.

Each run writes:

```text
sec_xbrl_integrity_repair_manifest.json
sec_xbrl_integrity_repair_events.jsonl
sec_xbrl_integrity_repair_summary.json
sec_xbrl_integrity_repair_summary.md
drop_legacy_v1_tables.sql
repair_xbrl_missing_filing_parents.sql
repair_xbrl_missing_frame_parents.sql
```

## Validate After Repair

Run the scoped audit again:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_integrity_audit.py --archive-root-win D:/market-data/sec_core/daily_archives --scope-start-date 2019-01-01 --require-v2-tables
```

Expected outcome:

- no `sec_filing_document_v1` or `sec_filing_text_v1` table checks;
- `xbrl_company_facts_without_filing_in_scope = 0`;
- `xbrl_frame_observations_without_frame_in_scope = 0`.

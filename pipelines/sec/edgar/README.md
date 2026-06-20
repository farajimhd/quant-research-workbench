# SEC EDGAR Pipeline

This package contains the SEC EDGAR historical workflow:

- SEC bulk and daily archive download helpers;
- daily archive validation and content discovery;
- exact-file failed archive deletion;
- acceptance timestamp repair helpers;
- archive-derived acceptance timestamp repair for date-only parent rows;
- submissions-bulk acceptance timestamp repair for date-only parent rows;
- archive-derived filing document/text extraction and ClickHouse file ingest;
- historical backfill orchestration over the stages that exist today;
- legacy bulk mirror ingest helpers retained for traceability.

Preferred current historical orchestration path:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_historical_backfill_orchestrator.py --start-date 2019-01-01 --end-date 2026-06-17
```

Run a historical gap fill on the workstation:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_historical_backfill_orchestrator.py --start-date 2026-06-17 --end-date 2026-06-21 --execute
```

Run a full initial fill on the workstation:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_historical_backfill_orchestrator.py --start-date 2019-01-01 --end-date 2026-06-17 --stages initial-fill --execute
```

Targeted validation path:

```powershell
python -m pipelines.sec.edgar.sec_validate_downloaded_archives --help
```

Acceptance timestamp repair path:

```powershell
python -m pipelines.sec.edgar.sec_acceptance_archive_repair --help
```

Run the current archive-derived acceptance repair on the workstation:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_acceptance_archive_repair.py --archive-root-win D:/market-data/sec_core/daily_archives --output-root-win D:/market-data/prepared/sec_acceptance_archive_repair --start-date 2019-01-01 --end-date 2026-06-16 --archive-workers 4 --execute
```

Run the submissions-bulk fallback timestamp repair on the workstation:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_acceptance_fallback_submissions_repair.py --artifact-root-win D:/market-data/sec_core --output-root-win D:/market-data/prepared/sec_acceptance_fallback_submissions_repair --execute
```

See `sec_historical_backfill_orchestrator_guide.md` for the full stage order, one-command historical runs, smoke tests, and operational notes from the manual runs.

SEC filing text path:

```powershell
python -m pipelines.sec.edgar.sec_filing_text_extract_parts --help
python -m pipelines.sec.edgar.sec_filing_text_clickhouse_file_ingest --help
```

Run `sec_filing_text_extract_parts_guide.md` first, then `sec_filing_text_clickhouse_file_ingest_guide.md`.

Old `research/mlops/sec_*.py` wrappers are archived under `pipelines/archive/legacy_wrappers/research_mlops/`. Do not use them for new runs.

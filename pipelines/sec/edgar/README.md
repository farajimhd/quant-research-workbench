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

Run a full historical fill on the workstation. This refreshes SEC bulk files first, including `submissions.zip`:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_historical_backfill_orchestrator.py --start-date 2026-06-17 --end-date 2026-06-21 --execute
```

Run a filing-content gap fill only when SEC bulk files are already current:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_historical_backfill_orchestrator.py --start-date 2026-06-17 --end-date 2026-06-21 --stages gap-fill --execute
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

Run the XBRL companyfacts catch-up when filing/text tables are newer than
`sec_xbrl_company_fact_v1`:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_xbrl_companyfacts_catchup.py --read-database q_live --write-database q_live --workers 4 --batch-size 10000 --execute
```

Run the XBRL integrity repair after an audit reports missing XBRL filing parents
or frame parents. This also drops stale `sec_filing_document_v1` and
`sec_filing_text_v1`:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_xbrl_integrity_repair.py --database q_live --scope-start-date 2019-01-01 --execute
```

See `sec_historical_backfill_orchestrator_guide.md` for the full stage order, one-command historical runs, smoke tests, and operational notes from the manual runs.
See `sec_xbrl_companyfacts_catchup_guide.md` for dry runs, temp-db smoke tests,
and XBRL catch-up behavior.
See `sec_xbrl_integrity_repair_guide.md` for the XBRL relationship repair and
legacy v1 table drop commands.

SEC filing text path:

```powershell
python -m pipelines.sec.edgar.sec_filing_text_extract_parts --help
python -m pipelines.sec.edgar.sec_filing_text_clickhouse_file_ingest --help
```

Run `sec_filing_text_extract_parts_guide.md` first, then `sec_filing_text_clickhouse_file_ingest_guide.md`.

Old `research/mlops/sec_*.py` wrappers are archived under `pipelines/archive/legacy_wrappers/research_mlops/`. Do not use them for new runs.

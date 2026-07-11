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

Preferred current historical gap-fill path used by SEC Gateway:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_historical_gap_fill.py --start-date 2026-06-17 --end-date 2026-06-21 --read-database q_live --write-database q_live --coverage-table sec_coverage_manifest_v1 --bulk-mirror-database sec_core --artifact-root-win D:/market-data/sec_core --core-output-root-win D:/market-data/prepared/sec_core --output-root-win D:/market-data/prepared/sec_historical_gap_fill --daily-archive-output-root-win D:/market-data/prepared/sec_daily_feed_archives --archive-validation-output-root-win D:/market-data/prepared/sec_downloaded_archive_validation --text-parts-output-root-win D:/market-data/prepared/sec_filing_text_parts --xbrl-output-root-win D:/market-data/prepared/sec_xbrl_companyfacts_catchup --xbrl-repair-output-root-win D:/market-data/prepared/sec_xbrl_integrity_repair --integrity-audit-output-root-win D:/market-data/prepared/sec_integrity_audit --parts-root-win D:/market-data --parts-root-ch /mnt/d/market-data --bulk-sources submissions,companyfacts --bulk-download-concurrency 2 --bulk-ingest-batch-size 50000 --archive-download-concurrency 2 --archive-validation-workers 4 --text-extract-workers 4 --xbrl-workers 4 --sec-request-min-interval-seconds 0.12 --request-timeout-seconds 30 --max-retries 8 --retry-base-seconds 30 --pending-multiplier 2 --sample-limit 1000 --sample-text-chars 2000 --min-text-chars 40 --max-text-chars 0 --resume-from-coverage --execute
```

This unified gap-fill entry point refreshes SEC bulk `submissions` and
`companyfacts`, mirrors those bulk files into `sec_core`, derives canonical
filing parents and XBRL rows from that mirror, downloads missing daily archives,
validates them, extracts normalized filing/document/text rows, inserts them,
runs API fallback for missing recent XBRL, repairs XBRL relationships, audits
the result, and writes coverage rows.

Focused text repair after parser/storage bugs:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_filing_text_repair_rebuild.py --start-date 2026-07-01 --end-date 2026-07-11 --archive-root-win D:/market-data/sec_core/daily_archives --database q_live --parts-root-win D:/market-data --parts-root-ch /mnt/d/market-data --max-text-chars 0 --cleanup-stale-skips --execute
```

Use this focused repair when raw daily archives are already present and the goal
is to rebuild `sec_filing_document_v2`/`sec_filing_text_v2` with the current
parser. It force-inserts replacement part files and can remove stale skip rows
for documents that now have extracted text. It does not repair filing-parent
timestamps; run the acceptance timestamp repair scripts separately for
`sec_filing_v2.accepted_at_utc`.

Use the full argument form above for manual runs. The SEC gateway generates the
same explicit shape so the workstation script does not depend on ambient shell
defaults. `--resume-from-coverage` is enabled by default and records
`sec_stage_<stage_name>` rows after each successful stage. If a run fails, rerun
the same command; completed stages for the same date range are skipped, and the
final semantic coverage rows are written only after the whole run succeeds.
The validation stage is self-healing for corrupt daily archives selected from
the downloader manifest: if an archive scan fails, it redownloads that archive
from the SEC source URL and rescans it before returning a failed status. This is
important on reruns where `daily-archive-download` is skipped by coverage but a
previously reused `.nc.tar.gz` later proves truncated.

Legacy manual historical orchestration path:

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

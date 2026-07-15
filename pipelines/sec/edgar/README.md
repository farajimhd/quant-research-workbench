# SEC EDGAR Pipeline

For the complete v3 stage lifecycle, source-of-truth rules, table boundaries,
historical defect ledger, remedies, and remaining renderer work, see
[SEC Pipeline Lifecycle and Remediation Reference](SEC_PIPELINE_LIFECYCLE_AND_REMEDIATION.md).

This package contains the SEC EDGAR historical workflow:

- SEC bulk and daily archive download helpers;
- daily archive validation and content discovery;
- exact-file failed archive deletion;
- acceptance timestamp repair helpers;
- archive-derived acceptance timestamp repair for date-only parent rows;
- accession-level archive occurrence inventory and targeted missing-document repair;
- bulk plus rate-limited direct-submissions relationship reconciliation and exact UTC timestamp repair;
- archive-derived filing document/text extraction and ClickHouse file ingest;
- historical backfill orchestration over the stages that exist today;
- versioned SEC bulk mirror ingestion for current and historical-fragment submission relationships.

Preferred current historical gap-fill path used by SEC Gateway:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_historical_gap_fill.py --execute
```

The default rebuild window is `2019-01-01` through tomorrow UTC as an exclusive
end date. All v3 table names, workstation `D:/market-data` output roots,
`sec_core` mirror roots, rich progress, 32 bounded archive worker lanes, and
resume-from-coverage are defaults. Override `--start-date` or `--end-date` only
when intentionally running a smaller range.

The bulk-to-canonical stage also defaults
`max_partitions_per_insert_block=10000` for wide XBRL historical inserts, because
the full 2019+ rebuild can legitimately touch more than the ClickHouse server
default of 100 partitions in a single insert block.

This unified gap-fill entry point refreshes SEC bulk `submissions`,
`companyfacts`, `company_tickers`, `company_tickers_exchange`, and
`company_tickers_mf`, mirrors those source snapshots into `sec_core`, keeps submissions
as accession-to-CIK relationships, derives XBRL rows from that mirror, downloads missing daily archives,
validates them, extracts normalized filing/document/text rows, inserts them,
removes dependency-free submissions parents, repairs date-only parent timestamps from explicit
UTC acceptance metadata, and refreshes unresolved filing CIKs from the real-time per-CIK
submissions API and its referenced history fragments. The CIK comes from the parsed SGML
relationship and is never inferred from the accession prefix or another entity sharing the
accession. A parallel source audit verifies residual archive identities directly against SGML.
The pipeline then runs API fallback for missing recent XBRL, repairs XBRL relationships, rebuilds
`id_sec_market_bridge_v3`, builds SEC context tables in `market_sip_compact`,
audits the result, and writes coverage rows.

Archive inventory and targeted finalization can be run without rebuilding the
historical text corpus:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_historical_gap_fill.py --finalize-only --execute
```

This scans SGML headers into `sec_filing_entity_v3` and
`sec_filing_archive_accession_v3`, preserving embedded CIK relationships,
archive occurrence, document counts, source hashes, revision rank,
`ACCEPTANCE-DATETIME`, and `PRIVATE-TO-PUBLIC` evidence. It then extracts only
archive-backed parents whose public documents are absent, repairs only
date-based acceptance fallbacks that have an exact source timestamp, refreshes
the bridge/context products, and runs the final integrity audit. Metadata-only
filings and source records with no acceptance timestamp remain explicit
nonfatal unresolved classifications.

Before the final archive identity audit, finalization also repairs existing
document/text rows stored under a non-primary entity CIK. It reparses only the
mismatched archive members, inserts and verifies the complete subject-company
filing/document/source/rendered lineage, invalidates stale v3 model rows, and
synchronously deletes the old document key last. This makes the repair
restart-safe while retaining the reporting-person relationship in
`sec_filing_entity_v3`.

The acceptance repair measures the number of corrected monthly target partitions
before writing. Its default maintenance bound is 1,000 partitions, which permits
the current 145-partition SEC history in one server-side insert while failing
before mutation if an unexpectedly wider range is encountered. Replacement rows
are inserted before matched date-only fallbacks are synchronously deleted, so
cross-month corrections are restart-safe.

Archive text rebuild is transactional per daily `.nc.tar.gz`: each fixed worker
lane extracts and renders one archive into byte-bounded Parquet shards, validates
their footers, inserts them through ClickHouse's parallel native Parquet reader,
records archive-level completion, and then deletes the temporary shards. Original
daily archives remain the durable source.
Interrupted runs reuse archive completion rows and any prior fully extracted
parts that can be proven complete from their state journal or legacy successful
extract log. Partial files are never treated as complete input.
Failed ClickHouse part inserts are repaired before resume: the archive rebuild
uses the latest part manifest to identify failed `(source_run_id, archive_date,
dataset)` units, synchronously deletes and verifies only those date-scoped rows,
then retries the complete dataset through Parquet. Successful datasets for the
same archive remain checkpoints and are not rewritten.

Focused text repair after parser/storage bugs:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_filing_text_repair_rebuild.py --start-date 2026-07-01 --end-date 2026-07-11 --archive-root-win D:/market-data/sec_core/daily_archives --database q_live --parts-root-win D:/market-data --parts-root-ch /mnt/d/market-data --max-text-chars 0 --cleanup-stale-skips --execute
```

Use this focused repair when raw daily archives are already present and the goal
is to rebuild `sec_filing_document_v3`/`sec_filing_text_v3`/
`sec_filing_text_rendered_v3` with the current parser and renderer. It
force-inserts replacement part files and can remove stale skip rows
for documents that now have extracted text. It does not repair filing-parent
timestamps; run the acceptance timestamp repair scripts separately for
`sec_filing_v3.accepted_at_utc`.

The SEC gateway generates the same explicit shape so the workstation script does
not depend on ambient shell defaults. `--resume-from-coverage` is enabled by default and records
`sec_stage_<stage_name>` rows after each successful stage. If a run fails, rerun
the same command; completed stages for the same date range are skipped, and the
final semantic coverage rows are written only after the whole run succeeds.
The downloader requires the terminal completed weekday archive to exist in the
SEC listing. It exits before extraction instead of recording requested-range
coverage when that archive has not been published yet.
Archive extraction/insertion stops all worker lanes after the first failure and
keeps the first archive exception visible in the Rich terminal. Uncapped source
and rendered text use 256 MiB Parquet row groups, 1 GiB files, eight concurrent
inserts, and eight ClickHouse threads per insert by default. One submitted
document always remains one database row, including documents larger than a row
group target. Legacy retained JSON parts are converted to bounded Parquet shards
before recovery insertion.
The validation stage is self-healing for corrupt daily archives selected from
the downloader manifest: if an archive scan fails, it redownloads that archive
from the SEC source URL and rescans it before returning a failed status. This is
important on reruns where `daily-archive-download` is skipped by coverage but a
previously reused `.nc.tar.gz` later proves truncated.

Incremental edge recovery does not rerun the historical corpus. For example,
after July 10 was published later than the original run, use:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_historical_gap_fill.py --start-date 2026-07-10 --end-date 2026-07-11 --execute
```

Bulk download and mirror ingest always run as idempotent snapshot reconciliation;
date-range coverage cannot skip mutable SEC bulk inputs. The unified historical
fill rejects a partial bulk-source list, while the component downloader and ingest
scripts remain available for targeted repairs.
Archive extraction selects only the requested one-day range, and the archive
manifest prevents completed archives from being rewritten. Acceptance metadata,
the bridge, context tables, and the final audit are reconciled afterward.

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
`sec_xbrl_company_fact_v3`:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_xbrl_companyfacts_catchup.py --read-database q_live --write-database q_live --workers 4 --batch-size 10000 --execute
```

Run the XBRL integrity repair after an audit reports missing XBRL filing parents
or frame parents. This also drops stale `sec_filing_document_v1`:

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

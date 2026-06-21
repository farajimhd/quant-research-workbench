# SEC Integrity Audit Guide

Run this before building or loading archive-derived SEC filing text. The audit is read-only and writes JSONL/Markdown reports under `D:/market-data/prepared/sec_integrity_audit` by default.

## What It Checks

- `q_live.sec_filing_v2` exists, has logical rows, has no missing `accepted_at_utc`, and has no duplicate `(cik, accession_number)`.
- Current v2 text tables have no duplicate text keys or text rows without document parents.
- `sec_filing_document_v2`, `sec_filing_text_v2`, and `sec_filing_document_skip_v1` presence when `--require-v2-tables` is passed.
- Required v2 columns such as `text_sha256`, `normalizer_version`, `quality_flags`, `source_archive_date`, and `source_archive_member`.
- Structured SEC/XBRL table presence.
- A bounded XBRL accession sample join against `sec_filing_v2`.
- A date-scoped SEC/XBRL integrity report. The default actionable scope starts
  at `2019-01-01`; older XBRL rows are summarized as legacy and are not treated
  as blockers.
- XBRL-looking archive documents that do not have SEC companyfacts rows,
  grouped by form type. These are warnings because not all XML/XBRL-looking SEC
  documents belong in `sec_xbrl_company_fact_v1`.
- Local daily archive inventory without scanning archive contents.

`sec_filing_document_v1` and `sec_filing_text_v1` were legacy/provisional tables
and are no longer part of the current schema or audit target. The archive-derived
document/text path is v2 only.

## Local Laptop Command

```powershell
python D:\TradingCodes\quant-research-workbench\pipelines\sec\edgar\sec_integrity_audit.py --archive-root-win \\DESKTOP-SAAI85T\Workstation-D\market-data\sec_core\daily_archives
```

## Workstation Runtime Command

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_integrity_audit.py --archive-root-win D:/market-data/sec_core/daily_archives
```

## Current Scoped Audit

Use this for the 2019+ SEC database audit after XBRL catch-up:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_integrity_audit.py --archive-root-win D:/market-data/sec_core/daily_archives --scope-start-date 2019-01-01 --require-v2-tables
```

Rows before `2019-01-01` are reported under the legacy XBRL summary and should
not block the current SEC/news/model pipeline unless a task explicitly expands
the training horizon.

## After v2 Schema Creation

Use `--require-v2-tables` to make missing v2 targets a failure:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_integrity_audit.py --archive-root-win D:/market-data/sec_core/daily_archives --require-v2-tables
```

Use `--skip-xbrl-sample` when you only need a quick schema/archive audit:

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_integrity_audit.py --archive-root-win D:/market-data/sec_core/daily_archives --skip-xbrl-sample
```

## Output

Each run writes:

```text
sec_integrity_audit_manifest.json
sec_integrity_audit_checks.jsonl
sec_integrity_audit_summary.md
```

Treat failures as blockers before extractor implementation or loading. Treat warnings as explicit decisions to review.

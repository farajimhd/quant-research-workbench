# SEC Integrity Audit Guide

Run this before building or loading archive-derived SEC filing text. The audit is read-only and writes JSONL/Markdown reports under `D:/market-data/prepared/sec_integrity_audit` by default.

## What It Checks

- `q_live.sec_filing_v2` exists, has logical rows, has no missing `accepted_at_utc`, and has no duplicate `(cik, accession_number)`.
- Current `q_live.sec_filing_document_v1` relation integrity and its known synthetic bridge fingerprint.
- Current text tables have no duplicate text keys or text rows without document parents when populated.
- `sec_filing_document_v2`, `sec_filing_text_v2`, and `sec_filing_document_skip_v1` presence when `--require-v2-tables` is passed.
- Required v2 columns such as `text_sha256`, `normalizer_version`, `quality_flags`, `source_archive_date`, and `source_archive_member`.
- Structured SEC/XBRL table presence.
- A bounded XBRL accession sample join against `sec_filing_v2`.
- Local daily archive inventory without scanning archive contents.

Warnings are expected before v2 schema creation because the v2 tables do not exist yet and `sec_filing_document_v1` is intentionally provisional.

## Local Laptop Command

```powershell
python D:\TradingCodes\quant-research-workbench\pipelines\sec\edgar\sec_integrity_audit.py --archive-root-win \\DESKTOP-SAAI85T\Workstation-D\market-data\sec_core\daily_archives
```

## Workstation Runtime Command

```powershell
python D:\TradingML\codes\quant_research_workbench_pipelines\pipelines\sec\edgar\sec_integrity_audit.py --archive-root-win D:/market-data/sec_core/daily_archives
```

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

Treat failures as blockers before extractor implementation or loading. Treat warnings as explicit decisions to review; the known `sec_filing_document_v1` synthetic bridge warning is expected until v2 archive-derived metadata is loaded.

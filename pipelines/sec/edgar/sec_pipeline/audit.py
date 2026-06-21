from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from pipelines.sec.edgar import sec_integrity_audit as integrity
from research.mlops.clickhouse import ClickHouseHttpClient


@dataclass(frozen=True, slots=True)
class SecAuditSummary:
    checks: int
    passed: int
    warnings: int
    failed: int
    output_path: Path | None = None


def run_sec_audit(client: ClickHouseHttpClient, *, database: str, output_path: Path | None = None) -> SecAuditSummary:
    table_meta = integrity.query_table_metadata(client, database)
    column_map = integrity.query_column_map(client, database)
    checks: list[dict[str, object]] = []
    checks.extend(integrity.check_required_tables(table_meta, require_v2_tables=True))
    scope_start = date(2019, 1, 1)
    if "sec_filing_v2" in table_meta:
        checks.extend(integrity.check_filing_parent(client, database, scope_start))
    if "sec_filing_document_v2" in table_meta:
        checks.extend(integrity.check_document_v2_shape(column_map))
    if "sec_filing_text_v2" in table_meta:
        checks.extend(integrity.check_text_v2_shape(column_map))
        if "sec_filing_document_v2" in table_meta:
            checks.extend(integrity.check_text_table(client, database, text_table="sec_filing_text_v2", document_table="sec_filing_document_v2"))
    checks.extend(integrity.check_xbrl_presence(table_meta))
    if {"sec_xbrl_company_fact_v1", "sec_filing_v2"}.issubset(table_meta):
        checks.extend(integrity.check_xbrl_sample(client, database, 200000, scope_start))
    passed = sum(1 for row in checks if row.get("status") == "pass")
    warnings = sum(1 for row in checks if row.get("status") == "warn")
    failed = sum(1 for row in checks if row.get("status") == "fail")
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as handle:
            for row in checks:
                handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    return SecAuditSummary(checks=len(checks), passed=passed, warnings=warnings, failed=failed, output_path=output_path)

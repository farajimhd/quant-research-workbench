from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from research.mlops.clickhouse import ClickHouseHttpClient


FILING_TABLE = "sec_filing_v2"
DOCUMENT_TABLE = "sec_filing_document_v2"
TEXT_TABLE = "sec_filing_text_v2"
SKIP_TABLE = "sec_filing_document_skip_v1"


@dataclass(frozen=True, slots=True)
class SecWriteResult:
    filing_rows: int = 0
    document_rows: int = 0
    text_rows: int = 0
    skip_rows: int = 0
    skipped_existing: bool = False


class SecClickHouseWriter:
    def __init__(self, client: ClickHouseHttpClient, *, database: str) -> None:
        self.client = client
        self.database = database

    def validate_tables(self) -> None:
        required = {FILING_TABLE, DOCUMENT_TABLE, TEXT_TABLE, SKIP_TABLE}
        rows = self.client.execute(
            f"""
            SELECT name
            FROM system.tables
            WHERE database = {sql_string(self.database)}
              AND name IN ({','.join(sql_string(item) for item in sorted(required))})
            FORMAT TSV
            """
        )
        present = {line.strip() for line in rows.splitlines() if line.strip()}
        missing = sorted(required - present)
        if missing:
            raise RuntimeError(f"missing SEC target tables in {self.database}: {missing}")

    def filing_exists(self, cik: str, accession_number: str) -> bool:
        out = self.client.execute(
            f"""
            SELECT count()
            FROM {qi(self.database)}.{qi(FILING_TABLE)} FINAL
            WHERE cik = {sql_string(cik)}
              AND accession_number = {sql_string(accession_number)}
            FORMAT TSV
            """
        )
        return int(out.strip() or "0") > 0

    def write_accession(
        self,
        *,
        filing_row: dict[str, Any],
        document_rows: list[dict[str, Any]],
        text_rows: list[dict[str, Any]],
        skip_rows: list[dict[str, Any]],
        skip_existing: bool = True,
    ) -> SecWriteResult:
        if skip_existing and self.filing_exists(str(filing_row["cik"]), str(filing_row["accession_number"])):
            return SecWriteResult(skipped_existing=True)
        self.insert_rows(FILING_TABLE, [filing_row])
        self.insert_rows(DOCUMENT_TABLE, document_rows)
        self.insert_rows(TEXT_TABLE, text_rows)
        self.insert_rows(SKIP_TABLE, skip_rows)
        return SecWriteResult(
            filing_rows=1,
            document_rows=len(document_rows),
            text_rows=len(text_rows),
            skip_rows=len(skip_rows),
            skipped_existing=False,
        )

    def insert_rows(self, table: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        body = "\n".join(json.dumps(row, ensure_ascii=False, separators=(",", ":"), default=str) for row in rows)
        self.client.execute(f"INSERT INTO {qi(self.database)}.{qi(table)} FORMAT JSONEachRow\n{body}")


def qi(value: str) -> str:
    return "`" + value.replace("`", "``") + "`"


def sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"

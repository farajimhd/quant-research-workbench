from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from research.mlops.clickhouse import ClickHouseHttpClient


FILING_TABLE = "sec_filing_v2"
DOCUMENT_TABLE = "sec_filing_document_v2"
TEXT_TABLE = "sec_filing_text_v2"
SKIP_TABLE = "sec_filing_document_skip_v1"
XBRL_CONCEPT_TABLE = "sec_xbrl_concept_v1"
XBRL_COMPANY_FACT_TABLE = "sec_xbrl_company_fact_v1"
XBRL_FRAME_TABLE = "sec_xbrl_frame_v1"
XBRL_FRAME_OBSERVATION_TABLE = "sec_xbrl_frame_observation_v1"
WRITE_TABLES = [
    FILING_TABLE,
    DOCUMENT_TABLE,
    TEXT_TABLE,
    SKIP_TABLE,
    XBRL_CONCEPT_TABLE,
    XBRL_COMPANY_FACT_TABLE,
    XBRL_FRAME_TABLE,
    XBRL_FRAME_OBSERVATION_TABLE,
]


@dataclass(frozen=True, slots=True)
class SecWriteResult:
    filing_rows: int = 0
    document_rows: int = 0
    text_rows: int = 0
    skip_rows: int = 0
    xbrl_concept_rows: int = 0
    xbrl_company_fact_rows: int = 0
    xbrl_frame_rows: int = 0
    xbrl_frame_observation_rows: int = 0
    skipped_existing: bool = False


@dataclass(frozen=True, slots=True)
class SecWriteAudit:
    filing_rows: int
    document_rows: int
    text_rows: int
    skip_rows: int
    xbrl_concept_rows: int
    xbrl_company_fact_rows: int
    xbrl_frame_rows: int
    xbrl_frame_observation_rows: int
    duplicate_filing_keys: int
    documents_without_filing: int
    texts_without_document: int
    texts_without_filing: int
    company_facts_without_filing: int
    frame_observations_without_company_fact: int

    @property
    def ok(self) -> bool:
        return (
            self.duplicate_filing_keys == 0
            and self.documents_without_filing == 0
            and self.texts_without_document == 0
            and self.texts_without_filing == 0
            and self.company_facts_without_filing == 0
            and self.frame_observations_without_company_fact == 0
        )


class SecClickHouseWriter:
    def __init__(self, client: ClickHouseHttpClient, *, database: str) -> None:
        self.client = client
        self.database = database

    def validate_tables(self) -> None:
        required = set(WRITE_TABLES)
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

    def audit_integrity(self) -> SecWriteAudit:
        return SecWriteAudit(
            filing_rows=scalar_int(self.client, f"SELECT count() FROM {qi(self.database)}.{qi(FILING_TABLE)} FINAL"),
            document_rows=scalar_int(self.client, f"SELECT count() FROM {qi(self.database)}.{qi(DOCUMENT_TABLE)} FINAL"),
            text_rows=scalar_int(self.client, f"SELECT count() FROM {qi(self.database)}.{qi(TEXT_TABLE)} FINAL"),
            skip_rows=scalar_int(self.client, f"SELECT count() FROM {qi(self.database)}.{qi(SKIP_TABLE)} FINAL"),
            xbrl_concept_rows=scalar_int(self.client, f"SELECT count() FROM {qi(self.database)}.{qi(XBRL_CONCEPT_TABLE)} FINAL"),
            xbrl_company_fact_rows=scalar_int(self.client, f"SELECT count() FROM {qi(self.database)}.{qi(XBRL_COMPANY_FACT_TABLE)} FINAL"),
            xbrl_frame_rows=scalar_int(self.client, f"SELECT count() FROM {qi(self.database)}.{qi(XBRL_FRAME_TABLE)} FINAL"),
            xbrl_frame_observation_rows=scalar_int(self.client, f"SELECT count() FROM {qi(self.database)}.{qi(XBRL_FRAME_OBSERVATION_TABLE)} FINAL"),
            duplicate_filing_keys=scalar_int(
                self.client,
                f"""
                SELECT count()
                FROM (
                    SELECT cik, accession_number, count() AS c
                    FROM {qi(self.database)}.{qi(FILING_TABLE)} FINAL
                    GROUP BY cik, accession_number
                    HAVING c > 1
                )
                """,
            ),
            documents_without_filing=scalar_int(
                self.client,
                f"""
                SELECT count()
                FROM (SELECT cik, accession_number FROM {qi(self.database)}.{qi(DOCUMENT_TABLE)} FINAL) AS d
                LEFT ANTI JOIN (SELECT cik, accession_number FROM {qi(self.database)}.{qi(FILING_TABLE)} FINAL) AS f
                ON d.cik = f.cik AND d.accession_number = f.accession_number
                """,
            ),
            texts_without_document=scalar_int(
                self.client,
                f"""
                SELECT count()
                FROM (SELECT cik, accession_number, document_id FROM {qi(self.database)}.{qi(TEXT_TABLE)} FINAL) AS t
                LEFT ANTI JOIN (SELECT cik, accession_number, document_id FROM {qi(self.database)}.{qi(DOCUMENT_TABLE)} FINAL) AS d
                ON t.cik = d.cik
                   AND t.accession_number = d.accession_number
                   AND t.document_id = d.document_id
                """,
            ),
            texts_without_filing=scalar_int(
                self.client,
                f"""
                SELECT count()
                FROM (SELECT cik, accession_number FROM {qi(self.database)}.{qi(TEXT_TABLE)} FINAL) AS t
                LEFT ANTI JOIN (SELECT cik, accession_number FROM {qi(self.database)}.{qi(FILING_TABLE)} FINAL) AS f
                ON t.cik = f.cik AND t.accession_number = f.accession_number
                """,
            ),
            company_facts_without_filing=scalar_int(
                self.client,
                f"""
                SELECT count()
                FROM (SELECT cik, accession_number FROM {qi(self.database)}.{qi(XBRL_COMPANY_FACT_TABLE)} FINAL WHERE accession_number IS NOT NULL AND accession_number != '') AS x
                LEFT ANTI JOIN (SELECT cik, accession_number FROM {qi(self.database)}.{qi(FILING_TABLE)} FINAL) AS f
                ON x.cik = f.cik AND x.accession_number = f.accession_number
                """,
            ),
            frame_observations_without_company_fact=scalar_int(
                self.client,
                f"""
                SELECT count()
                FROM (
                    SELECT cik, accession_number, taxonomy, tag, unit_code, period_end_date
                    FROM {qi(self.database)}.{qi(XBRL_FRAME_OBSERVATION_TABLE)} FINAL
                ) AS o
                LEFT ANTI JOIN (
                    SELECT cik, accession_number, taxonomy, tag, unit_code, period_end_date
                    FROM {qi(self.database)}.{qi(XBRL_COMPANY_FACT_TABLE)} FINAL
                    WHERE accession_number IS NOT NULL
                ) AS f
                ON o.cik = f.cik
                   AND o.accession_number = f.accession_number
                   AND o.taxonomy = f.taxonomy
                   AND o.tag = f.tag
                   AND o.unit_code = f.unit_code
                   AND o.period_end_date = f.period_end_date
                """,
            ),
        )

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
        xbrl_concept_rows: list[dict[str, Any]] | None = None,
        xbrl_company_fact_rows: list[dict[str, Any]] | None = None,
        xbrl_frame_rows: list[dict[str, Any]] | None = None,
        xbrl_frame_observation_rows: list[dict[str, Any]] | None = None,
        skip_existing: bool = True,
    ) -> SecWriteResult:
        if skip_existing and self.filing_exists(str(filing_row["cik"]), str(filing_row["accession_number"])):
            return SecWriteResult(skipped_existing=True)
        self.insert_rows(FILING_TABLE, [filing_row])
        self.insert_rows(DOCUMENT_TABLE, document_rows)
        self.insert_rows(TEXT_TABLE, text_rows)
        self.insert_rows(SKIP_TABLE, skip_rows)
        self.insert_rows(XBRL_CONCEPT_TABLE, xbrl_concept_rows or [])
        self.insert_rows(XBRL_COMPANY_FACT_TABLE, xbrl_company_fact_rows or [])
        self.insert_rows(XBRL_FRAME_TABLE, xbrl_frame_rows or [])
        self.insert_rows(XBRL_FRAME_OBSERVATION_TABLE, xbrl_frame_observation_rows or [])
        return SecWriteResult(
            filing_rows=1,
            document_rows=len(document_rows),
            text_rows=len(text_rows),
            skip_rows=len(skip_rows),
            xbrl_concept_rows=len(xbrl_concept_rows or []),
            xbrl_company_fact_rows=len(xbrl_company_fact_rows or []),
            xbrl_frame_rows=len(xbrl_frame_rows or []),
            xbrl_frame_observation_rows=len(xbrl_frame_observation_rows or []),
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


def ensure_sec_write_database(
    client: ClickHouseHttpClient,
    *,
    read_database: str,
    write_database: str,
) -> list[str]:
    client.execute(f"CREATE DATABASE IF NOT EXISTS {qi(write_database)}")
    required = WRITE_TABLES
    created_or_present: list[str] = []
    for table in required:
        if not table_exists(client, read_database, table):
            raise RuntimeError(f"source SEC table is missing: {read_database}.{table}")
        if not table_exists(client, write_database, table):
            clone_table_schema(client, source_database=read_database, target_database=write_database, table=table)
        created_or_present.append(f"{write_database}.{table}")
    return created_or_present


def table_exists(client: ClickHouseHttpClient, database: str, table: str) -> bool:
    out = client.execute(
        f"""
        SELECT count()
        FROM system.tables
        WHERE database = {sql_string(database)}
          AND name = {sql_string(table)}
        FORMAT TSV
        """
    )
    return int(out.strip() or "0") > 0


def clone_table_schema(client: ClickHouseHttpClient, *, source_database: str, target_database: str, table: str) -> None:
    ddl = client.execute(f"SHOW CREATE TABLE {qi(source_database)}.{qi(table)} FORMAT TSVRaw").strip()
    pattern = re.compile(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?"
        + r"(?:`[^`]+`|[A-Za-z_][A-Za-z0-9_]*)\."
        + r"(?:`"
        + re.escape(table)
        + r"`|"
        + re.escape(table)
        + r")",
        flags=re.IGNORECASE,
    )
    replacement = f"CREATE TABLE IF NOT EXISTS {qi(target_database)}.{qi(table)}"
    cloned = pattern.sub(replacement, ddl, count=1)
    if cloned == ddl:
        raise RuntimeError(f"could not rewrite SHOW CREATE TABLE DDL for {source_database}.{table}")
    cloned = re.sub(r"\s+UUID\s+'[^']+'", "", cloned, count=1, flags=re.IGNORECASE)
    client.execute(cloned)


def scalar_int(client: ClickHouseHttpClient, sql: str) -> int:
    out = client.execute(sql + " FORMAT TSV").strip()
    return int(out or "0")

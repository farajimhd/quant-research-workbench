from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from pipelines.market_sip.events.clickhouse_build_sec_context import create_xbrl_context_table_sql
from research.mlops.clickhouse import ClickHouseHttpClient, mergetree_settings_sql, parse_size_bytes, quote_ident, sql_string


DEFAULT_CONTEXT_DATABASE = "market_sip_compact"
DEFAULT_CONTEXT_TABLE = "sec_xbrl_context_v3"
DEFAULT_SYNC_MANIFEST_TABLE = "sec_xbrl_context_sync_manifest_v3"
DEFAULT_SOURCE_DATABASE = "q_live"
DEFAULT_FILING_TABLE = "sec_filing_v3"
DEFAULT_BRIDGE_TABLE = "id_sec_market_bridge_v3"
DEFAULT_COMPANY_FACT_TABLE = "sec_xbrl_company_fact_v3"
DEFAULT_FRAME_OBSERVATION_TABLE = "sec_xbrl_frame_observation_v3"
RETRYABLE_STATUSES = ("pending", "pending_source", "pending_mapping", "failed")


@dataclass(frozen=True, slots=True)
class XbrlContextSyncConfig:
    source_database: str = DEFAULT_SOURCE_DATABASE
    bridge_database: str = DEFAULT_SOURCE_DATABASE
    context_database: str = DEFAULT_CONTEXT_DATABASE
    filing_table: str = DEFAULT_FILING_TABLE
    bridge_table: str = DEFAULT_BRIDGE_TABLE
    company_fact_table: str = DEFAULT_COMPANY_FACT_TABLE
    frame_observation_table: str = DEFAULT_FRAME_OBSERVATION_TABLE
    context_table: str = DEFAULT_CONTEXT_TABLE
    manifest_table: str = DEFAULT_SYNC_MANIFEST_TABLE
    storage_policy: str = ""
    max_threads: int = 8
    max_memory_usage: str = "16G"
    insert_batch_rows: int = 10_000


@dataclass(frozen=True, slots=True)
class XbrlContextSyncResult:
    cik: str
    accession_number: str
    status: str
    source_company_fact_rows: int = 0
    source_frame_observation_rows: int = 0
    candidate_rows: int = 0
    inserted_rows: int = 0
    missing_rows: int = 0
    error: str = ""


class SecXbrlContextSync:
    def __init__(self, client: ClickHouseHttpClient, config: XbrlContextSyncConfig) -> None:
        self.client = client
        self.config = config

    def ensure_tables(self) -> None:
        self._validate_source_tables()
        self.client.execute(f"CREATE DATABASE IF NOT EXISTS {quote_ident(self.config.context_database)}")
        self.client.execute(
            create_xbrl_context_table_sql(
                self.config.context_database,
                self.config.context_table,
                self.config.storage_policy,
            )
        )
        self.client.execute(create_sync_manifest_table_sql(self.config))

    def mark_pending(
        self,
        *,
        cik: str,
        accession_number: str,
        expected_company_fact_rows: int,
        expected_frame_observation_rows: int,
    ) -> None:
        self._insert_manifest(
            XbrlContextSyncResult(
                cik=str(cik),
                accession_number=str(accession_number),
                status="pending",
                source_company_fact_rows=max(0, int(expected_company_fact_rows)),
                source_frame_observation_rows=max(0, int(expected_frame_observation_rows)),
            )
        )

    def sync_accession(self, *, cik: str, accession_number: str) -> XbrlContextSyncResult:
        cik = str(cik)
        accession_number = str(accession_number)
        expected = self._latest_manifest(cik=cik, accession_number=accession_number)
        expected_facts = int(expected.get("source_company_fact_rows") or 0)
        expected_frames = int(expected.get("source_frame_observation_rows") or 0)
        try:
            company_fact_rows, frame_observation_rows = self._source_rows(
                cik=cik,
                accession_number=accession_number,
            )
        except Exception as exc:
            result = XbrlContextSyncResult(
                cik=cik,
                accession_number=accession_number,
                status="failed",
                source_company_fact_rows=expected_facts,
                source_frame_observation_rows=expected_frames,
                error=repr(exc),
            )
            self._insert_manifest(result)
            raise
        source_facts = len(company_fact_rows)
        source_frames = len(frame_observation_rows)
        if source_facts < expected_facts or source_frames < expected_frames:
            result = XbrlContextSyncResult(
                cik=cik,
                accession_number=accession_number,
                status="pending_source",
                source_company_fact_rows=max(source_facts, expected_facts),
                source_frame_observation_rows=max(source_frames, expected_frames),
                missing_rows=max(0, expected_facts - source_facts) + max(0, expected_frames - source_frames),
            )
            self._insert_manifest(result)
            return result
        if source_facts + source_frames == 0:
            result = XbrlContextSyncResult(cik=cik, accession_number=accession_number, status="not_applicable")
            self._insert_manifest(result)
            return result
        return self.sync_rows(
            cik=cik,
            accession_number=accession_number,
            company_fact_rows=company_fact_rows,
            frame_observation_rows=frame_observation_rows,
        )

    def sync_rows(
        self,
        *,
        cik: str,
        accession_number: str,
        company_fact_rows: list[dict[str, Any]],
        frame_observation_rows: list[dict[str, Any]],
    ) -> XbrlContextSyncResult:
        cik = str(cik)
        accession_number = str(accession_number)
        source_facts = len(company_fact_rows)
        source_frames = len(frame_observation_rows)
        if source_facts + source_frames == 0:
            result = XbrlContextSyncResult(cik=cik, accession_number=accession_number, status="not_applicable")
            self._insert_manifest(result)
            return result
        try:
            mappings = self._mapped_filings(cik=cik, accession_number=accession_number)
            if not mappings:
                result = XbrlContextSyncResult(
                    cik=cik,
                    accession_number=accession_number,
                    status="pending_mapping",
                    source_company_fact_rows=source_facts,
                    source_frame_observation_rows=source_frames,
                    missing_rows=source_facts + source_frames,
                )
                self._insert_manifest(result)
                return result
            context_rows = deduplicate_context_rows(
                build_context_rows(
                    mappings=mappings,
                    company_fact_rows=company_fact_rows,
                    frame_observation_rows=frame_observation_rows,
                )
            )
            rows_to_insert: list[dict[str, Any]] = []
            for mapping in mappings:
                existing = self._context_identities(
                    ticker=str(mapping["ticker"]),
                    timestamp_us=int(mapping["timestamp_us"]),
                    accession_number=accession_number,
                )
                rows_to_insert.extend(
                    row
                    for row in context_rows
                    if str(row["ticker"]) == str(mapping["ticker"])
                    and int(row["timestamp_us"]) == int(mapping["timestamp_us"])
                    and context_row_identity(row) not in existing
                )
            insert_context_rows(self.client, self.config, rows_to_insert)

            missing = 0
            for mapping in mappings:
                expected_identities = {
                    context_row_identity(row)
                    for row in context_rows
                    if str(row["ticker"]) == str(mapping["ticker"])
                    and int(row["timestamp_us"]) == int(mapping["timestamp_us"])
                }
                actual_identities = self._context_identities(
                    ticker=str(mapping["ticker"]),
                    timestamp_us=int(mapping["timestamp_us"]),
                    accession_number=accession_number,
                )
                missing += len(expected_identities - actual_identities)
            result = XbrlContextSyncResult(
                cik=cik,
                accession_number=accession_number,
                status="ok" if missing == 0 else "failed",
                source_company_fact_rows=source_facts,
                source_frame_observation_rows=source_frames,
                candidate_rows=len(context_rows),
                inserted_rows=len(rows_to_insert),
                missing_rows=missing,
                error="" if missing == 0 else f"context verification found {missing} missing row(s)",
            )
            self._insert_manifest(result)
            if missing:
                raise RuntimeError(result.error)
            return result
        except Exception as exc:
            result = XbrlContextSyncResult(
                cik=cik,
                accession_number=accession_number,
                status="failed",
                source_company_fact_rows=source_facts,
                source_frame_observation_rows=source_frames,
                error=repr(exc),
            )
            self._insert_manifest(result)
            raise

    def reconcile_pending(self, *, limit: int) -> list[XbrlContextSyncResult]:
        if int(limit) <= 0:
            return []
        rows = self._pending_rows(limit=max(1, int(limit)))
        results: list[XbrlContextSyncResult] = []
        for row in rows:
            try:
                results.append(self.sync_accession(cik=str(row["cik"]), accession_number=str(row["accession_number"])))
            except Exception as exc:  # noqa: BLE001
                results.append(
                    XbrlContextSyncResult(
                        cik=str(row["cik"]),
                        accession_number=str(row["accession_number"]),
                        status="failed",
                        error=repr(exc),
                    )
                )
        return results

    def reconcile_stale_mappings(self, *, limit: int) -> list[XbrlContextSyncResult]:
        if int(limit) <= 0:
            return []
        rows = json_each_row(self.client.execute(stale_context_mapping_rows_sql(self.config, limit=max(1, int(limit)))))
        if not rows:
            return []
        stale_bridge_ids = sorted({str(row.get("bridge_id") or "") for row in rows if str(row.get("bridge_id") or "")})
        if stale_bridge_ids:
            values = ", ".join(sql_string(value) for value in stale_bridge_ids)
            target = f"{quote_ident(self.config.context_database)}.{quote_ident(self.config.context_table)}"
            self.client.execute(
                f"ALTER TABLE {target} DELETE WHERE bridge_id IN ({values}) SETTINGS mutations_sync = 2"
            )
            remaining = self.client.execute(
                f"SELECT count() FROM {target} FINAL WHERE bridge_id IN ({values}) FORMAT TSV"
            ).strip()
            if int(remaining or 0):
                raise RuntimeError(f"failed to remove {remaining} stale SEC XBRL context row(s)")
        accessions = sorted({(str(row["cik"]), str(row["accession_number"])) for row in rows})
        return [self.sync_accession(cik=cik, accession_number=accession_number) for cik, accession_number in accessions]

    def pending_source_accessions(self, accession_numbers: list[str]) -> set[str]:
        values = sorted({str(value) for value in accession_numbers if str(value)})
        if not values:
            return set()
        values_sql = ", ".join(sql_string(value) for value in values)
        text = self.client.execute(
            f"""
SELECT accession_number
FROM {quote_ident(self.config.context_database)}.{quote_ident(self.config.manifest_table)} FINAL
WHERE accession_number IN ({values_sql})
  AND status IN ('pending', 'pending_source')
FORMAT TSV
"""
        )
        return {line.strip() for line in text.splitlines() if line.strip()}

    def _validate_source_tables(self) -> None:
        required_source = {
            self.config.filing_table,
            self.config.company_fact_table,
            self.config.frame_observation_table,
        }
        values_sql = ", ".join(sql_string(value) for value in sorted(required_source))
        text = self.client.execute(
            f"SELECT name FROM system.tables WHERE database = {sql_string(self.config.source_database)} "
            f"AND name IN ({values_sql}) FORMAT TSV"
        )
        present = {line.strip() for line in text.splitlines() if line.strip()}
        missing = sorted(required_source - present)
        if missing:
            raise RuntimeError(f"missing SEC XBRL context source tables in {self.config.source_database}: {missing}")
        bridge_exists = self.client.execute(
            "SELECT count() FROM system.tables "
            f"WHERE database = {sql_string(self.config.bridge_database)} "
            f"AND name = {sql_string(self.config.bridge_table)} FORMAT TSV"
        ).strip()
        if int(bridge_exists or 0) != 1:
            raise RuntimeError(
                "missing SEC XBRL context bridge table: "
                f"{self.config.bridge_database}.{self.config.bridge_table}"
            )

    def _source_rows(self, *, cik: str, accession_number: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        company_facts = json_each_row(
            self.client.execute(
                source_company_fact_rows_sql(self.config, cik=cik, accession_number=accession_number)
            )
        )
        frame_observations = json_each_row(
            self.client.execute(
                source_frame_observation_rows_sql(self.config, cik=cik, accession_number=accession_number)
            )
        )
        return company_facts, frame_observations

    def _mapped_filings(self, *, cik: str, accession_number: str) -> list[dict[str, Any]]:
        return json_each_row(
            self.client.execute(mapped_filing_rows_sql(self.config, cik=cik, accession_number=accession_number))
        )

    def _context_identities(self, *, ticker: str, timestamp_us: int, accession_number: str) -> set[tuple[str, str]]:
        text = self.client.execute(
            context_identities_sql(
                self.config,
                ticker=ticker,
                timestamp_us=timestamp_us,
                accession_number=accession_number,
            )
        )
        return {
            (fields[0], fields[1])
            for fields in (line.split("\t", 1) for line in text.splitlines() if line.strip())
            if len(fields) == 2
        }

    def _latest_manifest(self, *, cik: str, accession_number: str) -> dict[str, Any]:
        text = self.client.execute(
            f"""
SELECT *
FROM {quote_ident(self.config.context_database)}.{quote_ident(self.config.manifest_table)} FINAL
WHERE cik = {sql_string(cik)}
  AND accession_number = {sql_string(accession_number)}
FORMAT JSONEachRow
"""
        ).strip()
        return json.loads(text.splitlines()[0]) if text else {}

    def _pending_rows(self, *, limit: int) -> list[dict[str, Any]]:
        statuses = ", ".join(sql_string(value) for value in RETRYABLE_STATUSES)
        text = self.client.execute(
            f"""
SELECT cik, accession_number
FROM {quote_ident(self.config.context_database)}.{quote_ident(self.config.manifest_table)} FINAL
WHERE status IN ({statuses})
ORDER BY updated_at_utc, cik, accession_number
LIMIT {int(limit)}
FORMAT JSONEachRow
"""
        ).strip()
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def _insert_manifest(self, result: XbrlContextSyncResult) -> None:
        row = {
            "cik": result.cik,
            "accession_number": result.accession_number,
            "source_company_fact_rows": result.source_company_fact_rows,
            "source_frame_observation_rows": result.source_frame_observation_rows,
            "candidate_rows": result.candidate_rows,
            "inserted_rows": result.inserted_rows,
            "missing_rows": result.missing_rows,
            "status": result.status,
            "error": result.error[:4000],
        }
        self.client.execute(
            f"INSERT INTO {quote_ident(self.config.context_database)}.{quote_ident(self.config.manifest_table)} "
            "FORMAT JSONEachRow\n" + json.dumps(row, separators=(",", ":"), ensure_ascii=False)
        )


def create_sync_manifest_table_sql(config: XbrlContextSyncConfig) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(config.context_database)}.{quote_ident(config.manifest_table)}
(
    cik String,
    accession_number String,
    source_company_fact_rows UInt64,
    source_frame_observation_rows UInt64,
    candidate_rows UInt64,
    inserted_rows UInt64,
    missing_rows UInt64,
    status LowCardinality(String),
    error String,
    updated_at_utc DateTime64(9, 'UTC') DEFAULT now64(9, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at_utc)
ORDER BY (cik, accession_number)
{mergetree_settings_sql(config.storage_policy)}
"""


def source_company_fact_rows_sql(config: XbrlContextSyncConfig, *, cik: str, accession_number: str) -> str:
    source = quote_ident(config.source_database)
    return f"""
SELECT
    cik,
    accession_number,
    company_fact_id,
    issuer_id,
    taxonomy,
    tag,
    unit_code,
    fiscal_year,
    fiscal_period,
    form_type,
    period_end_date,
    value
FROM {source}.{quote_ident(config.company_fact_table)} FINAL
WHERE cik = {sql_string(cik)}
  AND accession_number = {sql_string(accession_number)}
{query_settings_sql(config)}
FORMAT JSONEachRow
"""


def source_frame_observation_rows_sql(config: XbrlContextSyncConfig, *, cik: str, accession_number: str) -> str:
    source = quote_ident(config.source_database)
    return f"""
SELECT
    cik,
    accession_number,
    frame_observation_id,
    issuer_id,
    taxonomy,
    tag,
    unit_code,
    period_end_date,
    value,
    calendar_period_code,
    location_code
FROM {source}.{quote_ident(config.frame_observation_table)} FINAL
WHERE cik = {sql_string(cik)}
  AND accession_number = {sql_string(accession_number)}
{query_settings_sql(config)}
FORMAT JSONEachRow
"""


def mapped_filing_rows_sql(config: XbrlContextSyncConfig, *, cik: str, accession_number: str) -> str:
    source = quote_ident(config.source_database)
    bridge_database = quote_ident(config.bridge_database)
    return f"""
WITH bridge AS
(
    SELECT
        ifNull(ticker, '') AS ticker,
        cik,
        ifNull(accession_number, '') AS accession_number,
        valid_from_date,
        valid_to_date_exclusive,
        any(bridge_id) AS bridge_id,
        max(confidence_score) AS confidence_score
    FROM {bridge_database}.{quote_ident(config.bridge_table)}
    WHERE ifNull(ticker, '') != ''
      AND mapping_status IN ('active', 'mapped', 'accepted', '')
      AND cik = {sql_string(cik)}
      AND (ifNull(accession_number, '') = '' OR accession_number = {sql_string(accession_number)})
    GROUP BY ticker, cik, accession_number, valid_from_date, valid_to_date_exclusive
)
SELECT
    b.ticker AS ticker,
    toUInt64(toUnixTimestamp64Micro(f.accepted_at_utc)) AS timestamp_us,
    formatDateTime(f.accepted_at_utc, '%Y-%m-%dT%H:%i:%S.%fZ', 'UTC') AS accepted_at_utc,
    f.cik AS cik,
    f.accession_number AS accession_number,
    ifNull(f.accepted_at_source, '') AS accepted_at_source,
    toFloat32(b.confidence_score) AS mapping_confidence,
    b.bridge_id AS bridge_id
FROM {source}.{quote_ident(config.filing_table)} AS f FINAL
INNER JOIN bridge AS b ON b.cik = f.cik
WHERE f.cik = {sql_string(cik)}
  AND f.accession_number = {sql_string(accession_number)}
  AND f.accepted_at_utc IS NOT NULL
  AND (b.accession_number = '' OR b.accession_number = f.accession_number)
  AND (b.valid_from_date IS NULL OR b.valid_from_date <= toDate(f.accepted_at_utc))
  AND (b.valid_to_date_exclusive IS NULL OR b.valid_to_date_exclusive > toDate(f.accepted_at_utc))
{query_settings_sql(config)}
FORMAT JSONEachRow
"""


def stale_context_mapping_rows_sql(config: XbrlContextSyncConfig, *, limit: int) -> str:
    context = quote_ident(config.context_database)
    bridge = quote_ident(config.bridge_database)
    return f"""
SELECT DISTINCT c.cik, c.accession_number, c.bridge_id
FROM {context}.{quote_ident(config.context_table)} AS c FINAL
LEFT JOIN
(
    SELECT bridge_id
    FROM {bridge}.{quote_ident(config.bridge_table)} FINAL
    WHERE mapping_status IN ('active', 'mapped', 'accepted', '')
) AS b ON b.bridge_id = c.bridge_id
WHERE c.bridge_id != ''
  AND b.bridge_id = ''
ORDER BY c.cik, c.accession_number, c.bridge_id
LIMIT {max(1, int(limit))}
FORMAT JSONEachRow
"""


def build_context_rows(
    *,
    mappings: list[dict[str, Any]],
    company_fact_rows: list[dict[str, Any]],
    frame_observation_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for mapping in mappings:
        common = {
            "ticker": str(mapping.get("ticker") or ""),
            "timestamp_us": int(mapping.get("timestamp_us") or 0),
            "accepted_at_utc": mapping.get("accepted_at_utc"),
            "accepted_at_source": str(mapping.get("accepted_at_source") or ""),
            "mapping_confidence": float(mapping.get("mapping_confidence") or 0.0),
            "bridge_id": str(mapping.get("bridge_id") or ""),
        }
        for row in company_fact_rows:
            output.append(
                {
                    **common,
                    "cik": str(row.get("cik") or ""),
                    "accession_number": str(row.get("accession_number") or ""),
                    "source_id": str(row.get("company_fact_id") or ""),
                    "issuer_id": str(row.get("issuer_id") or ""),
                    "xbrl_row_kind": "company_fact",
                    "taxonomy": str(row.get("taxonomy") or ""),
                    "tag": str(row.get("tag") or ""),
                    "unit_code": str(row.get("unit_code") or ""),
                    "fiscal_year": int(row.get("fiscal_year") or 0),
                    "fiscal_period": str(row.get("fiscal_period") or ""),
                    "form_type": str(row.get("form_type") or ""),
                    "period_end_date": row.get("period_end_date") or "1970-01-01",
                    "value": float(row.get("value") or 0.0),
                    "calendar_period_code": "",
                    "location_code": "",
                }
            )
        for row in frame_observation_rows:
            output.append(
                {
                    **common,
                    "cik": str(row.get("cik") or ""),
                    "accession_number": str(row.get("accession_number") or ""),
                    "source_id": str(row.get("frame_observation_id") or ""),
                    "issuer_id": str(row.get("issuer_id") or ""),
                    "xbrl_row_kind": "frame_observation",
                    "taxonomy": str(row.get("taxonomy") or ""),
                    "tag": str(row.get("tag") or ""),
                    "unit_code": str(row.get("unit_code") or ""),
                    "fiscal_year": 0,
                    "fiscal_period": "",
                    "form_type": "",
                    "period_end_date": row.get("period_end_date") or "1970-01-01",
                    "value": float(row.get("value") or 0.0),
                    "calendar_period_code": str(row.get("calendar_period_code") or ""),
                    "location_code": str(row.get("location_code") or ""),
                }
            )
    return output


def insert_context_rows(client: ClickHouseHttpClient, config: XbrlContextSyncConfig, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    target = f"{quote_ident(config.context_database)}.{quote_ident(config.context_table)}"
    batch_size = max(1, int(config.insert_batch_rows))
    for offset in range(0, len(rows), batch_size):
        batch = rows[offset : offset + batch_size]
        body = "\n".join(json.dumps(row, separators=(",", ":"), ensure_ascii=False, default=str) for row in batch)
        client.execute(f"INSERT INTO {target} SETTINGS date_time_input_format = 'best_effort' FORMAT JSONEachRow\n{body}")


def context_identities_sql(
    config: XbrlContextSyncConfig,
    *,
    ticker: str,
    timestamp_us: int,
    accession_number: str,
) -> str:
    target = f"{quote_ident(config.context_database)}.{quote_ident(config.context_table)}"
    return f"""
SELECT xbrl_row_kind, source_id
FROM {target} FINAL
WHERE ticker = {sql_string(ticker)}
  AND timestamp_us = {int(timestamp_us)}
  AND accession_number = {sql_string(accession_number)}
GROUP BY xbrl_row_kind, source_id
ORDER BY xbrl_row_kind, source_id
FORMAT TSV
"""


def context_row_identity(row: dict[str, Any]) -> tuple[str, str]:
    return str(row.get("xbrl_row_kind") or ""), str(row.get("source_id") or "")


def deduplicate_context_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[tuple[str, int, str, str, str], dict[str, Any]] = {}
    for row in rows:
        if not str(row.get("source_id") or ""):
            raise RuntimeError(
                "SEC XBRL context row is missing its source identity: "
                f"kind={row.get('xbrl_row_kind')!r} accession={row.get('accession_number')!r}"
            )
        key = (
            str(row.get("ticker") or ""),
            int(row.get("timestamp_us") or 0),
            str(row.get("accession_number") or ""),
            *context_row_identity(row),
        )
        previous = unique.get(key)
        if previous is not None and previous != row:
            raise RuntimeError(f"conflicting SEC XBRL context rows share identity {key!r}")
        unique[key] = row
    return list(unique.values())


def query_settings_sql(config: XbrlContextSyncConfig) -> str:
    settings = [f"max_threads = {max(1, int(config.max_threads))}"]
    if str(config.max_memory_usage).strip() not in {"", "0"}:
        settings.append(f"max_memory_usage = {parse_size_bytes(str(config.max_memory_usage))}")
    return "SETTINGS " + ", ".join(settings)


def json_each_row(text: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]

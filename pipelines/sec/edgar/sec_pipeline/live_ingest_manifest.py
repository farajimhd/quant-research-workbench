from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from research.mlops.clickhouse import ClickHouseHttpClient, mergetree_settings_sql, quote_ident, sql_string


DEFAULT_LIVE_INGEST_MANIFEST_TABLE = "sec_filing_live_ingest_manifest_v3"
COMPLETE_STATUS = "complete"
PENDING_STATUS = "pending"
PENDING_SOURCE_STATUS = "pending_source"
FAILED_STATUS = "failed"


@dataclass(frozen=True, slots=True)
class LiveIngestManifestConfig:
    database: str = "q_live"
    table: str = DEFAULT_LIVE_INGEST_MANIFEST_TABLE
    storage_policy: str = ""


class SecLiveIngestManifest:
    def __init__(self, client: ClickHouseHttpClient, config: LiveIngestManifestConfig) -> None:
        self.client = client
        self.config = config

    def ensure_table(self) -> None:
        self.client.execute(create_live_ingest_manifest_table_sql(self.config))

    def completed_revisions(self, accession_numbers: list[str]) -> dict[str, datetime]:
        values = sorted({str(value) for value in accession_numbers if str(value)})
        if not values:
            return {}
        values_sql = ", ".join(sql_string(value) for value in values)
        text = self.client.execute(
            f"""
SELECT accession_number, toString(source_revision_at)
FROM {quote_ident(self.config.database)}.{quote_ident(self.config.table)} FINAL
WHERE accession_number IN ({values_sql})
  AND status = {sql_string(COMPLETE_STATUS)}
FORMAT TSV
"""
        )
        revisions: dict[str, datetime] = {}
        for line in text.splitlines():
            fields = line.split("\t", 1)
            if len(fields) != 2 or not fields[0] or not fields[1]:
                continue
            revisions[fields[0]] = datetime.fromisoformat(fields[1].replace("Z", "+00:00")).replace(tzinfo=UTC)
        return revisions

    def deferred_accessions(self, accession_numbers: list[str]) -> set[str]:
        values = sorted({str(value) for value in accession_numbers if str(value)})
        if not values:
            return set()
        values_sql = ", ".join(sql_string(value) for value in values)
        text = self.client.execute(
            f"""
SELECT accession_number
FROM {quote_ident(self.config.database)}.{quote_ident(self.config.table)} FINAL
WHERE accession_number IN ({values_sql})
  AND status = {sql_string(PENDING_SOURCE_STATUS)}
  AND retry_after_utc IS NOT NULL
  AND retry_after_utc > now64(3, 'UTC')
FORMAT TSV
"""
        )
        return {line.strip() for line in text.splitlines() if line.strip()}

    def mark_pending(self, **row: Any) -> None:
        self._insert({**row, "status": PENDING_STATUS, "error": ""})

    def mark_pending_source(self, *, error: str, **row: Any) -> None:
        self._insert({**row, "status": PENDING_SOURCE_STATUS, "error": error})

    def mark_failed(self, *, error: str, **row: Any) -> None:
        self._insert({**row, "status": FAILED_STATUS, "error": error})

    def mark_complete(self, **row: Any) -> None:
        self._insert({**row, "status": COMPLETE_STATUS, "error": ""})

    def _insert(self, row: dict[str, Any]) -> None:
        payload = {
            "accession_number": str(row["accession_number"]),
            "source_cik": str(row.get("source_cik") or ""),
            "primary_cik": str(row.get("primary_cik") or ""),
            "source_version_key": str(row.get("source_version_key") or ""),
            "source_revision_at": row["source_revision_at"],
            "source_revision_rank": int(row.get("source_revision_rank") or 0),
            "expected_document_rows": int(row.get("expected_document_rows") or 0),
            "expected_text_source_rows": int(row.get("expected_text_source_rows") or 0),
            "expected_rendered_text_rows": int(row.get("expected_rendered_text_rows") or 0),
            "expected_skip_rows": int(row.get("expected_skip_rows") or 0),
            "expected_xbrl_company_fact_rows": int(row.get("expected_xbrl_company_fact_rows") or 0),
            "expected_xbrl_frame_observation_rows": int(row.get("expected_xbrl_frame_observation_rows") or 0),
            "metadata_status": str(row.get("metadata_status") or ""),
            "xbrl_status": str(row.get("xbrl_status") or ""),
            "status": str(row["status"]),
            "error": str(row.get("error") or "")[:4000],
            "retry_after_utc": row.get("retry_after_utc"),
            "source_run_id": str(row.get("source_run_id") or ""),
        }
        self.client.execute(
            f"INSERT INTO {quote_ident(self.config.database)}.{quote_ident(self.config.table)} "
            "SETTINGS date_time_input_format = 'best_effort' FORMAT JSONEachRow\n"
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
        )


def create_live_ingest_manifest_table_sql(config: LiveIngestManifestConfig) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(config.database)}.{quote_ident(config.table)}
(
    accession_number String,
    source_cik String,
    primary_cik String,
    source_version_key String,
    source_revision_at DateTime64(3, 'UTC'),
    source_revision_rank UInt64,
    expected_document_rows UInt64,
    expected_text_source_rows UInt64,
    expected_rendered_text_rows UInt64,
    expected_skip_rows UInt64,
    expected_xbrl_company_fact_rows UInt64,
    expected_xbrl_frame_observation_rows UInt64,
    metadata_status LowCardinality(String),
    xbrl_status LowCardinality(String),
    status LowCardinality(String),
    error String,
    retry_after_utc Nullable(DateTime64(3, 'UTC')),
    source_run_id String,
    updated_at_utc DateTime64(9, 'UTC') DEFAULT now64(9, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at_utc)
ORDER BY accession_number
{mergetree_settings_sql(config.storage_policy)}
"""

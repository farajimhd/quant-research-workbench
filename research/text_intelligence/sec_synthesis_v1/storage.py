from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from research.mlops.clickhouse import ClickHouseHttpClient, insert_json_each_row, quote_ident, sql_string

from .contracts import CONTRACT_VERSION, ENGINE_VERSION, canonical_json


SYNTHESIS_TABLE = "sec_synthesis_v1"


def create_tables(client: ClickHouseHttpClient, database: str) -> None:
    client.execute(f"""
CREATE TABLE IF NOT EXISTS {quote_ident(database)}.{quote_ident(SYNTHESIS_TABLE)}
(
    accession_number String,
    cik String,
    accepted_at_utc DateTime64(9,'UTC'),
    source_hash String,
    contract_version LowCardinality(String),
    engine_version LowCardinality(String),
    form_type LowCardinality(String),
    ticker LowCardinality(String),
    composite_sentiment LowCardinality(String),
    positive_strength UInt8,
    negative_strength UInt8,
    disclosure_concepts Array(LowCardinality(String)),
    eligible_products Array(LowCardinality(String)),
    readable_summary String,
    synthesis_json String CODEC(ZSTD(6)),
    updated_at_utc DateTime64(6,'UTC') DEFAULT now64(6,'UTC')
)
ENGINE=ReplacingMergeTree(updated_at_utc)
PARTITION BY toYYYYMM(accepted_at_utc)
ORDER BY (cik,accession_number,contract_version)
""")


def persistence_row(document: Mapping[str, Any]) -> dict[str, Any]:
    view = (document.get("issuer_views") or [{}])[0]
    envelope = document.get("filing_envelope") or {}
    return {
        "accession_number": str(document["accession_number"]),
        "cik": str(document["cik"]),
        "accepted_at_utc": str(document["accepted_at_utc"]),
        "source_hash": str(document["source_hash"]),
        "contract_version": CONTRACT_VERSION,
        "engine_version": ENGINE_VERSION,
        "form_type": str(envelope.get("form_type") or ""),
        "ticker": str(view.get("ticker") or ""),
        "composite_sentiment": str(view.get("composite_sentiment") or "neutral"),
        "positive_strength": int(view.get("positive_strength") or 0),
        "negative_strength": int(view.get("negative_strength") or 0),
        "disclosure_concepts": sorted({str(row.get("concept") or "") for row in document.get("narrative_disclosures", []) if row.get("concept")}),
        "eligible_products": sorted({str(row.get("product") or "") for row in document.get("eligibility", []) if row.get("eligible")}),
        "readable_summary": str((document.get("synthesis") or {}).get("readable_summary") or ""),
        "synthesis_json": canonical_json(document),
    }


def persist_document(client: ClickHouseHttpClient, database: str, document: Mapping[str, Any]) -> None:
    row = persistence_row(document)
    insert_json_each_row(client, database, SYNTHESIS_TABLE, list(row), [row])


def load_documents(client: ClickHouseHttpClient, database: str, accessions: Sequence[str]) -> dict[str, dict[str, Any]]:
    values = sorted({str(value) for value in accessions if value})
    if not values:
        return {}
    clause = ",".join(sql_string(value) for value in values)
    rows = client.iter_json_each_row(f"""
SELECT accession_number,synthesis_json
FROM {quote_ident(database)}.{quote_ident(SYNTHESIS_TABLE)} FINAL
WHERE contract_version={sql_string(CONTRACT_VERSION)}
  AND engine_version={sql_string(ENGINE_VERSION)}
  AND accession_number IN ({clause})
FORMAT JSONEachRow
""")
    return {str(row["accession_number"]): json.loads(str(row["synthesis_json"])) for row in rows}

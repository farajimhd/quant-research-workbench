from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from research.mlops.clickhouse import ClickHouseHttpClient, quote_ident, sql_string


DEFAULT_RELATIONSHIP_TABLE = "id_issuer_relationship_v1"
DEFAULT_RELATIONSHIP_PATH = Path(__file__).resolve().parent / "curated" / "sec_issuer_relationships_v1.json"
SUPPORTED_RELATIONSHIP_TYPES = {"listed_ultimate_parent"}
CIK_PATTERN = re.compile(r"^\d{10}$")


@dataclass(frozen=True, slots=True)
class ResolvedRelationship:
    relationship_id: str
    child_issuer_id: str
    parent_issuer_id: str
    child_cik: str
    parent_cik: str
    relationship_type: str
    valid_from_date: str | None
    valid_to_date_exclusive: str | None
    confidence_score: float
    evidence_source: str
    evidence_url: str
    evidence_accession_number: str | None
    evidence_summary: str
    evidence_json: str
    source_content_sha256: str


def load_relationship_definitions(path: Path = DEFAULT_RELATIONSHIP_PATH) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list) or not value:
        raise ValueError(f"relationship file must contain a nonempty JSON array: {path}")
    rows = [validate_relationship_definition(dict(row)) for row in value]
    keys = [(row["child_cik"], row["parent_cik"], row["relationship_type"]) for row in rows]
    if len(keys) != len(set(keys)):
        raise ValueError("relationship file contains duplicate child/parent/type rows")
    return rows


def validate_relationship_definition(row: dict[str, Any]) -> dict[str, Any]:
    for key in ("child_cik", "parent_cik"):
        value = str(row.get(key) or "")
        if not CIK_PATTERN.fullmatch(value):
            raise ValueError(f"{key} must be a zero-padded 10-digit CIK: {value!r}")
        row[key] = value
    if row["child_cik"] == row["parent_cik"]:
        raise ValueError("child_cik and parent_cik must differ")
    relationship_type = str(row.get("relationship_type") or "")
    if relationship_type not in SUPPORTED_RELATIONSHIP_TYPES:
        raise ValueError(f"unsupported relationship_type: {relationship_type!r}")
    for key in ("child_name", "parent_name", "evidence_source", "evidence_url", "evidence_summary", "scope_note"):
        if not str(row.get(key) or "").strip():
            raise ValueError(f"{key} is required for {row['child_cik']} -> {row['parent_cik']}")
    if not str(row["evidence_url"]).startswith("https://www.sec.gov/"):
        raise ValueError("relationship evidence_url must point to an official SEC source")
    start = parse_optional_date(row.get("valid_from_date"), "valid_from_date")
    end = parse_optional_date(row.get("valid_to_date_exclusive"), "valid_to_date_exclusive")
    if start and end and start >= end:
        raise ValueError("valid_to_date_exclusive must be later than valid_from_date")
    confidence = float(row.get("confidence_score") or 0.0)
    if not 0.5 <= confidence <= 1.0:
        raise ValueError("confidence_score must be between 0.5 and 1.0")
    row["confidence_score"] = confidence
    row["valid_from_date"] = start.isoformat() if start else None
    row["valid_to_date_exclusive"] = end.isoformat() if end else None
    accession = str(row.get("evidence_accession_number") or "").strip()
    row["evidence_accession_number"] = accession or None
    return row


def parse_optional_date(value: Any, field: str) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ValueError(f"{field} must be YYYY-MM-DD: {value!r}") from exc


def relationship_table_ddl(database: str, table: str, storage_policy: str = "") -> str:
    settings = "SETTINGS index_granularity = 8192"
    if storage_policy:
        settings += f", storage_policy = {sql_string(storage_policy)}"
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(database)}.{quote_ident(table)}
(
    relationship_id String,
    child_issuer_id String,
    parent_issuer_id String,
    child_cik String,
    parent_cik String,
    relationship_type LowCardinality(String),
    relationship_status LowCardinality(String),
    valid_from_date Nullable(Date),
    valid_to_date_exclusive Nullable(Date),
    confidence_score Float64,
    evidence_source LowCardinality(String),
    evidence_url String,
    evidence_accession_number Nullable(String),
    evidence_summary String,
    evidence_json String,
    first_seen_at_utc DateTime64(3, 'UTC'),
    last_seen_at_utc DateTime64(3, 'UTC'),
    source_run_id String,
    source_content_sha256 String,
    inserted_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(last_seen_at_utc)
ORDER BY (child_cik, relationship_type, parent_cik, relationship_id)
{settings}
""".strip()


def resolve_relationships(
    client: ClickHouseHttpClient,
    *,
    database: str,
    definitions: list[dict[str, Any]],
) -> list[ResolvedRelationship]:
    ciks = sorted({row[key] for row in definitions for key in ("child_cik", "parent_cik")})
    cik_sql = ", ".join(sql_string(cik) for cik in ciks)
    text = client.execute(
        f"""
SELECT identifier_value_normalized AS cik, issuer_id
FROM {quote_ident(database)}.id_issuer_identifier_v1 FINAL
WHERE identifier_kind = 'cik'
  AND identifier_value_normalized IN ({cik_sql})
FORMAT JSONEachRow
"""
    )
    issuer_ids: dict[str, set[str]] = {cik: set() for cik in ciks}
    for line in text.splitlines():
        if line.strip():
            item = json.loads(line)
            issuer_ids[str(item["cik"])].add(str(item["issuer_id"]))
    invalid = {cik: sorted(values) for cik, values in issuer_ids.items() if len(values) != 1}
    if invalid:
        raise RuntimeError(f"each curated CIK must resolve to exactly one issuer_id: {invalid}")

    output: list[ResolvedRelationship] = []
    for row in definitions:
        canonical = json.dumps(row, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        relation_key = f"{row['child_cik']}:{row['relationship_type']}:{row['parent_cik']}"
        evidence = {
            "source": row["evidence_source"],
            "url": row["evidence_url"],
            "accession_number": row["evidence_accession_number"],
            "summary": row["evidence_summary"],
            "scope_note": row["scope_note"],
        }
        output.append(
            ResolvedRelationship(
                relationship_id="issuer-relationship:" + hashlib.sha256(relation_key.encode("ascii")).hexdigest()[:32],
                child_issuer_id=next(iter(issuer_ids[row["child_cik"]])),
                parent_issuer_id=next(iter(issuer_ids[row["parent_cik"]])),
                child_cik=row["child_cik"],
                parent_cik=row["parent_cik"],
                relationship_type=row["relationship_type"],
                valid_from_date=row["valid_from_date"],
                valid_to_date_exclusive=row["valid_to_date_exclusive"],
                confidence_score=row["confidence_score"],
                evidence_source=row["evidence_source"],
                evidence_url=row["evidence_url"],
                evidence_accession_number=row["evidence_accession_number"],
                evidence_summary=row["evidence_summary"],
                evidence_json=json.dumps(evidence, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
                source_content_sha256=digest,
            )
        )
    return output


def insert_relationships(
    client: ClickHouseHttpClient,
    *,
    database: str,
    table: str,
    relationships: list[ResolvedRelationship],
    run_id: str,
    observed_at: datetime | None = None,
) -> tuple[int, int]:
    timestamp = (observed_at or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    existing_text = client.execute(
        f"""
SELECT *
FROM {quote_ident(database)}.{quote_ident(table)} FINAL
WHERE relationship_type IN ({', '.join(sql_string(value) for value in sorted(SUPPORTED_RELATIONSHIP_TYPES))})
FORMAT JSONEachRow
"""
    )
    existing = {
        str(item["relationship_id"]): item
        for item in (json.loads(line) for line in existing_text.splitlines() if line.strip())
    }
    rows = []
    desired_ids = {row.relationship_id for row in relationships}
    for row in relationships:
        previous = existing.get(row.relationship_id)
        if (
            previous
            and str(previous.get("relationship_status") or "") == "active"
            and str(previous.get("source_content_sha256") or "") == row.source_content_sha256
        ):
            continue
        item = {
            **{field: getattr(row, field) for field in row.__dataclass_fields__},
            "relationship_status": "active",
            "first_seen_at_utc": str(previous.get("first_seen_at_utc") or timestamp) if previous else timestamp,
            "last_seen_at_utc": timestamp,
            "source_run_id": run_id,
            "inserted_at": timestamp,
        }
        rows.append(json.dumps(item, ensure_ascii=True, separators=(",", ":")))
    deactivated = 0
    for relationship_id, previous in existing.items():
        if relationship_id in desired_ids or str(previous.get("relationship_status") or "") != "active":
            continue
        item = dict(previous)
        item.update(
            relationship_status="inactive_removed_from_curated_snapshot",
            last_seen_at_utc=timestamp,
            source_run_id=run_id,
            inserted_at=timestamp,
        )
        rows.append(json.dumps(item, ensure_ascii=True, separators=(",", ":")))
        deactivated += 1
    if rows:
        client.execute(
            f"INSERT INTO {quote_ident(database)}.{quote_ident(table)} FORMAT JSONEachRow\n" + "\n".join(rows)
        )
    return len(rows) - deactivated, deactivated


def active_parent_listing_count_sql(database: str, table: str) -> str:
    db = quote_ident(database)
    return f"""
SELECT count()
FROM {db}.{quote_ident(table)} AS rel FINAL
INNER JOIN {db}.id_security_v1 AS sec FINAL ON sec.issuer_id = rel.parent_issuer_id
INNER JOIN {db}.id_listing_v1 AS listing FINAL ON listing.security_id = sec.security_id
INNER JOIN {db}.id_symbol_v1 AS symbol FINAL ON symbol.listing_id = listing.listing_id AND symbol.primary_symbol_flag = 1
INNER JOIN {db}.ref_exchange_v1 AS exchange FINAL ON exchange.exchange_code = listing.exchange_code
WHERE rel.relationship_status = 'active'
  AND rel.relationship_type = 'listed_ultimate_parent'
  AND sec.status = 'active'
  AND listing.listing_status = 'active'
  AND listing.currency_code = 'USD'
  AND exchange.iso_country_code = 'US'
  AND sec.product_type = 'STK'
  AND symbol.asset_type = 'stock'
  AND symbol.instrument_type IN ('ADRC', 'CS')
""".strip()

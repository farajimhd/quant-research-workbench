from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any, Iterable

from src.backend.application_registry import (
    DISCOVERY_FIELD_PRESENTATIONS,
    FIELD_DEFINITIONS,
)


FEATURE_PROJECTION_SCHEMA_VERSION = 1
_FIELDS_BY_ID = {field.field_id: field for field in FIELD_DEFINITIONS}
_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "symbol": ("symbol", "ticker"),
    "last_price": ("last_price", "last", "price", "current_open"),
    "short_interest_pct": ("short_interest_pct", "short_crowding_pct"),
}


def compact_feature_projection(
    rows: Iterable[dict[str, Any]],
    *,
    as_of: datetime | str,
    source_revision: str = "",
    source_schema_version: int | str = 1,
) -> dict[str, Any]:
    """Describe flat Scanner columns without repeating provenance on every row."""
    materialized = list(rows)
    as_of_value = _timestamp(as_of)
    fields: dict[str, dict[str, Any]] = {}
    for presentation in DISCOVERY_FIELD_PRESENTATIONS:
        column = presentation.column_id
        if not column:
            continue
        definition = _FIELDS_BY_ID.get(presentation.field_id)
        present = [row for row in materialized if _column_value(row, column) not in (None, "")]
        null_rows = [row for row in materialized if _column_value(row, column) in (None, "")]
        latest_available_at = _latest_available_at(materialized, column)
        null_reasons = Counter(
            str(row.get(f"{column}_null_reason") or "not_available_at_clock")
            for row in null_rows
        )
        fields[column] = {
            "field_id": presentation.field_id or presentation.source_id,
            "source_id": presentation.source_id,
            "owner": definition.owner if definition else "qmd_gateway",
            "source_path": definition.source_path if definition else "product://qmd/scanner",
            "query_plan_id": definition.query_plan_id if definition else "qmd.scanner.snapshot.v1",
            "schema_version": definition.schema_version if definition else 1,
            "source_revision": source_revision,
            "source_schema_version": source_schema_version,
            "event_clock": definition.event_at if definition else "QMD event time",
            "availability_clock": definition.available_at if definition else "QMD processing time",
            "latest_available_at": latest_available_at,
            "freshness_policy": definition.freshness_policy if definition else "QMD snapshot freshness",
            "status": definition.status if definition else "implemented",
            "coverage_count": len(present),
            "coverage_pct": round(len(present) / max(1, len(materialized)) * 100, 1),
            "null_count": len(null_rows),
            "null_reasons": dict(sorted(null_reasons.items())),
        }
    return {
        "schema_version": FEATURE_PROJECTION_SCHEMA_VERSION,
        "authority": "application_field_registry",
        "as_of": as_of_value,
        "row_count": len(materialized),
        "source_revision": source_revision,
        "source_schema_version": source_schema_version,
        "fields": fields,
    }


def _latest_available_at(rows: list[dict[str, Any]], column: str) -> str | None:
    candidates: list[str] = []
    for row in rows:
        for key in (
            f"{column}_available_at",
            f"{column}_published_at",
            f"{column}_source_date",
            "available_at",
        ):
            value = row.get(key)
            if value not in (None, ""):
                candidates.append(str(value))
                break
    return max(candidates) if candidates else None


def _column_value(row: dict[str, Any], column: str) -> Any:
    for key in _COLUMN_ALIASES.get(column, (column,)):
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _timestamp(value: datetime | str) -> str:
    if isinstance(value, datetime):
        aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        return aware.astimezone(UTC).isoformat()
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()

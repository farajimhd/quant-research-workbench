from __future__ import annotations

from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "sec_issuer_review_schema_v1"
_DIRECTIONS = {"positive", "negative", "mixed", "neutral", "contextual", "uncertain"}
_RISK = {"increased", "decreased", "unchanged", "mixed", "uncertain"}
_GUIDANCE = {"raised", "lowered", "reaffirmed", "withdrawn", "introduced", "none", "uncertain"}

TRANSPORT_SCHEMA: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "sec_issuer_review_v1",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["accession_number", "cik", "ticker", "materiality_probability", "forecast_relevance_probability", "positive_implication_probability", "negative_implication_probability", "fundamental_direction", "risk_change", "guidance_change", "event_tags", "evidence_ids", "conflict_ids", "abstain", "abstention_reasons", "summary"],
            "properties": {
                "accession_number": {"type": "string"}, "cik": {"type": "string"}, "ticker": {"type": "string"},
                "materiality_probability": {"type": "number", "minimum": 0, "maximum": 1},
                "forecast_relevance_probability": {"type": "number", "minimum": 0, "maximum": 1},
                "positive_implication_probability": {"type": "number", "minimum": 0, "maximum": 1},
                "negative_implication_probability": {"type": "number", "minimum": 0, "maximum": 1},
                "fundamental_direction": {"type": "string", "enum": sorted(_DIRECTIONS)},
                "risk_change": {"type": "string", "enum": sorted(_RISK)},
                "guidance_change": {"type": "string", "enum": sorted(_GUIDANCE)},
                "event_tags": {"type": "array", "items": {"type": "string"}, "maxItems": 12},
                "evidence_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 12},
                "conflict_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 12},
                "abstain": {"type": "boolean"},
                "abstention_reasons": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
                "summary": {"type": "string", "maxLength": 1200},
            },
        },
    },
}


def validate_output(result: Mapping[str, Any], synthesis: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if str(result.get("accession_number") or "") != str(synthesis.get("accession_number") or ""):
        errors.append("accession_number does not match the reviewed synthesis")
    if str(result.get("cik") or "") != str(synthesis.get("cik") or ""):
        errors.append("cik does not match the reviewed synthesis")
    known_evidence = {
        str(evidence.get("evidence_id") or "")
        for row in synthesis.get("narrative_disclosures") or ()
        for evidence in row.get("evidence") or ()
    }
    known_conflicts = {
        str(row.get("reconciliation_id") or "")
        for row in synthesis.get("reconciliation") or ()
        if row.get("state") == "contradiction"
    }
    unknown_evidence = sorted(set(_strings(result.get("evidence_ids"))) - known_evidence)
    unknown_conflicts = sorted(set(_strings(result.get("conflict_ids"))) - known_conflicts)
    if unknown_evidence:
        errors.append(f"unknown evidence_ids: {unknown_evidence}")
    if unknown_conflicts:
        errors.append(f"unknown conflict_ids: {unknown_conflicts}")
    for field in ("materiality_probability", "forecast_relevance_probability", "positive_implication_probability", "negative_implication_probability"):
        try:
            value = float(result.get(field))
        except (TypeError, ValueError):
            errors.append(f"{field} is not numeric")
        else:
            if not 0 <= value <= 1:
                errors.append(f"{field} is outside [0,1]")
    if result.get("fundamental_direction") not in _DIRECTIONS:
        errors.append("invalid fundamental_direction")
    if result.get("risk_change") not in _RISK:
        errors.append("invalid risk_change")
    if result.get("guidance_change") not in _GUIDANCE:
        errors.append("invalid guidance_change")
    if bool(result.get("abstain")) and not _strings(result.get("abstention_reasons")):
        errors.append("abstention_reasons required when abstain is true")
    return errors


def _strings(value: Any) -> Sequence[str]:
    return [str(item) for item in value] if isinstance(value, list) else []

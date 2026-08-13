from __future__ import annotations

from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "llm_issuer_news_labels_v3"
EVENT_TAGS = (
    "acquisition", "analyst_action", "asset_sale", "capital_return",
    "capital_structure", "clinical_trial", "commercial_contract", "earnings",
    "financing", "financial_condition", "guidance", "legal", "listing",
    "management_governance", "market_observation", "operations", "ownership",
    "partnership", "product", "regulatory", "solvency", "strategy", "workforce",
    "other_material",
)
ISSUER_ROLES = (
    "primary_subject", "acquirer", "target", "buyer", "seller", "partner",
    "customer", "supplier", "borrower", "lender", "investor", "investee",
    "plaintiff", "defendant", "regulatory_subject", "competitor", "mentioned_other",
)
TIME_SCOPES = ("current", "forward", "historical", "mixed", "unclear")
CLAIM_SOURCES = ("issuer", "regulator", "analyst", "editorial", "mixed", "unknown")
IDENTITY_SOURCES = ("explicit_text", "metadata", "llm_inference")
ISSUER_REQUIRED_FIELDS = tuple(
    [
        "issuer_name", "ticker", "exchange", "identity_source",
        "identity_confidence_probability", "forecast_relevance_probability",
        "positive_implication_probability", "negative_implication_probability",
        "event_tags", "issuer_roles", "time_scope", "claim_source",
        "evidence_sentence_ids",
    ]
)
OUTPUT_REQUIRED_FIELDS = ("schema_version", "issuers", "unresolved_issuer_mentions")


ISSUER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "issuer_name", "ticker", "exchange", "identity_source",
        "identity_confidence_probability", "forecast_relevance_probability",
        "positive_implication_probability", "negative_implication_probability",
        "event_tags", "issuer_roles", "time_scope", "claim_source",
        "evidence_sentence_ids",
    ],
    "properties": {
        "issuer_name": {"type": "string", "minLength": 1},
        "ticker": {"type": ["string", "null"]},
        "exchange": {"type": ["string", "null"]},
        "identity_source": {"type": "string", "enum": list(IDENTITY_SOURCES)},
        "identity_confidence_probability": {"type": "number", "minimum": 0, "maximum": 1},
        "forecast_relevance_probability": {"type": "number", "minimum": 0, "maximum": 1},
        "positive_implication_probability": {"type": "number", "minimum": 0, "maximum": 1},
        "negative_implication_probability": {"type": "number", "minimum": 0, "maximum": 1},
        "event_tags": {
            "type": "array", "items": {"type": "string", "enum": list(EVENT_TAGS)},
            "uniqueItems": True,
        },
        "issuer_roles": {
            "type": "array", "items": {"type": "string", "enum": list(ISSUER_ROLES)},
            "uniqueItems": True,
        },
        "time_scope": {"type": "string", "enum": list(TIME_SCOPES)},
        "claim_source": {"type": "string", "enum": list(CLAIM_SOURCES)},
        "evidence_sentence_ids": {
            "type": "array", "minItems": 1, "maxItems": 3, "uniqueItems": True,
            "items": {"type": "integer", "minimum": 1},
        },
    },
}

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "issuers", "unresolved_issuer_mentions"],
    "properties": {
        "schema_version": {"type": "string", "const": SCHEMA_VERSION},
        "issuers": {"type": "array", "items": ISSUER_SCHEMA},
        "unresolved_issuer_mentions": {
            "type": "array", "items": {"type": "string", "minLength": 1},
            "uniqueItems": True,
        },
    },
}


def _transport_schema(value: Any) -> Any:
    """Remove unsupported grammar keywords; local validation retains them."""
    if isinstance(value, dict):
        return {key: _transport_schema(item) for key, item in value.items() if key != "uniqueItems"}
    if isinstance(value, list):
        return [_transport_schema(item) for item in value]
    return value


TRANSPORT_SCHEMA: dict[str, Any] = _transport_schema(OUTPUT_SCHEMA)


def normalize_ticker(value: Any) -> str:
    return str(value or "").strip().upper()


def canonicalize_output(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Canonicalize ordering only; never alter semantic values."""
    result = dict(payload)
    issuers: list[dict[str, Any]] = []
    for raw in payload.get("issuers", []):
        row = dict(raw)
        for field in ("event_tags", "issuer_roles", "evidence_sentence_ids"):
            row[field] = sorted(set(row.get(field, [])))
        issuers.append(row)
    result["issuers"] = sorted(issuers, key=lambda row: str(row.get("issuer_name") or "").casefold())
    result["unresolved_issuer_mentions"] = sorted(set(payload.get("unresolved_issuer_mentions", [])), key=str.casefold)
    return result


def validate_output(payload: Mapping[str, Any], sentence_ids: Sequence[int]) -> list[str]:
    errors: list[str] = []
    unexpected = sorted(set(payload) - set(OUTPUT_REQUIRED_FIELDS))
    missing = sorted(set(OUTPUT_REQUIRED_FIELDS) - set(payload))
    if unexpected:
        errors.append(f"unexpected output fields: {unexpected}")
    if missing:
        errors.append(f"missing output fields: {missing}")
    if payload.get("schema_version") != SCHEMA_VERSION:
        errors.append("invalid schema_version")
    issuers = payload.get("issuers")
    if not isinstance(issuers, list):
        return errors + ["issuers must be a list"]
    valid_sentence_ids = set(sentence_ids)
    seen_tickers: set[str] = set()
    seen_names: set[str] = set()
    previous_name = ""
    for index, row in enumerate(issuers):
        prefix = f"issuers[{index}]"
        if not isinstance(row, Mapping):
            errors.append(f"{prefix} must be an object")
            continue
        unexpected_fields = sorted(set(row) - set(ISSUER_REQUIRED_FIELDS))
        missing_fields = sorted(set(ISSUER_REQUIRED_FIELDS) - set(row))
        if unexpected_fields:
            errors.append(f"{prefix} has unexpected fields: {unexpected_fields}")
        if missing_fields:
            errors.append(f"{prefix} is missing fields: {missing_fields}")
        name = str(row.get("issuer_name") or "").strip()
        if not isinstance(row.get("issuer_name"), str) or not name:
            errors.append(f"{prefix}.issuer_name is empty")
        ticker_value = row.get("ticker")
        exchange_value = row.get("exchange")
        if ticker_value is not None and not isinstance(ticker_value, str):
            errors.append(f"{prefix}.ticker must be a string or null")
        if exchange_value is not None and not isinstance(exchange_value, str):
            errors.append(f"{prefix}.exchange must be a string or null")
        if name.casefold() < previous_name.casefold():
            errors.append("issuers are not sorted by issuer_name")
        previous_name = name
        if name.casefold() in seen_names:
            errors.append(f"duplicate issuer_name: {name}")
        seen_names.add(name.casefold())
        ticker = normalize_ticker(row.get("ticker"))
        if ticker and ticker in seen_tickers:
            errors.append(f"duplicate ticker: {ticker}")
        if ticker:
            seen_tickers.add(ticker)
        for field in (
            "identity_confidence_probability", "forecast_relevance_probability",
            "positive_implication_probability", "negative_implication_probability",
        ):
            value = row.get(field)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 1:
                errors.append(f"{prefix}.{field} must be a number in [0,1]")
        _enum(errors, prefix, row, "identity_source", IDENTITY_SOURCES)
        _enum(errors, prefix, row, "time_scope", TIME_SCOPES)
        _enum(errors, prefix, row, "claim_source", CLAIM_SOURCES)
        _sorted_enum_list(errors, prefix, row, "event_tags", EVENT_TAGS)
        _sorted_enum_list(errors, prefix, row, "issuer_roles", ISSUER_ROLES)
        evidence = row.get("evidence_sentence_ids")
        if not isinstance(evidence, list) or not 1 <= len(evidence) <= 3:
            errors.append(f"{prefix}.evidence_sentence_ids must contain 1-3 IDs")
        elif evidence != sorted(set(evidence)):
            errors.append(f"{prefix}.evidence_sentence_ids must be unique and sorted")
        elif any(isinstance(value, bool) or not isinstance(value, int) or value not in valid_sentence_ids for value in evidence):
            errors.append(f"{prefix}.evidence_sentence_ids contains an unknown ID")
    unresolved = payload.get("unresolved_issuer_mentions")
    if not isinstance(unresolved, list) or any(not isinstance(value, str) or not value.strip() for value in unresolved):
        errors.append("unresolved_issuer_mentions must be a list of nonempty strings")
    elif unresolved != sorted(set(unresolved), key=str.casefold):
        errors.append("unresolved_issuer_mentions must be unique and sorted")
    return errors


def _enum(errors: list[str], prefix: str, row: Mapping[str, Any], field: str, allowed: Sequence[str]) -> None:
    if row.get(field) not in allowed:
        errors.append(f"{prefix}.{field} is invalid")


def _sorted_enum_list(
    errors: list[str], prefix: str, row: Mapping[str, Any], field: str, allowed: Sequence[str]
) -> None:
    values = row.get(field)
    if not isinstance(values, list) or any(value not in allowed for value in values):
        errors.append(f"{prefix}.{field} contains an invalid value")
    elif values != sorted(set(values)):
        errors.append(f"{prefix}.{field} must be sorted and unique")

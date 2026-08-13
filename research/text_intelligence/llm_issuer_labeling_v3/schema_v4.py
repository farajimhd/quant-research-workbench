from __future__ import annotations

from typing import Any, Mapping

from .schema import (
    CLAIM_SOURCES,
    EVENT_TAGS,
    IDENTITY_SOURCES,
    ISSUER_ROLES,
    TIME_SCOPES,
    normalize_ticker,
)


SCHEMA_VERSION = "llm_issuer_news_labels_v4"
ISSUER_REQUIRED_FIELDS = (
    "issuer_name",
    "ticker",
    "exchange",
    "identity_source",
    "identity_confidence_probability",
    "forecast_relevance_probability",
    "positive_implication_probability",
    "negative_implication_probability",
    "event_tags",
    "issuer_roles",
    "time_scope",
    "claim_source",
)
OUTPUT_REQUIRED_FIELDS = (
    "schema_version",
    "article_forecast_eligible",
    "issuers",
    "unresolved_issuer_mentions",
)


def canonicalize_output(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Canonicalize ordering without changing semantic values."""

    result = dict(payload)
    issuers: list[dict[str, Any]] = []
    for raw in payload.get("issuers", []):
        row = dict(raw)
        for field in ("event_tags", "issuer_roles"):
            values = row.get(field)
            if isinstance(values, list):
                row[field] = sorted(set(values))
        issuers.append(row)
    result["issuers"] = sorted(
        issuers,
        key=lambda row: (
            str(row.get("issuer_name") or "").casefold(),
            normalize_ticker(row.get("ticker")),
            str(row.get("exchange") or "").casefold(),
        ),
    )
    result["unresolved_issuer_mentions"] = sorted(
        set(payload.get("unresolved_issuer_mentions", [])), key=str.casefold
    )
    return result


def derive_article_forecast_eligible(payload: Mapping[str, Any]) -> bool:
    return any(
        isinstance(row.get("forecast_relevance_probability"), (int, float))
        and not isinstance(row.get("forecast_relevance_probability"), bool)
        and float(row["forecast_relevance_probability"]) >= 0.5
        for row in payload.get("issuers", [])
        if isinstance(row, Mapping)
    )


def validate_output(payload: Mapping[str, Any], *, allow_legacy_nulls: bool = False) -> list[str]:
    """Validate V4 labels.

    ``allow_legacy_nulls`` is reserved for provenance-preserving conversions of
    older authorities that never certified all V4 fields. Fresh annotations
    must leave it false.
    """

    errors: list[str] = []
    unexpected = sorted(set(payload) - set(OUTPUT_REQUIRED_FIELDS))
    missing = sorted(set(OUTPUT_REQUIRED_FIELDS) - set(payload))
    if unexpected:
        errors.append(f"unexpected output fields: {unexpected}")
    if missing:
        errors.append(f"missing output fields: {missing}")
    if payload.get("schema_version") != SCHEMA_VERSION:
        errors.append("invalid schema_version")
    if not isinstance(payload.get("article_forecast_eligible"), bool):
        errors.append("article_forecast_eligible must be boolean")
    issuers = payload.get("issuers")
    if not isinstance(issuers, list):
        return errors + ["issuers must be a list"]

    seen_tickers: set[str] = set()
    seen_identity_keys: set[tuple[str, str, str]] = set()
    previous_key: tuple[str, str, str] | None = None
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

        name_value = row.get("issuer_name")
        if name_value is None and not allow_legacy_nulls:
            errors.append(f"{prefix}.issuer_name is null")
        elif name_value is not None and (
            not isinstance(name_value, str) or not name_value.strip()
        ):
            errors.append(f"{prefix}.issuer_name must be a nonempty string or legacy null")
        for field in ("ticker", "exchange"):
            if row.get(field) is not None and not isinstance(row.get(field), str):
                errors.append(f"{prefix}.{field} must be a string or null")

        key = (
            str(name_value or "").casefold(),
            normalize_ticker(row.get("ticker")),
            str(row.get("exchange") or "").casefold(),
        )
        if previous_key is not None and key < previous_key:
            errors.append("issuers are not canonically sorted")
        previous_key = key
        if key in seen_identity_keys:
            errors.append(f"duplicate issuer identity: {key}")
        seen_identity_keys.add(key)
        ticker = normalize_ticker(row.get("ticker"))
        if ticker and ticker in seen_tickers:
            errors.append(f"duplicate ticker: {ticker}")
        if ticker:
            seen_tickers.add(ticker)

        _nullable_enum(
            errors, prefix, row, "identity_source", IDENTITY_SOURCES, allow_legacy_nulls
        )
        _nullable_enum(errors, prefix, row, "time_scope", TIME_SCOPES, allow_legacy_nulls)
        _nullable_enum(
            errors, prefix, row, "claim_source", CLAIM_SOURCES, allow_legacy_nulls
        )
        for field in (
            "identity_confidence_probability",
            "forecast_relevance_probability",
            "positive_implication_probability",
            "negative_implication_probability",
        ):
            value = row.get(field)
            if value is None and allow_legacy_nulls:
                continue
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not 0 <= value <= 1
            ):
                errors.append(f"{prefix}.{field} must be a number in [0,1]")
        _nullable_sorted_enum_list(
            errors, prefix, row, "event_tags", EVENT_TAGS, allow_legacy_nulls
        )
        _nullable_sorted_enum_list(
            errors, prefix, row, "issuer_roles", ISSUER_ROLES, allow_legacy_nulls
        )

    unresolved = payload.get("unresolved_issuer_mentions")
    if not isinstance(unresolved, list) or any(
        not isinstance(value, str) or not value.strip() for value in unresolved
    ):
        errors.append("unresolved_issuer_mentions must be a list of nonempty strings")
    elif unresolved != sorted(set(unresolved), key=str.casefold):
        errors.append("unresolved_issuer_mentions must be unique and sorted")
    if isinstance(payload.get("article_forecast_eligible"), bool):
        derived = derive_article_forecast_eligible(payload)
        if payload["article_forecast_eligible"] != derived:
            errors.append("article_forecast_eligible disagrees with issuer probabilities")
    return errors


def _nullable_enum(
    errors: list[str],
    prefix: str,
    row: Mapping[str, Any],
    field: str,
    allowed: tuple[str, ...],
    allow_null: bool,
) -> None:
    if row.get(field) is None and allow_null:
        return
    if row.get(field) not in allowed:
        errors.append(f"{prefix}.{field} is invalid")


def _nullable_sorted_enum_list(
    errors: list[str],
    prefix: str,
    row: Mapping[str, Any],
    field: str,
    allowed: tuple[str, ...],
    allow_null: bool,
) -> None:
    values = row.get(field)
    if values is None and allow_null:
        return
    if not isinstance(values, list) or any(value not in allowed for value in values):
        errors.append(f"{prefix}.{field} contains an invalid value")
    elif values != sorted(set(values)):
        errors.append(f"{prefix}.{field} must be sorted and unique")

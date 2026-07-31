from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


SAMPLE_VERSION = "news_semantic_ground_truth_sample_v1"
ANNOTATION_VERSION_V1 = "news_semantic_ground_truth_annotation_v1"
ANNOTATION_VERSION = "news_semantic_ground_truth_annotation_v2"
ANNOTATION_VERSIONS = {ANNOTATION_VERSION_V1, ANNOTATION_VERSION}

EXTRACTION_DECISIONS = {
    "labeled",
    "no_supported_event",
    "non_issuer_market_content",
    "identity_not_found",
    "issuer_ambiguous",
    "passage_ambiguous",
    "empty_semantic_text",
    "unsupported_instrument",
}
CONTENT_ROLES = {
    "primary_event",
    "regulatory_event",
    "analyst_event",
    "editorial_analysis",
    "automated_summary",
    "market_roundup",
    "mover_recap",
    "why_moving_followup",
    "preview",
}
SOURCE_ORIGINS = {
    "issuer_direct",
    "regulatory_primary",
    "analyst_research",
    "editorial_original",
    "editorial_aggregation",
    "automated_summary",
}
ISSUER_ROLES = {
    "primary_subject",
    "target",
    "acquirer",
    "counterparty",
    "analyst_subject",
    "mentioned_subject",
}
EVIDENCE_SCOPES = {"ticker_specific", "shared_event", "document_context"}
MODALITIES = {"confirmed", "planned", "expected", "opinion", "rumored", "mixed"}
TIME_ORIENTATIONS = {"historical", "current", "forward", "mixed"}
DIRECTIONS = {"positive", "negative", "neutral", "mixed"}
RATING_ACTIONS = {
    "not_stated",
    "stated",
    "initiated",
    "maintained",
    "reiterated",
    "upgraded",
    "downgraded",
    "resumed",
    "suspended",
}
PRICE_TARGET_ACTIONS = {
    "not_stated",
    "stated",
    "set",
    "maintained",
    "raised",
    "lowered",
    "removed",
}
ANALYST_OPINION_KINDS = {"individual", "firm", "consensus_aggregate"}
NULLABLE_ANALYST_TEXT_FIELDS = {
    "analyst_name",
    "firm_name",
    "employment_valid_from",
    "employment_valid_to",
    "rating_from",
    "rating_to",
    "price_target_currency",
    "forecast_horizon_text",
    "ambiguity_notes",
}


@dataclass(frozen=True, slots=True)
class AnnotationValidation:
    errors: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not self.errors


def stable_json_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_annotation(
    annotation: Mapping[str, Any],
    *,
    expected_item: Mapping[str, Any] | None = None,
) -> AnnotationValidation:
    errors: list[str] = []
    _choice(errors, annotation, "extraction_decision", EXTRACTION_DECISIONS)
    _choice(errors, annotation, "content_role", CONTENT_ROLES)
    _choice(errors, annotation, "source_origin", SOURCE_ORIGINS)
    _bounded_int(errors, annotation, "reviewer_confidence", 0, 4)
    annotation_version = annotation.get("annotation_version")
    if annotation_version not in ANNOTATION_VERSIONS:
        errors.append("annotation_version_mismatch")
    if expected_item is not None:
        for field in ("sample_id", "source_id", "source_timestamp", "source_text_sha256"):
            if str(annotation.get(field) or "") != str(expected_item.get(field) or ""):
                errors.append(f"{field}_mismatch")
    units = annotation.get("issuer_units")
    if not isinstance(units, list):
        errors.append("issuer_units_must_be_list")
        units = []
    if annotation.get("extraction_decision") == "labeled" and not units:
        errors.append("labeled_requires_issuer_unit")
    if annotation.get("extraction_decision") != "labeled" and units:
        errors.append("abstention_cannot_have_issuer_units")
    seen: set[tuple[str, str]] = set()
    for index, raw in enumerate(units):
        prefix = f"issuer_units[{index}]"
        if not isinstance(raw, Mapping):
            errors.append(f"{prefix}_must_be_object")
            continue
        ticker = str(raw.get("ticker") or "").strip().upper()
        if not ticker:
            errors.append(f"{prefix}.ticker_required")
        _choice(errors, raw, "issuer_role", ISSUER_ROLES, prefix)
        _choice(errors, raw, "evidence_scope", EVIDENCE_SCOPES, prefix)
        _choice(errors, raw, "modality", MODALITIES, prefix)
        _choice(errors, raw, "time_orientation", TIME_ORIENTATIONS, prefix)
        _choice(errors, raw, "semantic_direction", DIRECTIONS, prefix)
        _bounded_int(errors, raw, "positive_evidence_level", 0, 4, prefix)
        _bounded_int(errors, raw, "negative_evidence_level", 0, 4, prefix)
        _bounded_int(errors, raw, "annotation_confidence", 0, 4, prefix)
        concepts = raw.get("event_concepts")
        if not isinstance(concepts, list) or any(not str(value).strip() for value in concepts):
            errors.append(f"{prefix}.event_concepts_must_be_nonempty_strings")
        evidence = raw.get("evidence_quotes")
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"{prefix}.evidence_quotes_required")
        spans = raw.get("evidence_spans")
        if not isinstance(spans, list) or not spans:
            errors.append(f"{prefix}.evidence_spans_required")
            spans = []
        for span_index, span in enumerate(spans):
            span_prefix = f"{prefix}.evidence_spans[{span_index}]"
            if not isinstance(span, Mapping):
                errors.append(f"{span_prefix}_must_be_object")
                continue
            if span.get("source_field") not in {
                "title",
                "teaser",
                "rendered_text",
                "source_lane",
            }:
                errors.append(f"{span_prefix}.source_field_invalid")
            if not str(span.get("quote") or "").strip():
                errors.append(f"{span_prefix}.quote_required")
            start = span.get("start")
            end = span.get("end")
            if (
                not isinstance(start, int)
                or isinstance(start, bool)
                or not isinstance(end, int)
                or isinstance(end, bool)
                or start < 0
                or end <= start
            ):
                errors.append(f"{span_prefix}.range_invalid")
        if not str(raw.get("semantic_rationale") or "").strip():
            errors.append(f"{prefix}.semantic_rationale_required")
        for flag in (
            "forecast_trigger_eligible",
            "reaction_evaluation_eligible",
            "issuer_history_context_eligible",
        ):
            if not isinstance(raw.get(flag), bool):
                errors.append(f"{prefix}.{flag}_must_be_boolean")
        if annotation_version == ANNOTATION_VERSION:
            _validate_v2_unit(errors, raw, prefix)
        key = (ticker, str(raw.get("issuer_role") or ""))
        if key in seen:
            errors.append(f"{prefix}.duplicate_ticker_role")
        seen.add(key)
        _validate_direction_levels(errors, raw, prefix)
    return AnnotationValidation(tuple(errors))


def _validate_v2_unit(
    errors: list[str],
    value: Mapping[str, Any],
    prefix: str,
) -> None:
    for flag in (
        "analyst_context_eligible",
        "analyst_evaluation_eligible",
    ):
        if not isinstance(value.get(flag), bool):
            errors.append(f"{prefix}.{flag}_must_be_boolean")
    opinions = value.get("analyst_opinions")
    if not isinstance(opinions, list):
        errors.append(f"{prefix}.analyst_opinions_must_be_list")
        opinions = []
    if opinions and not value.get("analyst_context_eligible"):
        errors.append(f"{prefix}.analyst_opinions_require_context_eligibility")
    if value.get("analyst_evaluation_eligible") and not opinions:
        errors.append(f"{prefix}.analyst_evaluation_requires_opinion")
    for index, opinion in enumerate(opinions):
        opinion_prefix = f"{prefix}.analyst_opinions[{index}]"
        if not isinstance(opinion, Mapping):
            errors.append(f"{opinion_prefix}_must_be_object")
            continue
        _choice(
            errors,
            opinion,
            "opinion_kind",
            ANALYST_OPINION_KINDS,
            opinion_prefix,
        )
        if opinion.get("opinion_kind") != "consensus_aggregate" and not (
            str(opinion.get("analyst_name") or "").strip()
            or str(opinion.get("firm_name") or "").strip()
        ):
            errors.append(f"{opinion_prefix}.analyst_or_firm_required")
        for field in NULLABLE_ANALYST_TEXT_FIELDS:
            raw_text = opinion.get(field)
            if raw_text is not None and not isinstance(raw_text, str):
                errors.append(f"{opinion_prefix}.{field}_must_be_string_or_null")
        for field in ("analyst_aliases", "firm_aliases"):
            aliases = opinion.get(field)
            if not isinstance(aliases, list) or any(
                not isinstance(alias, str) or not alias.strip() for alias in aliases
            ):
                errors.append(f"{opinion_prefix}.{field}_must_be_string_list")
        _choice(errors, opinion, "rating_action", RATING_ACTIONS, opinion_prefix)
        _choice(
            errors,
            opinion,
            "price_target_action",
            PRICE_TARGET_ACTIONS,
            opinion_prefix,
        )
        for field in ("price_target_from", "price_target_to"):
            raw = opinion.get(field)
            if raw is not None and (
                not isinstance(raw, (int, float))
                or isinstance(raw, bool)
                or raw < 0
            ):
                errors.append(f"{opinion_prefix}.{field}_must_be_nonnegative_number_or_null")
        if (
            opinion.get("price_target_from") is not None
            or opinion.get("price_target_to") is not None
        ) and not str(opinion.get("price_target_currency") or "").strip():
            errors.append(f"{opinion_prefix}.price_target_currency_required")
        target_action = opinion.get("price_target_action")
        if target_action in {"raised", "lowered"} and (
            opinion.get("price_target_from") is None
            or opinion.get("price_target_to") is None
        ):
            errors.append(f"{opinion_prefix}.target_change_requires_from_and_to")
        if target_action in {"set", "stated"} and opinion.get("price_target_to") is None:
            errors.append(f"{opinion_prefix}.target_set_requires_to")
        if target_action == "maintained" and opinion.get("price_target_to") is None:
            errors.append(f"{opinion_prefix}.target_maintained_requires_to")
        if target_action == "removed" and opinion.get("price_target_from") is None:
            errors.append(f"{opinion_prefix}.target_removed_requires_from")
        rating_action = opinion.get("rating_action")
        rating_from = str(opinion.get("rating_from") or "").strip()
        rating_to = str(opinion.get("rating_to") or "").strip()
        if (
            rating_action in {"upgraded", "downgraded"}
            and opinion.get("opinion_kind") != "consensus_aggregate"
            and not (rating_from and rating_to)
        ):
            errors.append(f"{opinion_prefix}.rating_change_requires_from_and_to")
        if rating_action in {"initiated", "stated"} and not rating_to:
            errors.append(f"{opinion_prefix}.rating_initiated_requires_to")
        if rating_action in {"maintained", "reiterated"}:
            if not (rating_from and rating_to):
                errors.append(
                    f"{opinion_prefix}.rating_maintained_requires_from_and_to"
                )
            elif rating_from.casefold() != rating_to.casefold():
                errors.append(
                    f"{opinion_prefix}.rating_maintained_requires_equal_endpoints"
                )
        if rating_action == "resumed" and not rating_to:
            errors.append(f"{opinion_prefix}.rating_resumed_requires_to")
        if rating_action == "suspended" and not rating_from:
            errors.append(f"{opinion_prefix}.rating_suspended_requires_from")
        if rating_action == "not_stated" and (rating_from or rating_to):
            errors.append(f"{opinion_prefix}.rating_not_stated_conflicts_with_values")
        if target_action == "not_stated" and (
            opinion.get("price_target_from") is not None
            or opinion.get("price_target_to") is not None
        ):
            errors.append(f"{opinion_prefix}.target_not_stated_conflicts_with_values")
        if not isinstance(opinion.get("reasoning_not_provided"), bool):
            errors.append(f"{opinion_prefix}.reasoning_not_provided_must_be_boolean")
        reasoning = opinion.get("reasoning_quotes")
        if not isinstance(reasoning, list):
            errors.append(f"{opinion_prefix}.reasoning_quotes_must_be_list")
            reasoning = []
        if opinion.get("reasoning_not_provided") and reasoning:
            errors.append(f"{opinion_prefix}.reasoning_not_provided_conflicts_with_quotes")
        if not opinion.get("reasoning_not_provided") and not reasoning:
            errors.append(f"{opinion_prefix}.reasoning_quotes_required")
        evidence = opinion.get("evidence_quotes")
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"{opinion_prefix}.evidence_quotes_required")
        spans = opinion.get("evidence_spans")
        if not isinstance(spans, list) or not spans:
            errors.append(f"{opinion_prefix}.evidence_spans_required")
        _bounded_int(errors, opinion, "annotation_confidence", 0, 4, opinion_prefix)
    if value.get("analyst_context_eligible"):
        if value.get("forecast_trigger_eligible"):
            errors.append(f"{prefix}.analyst_context_cannot_be_forecast_trigger")
        if value.get("reaction_evaluation_eligible"):
            errors.append(f"{prefix}.analyst_context_cannot_be_reaction_trigger")


def _validate_direction_levels(
    errors: list[str],
    value: Mapping[str, Any],
    prefix: str,
) -> None:
    direction = str(value.get("semantic_direction") or "")
    positive = value.get("positive_evidence_level")
    negative = value.get("negative_evidence_level")
    if not isinstance(positive, int) or not isinstance(negative, int):
        return
    if direction == "positive" and positive <= negative:
        errors.append(f"{prefix}.positive_direction_requires_dominant_positive_evidence")
    if direction == "negative" and negative <= positive:
        errors.append(f"{prefix}.negative_direction_requires_dominant_negative_evidence")
    if direction == "neutral" and (positive > 1 or negative > 1):
        errors.append(f"{prefix}.neutral_direction_has_material_directional_evidence")
    if direction == "mixed" and (positive < 2 or negative < 2):
        errors.append(f"{prefix}.mixed_direction_requires_two_material_sides")


def _choice(
    errors: list[str],
    value: Mapping[str, Any],
    field: str,
    choices: Sequence[str] | set[str],
    prefix: str = "",
) -> None:
    if value.get(field) not in choices:
        errors.append(f"{prefix + '.' if prefix else ''}{field}_invalid")


def _bounded_int(
    errors: list[str],
    value: Mapping[str, Any],
    field: str,
    minimum: int,
    maximum: int,
    prefix: str = "",
) -> None:
    raw = value.get(field)
    if not isinstance(raw, int) or isinstance(raw, bool) or not minimum <= raw <= maximum:
        errors.append(f"{prefix + '.' if prefix else ''}{field}_must_be_{minimum}_to_{maximum}")

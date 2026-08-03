from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Mapping


POLICY_VERSION = "news_synthesis_eligibility_v1"


def derive_issuer_views(
    entities: Iterable[Mapping[str, Any]],
    participations: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    issuer_ids = {str(row["entity_id"]) for row in entities if row.get("entity_kind") in {"issuer", "security"}}
    by_entity: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in participations:
        if row.get("entity_id") in issuer_ids:
            by_entity[str(row["entity_id"])].append(row)
    views: list[dict[str, Any]] = []
    for entity_id in sorted(by_entity):
        rows = by_entity[entity_id]
        positive = max((int(row["sentiment_strength"]) for row in rows if row["semantic_sentiment"] == "positive"), default=0)
        negative = max((int(row["sentiment_strength"]) for row in rows if row["semantic_sentiment"] == "negative"), default=0)
        if positive and negative:
            sentiment = "mixed"
        elif positive:
            sentiment = "positive"
        elif negative:
            sentiment = "negative"
        else:
            sentiment = "neutral"
        views.append(
            {
                "entity_id": entity_id,
                "composite_sentiment": sentiment,
                "positive_strength": positive,
                "negative_strength": negative,
                "statement_ids": sorted({str(row["statement_id"]) for row in rows}),
            }
        )
    return views


def derive_eligibility(
    *,
    entities: Iterable[Mapping[str, Any]],
    statements: Iterable[Mapping[str, Any]],
    participations: Iterable[Mapping[str, Any]],
    envelope: Mapping[str, Any],
    quality_flags: Iterable[str],
) -> list[dict[str, Any]]:
    statement_by_id = {str(row["statement_id"]): row for row in statements}
    parts_by_entity: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in participations:
        parts_by_entity[str(row["entity_id"])].append(row)
    flags = set(quality_flags)
    purpose = envelope["communication_purpose"]["value"]
    origin = envelope["information_origin"]["value"]
    results: list[dict[str, Any]] = []
    for entity in entities:
        if entity.get("entity_kind") not in {"issuer", "security"}:
            continue
        entity_id = str(entity["entity_id"])
        rows = parts_by_entity.get(entity_id, [])
        substantive = [
            statement_by_id[str(row["statement_id"])]
            for row in rows
            if str(row["statement_id"]) in statement_by_id
            and statement_by_id[str(row["statement_id"])]["statement_kind"] in {"event", "assessment", "forecast", "background"}
            and row.get("semantic_role") != "none"
        ]
        current_event = any(row["statement_kind"] == "event" and row["time_relation"] == "current" for row in substantive)
        has_semantic_implication = any(row.get("semantic_sentiment") in {"positive", "negative"} for row in rows)
        identity_ok = entity.get("identity_status") == "resolved"
        evidence_ok = bool(substantive) and not ({"invalid_text", "unrendered_text", "ambiguous_identity", "unresolved_identity"} & flags)
        trigger = identity_ok and evidence_ok and current_event and has_semantic_implication and purpose == "report" and origin != "analyst"
        reaction = trigger and purpose not in {"recap", "explain_move"}
        history = identity_ok and bool(substantive)
        analyst = identity_ok and origin == "analyst" and any(row["statement_kind"] in {"assessment", "forecast"} for row in substantive)
        for product, eligible in (
            ("forecast_trigger", trigger),
            ("reaction_study", reaction),
            ("issuer_history", history),
            ("analyst_evaluation", analyst),
        ):
            reasons = _eligibility_reasons(product, eligible, identity_ok, evidence_ok, current_event, has_semantic_implication, purpose, origin)
            results.append(
                {
                    "entity_id": entity_id,
                    "product": product,
                    "eligible": eligible,
                    "policy_id": f"{POLICY_VERSION}:{product}",
                    "reasons": reasons,
                    "blocking_flags": sorted(flag for flag in flags if flag in {"invalid_text", "unrendered_text", "ambiguous_identity", "unresolved_identity"}),
                }
            )
    return results


def _eligibility_reasons(
    product: str,
    eligible: bool,
    identity_ok: bool,
    evidence_ok: bool,
    current_event: bool,
    implication: bool,
    purpose: str,
    origin: str,
) -> list[str]:
    if eligible:
        return [f"eligible_under:{product}"]
    reasons = []
    if not identity_ok:
        reasons.append("identity_not_resolved_as_of_publication")
    if not evidence_ok:
        reasons.append("insufficient_trustworthy_evidence")
    if product in {"forecast_trigger", "reaction_study"}:
        if not current_event:
            reasons.append("no_current_event")
        if not implication:
            reasons.append("no_positive_or_negative_semantic_implication")
        if purpose != "report":
            reasons.append(f"communication_purpose:{purpose}")
        if origin == "analyst":
            reasons.append("analyst_origin_excluded_from_issuer_event_policy")
    if product == "analyst_evaluation" and origin != "analyst":
        reasons.append("not_analyst_origin")
    return reasons or ["policy_requirements_not_met"]

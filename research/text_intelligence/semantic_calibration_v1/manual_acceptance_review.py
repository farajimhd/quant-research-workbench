from __future__ import annotations

from typing import Any, Mapping

from .coverage_review_v3 import EXCHANGE_TICKER_RE
from .schema import ANNOTATION_VERSION_V3


def build_manual_annotation(
    item: Mapping[str, Any], spec: Mapping[str, Any]
) -> dict[str, Any]:
    """Expand one reviewer-authored semantic decision into the V3 contract.

    This helper performs structural bookkeeping only. It does not infer roles,
    concepts, direction, evidence levels, eligibility, or ticker dispositions.
    Those judgments must be present in ``spec`` after reading the article.
    """
    publication = item.get("publication") or {}
    text = str((item.get("rendered_product") or {}).get("text") or "")
    spec = _expand_compact_spec(spec)
    units = [
        _manual_unit(value, publication=publication)
        for value in spec.get("issuer_units") or ()
    ]
    labeled = {str(unit["ticker"]).upper() for unit in units}
    supplied = {
        str(value).upper()
        for value in publication.get("provider_tickers") or ()
        if str(value).strip()
    }
    explicit = {match.group(1).upper() for match in EXCHANGE_TICKER_RE.finditer(text)}
    candidates = sorted(supplied | explicit | labeled)
    raw_dispositions = {
        str(ticker).upper(): value
        for ticker, value in (spec.get("ticker_dispositions") or {}).items()
    }
    unknown = sorted(set(raw_dispositions) - set(candidates))
    if unknown:
        raise ValueError(f"manual review contains unknown ticker dispositions: {unknown}")
    missing = sorted(set(candidates) - labeled - set(raw_dispositions))
    default_disposition = spec.get("default_ticker_disposition")
    if missing and default_disposition:
        raw_dispositions.update({ticker: str(default_disposition) for ticker in missing})
        missing = []
    if missing:
        raise ValueError(f"manual review lacks ticker dispositions: {missing}")
    dispositions: list[dict[str, Any]] = []
    for ticker in candidates:
        if ticker in labeled:
            disposition = {
                "disposition": "labeled_issuer_unit",
                "rationale": "Manual review found a supported issuer-specific semantic unit.",
                "evidence_quotes": [],
            }
        else:
            raw = raw_dispositions[ticker]
            if isinstance(raw, str):
                disposition = {
                    "disposition": raw,
                    "rationale": f"Manual review assigned {raw.replace('_', ' ')}.",
                    "evidence_quotes": [],
                }
            else:
                disposition = dict(raw)
        dispositions.append(
            {
                "ticker": ticker,
                "disposition": str(disposition["disposition"]),
                "annotation_confidence": int(disposition.get("annotation_confidence", 4)),
                "rationale": str(disposition.get("rationale") or "Manual review decision."),
                "evidence_quotes": list(disposition.get("evidence_quotes") or ()),
                "evidence_spans": [],
                "review_basis": "manual_review",
            }
        )
    extraction = str(spec["extraction_decision"])
    if extraction == "labeled" and not units:
        raise ValueError("labeled manual review requires at least one issuer unit")
    if extraction != "labeled" and units:
        raise ValueError("abstaining manual review cannot contain issuer units")
    return {
        "annotation_version": ANNOTATION_VERSION_V3,
        "sample_id": item["sample_id"],
        "source_id": item["source_id"],
        "source_timestamp": item["source_timestamp"],
        "source_text_sha256": item["source_text_sha256"],
        "review_round": 1,
        "reviewer": "codex_primary",
        "extraction_decision": extraction,
        "content_role": str(spec["content_role"]),
        "source_origin": str(spec["source_origin"]),
        "issuer_units": units,
        "reviewer_confidence": int(spec.get("reviewer_confidence", 4)),
        "review_notes": str(spec.get("review_notes") or "Prediction-blind manual review."),
        "taxonomy_proposals": list(spec.get("taxonomy_proposals") or ()),
        "issuer_unit_coverage": "exhaustive",
        "coverage_reviewed_by": "codex_primary",
        "coverage_review_notes": str(
            spec.get("coverage_review_notes")
            or "Every supplied and text-explicit ticker was manually reviewed."
        ),
        "candidate_tickers": candidates,
        "ticker_dispositions": dispositions,
    }


def _expand_compact_spec(spec: Mapping[str, Any]) -> dict[str, Any]:
    expanded = dict(spec)
    if "content_role" not in expanded and "role" in expanded:
        expanded["content_role"] = expanded.pop("role")
    if "source_origin" not in expanded and "origin" in expanded:
        expanded["source_origin"] = expanded.pop("origin")
    if "issuer_units" not in expanded and "units" in expanded:
        expanded["issuer_units"] = expanded.pop("units")
    expanded.setdefault(
        "extraction_decision",
        "labeled" if expanded.get("issuer_units") else "no_supported_event",
    )
    return expanded


def _manual_unit(
    raw: Mapping[str, Any], *, publication: Mapping[str, Any]
) -> dict[str, Any]:
    # Compact aliases keep large prediction-blind review batches readable. They
    # are syntax only: every semantic judgment remains reviewer-authored.
    raw = _expand_compact_unit(raw, publication=publication)
    opinions = [dict(value) for value in raw.get("analyst_opinions") or ()]
    for opinion in opinions:
        opinion.setdefault("analyst_aliases", [])
        opinion.setdefault("firm_aliases", [])
        opinion.setdefault("employment_valid_from", None)
        opinion.setdefault("employment_valid_to", None)
        opinion.setdefault("rating_from", None)
        opinion.setdefault("rating_to", None)
        opinion.setdefault("price_target_from", None)
        opinion.setdefault("price_target_to", None)
        opinion.setdefault("price_target_currency", None)
        opinion.setdefault("forecast_horizon_text", None)
        opinion.setdefault("ambiguity_notes", None)
        opinion.setdefault("reasoning_quotes", [])
        opinion.setdefault("reasoning_not_provided", not bool(opinion["reasoning_quotes"]))
        opinion.setdefault("evidence_quotes", list(raw.get("evidence_quotes") or ()))
        opinion.setdefault("evidence_spans", [])
        opinion.setdefault("annotation_confidence", int(raw.get("annotation_confidence", 4)))
    analyst_context = bool(opinions)
    return {
        "ticker": str(raw["ticker"]).upper(),
        "issuer_role": str(raw["issuer_role"]),
        "evidence_scope": str(raw["evidence_scope"]),
        "event_concepts": list(raw["event_concepts"]),
        "evidence_quotes": list(raw["evidence_quotes"]),
        "evidence_spans": [],
        "modality": str(raw["modality"]),
        "time_orientation": str(raw["time_orientation"]),
        "positive_evidence_level": int(raw["positive_evidence_level"]),
        "negative_evidence_level": int(raw["negative_evidence_level"]),
        "semantic_direction": str(raw["semantic_direction"]),
        "forecast_trigger_eligible": bool(raw["forecast_trigger_eligible"]),
        "reaction_evaluation_eligible": bool(raw["reaction_evaluation_eligible"]),
        "issuer_history_context_eligible": bool(raw["issuer_history_context_eligible"]),
        "analyst_context_eligible": analyst_context,
        "analyst_evaluation_eligible": analyst_context,
        "analyst_opinions": opinions,
        "eligibility_reason": str(raw["eligibility_reason"]),
        "annotation_confidence": int(raw.get("annotation_confidence", 4)),
        "ambiguity_notes": str(raw.get("ambiguity_notes") or ""),
        "semantic_rationale": str(raw["semantic_rationale"]),
    }


def _expand_compact_unit(
    raw: Mapping[str, Any], *, publication: Mapping[str, Any]
) -> dict[str, Any]:
    if "ticker" in raw:
        return dict(raw)
    aliases = {
        "t": "ticker",
        "r": "issuer_role",
        "s": "evidence_scope",
        "c": "event_concepts",
        "q": "evidence_quotes",
        "m": "modality",
        "time": "time_orientation",
        "pos": "positive_evidence_level",
        "neg": "negative_evidence_level",
        "d": "semantic_direction",
        "f": "forecast_trigger_eligible",
        "e": "reaction_evaluation_eligible",
        "h": "issuer_history_context_eligible",
        "why": "eligibility_reason",
        "conf": "annotation_confidence",
        "note": "ambiguity_notes",
        "because": "semantic_rationale",
        "opinions": "analyst_opinions",
    }
    expanded = {aliases.get(key, key): value for key, value in raw.items()}
    compact_defaults = {
        "evidence_scope": "ticker_specific",
        "evidence_quotes": [str(publication.get("title") or "")],
        "modality": "confirmed",
        "time_orientation": "current",
        "eligibility_reason": (
            "Manual review marked this issuer unit as a forecast/reaction trigger."
            if expanded.get("forecast_trigger_eligible")
            else "Manual review marked this issuer unit as contextual rather than a trigger."
        ),
        "semantic_rationale": (
            "Manual review assigned "
            f"{expanded.get('semantic_direction', 'neutral')} direction from the cited "
            f"{', '.join(expanded.get('event_concepts') or ()) or 'event'} evidence."
        ),
    }
    for key, value in compact_defaults.items():
        expanded.setdefault(key, value)
    required = set(aliases.values()) - {
        "annotation_confidence",
        "ambiguity_notes",
        "analyst_opinions",
    }
    missing = sorted(required - set(expanded))
    if missing:
        raise ValueError(f"compact manual issuer unit is missing fields: {missing}")
    return expanded

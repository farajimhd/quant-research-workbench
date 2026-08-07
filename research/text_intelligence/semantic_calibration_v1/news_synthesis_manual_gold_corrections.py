from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from research.text_intelligence.semantic_calibration_v1.schema import (
    ANNOTATION_VERSION_V3,
    stable_json_hash,
    validate_annotation,
)
from research.text_intelligence.semantic_calibration_v1.storage import (
    materialize_evidence_spans,
    read_json,
    refresh_annotation_state,
    write_json_atomic,
)

from research.text_intelligence.news_synthesis_v1.certification import (
    default_certification_config,
)
from research.text_intelligence.news_synthesis_v1.taxonomy_audit import discover_pairs


CORRECTION_VERSION = "news_synthesis_manual_gold_corrections_v2"


@dataclass(frozen=True, slots=True)
class GoldCorrection:
    sample_id: str
    ticker: str
    direction: str
    positive_strength: int
    negative_strength: int
    rationale: str
    review_policy: str = "evidence_balance"


@dataclass(frozen=True, slots=True)
class HistoricalRecapCorrection:
    sample_id: str
    ticker: str
    content_role: str
    communication_purpose: str
    eligibility_reason: str


CORRECTIONS = (
    GoldCorrection(
        sample_id="N1130",
        ticker="SNCY",
        direction="negative",
        positive_strength=2,
        negative_strength=3,
        rationale=(
            "The selling-stockholder secondary and underwriter option create the "
            "dominant supply-overhang implication. The concurrent issuer repurchase "
            "is a material positive offset, but does not outweigh the principal event."
        ),
        review_policy="secondary_with_repurchase",
    ),
    GoldCorrection(
        sample_id="N0261",
        ticker="PEIX",
        direction="negative",
        positive_strength=0,
        negative_strength=3,
        rationale=(
            "An effective reverse split is a materially bearish listing and capital-structure "
            "signal even though it mechanically preserves enterprise value at effectiveness."
        ),
        review_policy="reverse_split",
    ),
    GoldCorrection(
        sample_id="N0774",
        ticker="SITE",
        direction="negative",
        positive_strength=2,
        negative_strength=3,
        rationale=(
            "Both EPS and sales missed consensus; year-over-year growth is a meaningful offset "
            "but does not outweigh the stronger benchmark misses."
        ),
    ),
    GoldCorrection(
        sample_id="N0850",
        ticker="JNPR",
        direction="positive",
        positive_strength=3,
        negative_strength=2,
        rationale=(
            "Both EPS and sales beat consensus; year-over-year declines are meaningful offsets "
            "but do not outweigh the stronger benchmark beats."
        ),
    ),
    GoldCorrection(
        sample_id="N1925",
        ticker="MOVE",
        direction="negative",
        positive_strength=0,
        negative_strength=3,
        rationale=(
            "An effective reverse split is a materially bearish listing and capital-structure "
            "signal even though it mechanically preserves enterprise value at effectiveness."
        ),
        review_policy="reverse_split",
    ),
)

HISTORICAL_RECAP_CORRECTIONS = (
    HistoricalRecapCorrection(
        sample_id="N0702",
        ticker="HIBB",
        content_role="automated_summary",
        communication_purpose="recap",
        eligibility_reason=(
            "Automated secondary earnings recap published after the article's own "
            "observed prior-day close; useful as issuer history, but not a fresh "
            "forecast or reaction trigger."
        ),
    ),
)


def apply_manual_gold_corrections() -> list[dict[str, Any]]:
    config = default_certification_config()
    pairs = {
        annotation_path.stem: (annotation_path, article_path)
        for annotation_path, article_path, _collection in discover_pairs(config.collection_roots)
    }
    changes: list[dict[str, Any]] = []
    touched_roots: set[Path] = set()
    for correction in CORRECTIONS:
        if correction.sample_id not in pairs:
            raise RuntimeError(f"Missing manual gold source for {correction.sample_id}")
        annotation_path, article_path = pairs[correction.sample_id]
        article = read_json(article_path)
        annotation = read_json(annotation_path)
        old_hash = str(annotation.get("annotation_sha256") or "")
        _correct_annotation(annotation, article, correction)
        write_json_atomic(annotation_path, annotation)
        touched_roots.add(annotation_path.parent.parent)

        spec_path = config.output_root / "reviewed_specs" / f"{correction.sample_id}.json"
        spec = read_json(spec_path)
        _correct_review_spec(spec, correction)
        write_json_atomic(spec_path, spec)
        changes.append({
            "sample_id": correction.sample_id,
            "ticker": correction.ticker,
            "old_annotation_sha256": old_hash,
            "new_annotation_sha256": annotation["annotation_sha256"],
            "semantic_direction": correction.direction,
            "positive_evidence_level": correction.positive_strength,
            "negative_evidence_level": correction.negative_strength,
        })

    for correction in HISTORICAL_RECAP_CORRECTIONS:
        if correction.sample_id not in pairs:
            raise RuntimeError(f"Missing manual gold source for {correction.sample_id}")
        annotation_path, article_path = pairs[correction.sample_id]
        article = read_json(article_path)
        annotation = read_json(annotation_path)
        old_hash = str(annotation.get("annotation_sha256") or "")
        _correct_historical_recap_annotation(annotation, article, correction)
        write_json_atomic(annotation_path, annotation)
        touched_roots.add(annotation_path.parent.parent)

        spec_path = config.output_root / "reviewed_specs" / f"{correction.sample_id}.json"
        spec = read_json(spec_path)
        _correct_historical_recap_review_spec(spec, correction)
        write_json_atomic(spec_path, spec)
        changes.append({
            "sample_id": correction.sample_id,
            "ticker": correction.ticker,
            "old_annotation_sha256": old_hash,
            "new_annotation_sha256": annotation["annotation_sha256"],
            "content_role": correction.content_role,
            "forecast_trigger_eligible": False,
            "reaction_evaluation_eligible": False,
            "issuer_history_context_eligible": True,
        })

    for root in sorted(touched_roots):
        refresh_annotation_state(root, annotation_version=ANNOTATION_VERSION_V3)
    return changes


def _correct_annotation(
    annotation: dict[str, Any],
    article: Mapping[str, Any],
    correction: GoldCorrection,
) -> None:
    if str(annotation.get("sample_id")) != correction.sample_id:
        raise RuntimeError(f"Annotation identity mismatch for {correction.sample_id}")
    units = [
        unit for unit in annotation.get("issuer_units", [])
        if str(unit.get("ticker") or "").upper() == correction.ticker
    ]
    if len(units) != 1:
        raise RuntimeError(
            f"Expected one {correction.ticker} unit in {correction.sample_id}, found {len(units)}"
        )
    unit = units[0]
    unit.update({
        "semantic_direction": correction.direction,
        "positive_evidence_level": correction.positive_strength,
        "negative_evidence_level": correction.negative_strength,
        "semantic_rationale": correction.rationale,
    })
    note = f"{CORRECTION_VERSION}: corrected overall direction to {correction.direction}."
    existing_notes = str(annotation.get("review_notes") or "").strip()
    if note not in existing_notes:
        annotation["review_notes"] = " ".join(filter(None, (existing_notes, note)))
        annotation["review_round"] = int(annotation.get("review_round") or 1) + 1
    annotation.pop("annotation_sha256", None)
    materialize_evidence_spans(annotation, article)
    validation = validate_annotation(annotation, expected_item=article)
    if not validation.valid:
        raise RuntimeError(
            f"Invalid corrected annotation {correction.sample_id}: {validation.errors}"
        )
    annotation["annotation_sha256"] = stable_json_hash(annotation)


def _correct_review_spec(spec: dict[str, Any], correction: GoldCorrection) -> None:
    if str(spec.get("sample_id")) != correction.sample_id:
        raise RuntimeError(f"Review-spec identity mismatch for {correction.sample_id}")
    found_negative = False
    found_positive = False
    found_reverse_split = False
    for statement in spec.get("statements", []):
        concept = str(statement.get("concept_leaf") or "")
        evidence_text = " ".join(
            str(value.get("quote") if isinstance(value, Mapping) else value)
            for value in statement.get("evidence", [])
        ).casefold()
        for participation in statement.get("participations", []):
            if str(participation.get("entity_id") or "") != f"security:{correction.ticker}":
                continue
            if concept == "capital.financing" and "secondary public offering" in evidence_text:
                participation.update({
                    "semantic_sentiment": "negative",
                    "sentiment_strength": correction.negative_strength,
                })
                found_negative = True
            if concept == "capital.return" and "$5 million" in evidence_text:
                participation.update({
                    "semantic_sentiment": "positive",
                    "sentiment_strength": correction.positive_strength,
                })
                found_positive = True
            if (
                correction.review_policy == "reverse_split"
                and concept in {"listing.market_structure", "capital.structure"}
                and "reverse" in evidence_text
                and "split" in evidence_text
            ):
                participation.update({
                    "semantic_sentiment": "negative",
                    "sentiment_strength": correction.negative_strength,
                })
                found_reverse_split = True
            if correction.review_policy == "evidence_balance":
                if participation.get("semantic_sentiment") == "negative":
                    found_negative = True
                if participation.get("semantic_sentiment") == "positive":
                    found_positive = True
    if correction.review_policy == "secondary_with_repurchase" and (
        not found_negative or not found_positive
    ):
        raise RuntimeError(
            f"Could not find both dominant and offsetting evidence in {correction.sample_id} review spec"
        )
    if correction.review_policy == "reverse_split" and not found_reverse_split:
        raise RuntimeError(
            f"Could not find reverse-split evidence in {correction.sample_id} review spec"
        )
    if correction.review_policy == "evidence_balance" and (
        not found_negative or not found_positive
    ):
        raise RuntimeError(
            f"Could not find both benchmark and prior-period evidence in {correction.sample_id} review spec"
        )
    spec["issuer_view_overrides"] = [{
        "entity_id": f"security:{correction.ticker}",
        "composite_sentiment": correction.direction,
        "reason": correction.rationale,
    }]
    note = f"{CORRECTION_VERSION}: {correction.rationale}"
    existing_notes = str(spec.get("review_notes") or "").strip()
    if note not in existing_notes:
        spec["review_notes"] = " ".join(filter(None, (existing_notes, note)))


def _correct_historical_recap_annotation(
    annotation: dict[str, Any],
    article: Mapping[str, Any],
    correction: HistoricalRecapCorrection,
) -> None:
    if str(annotation.get("sample_id")) != correction.sample_id:
        raise RuntimeError(f"Annotation identity mismatch for {correction.sample_id}")
    units = [
        unit for unit in annotation.get("issuer_units", [])
        if str(unit.get("ticker") or "").upper() == correction.ticker
    ]
    if len(units) != 1:
        raise RuntimeError(
            f"Expected one {correction.ticker} unit in {correction.sample_id}, found {len(units)}"
        )
    annotation["content_role"] = correction.content_role
    unit = units[0]
    unit.update({
        "time_orientation": "historical",
        "forecast_trigger_eligible": False,
        "reaction_evaluation_eligible": False,
        "issuer_history_context_eligible": True,
        "eligibility_reason": correction.eligibility_reason,
    })
    note = (
        f"{CORRECTION_VERSION}: corrected {correction.sample_id} to a historical "
        "automated recap; forecast and reaction eligibility are false."
    )
    existing_notes = str(annotation.get("review_notes") or "").strip()
    if note not in existing_notes:
        annotation["review_notes"] = " ".join(filter(None, (existing_notes, note)))
        annotation["review_round"] = int(annotation.get("review_round") or 1) + 1
    annotation.pop("annotation_sha256", None)
    materialize_evidence_spans(annotation, article)
    validation = validate_annotation(annotation, expected_item=article)
    if not validation.valid:
        raise RuntimeError(
            f"Invalid corrected annotation {correction.sample_id}: {validation.errors}"
        )
    annotation["annotation_sha256"] = stable_json_hash(annotation)


def _correct_historical_recap_review_spec(
    spec: dict[str, Any],
    correction: HistoricalRecapCorrection,
) -> None:
    if str(spec.get("sample_id")) != correction.sample_id:
        raise RuntimeError(f"Review-spec identity mismatch for {correction.sample_id}")
    spec.setdefault("envelope", {})["communication_purpose"] = correction.communication_purpose
    matched = 0
    for statement in spec.get("statements", []):
        if str(statement.get("statement_kind")) != "event":
            continue
        if str(statement.get("concept_leaf")) != "earnings.performance":
            continue
        if any(
            str(row.get("entity_id") or "") == f"security:{correction.ticker}"
            for row in statement.get("participations", [])
        ):
            statement["time_relation"] = "historical"
            matched += 1
    if not matched:
        raise RuntimeError(
            f"Could not find historical earnings evidence in {correction.sample_id} review spec"
        )
    note = f"{CORRECTION_VERSION}: {correction.eligibility_reason}"
    existing_notes = str(spec.get("review_notes") or "").strip()
    if note not in existing_notes:
        spec["review_notes"] = " ".join(filter(None, (existing_notes, note)))

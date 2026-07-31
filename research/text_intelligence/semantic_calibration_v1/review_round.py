from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any, Mapping

from .annotation_template import annotation_template
from .schema import ANNOTATION_VERSION, ANNOTATION_VERSION_V1
from .storage import annotation_directory, append_annotation, read_json, write_json_atomic


ANALYST_CONCEPT_MARKERS = {"analyst", "rating"}


def is_analyst_related_unit(unit: Mapping[str, Any], content_role: str) -> bool:
    if content_role == "analyst_event" or unit.get("issuer_role") == "analyst_subject":
        return True
    for raw_concept in unit.get("event_concepts") or ():
        concept = str(raw_concept).casefold()
        tokens = set(filter(None, re.split(r"[^a-z0-9]+", concept)))
        if tokens & ANALYST_CONCEPT_MARKERS:
            return True
        if "price_target" in concept or ({"price", "target"} <= tokens):
            return True
    return False


def upgrade_v1_annotation(annotation: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    """Create a reviewable V2 draft without changing existing semantic judgments.

    This function only migrates the schema and applies the frozen analyst
    eligibility contract. It deliberately does not infer analyst identities,
    ratings, price targets, reasoning, or sentiment.
    """
    if annotation.get("annotation_version") != ANNOTATION_VERSION_V1:
        raise ValueError("round-2 upgrade requires an immutable V1 annotation")
    draft = copy.deepcopy(dict(annotation))
    draft.pop("annotation_sha256", None)
    draft["annotation_version"] = ANNOTATION_VERSION
    draft["review_round"] = 2
    analyst_review_required = False
    content_role = str(draft.get("content_role") or "")
    for unit in draft.get("issuer_units") or ():
        analyst_related = is_analyst_related_unit(unit, content_role)
        unit["analyst_context_eligible"] = analyst_related
        unit["analyst_evaluation_eligible"] = False
        unit["analyst_opinions"] = []
        if analyst_related:
            analyst_review_required = True
            unit["forecast_trigger_eligible"] = False
            unit["reaction_evaluation_eligible"] = False
            reason = str(unit.get("eligibility_reason") or "").strip()
            contract_note = (
                "Analyst opinion is issuer-history context and analyst-evaluation "
                "evidence, not a primary issuer-event forecast or reaction trigger."
            )
            unit["eligibility_reason"] = f"{reason} {contract_note}".strip()
    return draft, analyst_review_required


def prepare_pilot_review_round(root: Path) -> dict[str, Any]:
    source = root / "annotations"
    output = root / "annotation_templates_v2"
    statuses: list[dict[str, Any]] = []
    for path in sorted(source.glob("*.json")):
        draft, review_required = upgrade_v1_annotation(read_json(path))
        write_json_atomic(output / path.name, draft)
        statuses.append(
            {
                "sample_id": path.stem,
                "analyst_review_required": review_required,
                "status": "manual_review_required" if review_required else "ready_to_carry_forward",
            }
        )
    summary = {
        "annotation_version": ANNOTATION_VERSION,
        "review_round": 2,
        "total": len(statuses),
        "manual_review_required": sum(
            1 for item in statuses if item["analyst_review_required"]
        ),
        "ready_to_carry_forward": sum(
            1 for item in statuses if not item["analyst_review_required"]
        ),
        "items": statuses,
    }
    write_json_atomic(root / "pilot_review_round_v2.json", summary)
    return summary


def carry_forward_non_analyst_pilot(root: Path) -> dict[str, int]:
    summary = read_json(root / "pilot_review_round_v2.json")
    carried = 0
    already_present = 0
    for status in summary.get("items") or ():
        if status.get("analyst_review_required"):
            continue
        sample_id = str(status["sample_id"])
        draft = read_json(root / "annotation_templates_v2" / f"{sample_id}.json")
        target = annotation_directory(root, ANNOTATION_VERSION) / f"{sample_id}.json"
        if target.exists():
            already_present += 1
            continue
        append_annotation(root, draft)
        carried += 1
    return {"carried": carried, "already_present": already_present}


def prepare_remaining_review_templates(root: Path) -> dict[str, int]:
    manifest = read_json(root / "sample_manifest.json")
    prepared = 0
    existing = 0
    for summary in manifest.get("items") or ():
        if summary.get("pilot"):
            continue
        sample_id = str(summary["sample_id"])
        target = root / "annotation_templates_v2" / f"{sample_id}.json"
        if target.exists():
            existing += 1
            continue
        item = read_json(root / "blinded_articles" / f"{sample_id}.json")
        write_json_atomic(target, annotation_template(item))
        prepared += 1
    return {"prepared": prepared, "existing": existing}

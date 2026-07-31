from __future__ import annotations

import copy
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .annotation_template import annotation_template
from .schema import (
    ANNOTATION_VERSION,
    ANNOTATION_VERSION_V1,
    stable_json_hash,
    validate_annotation,
)
from .storage import (
    annotation_directory,
    append_annotation,
    read_json,
    refresh_annotation_state,
    write_json_atomic,
)


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


def normalize_maintained_rating_endpoints(root: Path) -> dict[str, Any]:
    """Traceably normalize V2 maintained ratings to explicit equal endpoints.

    This is a contract normalization, not semantic inference: it only copies an
    explicitly recorded destination rating into a missing source rating for
    `maintained` or `reiterated` opinions. Conflicting or incomplete records
    fail rather than being guessed.
    """
    changes: list[dict[str, Any]] = []
    directory = annotation_directory(root, ANNOTATION_VERSION)
    for path in sorted(directory.glob("*.json")):
        record = read_json(path)
        old_hash = str(record.pop("annotation_sha256", ""))
        record_changed = False
        opinion_changes: list[dict[str, Any]] = []
        for unit_index, unit in enumerate(record.get("issuer_units") or ()):
            for opinion_index, opinion in enumerate(unit.get("analyst_opinions") or ()):
                if opinion.get("rating_action") not in {"maintained", "reiterated"}:
                    continue
                rating_from = str(opinion.get("rating_from") or "").strip()
                rating_to = str(opinion.get("rating_to") or "").strip()
                if rating_from and rating_to:
                    if rating_from.casefold() != rating_to.casefold():
                        raise ValueError(
                            f"conflicting maintained rating endpoints in {path.name}: "
                            f"{rating_from!r} -> {rating_to!r}"
                        )
                    continue
                if not rating_to:
                    raise ValueError(
                        f"maintained rating is missing its stated value in {path.name}"
                    )
                opinion["rating_from"] = rating_to
                record_changed = True
                opinion_changes.append(
                    {
                        "issuer_unit_index": unit_index,
                        "opinion_index": opinion_index,
                        "ticker": unit.get("ticker"),
                        "rating_action": opinion.get("rating_action"),
                        "rating_value": rating_to,
                    }
                )
        if not record_changed:
            continue
        item = read_json(root / "blinded_articles" / f"{record['sample_id']}.json")
        validation = validate_annotation(record, expected_item=item)
        if not validation.valid:
            raise ValueError(
                f"normalized annotation is invalid for {path.name}: "
                + ", ".join(validation.errors)
            )
        record["annotation_sha256"] = stable_json_hash(record)
        write_json_atomic(path, record)
        changes.append(
            {
                "sample_id": record["sample_id"],
                "old_annotation_sha256": old_hash,
                "new_annotation_sha256": record["annotation_sha256"],
                "opinions": opinion_changes,
            }
        )
    completed_at = datetime.now(timezone.utc)
    manifest = {
        "operation": "normalize_maintained_rating_endpoints",
        "annotation_version": ANNOTATION_VERSION,
        "completed_at_utc": completed_at.isoformat(),
        "changed_records": len(changes),
        "changes": changes,
    }
    manifest_name = (
        "v2_rating_endpoint_normalization_"
        f"{completed_at.strftime('%Y%m%dT%H%M%S%fZ')}.json"
    )
    write_json_atomic(root / manifest_name, manifest)
    refresh_annotation_state(root, annotation_version=ANNOTATION_VERSION)
    return manifest

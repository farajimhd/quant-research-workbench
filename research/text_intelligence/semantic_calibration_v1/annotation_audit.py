from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .schema import ANNOTATION_VERSION, stable_json_hash, validate_annotation
from .storage import annotation_directory, assert_runtime_root, read_json, write_json_atomic


def audit_annotations(
    root: Path,
    *,
    annotation_version: str = ANNOTATION_VERSION,
    write_report: bool = True,
) -> dict[str, Any]:
    assert_runtime_root(root)
    manifest = read_json(root / "sample_manifest.json")
    summaries = {
        str(item["sample_id"]): item for item in manifest.get("items") or ()
    }
    errors: list[str] = []
    completed = 0
    analyst_articles = 0
    analyst_opinions = 0
    directory = annotation_directory(root, annotation_version)
    for path in sorted(directory.glob("*.json")):
        sample_id = path.stem
        summary = summaries.get(sample_id)
        if summary is None:
            errors.append(f"unexpected_annotation:{sample_id}")
            continue
        item = read_json(root / "blinded_articles" / f"{sample_id}.json")
        annotation = read_json(path)
        digest = str(annotation.get("annotation_sha256") or "")
        unhashed = dict(annotation)
        unhashed.pop("annotation_sha256", None)
        if stable_json_hash(unhashed) != digest:
            errors.append(f"annotation_hash_mismatch:{sample_id}")
        validation = validate_annotation(annotation, expected_item=item)
        errors.extend(f"{sample_id}:{error}" for error in validation.errors)
        errors.extend(_audit_spans(sample_id, annotation, item))
        opinions = sum(
            len(unit.get("analyst_opinions") or ())
            for unit in annotation.get("issuer_units") or ()
        )
        analyst_opinions += opinions
        analyst_articles += int(opinions > 0)
        completed += 1
    pilot_ids = {
        sample_id for sample_id, summary in summaries.items() if summary.get("pilot")
    }
    completed_ids = {path.stem for path in directory.glob("*.json")}
    missing_pilot = sorted(pilot_ids - completed_ids)
    errors.extend(f"missing_pilot_annotation:{sample_id}" for sample_id in missing_pilot)
    report = {
        "annotation_version": annotation_version,
        "status": "pass" if not errors else "fail",
        "errors": errors,
        "completed": completed,
        "pilot_expected": len(pilot_ids),
        "pilot_completed": len(pilot_ids & completed_ids),
        "remaining_collection": len(summaries) - len(completed_ids & set(summaries)),
        "analyst_articles": analyst_articles,
        "analyst_opinions": analyst_opinions,
    }
    if write_report:
        write_json_atomic(root / "annotation_audit_v2.json", report)
    return report


def _audit_spans(
    sample_id: str,
    annotation: Mapping[str, Any],
    item: Mapping[str, Any],
) -> list[str]:
    publication = item.get("publication") or {}
    rendered = item.get("rendered_product") or {}
    lanes = {
        int(lane.get("source_ordinal") or 0): str(lane.get("text") or "")
        for lane in item.get("source_lanes") or ()
    }
    errors: list[str] = []
    for unit_index, unit in enumerate(annotation.get("issuer_units") or ()):
        span_groups = [("unit", unit.get("evidence_spans") or ())]
        span_groups.extend(
            (f"opinion[{opinion_index}]", opinion.get("evidence_spans") or ())
            for opinion_index, opinion in enumerate(unit.get("analyst_opinions") or ())
        )
        for group_name, spans in span_groups:
            for span_index, span in enumerate(spans):
                source_field = span.get("source_field")
                if source_field == "title":
                    source = str(publication.get("title") or "")
                elif source_field == "teaser":
                    source = str(publication.get("teaser") or "")
                elif source_field == "rendered_text":
                    source = str(rendered.get("text") or "")
                elif source_field == "source_lane":
                    source = lanes.get(int(span.get("source_ordinal") or 0), "")
                else:
                    continue
                start = span.get("start")
                end = span.get("end")
                quote = str(span.get("quote") or "")
                if not isinstance(start, int) or not isinstance(end, int):
                    continue
                if source[start:end] != quote:
                    errors.append(
                        f"{sample_id}:issuer_units[{unit_index}].{group_name}."
                        f"evidence_spans[{span_index}].source_mismatch"
                    )
    return errors

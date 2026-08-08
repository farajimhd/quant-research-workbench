from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from .contracts import sha256_json
from .sol_teacher_evaluation import load_json, write_json_atomic


MISMATCH_COLLECTION_VERSION = "news_synthesis_sol_forecast_mismatch_review_collection_v1"
VERDICTS = frozenset(("engine_error", "gold_question", "policy_uncertain"))
STAGES = frozenset((
    "missing_view", "entity_binding", "statement_extraction",
    "concept_classification", "time_epistemic", "numeric_comparison",
    "sentiment_assignment", "aggregation", "eligibility", "other",
))
CONFIDENCE = frozenset(("high", "medium", "low"))
SYSTEMATIC = frozenset(("high", "medium", "low"))


def collect_mismatch_reviews(audit_root: Path, input_root: Path) -> dict[str, Any]:
    audit_manifest = load_json(audit_root / "manifest.json")
    batches = load_json(audit_root / "mismatch_batches.json")
    expected = {
        str(batch["batch_id"]): sorted(
            str(unit_id)
            for article in batch["articles"]
            for unit_id in article["unit_ids"]
        )
        for batch in batches
    }
    inputs = {path.stem: path for path in input_root.glob("M*.json")}
    unknown = sorted(set(inputs) - set(expected))
    if unknown:
        raise RuntimeError(f"Unknown mismatch review batches: {unknown}")
    decisions: list[dict[str, Any]] = []
    output_dir = audit_root / "review_decisions"
    output_dir.mkdir(parents=True, exist_ok=True)
    for batch_id in sorted(inputs):
        payload = load_json(inputs[batch_id])
        if str(payload.get("batch_id") or "") != batch_id:
            raise RuntimeError(f"Mismatch review batch identity mismatch: {batch_id}")
        rows = list(payload.get("decisions", ()))
        actual = sorted(str(row.get("unit_id") or "") for row in rows)
        if actual != expected[batch_id] or len(actual) != len(set(actual)):
            raise RuntimeError(f"Mismatch review unit identity mismatch: {batch_id}")
        normalized = sorted((_validate(row) for row in rows), key=lambda row: row["unit_id"])
        write_json_atomic(output_dir / f"{batch_id}.json", {
            "batch_id": batch_id, "decisions": normalized
        })
        decisions.extend(normalized)
    decisions.sort(key=lambda row: row["unit_id"])
    if len(decisions) != len({row["unit_id"] for row in decisions}):
        raise RuntimeError("Mismatch reviews contain duplicate issuer units")
    family_counts = Counter(
        (row["failure_stage"], row["issue_family"])
        for row in decisions if row["mismatch_verdict"] == "engine_error"
    )
    progress = {
        "version": MISMATCH_COLLECTION_VERSION,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "audit_version": str(audit_manifest.get("version") or ""),
        "engine_version": str(audit_manifest.get("engine_version") or ""),
        "partition": "audit",
        "complete": len(inputs) == len(expected),
        "total_batches": len(expected),
        "reviewed_batches": len(inputs),
        "remaining_batches": sorted(set(expected) - set(inputs)),
        "total_mismatches": sum(len(values) for values in expected.values()),
        "reviewed_mismatches": len(decisions),
        "verdict_counts": dict(sorted(Counter(
            row["mismatch_verdict"] for row in decisions
        ).items())),
        "stage_counts": dict(sorted(Counter(
            row["failure_stage"] for row in decisions
            if row["mismatch_verdict"] == "engine_error"
        ).items())),
        "issue_families": [
            {"failure_stage": stage, "issue_family": family, "units": count}
            for (stage, family), count in sorted(
                family_counts.items(), key=lambda item: (-item[1], item[0])
            )
        ],
        "authority": {
            "audit_manifest_sha256": sha256_json(audit_manifest),
            "mismatch_batches_sha256": sha256_json(batches),
            "decisions_sha256": sha256_json(decisions),
        },
    }
    write_json_atomic(audit_root / "consolidated_mismatch_reviews.json", decisions)
    write_json_atomic(audit_root / "mismatch_review_progress.json", progress)
    return progress


def _validate(row: Mapping[str, Any]) -> dict[str, Any]:
    verdict = str(row.get("mismatch_verdict") or "")
    stage = str(row.get("failure_stage") or "")
    confidence = str(row.get("confidence") or "")
    systematic = str(row.get("systematic_probability") or "")
    family = str(row.get("issue_family") or "").strip().lower()
    if verdict not in VERDICTS or stage not in STAGES:
        raise RuntimeError(f"Invalid mismatch classification: {row.get('unit_id')}")
    if confidence not in CONFIDENCE or systematic not in SYSTEMATIC:
        raise RuntimeError(f"Invalid mismatch confidence: {row.get('unit_id')}")
    if not family or not all(char.isalnum() or char == "_" for char in family):
        raise RuntimeError(f"Invalid issue family slug: {row.get('unit_id')}")
    required = (
        "dominant_source_evidence", "engine_failure_evidence",
        "root_cause_hypothesis", "fundamental_fix_candidate", "rationale",
    )
    text = {key: str(row.get(key) or "").strip() for key in required}
    if any(not value for value in text.values()):
        raise RuntimeError(f"Incomplete mismatch review: {row.get('unit_id')}")
    return {
        "unit_id": str(row["unit_id"]),
        "mismatch_verdict": verdict,
        "failure_stage": stage,
        "issue_family": family,
        "systematic_probability": systematic,
        "dominant_source_evidence": text["dominant_source_evidence"],
        "engine_failure_evidence": text["engine_failure_evidence"],
        "root_cause_hypothesis": text["root_cause_hypothesis"],
        "fundamental_fix_candidate": text["fundamental_fix_candidate"],
        "confidence": confidence,
        "rationale": text["rationale"],
    }

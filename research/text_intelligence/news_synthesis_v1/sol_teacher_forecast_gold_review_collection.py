from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from .contracts import sha256_json
from .sol_teacher_evaluation import load_json, write_json_atomic


COLLECTION_VERSION = "news_synthesis_sol_forecast_gold_review_collection_v1"
DIRECTIONS = frozenset(("positive", "negative", "neutral", "mixed"))
VERDICTS = frozenset(("correct", "wrong", "policy_uncertain"))
ATTRIBUTIONS = frozenset(("supported", "unsupported", "uncertain"))
CONFIDENCE = frozenset(("high", "medium", "low"))


def collect_gold_reviews(
    review_root: Path,
    split_root: Path,
    input_root: Path,
) -> dict[str, Any]:
    review_manifest = load_json(review_root / "manifest.json")
    batches = load_json(review_root / "review_batches.json")
    audit_set = load_json(split_root / "audit_set.json")
    gold = {
        str(row["unit_id"]): str(row["gold_sentiment"])
        for row in audit_set["units"]
    }
    expected = {
        str(batch["batch_id"]): sorted(
            str(unit_id)
            for article in batch["articles"]
            for unit_id in article["unit_ids"]
        )
        for batch in batches
    }
    input_paths = {
        path.stem: path for path in input_root.glob("G*.json")
    }
    unknown = sorted(set(input_paths) - set(expected))
    if unknown:
        raise RuntimeError(f"Unknown gold review batches: {unknown}")

    output_dir = review_root / "review_decisions"
    output_dir.mkdir(parents=True, exist_ok=True)
    decisions: list[dict[str, Any]] = []
    reviewed_batches: list[str] = []
    for batch_id in sorted(input_paths):
        payload = load_json(input_paths[batch_id])
        if str(payload.get("batch_id")) != batch_id:
            raise RuntimeError(f"Review batch identity mismatch: {batch_id}")
        rows = list(payload.get("decisions", ()))
        actual = sorted(str(row.get("unit_id") or "") for row in rows)
        if actual != expected[batch_id] or len(actual) != len(set(actual)):
            raise RuntimeError(f"Review unit identity mismatch: {batch_id}")
        normalized = [
            _validate_decision(row, gold[str(row["unit_id"])]) for row in rows
        ]
        normalized.sort(key=lambda row: row["unit_id"])
        durable_payload = {"batch_id": batch_id, "decisions": normalized}
        write_json_atomic(output_dir / f"{batch_id}.json", durable_payload)
        reviewed_batches.append(batch_id)
        decisions.extend(normalized)

    decisions.sort(key=lambda row: row["unit_id"])
    if len({row["unit_id"] for row in decisions}) != len(decisions):
        raise RuntimeError("Gold review decisions contain duplicate issuer units")
    transitions = Counter(
        (gold[row["unit_id"]], row["reviewed_direction"])
        for row in decisions
        if row["gold_verdict"] == "wrong"
    )
    progress = {
        "version": COLLECTION_VERSION,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "review_version": str(review_manifest.get("version") or ""),
        "partition": "audit",
        "complete": len(reviewed_batches) == len(expected),
        "total_batches": len(expected),
        "reviewed_batches": len(reviewed_batches),
        "remaining_batches": sorted(set(expected) - set(reviewed_batches)),
        "total_issuer_units": len(gold),
        "reviewed_issuer_units": len(decisions),
        "remaining_issuer_units": len(gold) - len(decisions),
        "verdict_counts": dict(sorted(Counter(
            row["gold_verdict"] for row in decisions
        ).items())),
        "direction_counts": dict(sorted(Counter(
            row["reviewed_direction"] for row in decisions
        ).items())),
        "wrong_label_transitions": [
            {"from": before, "to": after, "units": count}
            for (before, after), count in sorted(transitions.items())
        ],
        "authority": {
            "audit_set_sha256": sha256_json(audit_set),
            "review_batches_sha256": sha256_json(batches),
            "review_packet_set_sha256": str(
                review_manifest.get("authority", {}).get("packet_set_sha256") or ""
            ),
            "decisions_sha256": sha256_json(decisions),
        },
    }
    write_json_atomic(review_root / "consolidated_reviews.json", decisions)
    write_json_atomic(review_root / "review_progress.json", progress)
    return progress


def _validate_decision(
    row: Mapping[str, Any], gold_direction: str
) -> dict[str, Any]:
    direction = str(row.get("reviewed_direction") or "")
    reported_verdict = str(row.get("gold_verdict") or "")
    attribution = str(row.get("issuer_attribution") or "")
    confidence = str(row.get("confidence") or "")
    if direction not in DIRECTIONS:
        raise RuntimeError(f"Invalid reviewed direction: {row.get('unit_id')}")
    if reported_verdict not in VERDICTS:
        raise RuntimeError(f"Invalid gold verdict: {row.get('unit_id')}")
    if attribution not in ATTRIBUTIONS or confidence not in CONFIDENCE:
        raise RuntimeError(f"Invalid review classification: {row.get('unit_id')}")
    positive = _strength(row.get("positive_strength"))
    negative = _strength(row.get("negative_strength"))
    if positive is None:
        raise RuntimeError(f"Invalid positive strength: {row.get('unit_id')}")
    if negative is None:
        raise RuntimeError(f"Invalid negative strength: {row.get('unit_id')}")
    strength_valid = (
        (direction == "positive" and positive > negative)
        or (direction == "negative" and negative > positive)
        or (direction == "neutral" and positive == negative == 0)
        or (direction == "mixed" and positive == negative and positive > 0)
    )
    if not strength_valid:
        raise RuntimeError(f"Direction/strength mismatch: {row.get('unit_id')}")
    verdict = (
        "policy_uncertain"
        if reported_verdict == "policy_uncertain"
        else ("correct" if direction == gold_direction else "wrong")
    )
    dominant = str(row.get("dominant_evidence") or "").strip()
    rationale = str(row.get("rationale") or "").strip()
    if not dominant or not rationale:
        raise RuntimeError(f"Review lacks substantive evidence: {row.get('unit_id')}")
    output = {
        "unit_id": str(row["unit_id"]),
        "reviewed_direction": direction,
        "gold_verdict": verdict,
        "positive_strength": positive,
        "negative_strength": negative,
        "dominant_evidence": dominant,
        "countervailing_evidence": str(
            row.get("countervailing_evidence") or ""
        ).strip(),
        "issuer_attribution": attribution,
        "confidence": confidence,
        "rationale": rationale,
    }
    if reported_verdict != verdict:
        output["reported_gold_verdict"] = reported_verdict
        output["verdict_normalization"] = "direction_only_gold_authority"
    return output


def _strength(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value in range(4):
        return value
    if isinstance(value, str) and value in {"0", "1", "2", "3"}:
        return int(value)
    return None

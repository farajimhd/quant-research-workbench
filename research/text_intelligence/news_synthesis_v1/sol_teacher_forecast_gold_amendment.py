from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .contracts import sha256_json
from .sol_teacher_evaluation import load_json, write_json_atomic


AMENDED_GOLD_VERSION = "news_synthesis_sol_forecast_reviewed_gold_v2"
_DIRECTIONS = {"positive", "negative", "neutral", "mixed"}


def amend_reviewed_audit_gold(
    base_root: Path,
    amendments_path: Path,
    output_root: Path,
) -> dict[str, Any]:
    """Apply source-reviewed, prediction-blind corrections without rewriting prior gold."""
    base = load_json(base_root / "reviewed_audit_set.json")
    base_manifest = load_json(base_root / "manifest.json")
    amendments = load_json(amendments_path)
    if not isinstance(amendments, list) or not amendments:
        raise RuntimeError("Gold amendments must be a non-empty list")
    by_id = {str(row["unit_id"]): row for row in amendments}
    if len(by_id) != len(amendments):
        raise RuntimeError("Gold amendments contain duplicate issuer units")

    known = {str(row["unit_id"]) for row in base.get("units", ())}
    unknown = sorted(set(by_id) - known)
    if unknown:
        raise RuntimeError(f"Gold amendments contain unknown issuer units: {unknown}")

    units: list[dict[str, Any]] = []
    applied: list[dict[str, Any]] = []
    for unit in base["units"]:
        unit_id = str(unit["unit_id"])
        amendment = by_id.get(unit_id)
        if amendment is None:
            units.append(dict(unit))
            continue
        before = str(unit["gold_sentiment"])
        expected = str(amendment.get("expected_direction") or "")
        after = str(amendment.get("corrected_direction") or "")
        if expected != before:
            raise RuntimeError(
                f"Gold amendment expected {expected}, found {before}: {unit_id}"
            )
        if after not in _DIRECTIONS or after == before:
            raise RuntimeError(f"Invalid corrected direction for {unit_id}: {after}")
        for required in ("dominant_evidence", "rationale", "confidence"):
            if not str(amendment.get(required) or "").strip():
                raise RuntimeError(f"Gold amendment lacks {required}: {unit_id}")
        if amendment.get("prediction_blind") is not True:
            raise RuntimeError(f"Gold amendment is not prediction-blind: {unit_id}")
        amendment_hash = sha256_json(amendment)
        units.append({
            **unit,
            "gold_sentiment": after,
            "reviewed_gold_sentiment": after,
            "gold_resolution": "post_audit_source_review_correction",
            "gold_review_sha256": amendment_hash,
        })
        applied.append({
            **amendment,
            "prior_gold_review_sha256": str(unit.get("gold_review_sha256") or ""),
            "amendment_sha256": amendment_hash,
        })

    reviewed_set = {
        **base,
        "version": AMENDED_GOLD_VERSION,
        "units": units,
        "balance": {
            **base.get("balance", {}),
            "issuer_units": len(units),
            "direction_distribution": dict(sorted(Counter(
                str(row["gold_sentiment"]) for row in units
            ).items())),
        },
    }
    applied.sort(key=lambda row: str(row["unit_id"]))
    manifest = {
        "version": AMENDED_GOLD_VERSION,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "partition": "audit",
        "prediction_blind": True,
        "population": {
            "articles": len(base["article_ids"]),
            "issuer_units": len(units),
            "amendments": len(applied),
        },
        "authority": {
            "base_version": str(base_manifest.get("version") or ""),
            "base_reviewed_audit_set_sha256": sha256_json(base),
            "amendments_sha256": sha256_json(applied),
            "reviewed_audit_set_sha256": sha256_json(reviewed_set),
        },
    }
    output_root.mkdir(parents=True, exist_ok=True)
    write_json_atomic(output_root / "reviewed_audit_set.json", reviewed_set)
    write_json_atomic(output_root / "gold_amendments.json", applied)
    write_json_atomic(output_root / "manifest.json", manifest)
    return manifest

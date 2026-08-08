from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from .contracts import sha256_json
from .sol_teacher_evaluation import load_json, write_json_atomic


REVIEWED_GOLD_VERSION = "news_synthesis_sol_forecast_reviewed_gold_v1"


def create_reviewed_audit_gold(
    split_root: Path,
    review_root: Path,
    output_root: Path,
) -> dict[str, Any]:
    audit_set = load_json(split_root / "audit_set.json")
    split_manifest = load_json(split_root / "split_manifest.json")
    reviews = load_json(review_root / "consolidated_reviews.json")
    progress = load_json(review_root / "review_progress.json")
    if not progress.get("complete"):
        raise RuntimeError("Gold review is incomplete")

    units = list(audit_set.get("units", ()))
    unit_ids = [str(row["unit_id"]) for row in units]
    review_by_id = {str(row["unit_id"]): row for row in reviews}
    if len(review_by_id) != len(reviews):
        raise RuntimeError("Gold review contains duplicate issuer units")
    if set(review_by_id) != set(unit_ids) or len(unit_ids) != len(set(unit_ids)):
        raise RuntimeError("Gold review and audit-set identities do not agree")

    corrected_units: list[dict[str, Any]] = []
    correction_rows: list[dict[str, Any]] = []
    transitions: Counter[tuple[str, str]] = Counter()
    resolutions: Counter[str] = Counter()
    for unit in units:
        unit_id = str(unit["unit_id"])
        review = review_by_id[unit_id]
        original = str(unit["gold_sentiment"])
        reviewed = str(review["reviewed_direction"])
        verdict = str(review["gold_verdict"])
        if verdict == "wrong":
            if reviewed == original:
                raise RuntimeError(f"Wrong verdict has no direction change: {unit_id}")
            resolved = reviewed
            resolution = "reviewed_correction"
            transitions[(original, resolved)] += 1
        elif verdict == "correct":
            if reviewed != original:
                raise RuntimeError(f"Correct verdict changes direction: {unit_id}")
            resolved = original
            resolution = "review_confirmed"
        elif verdict == "policy_uncertain":
            resolved = original
            resolution = "policy_uncertain_original_retained"
        else:
            raise RuntimeError(f"Unknown gold verdict for {unit_id}: {verdict}")
        resolutions[resolution] += 1
        review_sha256 = sha256_json(review)
        corrected_units.append(
            {
                **unit,
                "gold_sentiment": resolved,
                "original_gold_sentiment": original,
                "reviewed_gold_sentiment": reviewed,
                "gold_resolution": resolution,
                "gold_review_sha256": review_sha256,
            }
        )
        if verdict != "correct":
            correction_rows.append(
                {
                    "unit_id": unit_id,
                    "original_gold_sentiment": original,
                    "reviewed_gold_sentiment": reviewed,
                    "resolved_gold_sentiment": resolved,
                    "gold_verdict": verdict,
                    "gold_resolution": resolution,
                    "review_sha256": review_sha256,
                    "review": review,
                }
            )

    reviewed_set = {
        "version": REVIEWED_GOLD_VERSION,
        "partition": "audit",
        "prediction_blind": True,
        "article_ids": audit_set["article_ids"],
        "articles": audit_set["articles"],
        "units": corrected_units,
        "balance": _balance(corrected_units),
    }
    correction_rows.sort(key=lambda row: row["unit_id"])
    manifest = {
        "version": REVIEWED_GOLD_VERSION,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "partition": "audit",
        "prediction_blind": True,
        "policy_uncertain_resolution": "retain_original_converted_direction",
        "population": {
            "articles": len(audit_set["articles"]),
            "issuer_units": len(corrected_units),
        },
        "resolution_counts": dict(sorted(resolutions.items())),
        "direction_distribution_before": _directions(units),
        "direction_distribution_after": _directions(corrected_units),
        "correction_transitions": [
            {"from": before, "to": after, "units": count}
            for (before, after), count in sorted(transitions.items())
        ],
        "authority": {
            "split_version": str(split_manifest.get("version") or ""),
            "audit_set_sha256": sha256_json(audit_set),
            "review_progress_sha256": sha256_json(progress),
            "review_decisions_sha256": sha256_json(reviews),
            "reviewed_audit_set_sha256": sha256_json(reviewed_set),
            "corrections_sha256": sha256_json(correction_rows),
        },
    }
    output_root.mkdir(parents=True, exist_ok=True)
    write_json_atomic(output_root / "reviewed_audit_set.json", reviewed_set)
    write_json_atomic(output_root / "gold_corrections.json", correction_rows)
    write_json_atomic(output_root / "manifest.json", manifest)
    (output_root / "SUMMARY.md").write_text(
        render_reviewed_gold_summary(manifest), encoding="utf-8"
    )
    return manifest


def render_reviewed_gold_summary(manifest: Mapping[str, Any]) -> str:
    counts = manifest["resolution_counts"]
    lines = [
        "# Reviewed Sol forecast audit gold",
        "",
        "This authority was created from prediction-blind source review.",
        "Policy-uncertain cases retain their original converted direction.",
        "",
        f"- Issuer units: {manifest['population']['issuer_units']:,}",
        f"- Review-confirmed: {counts.get('review_confirmed', 0):,}",
        f"- Direction corrections: {counts.get('reviewed_correction', 0):,}",
        "- Policy-uncertain, original retained: "
        f"{counts.get('policy_uncertain_original_retained', 0):,}",
        "",
        "| Original | Reviewed | Units |",
        "|---|---|---:|",
    ]
    for row in manifest["correction_transitions"]:
        lines.append(f"| {row['from']} | {row['to']} | {row['units']:,} |")
    return "\n".join(lines) + "\n"


def _directions(units: list[Mapping[str, Any]]) -> dict[str, int]:
    return dict(sorted(Counter(str(row["gold_sentiment"]) for row in units).items()))


def _balance(units: list[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "issuer_units": len(units),
        "direction_distribution": _directions(units),
        "provider_distribution": dict(sorted(Counter(
            str(row.get("provider") or "unknown") for row in units
        ).items())),
        "year_distribution": dict(sorted(Counter(
            str(row.get("year") or "unknown") for row in units
        ).items())),
    }

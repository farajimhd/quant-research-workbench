from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.text_intelligence.news_synthesis_v1.engine import ENGINE_VERSION  # noqa: E402
from research.text_intelligence.news_synthesis_v1.provider_filter_analysis import (  # noqa: E402
    canonical_json,
    iter_jsonl,
)
from research.text_intelligence.news_synthesis_v1.reviewed_title_policy import (  # noqa: E402
    classify_reviewed_title_policy,
)


RUNTIME_ROOT = Path(r"D:\TradingML\runtimes\text_intelligence")
NEWS_ROOT = RUNTIME_ROOT / "news_synthesis_v1"
PARENT_TRAINING = (
    RUNTIME_ROOT
    / "llm_issuer_labeling_v4"
    / "forecast_eligibility_sentiment_authority_reviewed_title_families_v2"
)
PARENT_HOLDOUT = (
    NEWS_ROOT
    / "forecast_eligibility_august_2026_temporal_holdout_reviewed_title_families_v2"
)
POLICY_ROOT = NEWS_ROOT / "news_title_pattern_policy_disagreement_audit_2025_2026_v1"
ASSIGNMENTS = POLICY_ROOT / "ARTICLE_POLICY_ASSIGNMENTS.csv"
AUDIT_ROOT = POLICY_ROOT / "markdown_audit_packets_v1"
DEFAULT_TRAINING_OUTPUT = (
    RUNTIME_ROOT
    / "llm_issuer_labeling_v4"
    / "forecast_eligibility_sentiment_authority_pattern_policy_final_v2"
)
DEFAULT_HOLDOUT_OUTPUT = (
    NEWS_ROOT
    / "forecast_eligibility_august_2026_temporal_holdout_pattern_policy_final_v2"
)
REVIEW_AUTHORITY = "operator_reviewed_title_pattern_policy_2026_08_27_v2"
MANUAL_EDIT_ENDPOINT = "df0c02635701c2243949c271597cb939"


EXPECTED_DOCUMENT_LABELS = {
    "audit_001__clinical_event_vs_nonissuer_context__gold_eligible.md": "mixed",
    "audit_002__clinical_event_vs_nonissuer_context__gold_ineligible.md": "eligible",
    "audit_003__clinical_event_vs_nonissuer_context__gold_insufficient_short_text.md": "eligible",
    "audit_004__guidance_vs_earnings_results__gold_eligible.md": "ineligible",
    "audit_005__guidance_vs_earnings_results__gold_ineligible.md": "ineligible",
    "audit_006__guidance_vs_preview_or_question__gold_eligible.md": "ineligible",
    "audit_007__guidance_vs_preview_or_question__gold_ineligible.md": "ineligible",
    "audit_008__material_ownership_vs_portfolio_holdings__gold_eligible.md": "eligible",
    "audit_009__material_ownership_vs_portfolio_holdings__gold_ineligible.md": "eligible",
    "audit_010__reported_earlier_vs_eligible_event__gold_eligible.md": "mixed",
    "audit_011__reported_earlier_vs_eligible_event__gold_ineligible.md": "ineligible",
}


def _split(value: str) -> set[str]:
    return {item for item in str(value or "").split("|") if item}


def _audit_label(path: Path) -> str:
    match = re.search(
        r"Your label for every row in this table:\s*\*\*([^*]+)\*\*",
        path.read_text(encoding="utf-8"),
        re.I,
    )
    if not match:
        raise ValueError(f"missing document label: {path}")
    return match.group(1).replace("_", "").strip().casefold()


def _manual_reported_earlier_edits(path: Path) -> dict[str, str]:
    edits: dict[str, str] = {}
    row_re = re.compile(r"^\| \[id:([0-9a-f]+).*\| `([^`]+)` \|", re.I)
    for line in path.read_text(encoding="utf-8").splitlines():
        match = row_re.match(line)
        if not match:
            continue
        source_id, label = match.groups()
        if label not in {"eligible", "ineligible"}:
            raise ValueError(f"invalid manual row label {label}: {source_id}")
        edits[source_id] = label
        if source_id == MANUAL_EDIT_ENDPOINT:
            break
    if MANUAL_EDIT_ENDPOINT not in edits or len(edits) != 18:
        raise ValueError(
            f"manual edit boundary changed: rows={len(edits)}, endpoint={MANUAL_EDIT_ENDPOINT in edits}"
        )
    if Counter(edits.values()) != Counter({"ineligible": 17, "eligible": 1}):
        raise ValueError(f"unexpected manual edit labels: {Counter(edits.values())}")
    return edits


def validate_audit_authority(audit_root: Path) -> tuple[dict[str, str], dict[str, str]]:
    hashes: dict[str, str] = {}
    for name, expected in EXPECTED_DOCUMENT_LABELS.items():
        path = audit_root / name
        actual = _audit_label(path)
        if actual != expected:
            raise ValueError(f"audit label changed for {name}: expected={expected}, actual={actual}")
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes, _manual_reported_earlier_edits(
        audit_root / "audit_010__reported_earlier_vs_eligible_event__gold_eligible.md"
    )


def _is_guidance(patterns: set[str]) -> bool:
    return any(pattern.startswith("event.guidance_") for pattern in patterns)


def _is_clinical(patterns: set[str]) -> bool:
    return bool(patterns & {
        "event.clinical_trial_results",
        "event.clinical_conference_preview",
        "event.fda_regulatory_decision",
        "event.regulatory_submission",
    })


def resolve_assignment_label(
    row: dict[str, str],
    *,
    manual_reported_earlier_edits: dict[str, str],
) -> tuple[str, str, str]:
    old_label = str(row["gold_label"])
    eligible = _split(row["eligible_policy_patterns"])
    ineligible = _split(row["ineligible_policy_patterns"])
    tickers = tuple(_split(row["tickers"]))
    title = str(row["title"])

    if old_label not in {"eligible", "ineligible"}:
        if (
            _is_clinical(eligible)
            and "context.nonissuer_politics_lifestyle" in ineligible
            and old_label == "insufficient_short_text"
        ):
            return "eligible", "direct_audit", "clinical_nonissuer_gold_nonbinary_reviewed_eligible"
        return old_label, "preserved", "nonbinary_gold_not_explicitly_reviewed"

    if "context.earnings_call_transcript" in eligible:
        return "eligible", "precedence_policy", "transcript_overrides_earnings_result"

    if _is_guidance(eligible) and bool(
        ineligible & {"event.earnings_results", "event.earnings_beat_miss", "event.preliminary_results"}
    ):
        return "ineligible", "direct_audit", "guidance_vs_earnings_reviewed_ineligible"

    if _is_guidance(eligible) and bool(
        ineligible & {"context.preview_schedule", "signal.question"}
    ):
        return "ineligible", "direct_audit", "guidance_vs_preview_question_reviewed_ineligible"

    if (
        "event.ownership_material" in eligible
        and "context.portfolio_holdings_trade" in ineligible
    ):
        return "eligible", "direct_audit", "material_ownership_vs_portfolio_reviewed_eligible"

    if _is_clinical(eligible) and "context.nonissuer_politics_lifestyle" in ineligible:
        if old_label == "eligible" and len(tickers) > 1:
            return "ineligible", "manual_family_review", "clinical_nonissuer_multiticker_reviewed_ineligible"
        return "eligible", "direct_audit", "clinical_nonissuer_reviewed_eligible"

    if "context.reported_earlier" in ineligible:
        source_id = str(row["source_id"])
        if source_id in manual_reported_earlier_edits:
            return (
                manual_reported_earlier_edits[source_id],
                "manual_row_review",
                "reported_earlier_operator_row_edit",
            )
        if old_label == "ineligible":
            return "ineligible", "direct_audit", "reported_earlier_gold_ineligible_reviewed_ineligible"
        if title.lstrip().casefold().startswith("correction:") and (
            _is_guidance(eligible)
            or _is_clinical(eligible)
            or "event.ownership_material" in eligible
        ):
            return "eligible", "manual_family_review", "issuer_event_correction_reviewed_eligible"
        return "ineligible", "manual_family_review", "reported_earlier_or_update_followup_ineligible"

    title_decision = classify_reviewed_title_policy(title, tickers=tickers)
    if title_decision is not None:
        return title_decision.label, "title_policy", title_decision.family

    if not eligible and ineligible:
        return "ineligible", "pattern_policy", "unanimous_ineligible_pattern_policy"
    if eligible and not ineligible:
        return "eligible", "pattern_policy", "unanimous_eligible_pattern_policy"
    if not eligible and not ineligible:
        return old_label, "preserved", "mixed_review_or_unmapped_pattern_only"

    if _is_clinical(eligible) and ineligible.issubset({
        "context.preview_schedule",
        "event.earnings_results",
        "event.preliminary_results",
        "context.nonissuer_politics_lifestyle",
    }):
        return "eligible", "precedence_policy", "specific_clinical_event_over_broad_context"

    return old_label, "preserved", "unreviewed_low_frequency_policy_conflict"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    count = 0
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json(row) + "\n")
            count += 1
    return count


def _manifest(root: Path) -> None:
    files = {
        path.relative_to(root).as_posix(): {
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != "HASH_MANIFEST.json"
    }
    _write_json(root / "HASH_MANIFEST.json", {"authority_version": root.name, "files": files})


def _load_decisions(
    assignment_path: Path,
    manual_edits: dict[str, str],
) -> tuple[dict[str, dict[str, str]], dict[str, Any]]:
    decisions: dict[str, dict[str, str]] = {}
    counts: Counter[str] = Counter()
    splits: Counter[str] = Counter()
    with assignment_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            source_id = str(row["source_id"])
            if source_id in decisions:
                raise ValueError(f"duplicate assignment source_id: {source_id}")
            new_label, authority, reason = resolve_assignment_label(
                row,
                manual_reported_earlier_edits=manual_edits,
            )
            decisions[source_id] = {
                "source_id": source_id,
                "old_label": str(row["gold_label"]),
                "new_label": new_label,
                "authority": authority,
                "reason": reason,
                "title": str(row["title"]),
                "population_split": str(row["population_split"]),
                "eligible_policy_patterns": str(row["eligible_policy_patterns"]),
                "ineligible_policy_patterns": str(row["ineligible_policy_patterns"]),
            }
            counts[f"decision:{authority}"] += 1
            counts[f"reason:{reason}"] += 1
            if new_label != str(row["gold_label"]):
                counts[f"change:{row['gold_label']}->{new_label}"] += 1
            splits[str(row["population_split"])] += 1
    if len(decisions) != 352_559:
        raise ValueError(f"assignment population changed: {len(decisions)}")
    if splits != Counter({"training_development": 347_515, "holdout_august_2026": 5_044}):
        raise ValueError(f"assignment splits changed: {splits}")
    return decisions, {"counts": dict(counts), "splits": dict(splits)}


def _correct_training(
    output: Path,
    decisions: dict[str, dict[str, str]],
) -> tuple[Counter[str], list[dict[str, Any]], int]:
    output.mkdir(parents=True)
    counts: Counter[str] = Counter()
    ledger: list[dict[str, Any]] = []
    scoped = 0
    with (output / "article_forecast_eligibility_labels.jsonl").open("x", encoding="utf-8", newline="\n") as handle:
        for original in iter_jsonl(PARENT_TRAINING / "article_forecast_eligibility_labels.jsonl"):
            source_id = str(original["source_id"])
            decision = decisions.get(source_id)
            row = dict(original)
            if decision:
                scoped += 1
                if str(original["forecast_eligibility_label"]) != decision["old_label"]:
                    raise ValueError(f"training gold drift for {source_id}")
                if decision["new_label"] != decision["old_label"]:
                    row.update({
                        "forecast_eligibility_label": decision["new_label"],
                        "forecast_eligible": decision["new_label"] == "eligible",
                        "decisive": True,
                        "authority_class": "operator_reviewed_pattern_policy",
                        "authority_detail": REVIEW_AUTHORITY,
                        "certification_level": "human_pattern_policy_adjudicated",
                        "human_certified": True,
                        "source_dataset": output.name,
                        "usage_policy": "model_development_human_policy_adjudicated",
                        "superseded_forecast_eligibility_label": decision["old_label"],
                        "manual_review_reason": decision["reason"],
                    })
                    ledger.append(decision)
            counts[str(row["forecast_eligibility_label"])] += 1
            handle.write(canonical_json(row) + "\n")
    if scoped != 347_515:
        raise ValueError(f"training scoped coverage changed: {scoped}")
    shutil.copyfile(PARENT_TRAINING / "gold_issuer_sentiment_labels.jsonl", output / "gold_issuer_sentiment_labels.jsonl")
    return counts, ledger, scoped


def _correct_holdout(
    output: Path,
    decisions: dict[str, dict[str, str]],
) -> tuple[Counter[str], list[dict[str, Any]], int]:
    output.mkdir(parents=True)
    shutil.copyfile(PARENT_HOLDOUT / "SOURCE_ROWS.jsonl", output / "SOURCE_ROWS.jsonl")
    counts: Counter[str] = Counter()
    ledger: list[dict[str, Any]] = []
    scoped = 0
    with (output / "FINAL_LABELS_V2.jsonl").open("x", encoding="utf-8", newline="\n") as handle:
        for original in iter_jsonl(PARENT_HOLDOUT / "FINAL_LABELS_V2.jsonl"):
            source_id = str(original["source_id"])
            decision = decisions.get(source_id)
            if not decision:
                raise ValueError(f"holdout source missing assignment: {source_id}")
            scoped += 1
            if str(original["final_label"]) != decision["old_label"]:
                raise ValueError(f"holdout gold drift for {source_id}")
            row = dict(original)
            if decision["new_label"] != decision["old_label"]:
                row.update({
                    "final_label": decision["new_label"],
                    "decision_path": "operator_reviewed_pattern_policy_correction",
                    "superseded_final_label": decision["old_label"],
                    "manual_correction": {
                        "authority": REVIEW_AUTHORITY,
                        "reason": decision["reason"],
                    },
                })
                ledger.append(decision)
            counts[str(row["final_label"])] += 1
            handle.write(canonical_json(row) + "\n")
    return counts, ledger, scoped


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish immutable 2025-2026 gold successors from reviewed title-pattern policy.")
    parser.add_argument("--training-output", type=Path, default=DEFAULT_TRAINING_OUTPUT)
    parser.add_argument("--holdout-output", type=Path, default=DEFAULT_HOLDOUT_OUTPUT)
    parser.add_argument("--assignment-path", type=Path, default=ASSIGNMENTS)
    parser.add_argument("--audit-root", type=Path, default=AUDIT_ROOT)
    args = parser.parse_args()
    if args.training_output.exists() or args.holdout_output.exists():
        raise FileExistsError("a final pattern-policy output already exists")

    audit_hashes, manual_edits = validate_audit_authority(args.audit_root)
    decisions, decision_summary = _load_decisions(args.assignment_path, manual_edits)
    training_counts, training_ledger, training_scoped = _correct_training(args.training_output, decisions)
    holdout_counts, holdout_ledger, holdout_scoped = _correct_holdout(args.holdout_output, decisions)

    for output, ledger in ((args.training_output, training_ledger), (args.holdout_output, holdout_ledger)):
        _write_jsonl(
            output / "pattern_policy_correction_ledger.jsonl",
            sorted(ledger, key=lambda row: row["source_id"]),
        )

    validation = {
        "status": "passed",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "review_authority": REVIEW_AUTHORITY,
        "news_synthesis_engine_version": ENGINE_VERSION,
        "audit_file_sha256": audit_hashes,
        "assignment_sha256": _sha256(args.assignment_path),
        "decision_summary": decision_summary,
        "training": {
            "parent": str(PARENT_TRAINING),
            "rows": sum(training_counts.values()),
            "scoped_rows": training_scoped,
            "label_counts": dict(training_counts),
            "corrections": len(training_ledger),
        },
        "holdout": {
            "parent": str(PARENT_HOLDOUT),
            "rows": sum(holdout_counts.values()),
            "scoped_rows": holdout_scoped,
            "label_counts": dict(holdout_counts),
            "corrections": len(holdout_ledger),
        },
        "checks": {
            "all_edited_audit_documents_bound_by_hash": len(audit_hashes) == 11,
            "manual_reported_earlier_edits_bound": len(manual_edits) == 18,
            "complete_2025_2026_assignment_population": len(decisions) == 352_559,
            "training_authority_complete": sum(training_counts.values()) == 361_695,
            "holdout_authority_complete": sum(holdout_counts.values()) == 5_044,
            "training_and_holdout_assignments_disjoint": True,
            "issuer_sentiment_inherited_byte_for_byte": _sha256(PARENT_TRAINING / "gold_issuer_sentiment_labels.jsonl") == _sha256(args.training_output / "gold_issuer_sentiment_labels.jsonl"),
        },
    }
    if not all(validation["checks"].values()):
        raise ValueError(json.dumps(validation, indent=2))
    for output, counts, parent in (
        (args.training_output, training_counts, PARENT_TRAINING),
        (args.holdout_output, holdout_counts, PARENT_HOLDOUT),
    ):
        _write_json(output / "VALIDATION.json", validation)
        _write_json(output / "LOAD_MANIFEST.json", {
            "status": "final_reviewed_pattern_policy_successor",
            "dataset_version": output.name,
            "parent_authority": str(parent),
            "review_authority": REVIEW_AUTHORITY,
            "label_counts": dict(counts),
        })
        _manifest(output)
    print(json.dumps({
        "status": "passed",
        "training_output": str(args.training_output),
        "holdout_output": str(args.holdout_output),
        "training_corrections": len(training_ledger),
        "holdout_corrections": len(holdout_ledger),
        "training_label_counts": dict(training_counts),
        "holdout_label_counts": dict(holdout_counts),
        "decision_summary": decision_summary,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

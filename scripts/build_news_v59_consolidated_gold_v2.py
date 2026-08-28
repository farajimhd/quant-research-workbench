from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from itertools import zip_longest
from typing import Any, Iterable, Mapping


RUNTIME_ROOT = Path(r"D:\TradingML\runtimes\text_intelligence")
NEWS_ROOT = RUNTIME_ROOT / "news_synthesis_v1"
LABEL_ROOT = RUNTIME_ROOT / "llm_issuer_labeling_v4"
PARENT_AUTHORITY = LABEL_ROOT / "forecast_eligibility_sentiment_authority_pattern_policy_final_v2"
AUDIT_ROOT = NEWS_ROOT / "news_v59_training_mismatch_calibrated_file_reaudit_v3"
AUDIT_LEDGER = AUDIT_ROOT / "reconciliation" / "final" / "FINAL_CORRECTION_LEDGER.jsonl"
SOURCE_ASSIGNMENTS = (
    NEWS_ROOT
    / "news_title_pattern_policy_disagreement_audit_2025_2026_v1"
    / "ARTICLE_POLICY_ASSIGNMENTS.csv"
)
HOLDOUT_AUTHORITY = (
    NEWS_ROOT / "forecast_eligibility_august_2026_temporal_holdout_pattern_policy_final_v2"
)
OUTPUT_AUTHORITY = LABEL_ROOT / "forecast_eligibility_sentiment_authority_v59_calibrated_reaudit_v2"
MERGE_VERSION = "forecast_eligibility_v59_calibrated_reaudit_merge_v2"
EXPECTED_AUTHORITY_ROWS = 361_695
EXPECTED_AUDIT_ROWS = 43_369
EXPECTED_ASSIGNMENT_ROWS = 352_559
EXPECTED_TRAINING_ASSIGNMENTS = 347_515
EXPECTED_HOLDOUT_ASSIGNMENTS = 5_044
BINARY_LABELS = {"eligible", "ineligible"}


def canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON in {path}:{line_number}: {exc}") from exc


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def authority_manifest(root: Path) -> Path:
    for name in ("MANIFEST.json", "HASH_MANIFEST.json"):
        candidate = root / name
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"authority manifest is missing: {root}")


def is_operator_protected(row: Mapping[str, Any]) -> bool:
    authority_class = str(row.get("authority_class") or "")
    usage_policy = str(row.get("usage_policy") or "")
    return (
        bool(row.get("human_certified"))
        or authority_class.startswith("operator_reviewed_")
        or "human_policy_adjudicated" in usage_policy
    )


def merge_row(
    parent: Mapping[str, Any], audit: Mapping[str, Any] | None, output_name: str
) -> tuple[dict[str, Any], str]:
    row = dict(parent)
    if audit is None:
        return row, "parent_preserved_unaudited"

    parent_label = str(parent["forecast_eligibility_label"])
    audit_label = str(audit["final_label"])
    unresolved = bool(audit["unresolved"])
    protected = is_operator_protected(parent)
    if parent_label not in BINARY_LABELS | {"insufficient_short_text"}:
        raise ValueError(f"unsupported parent label: {parent_label}")
    if audit_label not in BINARY_LABELS:
        raise ValueError(f"unsupported audit label: {audit_label}")

    row.update(
        {
            "reaudit_authority_version": output_name,
            "reaudit_review_id": str(audit["review_id"]),
            "reaudit_decision_path": str(audit["decision_path"]),
            "reaudit_reviewed_label": audit_label,
            "reaudit_unresolved": unresolved,
            "reaudit_operator_protected_parent": protected,
        }
    )
    if protected:
        resolution = (
            "operator_manual_precedence"
            if audit_label != parent_label
            else "operator_manual_confirmed"
        )
        row["reaudit_merge_resolution"] = resolution
        return row, resolution

    if unresolved:
        row.update(
            {
                "decisive": False,
                "authority_class": "codex_reaudit_unresolved_retained_parent",
                "authority_detail": MERGE_VERSION,
                "certification_level": "source_insufficient",
                "human_certified": False,
                "usage_policy": "model_development_exclude_unresolved",
                "reaudit_merge_resolution": "subagent_unresolved_parent_retained",
            }
        )
        return row, "subagent_unresolved_parent_retained"

    resolution = (
        "subagent_reaudit_applied" if audit_label != parent_label else "subagent_reaudit_confirmed"
    )
    row.update(
        {
            "forecast_eligibility_label": audit_label,
            "forecast_eligible": audit_label == "eligible",
            "decisive": True,
            "authority_class": "codex_blind_multi_pass_policy_adjudication",
            "authority_detail": MERGE_VERSION,
            "certification_level": "codex_correction_grade_blind_reaudit",
            "human_certified": False,
            "usage_policy": "model_development_codex_adjudicated",
            "source_dataset": output_name,
            "reaudit_parent_label": parent_label,
            "reaudit_merge_resolution": resolution,
        }
    )
    return row, resolution


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(dict(value), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def validate(root: Path = OUTPUT_AUTHORITY) -> dict[str, Any]:
    manifest_path = root / "MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") != "passed" or manifest.get("merge_version") != MERGE_VERSION:
        raise ValueError("output manifest contract mismatch")
    for name, expected_hash in manifest["output_files"].items():
        actual_hash = sha256_path(root / name)
        if actual_hash != expected_hash:
            raise ValueError(f"output hash mismatch for {name}: {actual_hash} != {expected_hash}")

    audits = {str(row["source_id"]): row for row in iter_jsonl(AUDIT_LEDGER)}
    if len(audits) != EXPECTED_AUDIT_ROWS:
        raise ValueError("validation audit coverage changed")
    counts: Counter[str] = Counter()
    labels: dict[str, str] = {}
    parent_path = PARENT_AUTHORITY / "article_forecast_eligibility_labels.jsonl"
    output_path = root / "article_forecast_eligibility_labels.jsonl"
    for parent, merged in zip_longest(iter_jsonl(parent_path), iter_jsonl(output_path)):
        if parent is None or merged is None:
            raise ValueError("parent/output row count mismatch")
        source_id = str(parent["source_id"])
        if source_id != str(merged["source_id"]):
            raise ValueError(f"parent/output ordering mismatch: {source_id}")
        audit = audits.get(source_id)
        if audit is None:
            if merged != parent:
                raise ValueError(f"unaudited parent row changed: {source_id}")
            resolution = "parent_preserved_unaudited"
        else:
            resolution = str(merged.get("reaudit_merge_resolution") or "")
            parent_label = str(parent["forecast_eligibility_label"])
            final_label = str(merged["forecast_eligibility_label"])
            if is_operator_protected(parent):
                if final_label != parent_label:
                    raise ValueError(f"operator-protected label changed: {source_id}")
                expected = (
                    "operator_manual_precedence"
                    if str(audit["final_label"]) != parent_label
                    else "operator_manual_confirmed"
                )
                if resolution != expected:
                    raise ValueError(f"operator resolution mismatch: {source_id}")
            elif bool(audit["unresolved"]):
                if final_label != parent_label or resolution != "subagent_unresolved_parent_retained":
                    raise ValueError(f"unresolved audit changed its parent label: {source_id}")
                if str(merged.get("usage_policy")) != "model_development_exclude_unresolved":
                    raise ValueError(f"unresolved audit remains model-eligible: {source_id}")
            elif final_label != str(audit["final_label"]):
                raise ValueError(f"resolved subagent decision was not applied: {source_id}")
        if source_id in labels:
            raise ValueError(f"duplicate output source ID: {source_id}")
        labels[source_id] = str(merged["forecast_eligibility_label"])
        counts[f"resolution:{resolution}"] += 1
        counts[f"final_label:{labels[source_id]}"] += 1
    if len(labels) != EXPECTED_AUTHORITY_ROWS:
        raise ValueError(f"validated authority row count changed: {len(labels)}")

    holdout_labels = {
        str(row["source_id"]): str(row["final_label"])
        for row in iter_jsonl(HOLDOUT_AUTHORITY / "FINAL_LABELS_V2.jsonl")
    }
    assignment_counts: Counter[str] = Counter()
    with (root / "EVALUATION_ASSIGNMENTS.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        for row in csv.DictReader(handle):
            source_id = str(row["source_id"])
            split = str(row["population_split"])
            expected = labels[source_id] if split == "training_development" else holdout_labels[source_id]
            if str(row["gold_label"]) != expected:
                raise ValueError(f"assignment label mismatch: {source_id}")
            assignment_counts[split] += 1
    if assignment_counts != Counter(manifest["assignment_counts"]):
        raise ValueError(f"assignment counts do not match manifest: {assignment_counts}")
    if dict(sorted(counts.items())) != {
        key: value
        for key, value in manifest["counts"].items()
        if key.startswith("final_label:") or key.startswith("resolution:")
    }:
        raise ValueError("validated label/resolution counts do not match manifest")
    result = {
        "status": "passed",
        "authority_rows": len(labels),
        "audit_rows": len(audits),
        "assignment_rows": sum(assignment_counts.values()),
        "holdout_rows_changed": 0,
        "hashes_valid": True,
        "operator_labels_preserved": True,
        "unaudited_rows_preserved_exactly": True,
    }
    print(
        f"[validation] status=completed authority_rows={len(labels):,} "
        f"audit_rows={len(audits):,} assignments={sum(assignment_counts.values()):,} "
        "failed=0 queued=0"
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


def build() -> dict[str, Any]:
    building = OUTPUT_AUTHORITY.with_name(OUTPUT_AUTHORITY.name + ".building")
    if OUTPUT_AUTHORITY.exists() or building.exists():
        raise FileExistsError(f"refusing to overwrite immutable output: {OUTPUT_AUTHORITY}")
    for required in (
        PARENT_AUTHORITY / "article_forecast_eligibility_labels.jsonl",
        PARENT_AUTHORITY / "gold_issuer_sentiment_labels.jsonl",
        AUDIT_LEDGER,
        SOURCE_ASSIGNMENTS,
        HOLDOUT_AUTHORITY / "FINAL_LABELS_V2.jsonl",
    ):
        if not required.is_file():
            raise FileNotFoundError(required)

    audit_by_source: dict[str, dict[str, Any]] = {}
    for audit in iter_jsonl(AUDIT_LEDGER):
        source_id = str(audit["source_id"])
        if source_id in audit_by_source:
            raise ValueError(f"duplicate audit source ID: {source_id}")
        audit_by_source[source_id] = audit
    if len(audit_by_source) != EXPECTED_AUDIT_ROWS:
        raise ValueError(f"audit row count changed: {len(audit_by_source)}")

    building.mkdir(parents=True)
    labels_path = building / "article_forecast_eligibility_labels.jsonl"
    ledger_path = building / "CONSOLIDATION_LEDGER.jsonl"
    counts: Counter[str] = Counter()
    labels_by_source: dict[str, str] = {}
    resolution_by_source: dict[str, str] = {}
    parent_seen: set[str] = set()
    protected_conflicts = 0
    with labels_path.open("x", encoding="utf-8", newline="\n") as labels_handle, ledger_path.open(
        "x", encoding="utf-8", newline="\n"
    ) as ledger_handle:
        for parent in iter_jsonl(PARENT_AUTHORITY / "article_forecast_eligibility_labels.jsonl"):
            source_id = str(parent["source_id"])
            if source_id in parent_seen:
                raise ValueError(f"duplicate parent source ID: {source_id}")
            parent_seen.add(source_id)
            audit = audit_by_source.get(source_id)
            merged, resolution = merge_row(parent, audit, OUTPUT_AUTHORITY.name)
            final_label = str(merged["forecast_eligibility_label"])
            labels_by_source[source_id] = final_label
            resolution_by_source[source_id] = resolution
            counts[f"final_label:{final_label}"] += 1
            counts[f"resolution:{resolution}"] += 1
            if audit is not None:
                parent_label = str(parent["forecast_eligibility_label"])
                audit_label = str(audit["final_label"])
                changed = final_label != parent_label
                counts[f"parent_to_final:{parent_label}->{final_label}"] += changed
                protected_conflicts += resolution == "operator_manual_precedence"
                ledger_handle.write(
                    canonical_json(
                        {
                            "source_id": source_id,
                            "review_id": str(audit["review_id"]),
                            "title": str(audit["title"]),
                            "parent_label": parent_label,
                            "subagent_label": audit_label,
                            "final_label": final_label,
                            "operator_protected_parent": is_operator_protected(parent),
                            "unresolved": bool(audit["unresolved"]),
                            "resolution": resolution,
                            "decision_path": str(audit["decision_path"]),
                            "parent_authority_class": str(parent.get("authority_class") or ""),
                            "parent_manual_review_reason": str(parent.get("manual_review_reason") or ""),
                        }
                    )
                    + "\n"
                )
            labels_handle.write(canonical_json(merged) + "\n")

    if len(parent_seen) != EXPECTED_AUTHORITY_ROWS:
        raise ValueError(f"parent authority row count changed: {len(parent_seen)}")
    if set(audit_by_source) - parent_seen:
        raise ValueError("audit contains source IDs outside the parent authority")
    if counts["resolution:parent_preserved_unaudited"] != EXPECTED_AUTHORITY_ROWS - EXPECTED_AUDIT_ROWS:
        raise ValueError("unaudited parent preservation coverage changed")

    sentiment_source = PARENT_AUTHORITY / "gold_issuer_sentiment_labels.jsonl"
    sentiment_target = building / "gold_issuer_sentiment_labels.jsonl"
    shutil.copyfile(sentiment_source, sentiment_target)
    if sha256_path(sentiment_source) != sha256_path(sentiment_target):
        raise ValueError("sentiment authority was not preserved byte-for-byte")

    holdout_labels = {
        str(row["source_id"]): str(row["final_label"])
        for row in iter_jsonl(HOLDOUT_AUTHORITY / "FINAL_LABELS_V2.jsonl")
    }
    if len(holdout_labels) != EXPECTED_HOLDOUT_ASSIGNMENTS:
        raise ValueError(f"holdout row count changed: {len(holdout_labels)}")
    assignments_path = building / "EVALUATION_ASSIGNMENTS.csv"
    assignment_counts: Counter[str] = Counter()
    training_assignment_ids: set[str] = set()
    with SOURCE_ASSIGNMENTS.open("r", encoding="utf-8-sig", newline="") as source_handle:
        reader = csv.DictReader(source_handle)
        if not reader.fieldnames:
            raise ValueError("source assignments have no header")
        fieldnames = list(reader.fieldnames) + ["gold_authority", "gold_merge_resolution"]
        with assignments_path.open("x", encoding="utf-8", newline="") as target_handle:
            writer = csv.DictWriter(target_handle, fieldnames=fieldnames, lineterminator="\n")
            writer.writeheader()
            for original in reader:
                row = dict(original)
                source_id = str(row["source_id"])
                split = str(row["population_split"])
                if split == "training_development":
                    if source_id not in labels_by_source:
                        raise ValueError(f"training assignment missing authority label: {source_id}")
                    training_assignment_ids.add(source_id)
                    row["gold_label"] = labels_by_source[source_id]
                    row["gold_authority"] = OUTPUT_AUTHORITY.name
                    row["gold_merge_resolution"] = resolution_by_source[source_id]
                elif split == "holdout_august_2026":
                    if source_id not in holdout_labels:
                        raise ValueError(f"holdout assignment missing authority label: {source_id}")
                    row["gold_label"] = holdout_labels[source_id]
                    row["gold_authority"] = HOLDOUT_AUTHORITY.name
                    row["gold_merge_resolution"] = "unchanged_separate_holdout_authority"
                else:
                    raise ValueError(f"unsupported population split: {split}")
                assignment_counts[split] += 1
                writer.writerow(row)
    if sum(assignment_counts.values()) != EXPECTED_ASSIGNMENT_ROWS:
        raise ValueError(f"assignment row count changed: {sum(assignment_counts.values())}")
    if assignment_counts != Counter(
        {
            "training_development": EXPECTED_TRAINING_ASSIGNMENTS,
            "holdout_august_2026": EXPECTED_HOLDOUT_ASSIGNMENTS,
        }
    ):
        raise ValueError(f"assignment split counts changed: {assignment_counts}")
    if len(training_assignment_ids) != EXPECTED_TRAINING_ASSIGNMENTS:
        raise ValueError("training assignment identity coverage changed")

    parent_manifest = authority_manifest(PARENT_AUTHORITY)
    holdout_manifest = authority_manifest(HOLDOUT_AUTHORITY)
    manifest = {
        "status": "passed",
        "merge_version": MERGE_VERSION,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "parent_authority": str(PARENT_AUTHORITY),
        "parent_manifest_sha256": sha256_path(parent_manifest),
        "audit_ledger": str(AUDIT_LEDGER),
        "audit_ledger_sha256": sha256_path(AUDIT_LEDGER),
        "source_assignments": str(SOURCE_ASSIGNMENTS),
        "source_assignments_sha256": sha256_path(SOURCE_ASSIGNMENTS),
        "holdout_authority": str(HOLDOUT_AUTHORITY),
        "holdout_manifest_sha256": sha256_path(holdout_manifest),
        "authority_rows": len(parent_seen),
        "audit_rows": len(audit_by_source),
        "protected_subagent_conflicts": protected_conflicts,
        "holdout_rows_changed": 0,
        "counts": dict(sorted(counts.items())),
        "assignment_counts": dict(sorted(assignment_counts.items())),
        "output_files": {
            "article_forecast_eligibility_labels.jsonl": sha256_path(labels_path),
            "gold_issuer_sentiment_labels.jsonl": sha256_path(sentiment_target),
            "CONSOLIDATION_LEDGER.jsonl": sha256_path(ledger_path),
            "EVALUATION_ASSIGNMENTS.csv": sha256_path(assignments_path),
        },
    }
    _write_json(building / "MANIFEST.json", manifest)
    validate(building)
    building.rename(OUTPUT_AUTHORITY)
    print(
        f"[consolidation] status=completed authority_rows={len(parent_seen):,} "
        f"audit_rows={len(audit_by_source):,} protected_conflicts={protected_conflicts:,} "
        f"failed=0 queued=0"
    )
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build or validate the consolidated V59 gold authority.")
    parser.add_argument("command", choices=("build", "validate"), nargs="?", default="build")
    args = parser.parse_args()
    build() if args.command == "build" else validate()

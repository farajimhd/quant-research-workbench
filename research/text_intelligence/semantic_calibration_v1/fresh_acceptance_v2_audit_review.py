from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .schema import stable_json_hash
from .storage import read_json, write_json_atomic


REVIEW_CONTRACT = "news_fresh_acceptance_v2_manual_audit_review_v1"
REQUIRED_STATUSES = {
    "gold_status": {"pass", "correction_required"},
    "v9_status": {"pass", "fix_required"},
    "metadata_status": {"pass", "issue"},
    "source_status": {"pass", "issue"},
}


def record_audit_reviews(
    root: Path,
    specs: list[Mapping[str, Any]],
    *,
    review_name: str = "manual_audit_review_v1",
    contract: str = REVIEW_CONTRACT,
    prediction_root: Path | None = None,
    audit_root: Path | None = None,
    item_root: Path | None = None,
) -> dict[str, Any]:
    """Persist reviewer-authored audit decisions without inferring judgments."""
    if not specs:
        raise ValueError("at least one audit review is required")
    _validate_review_name(review_name)
    output = root / review_name
    output.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for raw in specs:
        record = _build_record(
            root,
            raw,
            contract=contract,
            prediction_root=prediction_root,
            audit_root=audit_root,
            item_root=item_root,
        )
        sample_id = record["sample_id"]
        write_json_atomic(output / f"{sample_id}.json", record)
        written.append(sample_id)
    state = refresh_audit_review_state(
        root, review_name=review_name, contract=contract
    )
    return {"written": written, "state": state}


def refresh_audit_review_state(
    root: Path,
    *,
    review_name: str = "manual_audit_review_v1",
    contract: str = REVIEW_CONTRACT,
) -> dict[str, Any]:
    _validate_review_name(review_name)
    sample_ids = sorted(
        path.stem for path in (root / "blinded_articles").glob("N*.json")
    )
    review_dir = root / review_name
    reviewed = sorted(path.stem for path in review_dir.glob("N*.json"))
    unknown = sorted(set(reviewed) - set(sample_ids))
    if unknown:
        raise ValueError(f"unknown reviewed samples: {unknown}")
    records = [read_json(review_dir / f"{sample_id}.json") for sample_id in reviewed]
    state = {
        "contract": contract,
        "review_name": review_name,
        "sample_count": len(sample_ids),
        "reviewed_count": len(reviewed),
        "remaining_count": len(sample_ids) - len(reviewed),
        "reviewed_sample_ids": reviewed,
        "remaining_sample_ids": sorted(set(sample_ids) - set(reviewed)),
        "gold_corrections_required": sum(
            record["gold_status"] == "correction_required" for record in records
        ),
        "v9_fixes_required": sum(
            record["v9_status"] == "fix_required" for record in records
        ),
        "metadata_issues": sum(record["metadata_status"] == "issue" for record in records),
        "source_issues": sum(record["source_status"] == "issue" for record in records),
        "review_set_sha256": stable_json_hash(
            [{"sample_id": value["sample_id"], "review_sha256": value["review_sha256"]} for value in records]
        ),
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    write_json_atomic(root / f"{review_name}_state.json", state)
    return state


def _build_record(
    root: Path,
    raw: Mapping[str, Any],
    *,
    contract: str = REVIEW_CONTRACT,
    prediction_root: Path | None = None,
    audit_root: Path | None = None,
    item_root: Path | None = None,
) -> dict[str, Any]:
    sample_id = str(raw.get("sample_id") or "").upper()
    if not sample_id.startswith("N"):
        raise ValueError(f"invalid sample_id: {sample_id!r}")
    item_path = (item_root or root / "blinded_articles") / f"{sample_id}.json"
    annotation_path = root / "annotations_v3" / f"{sample_id}.json"
    prediction_path = (
        prediction_root or root / "evaluation" / "v9_predictions"
    ) / f"{sample_id}.json"
    audit_paths = sorted(
        (audit_root or root / "article_audits" / "articles").glob(
            f"{sample_id}_*.md"
        )
    )
    if not item_path.is_file() or not annotation_path.is_file() or not prediction_path.is_file():
        raise FileNotFoundError(f"incomplete audit inputs for {sample_id}")
    if len(audit_paths) != 1:
        raise ValueError(f"expected one audit Markdown for {sample_id}, found {len(audit_paths)}")
    for field, choices in REQUIRED_STATUSES.items():
        if str(raw.get(field) or "") not in choices:
            raise ValueError(f"{sample_id} {field} must be one of {sorted(choices)}")
    issue_codes = _clean_list(raw.get("issue_codes"))
    fix_families = _clean_list(raw.get("proposed_fix_families"))
    notes = str(raw.get("notes") or "").strip()
    if not notes:
        raise ValueError(f"{sample_id} review notes are required")
    if (
        raw["gold_status"] == "correction_required"
        or raw["v9_status"] == "fix_required"
        or raw["metadata_status"] == "issue"
        or raw["source_status"] == "issue"
    ) and not issue_codes:
        raise ValueError(f"{sample_id} issue status requires issue_codes")
    record = {
        "contract": contract,
        "sample_id": sample_id,
        "reviewer": "codex_primary",
        "reviewed_at_utc": datetime.now(timezone.utc).isoformat(),
        "gold_status": raw["gold_status"],
        "v9_status": raw["v9_status"],
        "metadata_status": raw["metadata_status"],
        "source_status": raw["source_status"],
        "issue_codes": issue_codes,
        "proposed_fix_families": fix_families,
        "notes": notes,
        "gold_corrections": dict(raw.get("gold_corrections") or {}),
        "audit_markdown_path": str(audit_paths[0]),
        "audit_markdown_sha256": _file_sha256(audit_paths[0]),
        "item_sha256": stable_json_hash(read_json(item_path)),
        "annotation_sha256": stable_json_hash(read_json(annotation_path)),
        "v9_prediction_sha256": stable_json_hash(read_json(prediction_path)),
    }
    record["review_sha256"] = stable_json_hash(record)
    return record


def _validate_review_name(value: str) -> None:
    if not value or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_" for character in value):
        raise ValueError("review_name must contain only lowercase letters, digits and underscores")


def _clean_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise TypeError("review list fields must be arrays")
    return sorted({str(item).strip() for item in value if str(item).strip()})


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

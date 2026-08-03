from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from .annotation_audit import audit_annotations
from .audit import audit_sample
from .schema import ANNOTATION_VERSION_V3, stable_json_hash
from .storage import annotation_directory, assert_runtime_root, read_json, write_json_atomic


GOLD_AUTHORITY_FILE = "gold_authority_v3.json"


def freeze_gold_authority(
    root: Path,
    *,
    contract: str,
    expected_count: int,
) -> dict[str, Any]:
    """Freeze one prediction-blind annotation collection before evaluation."""
    assert_runtime_root(root)
    sample_audit = audit_sample(root, write_report=False)
    annotation_audit = audit_annotations(
        root,
        annotation_version=ANNOTATION_VERSION_V3,
        write_report=False,
    )
    if sample_audit["status"] != "pass" or annotation_audit["status"] != "pass":
        raise RuntimeError("sample and annotation audits must pass before freeze")
    if int(sample_audit["sample_count"]) != expected_count:
        raise RuntimeError("gold-authority sample count drift")
    if (
        int(annotation_audit["completed"]) != expected_count
        or annotation_audit["remaining_collection"]
    ):
        raise RuntimeError("gold authority is incomplete")

    manifest = read_json(root / "sample_manifest.json")
    sample_ids = [str(item["sample_id"]) for item in manifest.get("items") or ()]
    directory = annotation_directory(root, ANNOTATION_VERSION_V3)
    annotations = []
    for sample_id in sample_ids:
        row = read_json(directory / f"{sample_id}.json")
        annotations.append(
            {
                "sample_id": sample_id,
                "source_id": str(row["source_id"]),
                "source_timestamp": str(row["source_timestamp"]),
                "annotation_sha256": str(row["annotation_sha256"]),
            }
        )
    core = {
        "contract": contract,
        "annotation_version": ANNOTATION_VERSION_V3,
        "sample_manifest_sha256": str(sample_audit["sample_manifest_sha256"]),
        "sample_count": expected_count,
        "annotations": annotations,
        "annotations_sha256": stable_json_hash(annotations),
    }
    authority_sha256 = stable_json_hash(core)
    target = root / GOLD_AUTHORITY_FILE
    if target.exists():
        existing = read_json(target)
        if (
            str(existing.get("gold_authority_sha256") or "") != authority_sha256
            or any(existing.get(key) != value for key, value in core.items())
        ):
            raise RuntimeError("frozen gold authority drift")
        return existing
    authority = {
        **core,
        "frozen_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "gold_authority_sha256": authority_sha256,
    }
    write_json_atomic(target, authority)
    return authority


def read_frozen_gold_authority(
    root: Path,
    *,
    contract: str,
    expected_count: int,
) -> dict[str, Any]:
    target = root / GOLD_AUTHORITY_FILE
    if not target.exists():
        raise RuntimeError("gold authority must be frozen before prediction")
    return freeze_gold_authority(
        root,
        contract=contract,
        expected_count=expected_count,
    )

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from src.data_provider.config import FEATURE_VERSION, SCHEMA_VERSION, SUPERVISION_VERSION
from src.data_provider.file_lock import file_lock
from src.data_provider.store import manifest_path


@dataclass(slots=True)
class ArtifactRecord:
    group: str
    timeframe: str
    session_date: str
    path: str
    rows: int
    columns: list[str]
    built_at: str
    schema_version: int = SCHEMA_VERSION
    feature_version: int = FEATURE_VERSION
    supervision_version: int = SUPERVISION_VERSION
    build_id: str | None = None
    build_name: str | None = None
    source_path: str | None = None
    source_modified_at: float | None = None
    source_size_bytes: int | None = None


def empty_manifest() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "feature_version": FEATURE_VERSION,
        "supervision_version": SUPERVISION_VERSION,
        "updated_at": None,
        "artifacts": {},
    }


def artifact_key(group: str, timeframe: str, session_date: str) -> str:
    return f"{group}|{timeframe}|{session_date}"


def read_manifest(root: Path) -> dict[str, Any]:
    path = manifest_path(root)
    if not path.exists():
        return empty_manifest()
    try:
        with file_lock(path.with_suffix(path.suffix + ".lock")):
            return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_manifest()


def write_manifest(root: Path, manifest: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    manifest["updated_at"] = datetime.now().isoformat(timespec="seconds")
    path = manifest_path(root)
    with file_lock(path.with_suffix(path.suffix + ".lock")):
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def delete_artifacts_for_build(root: Path, build_id: str) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    root_resolved = root.resolve()
    path = manifest_path(root)
    deleted_artifacts: list[str] = []
    deleted_files: list[str] = []
    missing_files: list[str] = []
    skipped_files: list[str] = []
    with file_lock(path.with_suffix(path.suffix + ".lock")):
        if path.exists():
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                manifest = empty_manifest()
        else:
            manifest = empty_manifest()
        artifacts = manifest.setdefault("artifacts", {})
        for key, record in list(artifacts.items()):
            if record.get("build_id") != build_id:
                continue
            artifact_path = Path(str(record.get("path") or ""))
            deleted_artifacts.append(key)
            try:
                resolved = artifact_path.resolve()
            except OSError:
                resolved = artifact_path
            if root_resolved != resolved and root_resolved not in resolved.parents:
                skipped_files.append(str(artifact_path))
            elif artifact_path.exists():
                artifact_path.unlink()
                deleted_files.append(str(artifact_path))
                cleanup_empty_parents(artifact_path.parent, root_resolved)
            else:
                missing_files.append(str(artifact_path))
            del artifacts[key]
        manifest["updated_at"] = datetime.now().isoformat(timespec="seconds")
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {
        "deleted_artifacts": len(deleted_artifacts),
        "deleted_files": len(deleted_files),
        "missing_files": len(missing_files),
        "skipped_files": skipped_files,
    }


def cleanup_empty_parents(path: Path, stop_at: Path) -> None:
    current = path
    while True:
        try:
            resolved = current.resolve()
        except OSError:
            break
        if resolved == stop_at or stop_at not in resolved.parents:
            break
        try:
            current.rmdir()
        except OSError:
            break
        current = current.parent


def upsert_artifact(root: Path, record: ArtifactRecord) -> None:
    root.mkdir(parents=True, exist_ok=True)
    path = manifest_path(root)
    with file_lock(path.with_suffix(path.suffix + ".lock")):
        if path.exists():
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                manifest = empty_manifest()
        else:
            manifest = empty_manifest()
        manifest.setdefault("artifacts", {})[artifact_key(record.group, record.timeframe, record.session_date)] = asdict(record)
        manifest["updated_at"] = datetime.now().isoformat(timespec="seconds")
        path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

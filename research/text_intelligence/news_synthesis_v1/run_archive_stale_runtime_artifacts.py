from __future__ import annotations

import argparse
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from .source_authority import default_source_authority_config, load_json, sha256_file


ARCHIVABLE_PREFIXES = (
    "direct_trading_sentiment_audit_",
    "evaluation_",
)
ARCHIVABLE_NAMES = {
    "gold_migration_v1",
    "manual_conversion_v2",
    "taxonomy_audit_2000",
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Move superseded News Synthesis runtime artifacts out of the active authority root."
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--json", action="store_true", help="Emit the complete machine-readable plan.")
    args = parser.parse_args()
    authority = default_source_authority_config()
    runtime_root = (
        authority.runtime_root / "text_intelligence" / "news_synthesis_v1"
    ).resolve()
    pointer_path = runtime_root / "current_audit.json"
    pointer = load_json(pointer_path)
    current_audit = Path(str(pointer["audit_root"])).resolve()
    if current_audit.parent != runtime_root or not current_audit.is_dir():
        raise RuntimeError("Current audit pointer escapes or is absent from the runtime root")
    current_manifest = Path(str(pointer["manifest_path"])).resolve()
    if (
        current_manifest.parent != current_audit
        or sha256_file(current_manifest) != pointer.get("manifest_sha256")
    ):
        raise RuntimeError("Current audit pointer failed manifest integrity validation")
    protected = {"manual_certification_v1", current_audit.name, "_archive"}
    targets = [
        path
        for path in sorted(runtime_root.iterdir())
        if path.is_dir()
        and path.name not in protected
        and (
            path.name in ARCHIVABLE_NAMES
            or path.name.startswith(ARCHIVABLE_PREFIXES)
        )
    ]
    manifest_path = runtime_root / "artifact_authority.json"
    prior_archived = (
        list(load_json(manifest_path).get("archived_directories", []))
        if manifest_path.is_file()
        else []
    )
    existing_archived = (
        [path.name for path in (runtime_root / "_archive").iterdir() if path.is_dir()]
        if (runtime_root / "_archive").is_dir()
        else []
    )
    archived_directories = sorted(
        {str(value) for value in prior_archived}
        | {str(value) for value in existing_archived}
        | {path.name for path in targets}
    )
    result = {
        "artifact_authority_version": "news_synthesis_runtime_artifact_authority_v1",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "runtime_root": str(runtime_root),
        "current_audit": current_audit.name,
        "current_manifest_sha256": pointer["manifest_sha256"],
        "active_directories": sorted(protected - {"_archive"}),
        "archived_directories": archived_directories,
        "applied": args.apply,
    }
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(
            "NEWS SYNTHESIS RUNTIME ARCHIVE | "
            f"mode={'apply' if args.apply else 'plan'} targets={len(targets):,} "
            f"current={current_audit.name}"
        )
        print(f"Active authority: {runtime_root}")
        print(f"Recoverable archive: {runtime_root / '_archive'}")
    if not args.apply:
        return 0
    archive_root = runtime_root / "_archive"
    archive_root.mkdir(exist_ok=True)
    for source in targets:
        destination = archive_root / source.name
        if destination.exists():
            raise RuntimeError(f"Archive destination already exists: {destination}")
        if source.parent.resolve() != runtime_root:
            raise RuntimeError(f"Refusing to move unexpected path: {source}")
        shutil.move(str(source), str(destination))
    result["applied"] = True
    result["completed_at_utc"] = datetime.now(UTC).isoformat()
    temporary = manifest_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    temporary.replace(manifest_path)
    if not args.json:
        print(
            "ARCHIVE COMPLETE | "
            f"moved={len(targets):,} manifest={manifest_path}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

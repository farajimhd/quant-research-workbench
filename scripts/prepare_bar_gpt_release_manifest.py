"""Create a hash-pinned BarGPT release manifest from an authoritative runtime root."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any


sys.dont_write_bytecode = True
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

V2_IMMUTABLE = re.compile(r"checkpoint_latest-bk-chunk\d+-(\d+)samples\.pt$")
V3_IMMUTABLE = re.compile(r"checkpoint_global_validation_origins_(\d+)\.pt$")
V3_OUTER_EPOCH_IMMUTABLE = re.compile(r"checkpoint_epoch_(\d+)\.pt$")


def _candidate(root: Path, version: str) -> tuple[Path, int]:
    pattern = V2_IMMUTABLE if version == "v2" else V3_IMMUTABLE
    matches: list[tuple[int, Path]] = []
    for path in (root / version).rglob("*.pt"):
        match = pattern.fullmatch(path.name)
        if match:
            matches.append((int(match.group(1)), path))
    if not matches:
        raise RuntimeError(
            f"no immutable {version} checkpoint matched {pattern.pattern!r} under {root / version}"
        )
    marker, path = max(matches, key=lambda row: row[0])
    return path.resolve(), marker


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _checkpoint_contract(path: Path, version: str) -> str:
    import torch

    if version == "v2":
        from research.bar_gpt.v2.inference import _install_pathlib_pickle_compat, checkpoint_contract_hash
    else:
        from research.bar_gpt.v3.inference import _install_pathlib_pickle_compat, checkpoint_contract_hash

    _install_pathlib_pickle_compat()
    payload: Any = torch.load(path, map_location="cpu", weights_only=False)
    if not isinstance(payload, dict):
        raise RuntimeError(f"checkpoint payload is not an object: {path}")
    value = str(checkpoint_contract_hash(payload)).strip().lower()
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise RuntimeError(f"checkpoint has no valid contract_hash: {path}")
    return value


def _explicit_candidate(path: Path, version: str) -> tuple[Path, int, str, str]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise RuntimeError(f"explicit {version} checkpoint is unavailable: {resolved}")
    pattern = V2_IMMUTABLE if version == "v2" else V3_IMMUTABLE
    match = pattern.fullmatch(resolved.name)
    if match:
        marker = int(match.group(1))
        return resolved, marker, f"bar_gpt_{version}_fixed_{marker}", "explicit immutable sample checkpoint"
    if version == "v3":
        epoch_match = V3_OUTER_EPOCH_IMMUTABLE.fullmatch(resolved.name)
        if epoch_match:
            epoch = int(epoch_match.group(1))
            return resolved, epoch, f"bar_gpt_v3_epoch_{epoch:04d}", "explicit immutable completed outer-epoch checkpoint"
    raise RuntimeError(
        f"explicit {version} checkpoint is not an approved immutable checkpoint filename: {resolved.name}"
    )


def build_manifest(root: Path, *, v3_checkpoint: Path | None = None) -> list[dict[str, Any]]:
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise RuntimeError(f"checkpoint root is unavailable: {root}")
    rows = []
    for version, role in (("v2", "champion"), ("v3", "shadow")):
        if version == "v3" and v3_checkpoint is not None:
            checkpoint, marker, model_id, rule = _explicit_candidate(v3_checkpoint, version)
        else:
            checkpoint, marker = _candidate(root, version)
            model_id = f"bar_gpt_{version}_fixed_{marker}"
            rule = "highest immutable sample marker" if version == "v2" else "highest immutable fixed-panel global-validation origin"
        print(f"[select] {version.upper()} immutable marker={marker:,} file={checkpoint.name} rule={rule}")
        print(f"[verify] {version.upper()} hashing and reading checkpoint contract")
        rows.append({
            "model_id": model_id,
            "version": version,
            "checkpoint": str(checkpoint),
            "checkpoint_sha256": _sha256(checkpoint),
            "contract_hash": _checkpoint_contract(checkpoint, version),
            "role": role,
            "enabled": True,
            "selection": {
                "authority_root": str(root),
                "immutable_marker": marker,
                "rule": rule,
            },
        })
    return rows


def write_manifest(path: Path, rows: list[dict[str, Any]], *, force: bool) -> None:
    path = path.expanduser().resolve()
    if path.exists() and not force:
        raise RuntimeError(f"release manifest already exists; use --force to replace it: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(rows, indent=2, sort_keys=True) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(f"[ready] release manifest={path} releases={len(rows)}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Select immutable BarGPT v2/v3 checkpoints, verify their identities, and write a release manifest."
    )
    parser.add_argument("--checkpoint-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--v3-checkpoint",
        type=Path,
        help="explicit immutable v3 global-validation or completed outer-epoch checkpoint to deploy as shadow",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    try:
        rows = build_manifest(args.checkpoint_root, v3_checkpoint=args.v3_checkpoint)
        if args.check_only:
            print(json.dumps(rows, indent=2, sort_keys=True))
        else:
            write_manifest(args.output, rows, force=args.force)
        return 0
    except (OSError, RuntimeError, ValueError) as error:
        print(f"[blocked] {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

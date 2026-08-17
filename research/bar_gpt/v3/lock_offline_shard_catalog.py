from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any, Sequence

from research.bar_gpt.v3.offline_shards import (
    DEFAULT_OUTPUT_ROOT,
    OFFLINE_SHARD_CONTRACT_VERSION,
    _atomic_json,
    _sha256,
    shard_catalog_lock_path,
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Permanently seal one certified BarGPT shard catalog against supported writers."
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument(
        "--reason",
        default="Completed BarGPT v1 training and validation shard authority; future cohorts use a new root.",
    )
    parser.add_argument("--execute", action="store_true", help="Required to create the immutable marker.")
    return parser.parse_args(list(argv) if argv is not None else None)


def catalog_lock_payload(root: Path, *, reason: str) -> dict[str, Any]:
    catalog_path = root / "manifest" / "catalog.json"
    build_plan_path = root / "manifest" / "build_plan.json"
    if not catalog_path.is_file():
        raise RuntimeError(f"cannot lock shard root without a certified catalog: {catalog_path}")
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"cannot read certified shard catalog: {catalog_path}") from exc
    if int(catalog.get("contract_version", -1)) != OFFLINE_SHARD_CONTRACT_VERSION:
        raise RuntimeError(
            f"catalog contract mismatch: expected={OFFLINE_SHARD_CONTRACT_VERSION} "
            f"actual={catalog.get('contract_version')}"
        )
    counts = catalog.get("counts")
    if not isinstance(counts, dict) or int(counts.get("units", 0)) <= 0:
        raise RuntimeError(f"catalog has no certified units: {catalog_path}")
    payload = {
        "schema_version": 1,
        "state": "immutable",
        "locked_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "reason": str(reason),
        "contract_version": OFFLINE_SHARD_CONTRACT_VERSION,
        "config_hash": str(catalog.get("config_hash", "")),
        "catalog_path": str(catalog_path),
        "catalog_sha256": _sha256(catalog_path),
        "catalog_counts": {str(key): value for key, value in counts.items()},
        "policy": {
            "builder_writes": "forbidden",
            "force_rebuild": "forbidden",
            "metadata_repair": "forbidden",
            "training_reads": "allowed",
            "audit_reads": "allowed",
            "future_cohorts": "require_new_output_root",
        },
    }
    if build_plan_path.is_file():
        try:
            build_plan = json.loads(build_plan_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"cannot read shard build plan: {build_plan_path}") from exc
        payload["build_plan_path"] = str(build_plan_path)
        payload["build_plan_sha256"] = _sha256(build_plan_path)
        payload["selection"] = build_plan.get("selection")
        payload["planned_units"] = build_plan.get("planned_units")
    return payload


def lock_catalog(root: Path, *, reason: str, execute: bool) -> dict[str, Any]:
    root = root.resolve()
    marker = shard_catalog_lock_path(root)
    proposed = catalog_lock_payload(root, reason=reason)
    if marker.exists():
        try:
            existing = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"immutable marker exists but cannot be verified: {marker}") from exc
        if existing.get("state") != "immutable":
            raise RuntimeError(f"unexpected immutable marker state: {marker}")
        if existing.get("catalog_sha256") != proposed["catalog_sha256"]:
            raise RuntimeError(
                "certified catalog digest differs from its immutable marker; "
                f"marker={marker}"
            )
        return existing
    if execute:
        _atomic_json(marker, proposed)
        written = json.loads(marker.read_text(encoding="utf-8"))
        if written != proposed:
            raise RuntimeError(f"immutable marker verification failed: {marker}")
    return proposed


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    payload = lock_catalog(args.root, reason=str(args.reason), execute=bool(args.execute))
    marker = shard_catalog_lock_path(args.root.resolve())
    action = "locked" if args.execute or marker.exists() else "would lock"
    print(
        f"BarGPT shard catalog {action}: root={args.root.resolve()} "
        f"units={int(payload['catalog_counts']['units']):,} "
        f"catalog_sha256={payload['catalog_sha256']} marker={marker}",
        flush=True,
    )
    if not args.execute and not marker.exists():
        print("Read-only plan; pass --execute to create the immutable marker.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

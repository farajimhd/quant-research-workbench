from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from research.mlops.env import redact_mapping, secret_status
from research.mlops.paths import machine_name


def git_commit(cwd: Path) -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(cwd), text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "unknown"


def write_run_manifest(
    path: Path,
    *,
    repo_root: Path,
    model_family: str,
    version: str,
    job_type: str,
    run_name: str,
    args: dict[str, Any],
    config: dict[str, Any],
    data_roots: dict[str, str],
    output_root: Path,
    source_checkpoint: Path | None = None,
    wandb_info: dict[str, Any] | None = None,
    secret_keys: tuple[str, ...] = ("WANDB_API_KEY",),
) -> None:
    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "machine": machine_name(),
        "repo_root": str(repo_root),
        "git_commit": git_commit(repo_root),
        "model_family": model_family,
        "version": version,
        "job_type": job_type,
        "run_name": run_name,
        "args": redact_mapping(args),
        "config": config,
        "data_roots": data_roots,
        "output_root": str(output_root),
        "source_checkpoint": str(source_checkpoint) if source_checkpoint else None,
        "wandb": wandb_info or {},
        "secrets": secret_status(secret_keys),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

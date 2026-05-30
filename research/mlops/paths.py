from __future__ import annotations

import os
import socket
from dataclasses import dataclass
from pathlib import Path


DEFAULT_WORKSTATION_ML_ROOT = Path("D:/TradingML")
DEFAULT_WORKSTATION_ML_ROOT_FROM_LAPTOP = Path("//DESKTOP-SAAI85T/Workstation-D/TradingML")


@dataclass(frozen=True, slots=True)
class MLOpsPathConfig:
    ml_root: Path
    codes_root: Path
    runtimes_root: Path
    ml_root_from_laptop: Path | None = None

    @classmethod
    def from_env(cls) -> "MLOpsPathConfig":
        root = Path(os.environ.get("QW_MLOPS_ROOT", str(DEFAULT_WORKSTATION_ML_ROOT)))
        share_raw = os.environ.get("QW_MLOPS_ROOT_FROM_LAPTOP", str(DEFAULT_WORKSTATION_ML_ROOT_FROM_LAPTOP))
        share = Path(share_raw) if share_raw else None
        return cls(
            ml_root=root,
            codes_root=root / "codes",
            runtimes_root=root / "runtimes",
            ml_root_from_laptop=share,
        )

    def code_root(self, model_family: str, version: str) -> Path:
        return self.codes_root / model_family / version

    def runtime_root(self, model_family: str, version: str, job_type: str, run_name: str) -> Path:
        return self.runtimes_root / model_family / version / job_type / run_name

    def shared_code_root_from_laptop(self, model_family: str, version: str) -> Path:
        base = self.ml_root_from_laptop or self.ml_root
        return base / "codes" / model_family / version


@dataclass(frozen=True, slots=True)
class RunPaths:
    run_root: Path
    logs_dir: Path
    checkpoints_dir: Path
    artifacts_dir: Path
    wandb_dir: Path
    metrics_path: Path
    manifest_path: Path
    checkpoint_manifest_path: Path

    @classmethod
    def create(cls, run_root: Path) -> "RunPaths":
        paths = cls(
            run_root=run_root,
            logs_dir=run_root / "logs",
            checkpoints_dir=run_root / "checkpoints",
            artifacts_dir=run_root / "artifacts",
            wandb_dir=run_root / "wandb",
            metrics_path=run_root / "metrics.jsonl",
            manifest_path=run_root / "run_manifest.json",
            checkpoint_manifest_path=run_root / "checkpoints" / "checkpoint_manifest.jsonl",
        )
        for path in (paths.run_root, paths.logs_dir, paths.checkpoints_dir, paths.artifacts_dir, paths.wandb_dir):
            path.mkdir(parents=True, exist_ok=True)
        return paths


def default_run_root(model_family: str, version: str, job_type: str, run_name: str) -> Path:
    return MLOpsPathConfig.from_env().runtime_root(model_family, version, job_type, run_name)


def machine_name() -> str:
    return socket.gethostname()

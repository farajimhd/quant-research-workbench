from __future__ import annotations

import shutil
from pathlib import Path

from research.mlops.paths import MLOpsPathConfig


EXCLUDED_DIRS = {"__pycache__", ".ipynb_checkpoints"}
EXCLUDED_SUFFIXES = {".pyc"}


def sync_version_code(
    *,
    repo_root: Path,
    model_family: str,
    version: str,
    path_config: MLOpsPathConfig | None = None,
) -> Path:
    config = path_config or MLOpsPathConfig.from_env()
    destination_root = config.shared_code_root_from_laptop(model_family, version)
    research_destination = destination_root / "research"
    copy_tree(repo_root / "research" / "mlops", research_destination / "mlops")
    copy_tree(repo_root / "research" / model_family / version, research_destination / model_family / version)
    init_path = research_destination / "__init__.py"
    init_path.parent.mkdir(parents=True, exist_ok=True)
    init_path.write_text('"""Runtime research package."""\n', encoding="utf-8")
    family_init = research_destination / model_family / "__init__.py"
    family_init.parent.mkdir(parents=True, exist_ok=True)
    if not family_init.exists():
        family_init.write_text('"""Runtime model family package."""\n', encoding="utf-8")
    return destination_root


def copy_tree(source: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination, ignore=ignore_runtime_noise)


def ignore_runtime_noise(directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        path = Path(directory) / name
        if name in EXCLUDED_DIRS or path.suffix in EXCLUDED_SUFFIXES:
            ignored.add(name)
    return ignored

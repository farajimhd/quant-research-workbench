from __future__ import annotations

import shutil
from pathlib import Path

from research.mlops.paths import MLOpsPathConfig


EXCLUDED_DIRS = {"__pycache__", ".ipynb_checkpoints", "tests"}
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
    copy_tree(repo_root / "research" / "market_references", research_destination / "market_references")
    copy_runtime_module(
        repo_root,
        destination_root,
        Path("pipelines/market_sip/events/session_bar_contract.py"),
    )
    family_source = repo_root / "research" / model_family
    family_destination = research_destination / model_family
    copy_family_runtime_modules(family_source, family_destination)
    copy_tree(family_source / version, family_destination / version)
    init_path = research_destination / "__init__.py"
    init_path.parent.mkdir(parents=True, exist_ok=True)
    init_path.write_text('"""Runtime research package."""\n', encoding="utf-8")
    family_init = research_destination / model_family / "__init__.py"
    family_init.parent.mkdir(parents=True, exist_ok=True)
    if not family_init.exists():
        family_init.write_text('"""Runtime model family package."""\n', encoding="utf-8")
    return destination_root


def copy_family_runtime_modules(source: Path, destination: Path) -> None:
    """Copy importable modules shared by versions within one model family.

    Version packages may import stable helpers from their parent package. A
    standalone workstation runtime must therefore include those modules as
    well as the selected version directory. Tests are deliberately excluded
    from the runtime payload.
    """
    destination.mkdir(parents=True, exist_ok=True)
    for path in source.glob("*.py"):
        if path.name.startswith("test_") or should_ignore_runtime_path(path):
            stale_target = destination / path.name
            if stale_target.is_file():
                stale_target.unlink()
            continue
        shutil.copy2(path, destination / path.name)


def copy_runtime_module(repo_root: Path, destination_root: Path, relative_path: Path) -> None:
    """Copy one importable shared module plus the package markers it needs."""
    source = repo_root / relative_path
    if not source.is_file():
        raise FileNotFoundError(f"runtime module is absent: {source}")
    target = destination_root / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    package = relative_path.parent
    while package != Path("."):
        source_init = repo_root / package / "__init__.py"
        if source_init.is_file():
            target_init = destination_root / package / "__init__.py"
            target_init.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_init, target_init)
        package = package.parent


def copy_tree(source: Path, destination: Path) -> None:
    # Workstation runtime folders are often open in terminals while a run is
    # being debugged. Deleting the destination first can leave a partial package
    # if Windows blocks one file or directory. Copy in place so an interrupted
    # sync cannot remove import-critical files such as config.py/model.py.
    destination.mkdir(parents=True, exist_ok=True)
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        target = destination / relative
        if should_ignore_runtime_path(path):
            if target.is_file():
                target.unlink()
            elif target.is_dir():
                shutil.rmtree(target)
            continue
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def ignore_runtime_noise(directory: str, names: list[str]) -> set[str]:
    ignored: set[str] = set()
    for name in names:
        path = Path(directory) / name
        if name in EXCLUDED_DIRS or path.suffix in EXCLUDED_SUFFIXES:
            ignored.add(name)
    return ignored


def should_ignore_runtime_path(path: Path) -> bool:
    is_test_file = path.is_file() and path.suffix == ".py" and (
        path.name.startswith("test_") or path.name.endswith("_test.py") or path.name == "conftest.py"
    )
    return (
        any(part in EXCLUDED_DIRS for part in path.parts)
        or path.suffix in EXCLUDED_SUFFIXES
        or is_test_file
    )

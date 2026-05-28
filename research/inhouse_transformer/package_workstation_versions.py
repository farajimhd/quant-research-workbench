from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_VERSIONS = ("v21", "v22")
DRIVE_COPY_ROOT = Path("G:/My Drive/quant-research-workbench/workstation_code")
WORKSTATION_CODE_ROOT = Path("H:/My Drive/quant-research-workbench/workstation_code")
WANDB_ENTITY = "mehdifaraji"
DEFAULT_FLATFILES_ROOT = "D:/market-data/flatfiles/us_stocks_sip"
VERSION_SETTINGS = {
    "v21": {
        "wandb_project": "May2026-microstructure-hybrid-v21",
        "cache_root": "D:/market-data/flatfiles/us_stocks_sip/derived/microstructure_1s_v1",
        "output_root": "D:/TradingData/quant-research-workbench/market_data/models/inhouse_transformer/v21",
        "run_name": "v21-hybrid-1s10s-binary-mid-june2025",
        "batch_size": 4096,
        "preprocess_script": "preprocess_microstructure.py",
        "profile_script": "",
    },
    "v22": {
        "wandb_project": "May2026-microstructure-event-language-v22",
        "cache_root": "D:/market-data/flatfiles/us_stocks_sip/derived/event_chunks_v1",
        "output_root": "D:/TradingData/quant-research-workbench/market_data/models/inhouse_transformer/v22",
        "run_name": "v22-event-language-chunk250-nov2025",
        "batch_size": 512,
        "preprocess_script": "preprocess_event_chunks.py",
        "profile_script": "profile_event_chunks.py",
        "train_start_date": "2025-11-01",
        "train_end_date": "2025-11-30",
        "validation_start_date": "2025-12-01",
        "validation_end_date": "2025-12-05",
        "test_start_date": "2025-12-08",
        "test_end_date": "2025-12-12",
    },
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Package in-house transformer versions for workstation training.")
    parser.add_argument("--versions", nargs="+", default=list(DEFAULT_VERSIONS))
    parser.add_argument("--drive-code-root", default=str(DRIVE_COPY_ROOT))
    parser.add_argument("--workstation-code-root", default=str(WORKSTATION_CODE_ROOT))
    parser.add_argument("--skip-drive-copy", action="store_true")
    args = parser.parse_args()

    git_commit = current_git_commit()
    generated = []
    for version in args.versions:
        if version not in VERSION_SETTINGS:
            raise SystemExit(f"This workstation packager supports only: {', '.join(VERSION_SETTINGS)}")
        version_dir = REPO_ROOT / "research" / "inhouse_transformer" / version
        if not version_dir.exists():
            raise SystemExit(f"Version folder does not exist: {version_dir}")
        notebook_path = version_dir / "train_workstation.ipynb"
        dist_dir = version_dir / "dist"
        dist_dir.mkdir(parents=True, exist_ok=True)

        manifest = build_manifest(version, git_commit, Path(args.workstation_code_root))
        write_notebook(notebook_path, version, manifest)
        manifest_path = dist_dir / "workstation_manifest.json"
        readme_path = dist_dir / "README_WORKSTATION.md"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        write_workstation_readme(readme_path, version, manifest)

        zip_path = dist_dir / f"inhouse_transformer_{version}_workstation.zip"
        write_package_zip(zip_path, version, notebook_path, manifest_path, readme_path)

        if not args.skip_drive_copy:
            drive_dir = Path(args.drive_code_root) / version
            drive_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(zip_path, drive_dir / zip_path.name)
            shutil.copy2(notebook_path, drive_dir / notebook_path.name)
            settings = VERSION_SETTINGS[version]
            shutil.copy2(version_dir / settings["preprocess_script"], drive_dir / settings["preprocess_script"])
            if settings["profile_script"]:
                shutil.copy2(version_dir / settings["profile_script"], drive_dir / settings["profile_script"])
            shutil.copy2(manifest_path, drive_dir / manifest_path.name)
            shutil.copy2(readme_path, drive_dir / readme_path.name)

        generated.append(str(zip_path))
    print(json.dumps({"generated_packages": generated}, indent=2))


def current_git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return ""


def build_manifest(version: str, git_commit: str, workstation_code_root: Path) -> dict[str, Any]:
    settings = VERSION_SETTINGS[version]
    workstation_version_dir = workstation_code_root / version
    return {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "version": version,
        "git_commit": git_commit,
        "package_name": f"inhouse_transformer_{version}_workstation.zip",
        "drive_code_dir": workstation_version_dir.as_posix(),
        "default_flatfiles_root": DEFAULT_FLATFILES_ROOT,
        "default_cache_root": settings["cache_root"],
        "default_output_root": settings["output_root"],
        "train_start_date": settings.get("train_start_date", "2025-06-02"),
        "train_end_date": settings.get("train_end_date", "2025-06-30"),
        "validation_start_date": settings.get("validation_start_date", "2025-07-01"),
        "validation_end_date": settings.get("validation_end_date", "2025-07-07"),
        "test_start_date": settings.get("test_start_date", "2025-07-08"),
        "test_end_date": settings.get("test_end_date", "2025-07-11"),
        "tickers": "ALL",
        "default_epochs": 3,
        "default_batch_size": settings["batch_size"],
        "default_num_workers": 8,
        "default_prefetch_factor": 4,
        "default_preprocess_processes": 8,
        "default_polars_threads_per_process": 2,
        "preprocess_script": settings["preprocess_script"],
        "profile_script": settings["profile_script"],
        "wandb_entity": WANDB_ENTITY,
        "wandb_project": settings["wandb_project"],
        "wandb_run_name": settings["run_name"],
        "optimizer": "adamw",
        "loss": "binary_cross_entropy_with_logits",
        "lr_scheduler": "cosine_warm_restarts",
        "notes": [
            "API keys are read from the workstation environment or repo .env; no secrets are stored in this package.",
            "The default cache_root is a shared derived-data folder under the SIP flatfiles root so future versions can reuse it.",
            "The first pass builds per-session microstructure Parquet caches; later epochs reuse them.",
        ],
    }


def write_notebook(path: Path, version: str, manifest: dict[str, Any]) -> None:
    cells = [
        markdown_cell(
            f"# Train {version} on Workstation\n\n"
            f"This notebook extracts the packaged code locally and launches {version} training in-process so "
            "logs appear directly in the notebook. Keep API keys in environment variables or a local `.env`; "
            "do not paste secrets into cells."
        ),
        code_cell(
            "import json\n"
            "import os\n"
            "import shutil\n"
            "import sys\n"
            "import zipfile\n"
            "from pathlib import Path\n"
            "\n"
            f"VERSION = {version!r}\n"
            f"DEFAULT_DRIVE_CODE_DIR = Path({manifest['drive_code_dir']!r})\n"
            "DRIVE_CODE_DIR = Path(os.environ.get('QW_DRIVE_CODE_DIR', str(DEFAULT_DRIVE_CODE_DIR)))\n"
            "PACKAGE_ZIP = DRIVE_CODE_DIR / f'inhouse_transformer_{VERSION}_workstation.zip'\n"
            "MANIFEST_PATH = DRIVE_CODE_DIR / 'workstation_manifest.json'\n"
            "LOCAL_CODE_ROOT = Path(f'D:/TradingCodes/quant-research-workbench-{VERSION}-runtime')\n"
            "\n"
            "assert PACKAGE_ZIP.exists(), f'Missing package: {PACKAGE_ZIP}'\n"
            "assert MANIFEST_PATH.exists(), f'Missing manifest: {MANIFEST_PATH}'\n"
            "manifest = json.loads(MANIFEST_PATH.read_text())\n"
            "print(json.dumps(manifest, indent=2))\n"
            "\n"
            "if LOCAL_CODE_ROOT.exists():\n"
            "    shutil.rmtree(LOCAL_CODE_ROOT)\n"
            "LOCAL_CODE_ROOT.mkdir(parents=True, exist_ok=True)\n"
            "with zipfile.ZipFile(PACKAGE_ZIP) as package:\n"
            "    package.extractall(LOCAL_CODE_ROOT)\n"
            "sys.path.insert(0, str(LOCAL_CODE_ROOT))\n"
            "print('installed code at', LOCAL_CODE_ROOT)\n"
        ),
        code_cell(
            "# Edit FLATFILES_ROOT if you copy data from the HDD/Drive path to local SSD/NVMe.\n"
            "FLATFILES_ROOT = Path(manifest['default_flatfiles_root'])\n"
            "CACHE_ROOT = Path(manifest['default_cache_root'])\n"
            "OUTPUT_ROOT = Path(manifest['default_output_root'])\n"
            "print('flatfiles root:', FLATFILES_ROOT, 'exists=', FLATFILES_ROOT.exists())\n"
            "print('cache root:', CACHE_ROOT)\n"
            "print('output root:', OUTPUT_ROOT)\n"
            "CACHE_ROOT.mkdir(parents=True, exist_ok=True)\n"
            "OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)\n"
        ),
        code_cell(
            "%pip install -q polars pyarrow wandb torchinfo torchview graphviz\n"
        ),
        markdown_cell(
            "Optional: run the profiling cell before choosing chunk/cap settings. "
            "This is available for v22 and skipped for older versions."
        ),
        code_cell(profile_source(version)),
        markdown_cell(
            "Optional but recommended: prebuild the microstructure Parquet cache before training. "
            "This is the slow CSV decompression step; later training epochs reuse the cache."
        ),
        code_cell(preprocess_source(version)),
        code_cell(training_source(version)),
    ]
    notebook = {
        "cells": cells,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "name": "python3"},
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    path.write_text(json.dumps(notebook, indent=1) + "\n", encoding="utf-8")


def preprocess_source(version: str) -> str:
    script_name = VERSION_SETTINGS[version]["preprocess_script"]
    extra_args = ""
    if version == "v22":
        extra_args = (
            "    '--chunk-ms', '250',\n"
            "    '--max-quote-events', '96',\n"
            "    '--max-trade-events', '64',\n"
            "    '--max-total-events', '128',\n"
        )
    return (
        "import subprocess\n"
        "import sys\n"
        "\n"
        "RUN_PREPROCESS = False  # Set True to build/refresh the microstructure cache before training.\n"
        "PREPROCESS_PROCESSES = int(manifest.get('default_preprocess_processes', 8))\n"
        "POLARS_THREADS_PER_PROCESS = int(manifest.get('default_polars_threads_per_process', 2))\n"
        "REBUILD_PREPROCESS_CACHE = False\n"
        "\n"
        f"preprocess_py = LOCAL_CODE_ROOT / 'research' / 'inhouse_transformer' / {version!r} / {script_name!r}\n"
        "preprocess_args = [\n"
        "    '--flatfiles-root', str(FLATFILES_ROOT),\n"
        "    '--cache-root', str(CACHE_ROOT),\n"
        "    '--start-date', manifest['train_start_date'],\n"
        "    '--end-date', manifest['test_end_date'],\n"
        "    '--tickers', manifest.get('tickers', 'ALL'),\n"
        f"{extra_args}"
        "    '--processes', str(PREPROCESS_PROCESSES),\n"
        "    '--polars-threads-per-process', str(POLARS_THREADS_PER_PROCESS),\n"
        "]\n"
        "if REBUILD_PREPROCESS_CACHE:\n"
        "    preprocess_args.append('--rebuild-cache')\n"
        "\n"
        "if RUN_PREPROCESS:\n"
        "    print('Running:', ' '.join([str(preprocess_py), *preprocess_args]))\n"
        "    subprocess.check_call([sys.executable, str(preprocess_py), *preprocess_args])\n"
        "else:\n"
        "    print('Skipping preprocessing. Set RUN_PREPROCESS=True to build the cache first.')\n"
    )


def profile_source(version: str) -> str:
    script_name = VERSION_SETTINGS[version]["profile_script"]
    if not script_name:
        return "print('No separate profiling script for this version.')\n"
    return (
        "import subprocess\n"
        "import sys\n"
        "\n"
        "RUN_PROFILE = False  # Set True to profile chunk sizes and event caps before preprocessing.\n"
        "PROFILE_PROCESSES = int(manifest.get('default_preprocess_processes', 8))\n"
        "POLARS_THREADS_PER_PROCESS = int(manifest.get('default_polars_threads_per_process', 2))\n"
        f"profile_py = LOCAL_CODE_ROOT / 'research' / 'inhouse_transformer' / {version!r} / {script_name!r}\n"
        "profile_args = [\n"
        "    '--flatfiles-root', str(FLATFILES_ROOT),\n"
        "    '--start-date', manifest['train_start_date'],\n"
        "    '--end-date', manifest['validation_end_date'],\n"
        "    '--tickers', manifest.get('tickers', 'ALL'),\n"
        "    '--chunk-ms', '100,250,500,1000',\n"
        "    '--caps', '64,128,256,512',\n"
        "    '--processes', str(PROFILE_PROCESSES),\n"
        "    '--polars-threads-per-process', str(POLARS_THREADS_PER_PROCESS),\n"
        "]\n"
        "if RUN_PROFILE:\n"
        "    print('Running:', ' '.join([str(profile_py), *profile_args]))\n"
        "    subprocess.check_call([sys.executable, str(profile_py), *profile_args])\n"
        "else:\n"
        "    print('Skipping profiling. Set RUN_PROFILE=True to profile chunk/cap choices.')\n"
    )


def training_source(version: str) -> str:
    return (
        "import runpy\n"
        "import sys\n"
        "\n"
        "BATCH_SIZE = int(manifest.get('default_batch_size', 4096))\n"
        "EPOCHS = int(manifest.get('default_epochs', 3))\n"
        "NUM_WORKERS = int(manifest.get('default_num_workers', 8))\n"
        "PREFETCH_FACTOR = int(manifest.get('default_prefetch_factor', 4))\n"
        "MAX_STEPS = 0\n"
        "COUNT_COVERAGE = False\n"
        "DRY_RUN = False\n"
        "REBUILD_CACHE = False\n"
        "\n"
        f"train_py = LOCAL_CODE_ROOT / 'research' / 'inhouse_transformer' / {version!r} / 'train.py'\n"
        "args = [\n"
        "    '--flatfiles-root', str(FLATFILES_ROOT),\n"
        "    '--cache-root', str(CACHE_ROOT),\n"
        "    '--train-start-date', manifest['train_start_date'],\n"
        "    '--train-end-date', manifest['train_end_date'],\n"
        "    '--validation-start-date', manifest['validation_start_date'],\n"
        "    '--validation-end-date', manifest['validation_end_date'],\n"
        "    '--test-start-date', manifest['test_start_date'],\n"
        "    '--test-end-date', manifest['test_end_date'],\n"
        "    '--device', 'cuda',\n"
        "    '--output-root', str(OUTPUT_ROOT),\n"
        "    '--batch-size', str(BATCH_SIZE),\n"
        "    '--epochs', str(EPOCHS),\n"
        "    '--max-steps', str(MAX_STEPS),\n"
        "    '--num-workers', str(NUM_WORKERS),\n"
        "    '--prefetch-factor', str(PREFETCH_FACTOR),\n"
        "    '--tickers', manifest.get('tickers', 'ALL'),\n"
        "    '--checkpoint-policy', 'last_only',\n"
        "    '--wandb-entity', manifest['wandb_entity'],\n"
        "    '--wandb-project', manifest['wandb_project'],\n"
        "    '--wandb-run-name', manifest['wandb_run_name'],\n"
        "    '--output-name', manifest['wandb_run_name'],\n"
        "]\n"
        "if REBUILD_CACHE:\n"
        "    args.append('--rebuild-cache')\n"
        "if COUNT_COVERAGE:\n"
        "    args.append('--count-coverage')\n"
        "if DRY_RUN:\n"
        "    args.append('--dry-run')\n"
        "\n"
        "print('Running:', ' '.join([str(train_py), *args]))\n"
        "old_argv = sys.argv[:]\n"
        "try:\n"
        "    sys.argv = [str(train_py), *args]\n"
        "    runpy.run_path(str(train_py), run_name='__main__')\n"
        "finally:\n"
        "    sys.argv = old_argv\n"
    )


def write_workstation_readme(path: Path, version: str, manifest: dict[str, Any]) -> None:
    path.write_text(
        f"# Workstation package for {version}\n\n"
        "Open `train_workstation.ipynb` from the same Drive folder. The notebook extracts "
        "the zip to a local runtime directory and runs training in-process.\n\n"
        f"Run `{manifest['preprocess_script']}` first, or set `RUN_PREPROCESS=True` in the notebook, "
        "to prebuild the quote/trade Parquet cache.\n\n"
        f"Default W&B project: `{manifest['wandb_project']}`\n\n"
        "For performance, keep flatfiles and cache output on local SSD/NVMe and update `FLATFILES_ROOT` "
        "in the notebook if needed.\n",
        encoding="utf-8",
    )


def write_package_zip(zip_path: Path, version: str, notebook_path: Path, manifest_path: Path, readme_path: Path) -> None:
    include_files = [
        REPO_ROOT / "research" / "inhouse_transformer" / "model_artifacts.py",
        REPO_ROOT / "research" / "inhouse_transformer" / "v14" / "data.py",
        REPO_ROOT / "research" / "inhouse_transformer" / "v14" / "metrics.py",
        REPO_ROOT / "src" / "data_provider" / "config.py",
    ]
    version_dir = REPO_ROOT / "research" / "inhouse_transformer" / version
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in version_dir.rglob("*"):
            if (
                path.is_file()
                and "__pycache__" not in path.parts
                and "dist" not in path.parts
                and path != notebook_path
            ):
                archive.write(path, path.relative_to(REPO_ROOT))
        for path in include_files:
            archive.write(path, path.relative_to(REPO_ROOT))
        archive.write(notebook_path, notebook_path.relative_to(REPO_ROOT))
        archive.write(manifest_path, manifest_path.relative_to(REPO_ROOT))
        archive.write(readme_path, readme_path.relative_to(REPO_ROOT))


def markdown_cell(source: str) -> dict[str, Any]:
    return {"cell_type": "markdown", "metadata": {}, "source": source.splitlines(keepends=True)}


def code_cell(source: str) -> dict[str, Any]:
    return {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": source.splitlines(keepends=True)}


if __name__ == "__main__":
    main()

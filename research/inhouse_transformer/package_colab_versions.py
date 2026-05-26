from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_VERSIONS = ("v14", "v16", "v17", "v19")
DRIVE_CODE_ROOT = Path("G:/My Drive/quant-research-workbench/colab_code")
COLAB_DATA_ROOT = "/content/drive/MyDrive/quant-research-workbench/colab_data/v17_june2025/market_data"
TRAIN_START = "2025-06-02"
TRAIN_END = "2025-06-30"
VALIDATION_START = "2025-07-01"
VALIDATION_END = "2025-07-07"
WANDB_ENTITY = "mehdifaraji"
WANDB_PROJECT = "May2026-1m-timeseries-generalization"
DEFAULT_LEARNING_RATE = 3e-4
DEFAULT_WEIGHT_DECAY = 1e-4
DEFAULT_LR_SCHEDULER = "cosine_warm_restarts"
DEFAULT_COSINE_RESTART_T0_STEPS = 500
DEFAULT_COSINE_RESTART_T_MULT = 2
DEFAULT_MIN_LEARNING_RATE = 1e-6
DEFAULT_WARMUP_STEPS = 1000


def main() -> None:
    parser = argparse.ArgumentParser(description="Package in-house transformer versions for Colab.")
    parser.add_argument("--versions", nargs="+", default=list(DEFAULT_VERSIONS))
    parser.add_argument("--drive-code-root", default=str(DRIVE_CODE_ROOT))
    parser.add_argument("--skip-drive-copy", action="store_true")
    args = parser.parse_args()

    git_commit = current_git_commit()
    generated = []
    for version in args.versions:
        version_dir = REPO_ROOT / "research" / "inhouse_transformer" / version
        if not version_dir.exists():
            raise SystemExit(f"Version folder does not exist: {version_dir}")
        notebook_path = version_dir / "train_colab.ipynb"
        dist_dir = version_dir / "dist"
        dist_dir.mkdir(parents=True, exist_ok=True)

        manifest = build_manifest(version, git_commit)
        write_notebook(notebook_path, version, manifest)
        write_colab_readme(dist_dir / "README_COLAB.md", version, manifest)
        manifest_path = dist_dir / "colab_manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        zip_path = dist_dir / f"inhouse_transformer_{version}.zip"
        write_package_zip(zip_path, version, notebook_path, manifest_path, dist_dir / "README_COLAB.md")

        if not args.skip_drive_copy:
            drive_dir = Path(args.drive_code_root) / version
            drive_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(zip_path, drive_dir / zip_path.name)
            shutil.copy2(notebook_path, drive_dir / notebook_path.name)
            shutil.copy2(manifest_path, drive_dir / manifest_path.name)
            shutil.copy2(dist_dir / "README_COLAB.md", drive_dir / "README_COLAB.md")

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


def build_manifest(version: str, git_commit: str) -> dict[str, Any]:
    return {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "version": version,
        "git_commit": git_commit,
        "package_name": f"inhouse_transformer_{version}.zip",
        "colab_code_dir": f"/content/drive/MyDrive/quant-research-workbench/colab_code/{version}",
        "colab_data_root": COLAB_DATA_ROOT,
        "train_start_date": TRAIN_START,
        "train_end_date": TRAIN_END,
        "validation_start_date": VALIDATION_START,
        "validation_end_date": VALIDATION_END,
        "test_start_date": VALIDATION_START,
        "test_end_date": VALIDATION_END,
        "tickers": "ALL",
        "allow_target_across_session": True,
        "default_epochs": 3,
        "optimizer": "adamw",
        "loss": "binary_cross_entropy_with_logits",
        "learning_rate": DEFAULT_LEARNING_RATE,
        "weight_decay": DEFAULT_WEIGHT_DECAY,
        "lr_scheduler": DEFAULT_LR_SCHEDULER,
        "cosine_restart_t0_steps": DEFAULT_COSINE_RESTART_T0_STEPS,
        "cosine_restart_t_mult": DEFAULT_COSINE_RESTART_T_MULT,
        "min_learning_rate": DEFAULT_MIN_LEARNING_RATE,
        "warmup_steps": DEFAULT_WARMUP_STEPS,
        "wandb_entity": WANDB_ENTITY,
        "wandb_project": WANDB_PROJECT,
        "secret_names": ["WANDB_API_KEY"],
        "notes": [
            "API keys are read from Colab Secrets and are not stored in this package.",
            "The package contains code only; market data remains under the Drive colab_data folder.",
            "Training uses the overfit-aligned setup: AdamW, BCE-with-logits target loss, and cosine warm restarts.",
        ],
    }


def write_notebook(path: Path, version: str, manifest: dict[str, Any]) -> None:
    cells = [
        markdown_cell(
            f"# Train {version} on Colab\n\n"
            "This notebook mounts Google Drive, installs runtime dependencies, reads W&B credentials "
            "from Colab Secrets, installs the packaged version code, and launches training against "
            "the Drive-hosted market data package."
        ),
        markdown_cell(
            "Before running this notebook, add `WANDB_API_KEY` in Colab's Secrets panel. "
            "Do not paste API keys into notebook cells."
        ),
        code_cell(
            "from google.colab import drive, userdata\n"
            "drive.mount('/content/drive')\n"
            "\n"
            "import os\n"
            "wandb_api_key = userdata.get('WANDB_API_KEY')\n"
            "if wandb_api_key:\n"
            "    os.environ['WANDB_API_KEY'] = wandb_api_key\n"
            "else:\n"
            "    print('WANDB_API_KEY was not found in Colab Secrets; W&B login may prompt or run offline.')\n"
        ),
        code_cell(
            "!apt-get -qq update && apt-get -qq install -y graphviz\n"
            "%pip install -q polars pyarrow wandb torchinfo torchview graphviz\n"
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
            "DRIVE_ROOT = Path('/content/drive/MyDrive')\n"
            "CODE_DRIVE_DIR = DRIVE_ROOT / 'quant-research-workbench' / 'colab_code' / VERSION\n"
            "PACKAGE_ZIP = CODE_DRIVE_DIR / f'inhouse_transformer_{VERSION}.zip'\n"
            "MANIFEST_PATH = CODE_DRIVE_DIR / 'colab_manifest.json'\n"
            "CODE_ROOT = Path('/content/quant-research-workbench')\n"
            "\n"
            "print('package:', PACKAGE_ZIP)\n"
            "print('manifest:', MANIFEST_PATH)\n"
            "assert PACKAGE_ZIP.exists(), f'Missing package zip: {PACKAGE_ZIP}'\n"
            "assert MANIFEST_PATH.exists(), f'Missing manifest: {MANIFEST_PATH}'\n"
            "manifest = json.loads(MANIFEST_PATH.read_text())\n"
            "print(json.dumps(manifest, indent=2))\n"
            "\n"
            "if CODE_ROOT.exists():\n"
            "    shutil.rmtree(CODE_ROOT)\n"
            "CODE_ROOT.mkdir(parents=True, exist_ok=True)\n"
            "with zipfile.ZipFile(PACKAGE_ZIP) as package:\n"
            "    package.extractall(CODE_ROOT)\n"
            "sys.path.insert(0, str(CODE_ROOT))\n"
            "print('installed code at', CODE_ROOT)\n"
        ),
        code_cell(
            "from pathlib import Path\n"
            "\n"
            "PROCESSED_ROOT = Path(manifest['colab_data_root'])\n"
            "assert (PROCESSED_ROOT / 'bars' / '1m').exists(), f'Missing bars/1m under {PROCESSED_ROOT}'\n"
            "print('processed root:', PROCESSED_ROOT)\n"
        ),
        code_cell(training_command_source(version)),
    ]
    notebook = {
        "cells": cells,
        "metadata": {
            "accelerator": "GPU",
            "colab": {"provenance": []},
            "kernelspec": {"display_name": "Python 3", "name": "python3"},
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    path.write_text(json.dumps(notebook, indent=1) + "\n", encoding="utf-8")


def training_command_source(version: str) -> str:
    default_batch_size = 256 if version == "v17" else 512
    return (
        "import runpy\n"
        "import sys\n"
        "\n"
        "# Tune these in Colab before launching a long run. L4/A100 runtimes can often use larger batches.\n"
        f"BATCH_SIZE = {default_batch_size}\n"
        "EPOCHS = int(manifest.get('default_epochs', 3))\n"
        "MAX_STEPS = 0  # 0 means stream the configured epoch until exhaustion.\n"
        "TICKERS = manifest.get('tickers', 'ALL')\n"
        "LEARNING_RATE = float(manifest.get('learning_rate', 3e-4))\n"
        "WEIGHT_DECAY = float(manifest.get('weight_decay', 1e-4))\n"
        "LR_SCHEDULER = manifest.get('lr_scheduler', 'cosine_warm_restarts')\n"
        "COSINE_RESTART_T0_STEPS = int(manifest.get('cosine_restart_t0_steps', 500))\n"
        "COSINE_RESTART_T_MULT = int(manifest.get('cosine_restart_t_mult', 2))\n"
        "MIN_LEARNING_RATE = float(manifest.get('min_learning_rate', 1e-6))\n"
        "WARMUP_STEPS = int(manifest.get('warmup_steps', 1000))\n"
        "EVAL_STEPS = 500\n"
        "LOGGING_STEPS = 50\n"
        "VALIDATION_WINDOW_COUNT = 50000\n"
        "TEST_WINDOW_COUNT = 50000\n"
        "ALLOW_TARGET_ACROSS_SESSION = bool(manifest.get('allow_target_across_session', True))\n"
        "\n"
        f"train_py = CODE_ROOT / 'research' / 'inhouse_transformer' / {version!r} / 'train.py'\n"
        "args = [\n"
        "    '--processed-root', str(PROCESSED_ROOT),\n"
        "    '--train-start-date', manifest['train_start_date'],\n"
        "    '--train-end-date', manifest['train_end_date'],\n"
        "    '--validation-start-date', manifest['validation_start_date'],\n"
        "    '--validation-end-date', manifest['validation_end_date'],\n"
        "    '--test-start-date', manifest['test_start_date'],\n"
        "    '--test-end-date', manifest['test_end_date'],\n"
        "    '--device', 'cuda',\n"
        "    '--batch-size', str(BATCH_SIZE),\n"
        "    '--epochs', str(EPOCHS),\n"
        "    '--tickers', TICKERS,\n"
        "    '--learning-rate', str(LEARNING_RATE),\n"
        "    '--weight-decay', str(WEIGHT_DECAY),\n"
        "    '--lr-scheduler', LR_SCHEDULER,\n"
        "    '--cosine-restart-t0-steps', str(COSINE_RESTART_T0_STEPS),\n"
        "    '--cosine-restart-t-mult', str(COSINE_RESTART_T_MULT),\n"
        "    '--min-learning-rate', str(MIN_LEARNING_RATE),\n"
        "    '--warmup-steps', str(WARMUP_STEPS),\n"
        "    '--eval-steps', str(EVAL_STEPS),\n"
        "    '--logging-steps', str(LOGGING_STEPS),\n"
        "    '--validation-window-count', str(VALIDATION_WINDOW_COUNT),\n"
        "    '--test-window-count', str(TEST_WINDOW_COUNT),\n"
        "    '--wandb-entity', manifest['wandb_entity'],\n"
        "    '--wandb-project', manifest['wandb_project'],\n"
        "]\n"
        "if MAX_STEPS > 0:\n"
        "    args += ['--max-steps', str(MAX_STEPS)]\n"
        "if not ALLOW_TARGET_ACROSS_SESSION:\n"
        "    raise ValueError('Colab generalization runs are configured to require --allow-target-across-session.')\n"
        "args.append('--allow-target-across-session')\n"
        "\n"
        "print('Running in notebook process:')\n"
        "print('python', train_py, ' '.join(args), flush=True)\n"
        "old_argv = sys.argv[:]\n"
        "try:\n"
        "    sys.argv = [str(train_py), *args]\n"
        "    runpy.run_path(str(train_py), run_name='__main__')\n"
        "finally:\n"
        "    sys.argv = old_argv\n"
    )


def write_colab_readme(path: Path, version: str, manifest: dict[str, Any]) -> None:
    path.write_text(
        f"# Colab Package for {version}\n\n"
        "Files in this folder are generated for Colab training.\n\n"
        "## Drive Paths\n\n"
        f"- Code package in Colab: `{manifest['colab_code_dir']}`\n"
        f"- Data root in Colab: `{manifest['colab_data_root']}`\n\n"
        "## Secrets\n\n"
        "Add `WANDB_API_KEY` in Colab Secrets before running `train_colab.ipynb`. "
        "No API keys are stored in the notebook, manifest, or zip package.\n\n"
        "## Default Dates\n\n"
        f"- train: `{manifest['train_start_date']}` to `{manifest['train_end_date']}`\n"
        f"- validation: `{manifest['validation_start_date']}` to `{manifest['validation_end_date']}`\n"
        f"- test: `{manifest['test_start_date']}` to `{manifest['test_end_date']}`\n"
        f"- tickers: `{manifest['tickers']}`\n"
        f"- allow target across session: `{manifest['allow_target_across_session']}`\n"
        f"- default epochs: `{manifest['default_epochs']}`\n\n"
        "## Training Setup\n\n"
        f"- optimizer: `{manifest['optimizer']}`\n"
        f"- loss: `{manifest['loss']}`\n"
        f"- learning rate: `{manifest['learning_rate']}`\n"
        f"- weight decay: `{manifest['weight_decay']}`\n"
        f"- scheduler: `{manifest['lr_scheduler']}` "
        f"(T_0 steps `{manifest['cosine_restart_t0_steps']}`, "
        f"T_mult `{manifest['cosine_restart_t_mult']}`)\n",
        encoding="utf-8",
    )


def write_package_zip(zip_path: Path, version: str, notebook_path: Path, manifest_path: Path, readme_path: Path) -> None:
    version_dir = REPO_ROOT / "research" / "inhouse_transformer" / version
    include_paths: list[Path] = []
    include_paths.extend(sorted(version_dir.glob("*.py")))
    include_paths.append(version_dir / "README.md")
    include_paths.append(notebook_path)
    include_paths.append(REPO_ROOT / "research" / "inhouse_transformer" / "model_artifacts.py")
    include_paths.append(REPO_ROOT / "src" / "data_provider" / "config.py")
    include_paths.append(REPO_ROOT / "src" / "data_provider" / "store.py")
    include_paths.append(manifest_path)
    include_paths.append(readme_path)

    zip_path.unlink(missing_ok=True)
    with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as package:
        for path in include_paths:
            if not path.exists():
                raise FileNotFoundError(path)
            if path == manifest_path:
                arcname = Path("colab_manifest.json")
            elif path == readme_path:
                arcname = Path("README_COLAB.md")
            else:
                arcname = path.relative_to(REPO_ROOT)
            package.write(path, arcname.as_posix())


def markdown_cell(source: str) -> dict[str, Any]:
    return {"cell_type": "markdown", "metadata": {}, "source": source.splitlines(keepends=True)}


def code_cell(source: str) -> dict[str, Any]:
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": source.splitlines(keepends=True),
    }


if __name__ == "__main__":
    main()

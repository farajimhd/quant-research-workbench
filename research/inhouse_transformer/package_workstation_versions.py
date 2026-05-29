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
        "canonical_root": "D:/market-data/flatfiles/us_stocks_sip/derived/canonical_events_v2",
        "cache_root": "D:/market-data/flatfiles/us_stocks_sip/derived/event_chunks_v2",
        "output_root": "D:/TradingData/quant-research-workbench/market_data/models/inhouse_transformer/v22",
        "run_name": "v22-event-language-chunk500-nov2025",
        "batch_size": 512,
        "preprocess_script": "preprocess_event_chunks.py",
        "profile_script": "profile_event_chunks.py",
        "utility_scripts": ("inspect_flatfile_order.py",),
        "train_start_date": "2025-11-01",
        "train_end_date": "2025-11-30",
        "validation_start_date": "2025-12-01",
        "validation_end_date": "2025-12-05",
        "test_start_date": "2025-12-08",
        "test_end_date": "2025-12-12",
        "preprocess_processes": 16,
        "normalize_processes": 8,
        "quote_normalize_processes": 8,
        "trade_normalize_processes": 8,
        "canonical_processes": 16,
        "chunk_processes": 16,
        "polars_threads_per_process": 4,
        "preprocess_rebuild_cache": True,
        "preprocess_build_chunks": True,
        "preprocess_verbose_worker_steps": True,
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
            for utility_script in settings.get("utility_scripts", ()):
                shutil.copy2(version_dir / utility_script, drive_dir / utility_script)
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
        "default_canonical_root": settings.get("canonical_root", ""),
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
        "default_profile_processes": 2,
        "default_profile_sessions": 4,
        "default_preprocess_processes": settings.get("preprocess_processes", 4),
        "default_normalize_processes": settings.get("normalize_processes", settings.get("preprocess_processes", 4)),
        "default_quote_normalize_processes": settings.get("quote_normalize_processes", settings.get("normalize_processes", settings.get("preprocess_processes", 4))),
        "default_trade_normalize_processes": settings.get("trade_normalize_processes", settings.get("normalize_processes", settings.get("preprocess_processes", 4))),
        "default_canonical_processes": settings.get("canonical_processes", settings.get("preprocess_processes", 4)),
        "default_chunk_processes": settings.get("chunk_processes", settings.get("preprocess_processes", 4)),
        "default_preprocess_rebuild_cache": settings.get("preprocess_rebuild_cache", False),
        "default_preprocess_build_chunks": settings.get("preprocess_build_chunks", True),
        "default_preprocess_verbose_worker_steps": settings.get("preprocess_verbose_worker_steps", False),
        "default_polars_threads_per_process": settings.get("polars_threads_per_process", 8),
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
            "Run only the cells in this notebook. They do not start profiling, preprocessing, or training.\n\n"
            "The notebook extracts the package to a local runtime folder, lets you confirm paths and parameters, "
            "then generates PowerShell scripts. Run those scripts from a terminal so progress, failures, and logs "
            "are fully visible.\n\n"
            "Generated scripts: `run_install_deps.ps1`, `run_profile.ps1`, `run_preprocess.ps1`, and `run_train.ps1`."
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
        markdown_cell(
            "Confirm these paths before generating commands. Edit this cell if your flatfiles, cache, or model output "
            "should live somewhere else on the workstation."
        ),
        code_cell(
            "# Edit FLATFILES_ROOT if you copy data from the HDD/Drive path to local SSD/NVMe.\n"
            "FLATFILES_ROOT = Path(manifest['default_flatfiles_root'])\n"
            "CANONICAL_ROOT = Path(manifest.get('default_canonical_root') or (FLATFILES_ROOT / 'derived' / 'canonical_events_v1'))\n"
            "CACHE_ROOT = Path(manifest['default_cache_root'])\n"
            "OUTPUT_ROOT = Path(manifest['default_output_root'])\n"
            "print('flatfiles root:', FLATFILES_ROOT, 'exists=', FLATFILES_ROOT.exists())\n"
            "print('canonical root:', CANONICAL_ROOT)\n"
            "print('cache root:', CACHE_ROOT)\n"
            "print('output root:', OUTPUT_ROOT)\n"
            "CANONICAL_ROOT.mkdir(parents=True, exist_ok=True)\n"
            "CACHE_ROOT.mkdir(parents=True, exist_ok=True)\n"
            "OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)\n"
        ),
        markdown_cell(
            "Generate the terminal scripts. This cell creates all scripts in the extracted runtime folder and prints "
            "the exact PowerShell commands to run. It does not start any long-running job."
        ),
        code_cell(command_generation_source(version)),
        markdown_cell(
            "Run the generated scripts from PowerShell in this order:\n\n"
            "1. `run_install_deps.ps1` if dependencies are missing in the Python environment used by the scripts.\n"
            "2. `run_profile.ps1`\n"
            "3. `run_preprocess.ps1`\n"
            "4. `run_train.ps1`\n\n"
            "Each script writes a log under `OUTPUT_ROOT / 'workstation_logs'`."
        ),
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


def command_generation_source(version: str) -> str:
    settings = VERSION_SETTINGS[version]
    profile_script = settings["profile_script"]
    preprocess_script = settings["preprocess_script"]
    profile_block = ""
    if profile_script:
        profile_block = (
            f"profile_py = LOCAL_CODE_ROOT / 'research' / 'inhouse_transformer' / {version!r} / {profile_script!r}\n"
            "profile_args = [\n"
            "    '--flatfiles-root', str(FLATFILES_ROOT),\n"
            "    '--start-date', manifest['train_start_date'],\n"
            "    '--end-date', manifest['validation_end_date'],\n"
            "    '--tickers', manifest.get('tickers', 'ALL'),\n"
            "    '--chunk-ms', PROFILE_CHUNK_MS,\n"
            "    '--caps', PROFILE_CAPS,\n"
            "    '--max-profile-sessions', str(PROFILE_SESSIONS),\n"
            "    '--processes', str(PROFILE_PROCESSES),\n"
            "    '--polars-threads-per-process', str(POLARS_THREADS_PER_PROCESS),\n"
            "]\n"
        )
    else:
        profile_block = "print('No profile script for this version; run_profile.ps1 was not generated.')\n"

    extra_preprocess_args = ""
    canonical_arg = ""
    if version == "v22":
        canonical_arg = "    '--canonical-root', str(CANONICAL_ROOT),\n"
        extra_preprocess_args = (
            "    '--chunk-ms', CHUNK_MS,\n"
            "    '--max-quote-events', str(MAX_QUOTE_EVENTS),\n"
            "    '--max-trade-events', str(MAX_TRADE_EVENTS),\n"
            "    '--max-total-events', str(MAX_TOTAL_EVENTS),\n"
        )

    return (
        "# Configure these values, then run this cell once to create all PowerShell scripts.\n"
        "PROFILE_CHUNK_MS = '100,250,500,1000'\n"
        "PROFILE_CAPS = '64,128,256,512'\n"
        "CHUNK_MS = '500'\n"
        "MAX_QUOTE_EVENTS = 128\n"
        "MAX_TRADE_EVENTS = 192\n"
        "MAX_TOTAL_EVENTS = 256\n"
        "PROFILE_SESSIONS = int(manifest.get('default_profile_sessions', 4))\n"
        "PROFILE_PROCESSES = int(manifest.get('default_profile_processes', 2))\n"
        "PREPROCESS_PROCESSES = int(manifest.get('default_preprocess_processes', 4))\n"
        "NORMALIZE_PROCESSES = int(manifest.get('default_normalize_processes', PREPROCESS_PROCESSES))\n"
        "QUOTE_NORMALIZE_PROCESSES = int(manifest.get('default_quote_normalize_processes', NORMALIZE_PROCESSES))\n"
        "TRADE_NORMALIZE_PROCESSES = int(manifest.get('default_trade_normalize_processes', NORMALIZE_PROCESSES))\n"
        "CANONICAL_PROCESSES = int(manifest.get('default_canonical_processes', PREPROCESS_PROCESSES))\n"
        "CHUNK_PROCESSES = int(manifest.get('default_chunk_processes', PREPROCESS_PROCESSES))\n"
        "PREPROCESS_HEARTBEAT_SECONDS = 30\n"
        "PREPROCESS_MAX_PENDING = 0\n"
        "POLARS_THREADS_PER_PROCESS = int(manifest.get('default_polars_threads_per_process', 8))\n"
        "BUILD_EVENT_CHUNKS = bool(manifest.get('default_preprocess_build_chunks', True))\n"
        "REBUILD_PREPROCESS_CACHE = bool(manifest.get('default_preprocess_rebuild_cache', False))\n"
        "VERBOSE_WORKER_STEPS = bool(manifest.get('default_preprocess_verbose_worker_steps', False))\n"
        "BATCH_SIZE = int(manifest.get('default_batch_size', 4096))\n"
        "EPOCHS = int(manifest.get('default_epochs', 3))\n"
        "NUM_WORKERS = int(manifest.get('default_num_workers', 8))\n"
        "PREFETCH_FACTOR = int(manifest.get('default_prefetch_factor', 4))\n"
        "MAX_STEPS = 0\n"
        "COUNT_COVERAGE = False\n"
        "DRY_RUN = False\n"
        "REBUILD_CACHE = False\n"
        "\n"
        "RUN_LOG_DIR = OUTPUT_ROOT / 'workstation_logs'\n"
        "RUN_LOG_DIR.mkdir(parents=True, exist_ok=True)\n"
        "\n"
        "def ps_quote(value):\n"
        "    text = str(value).replace('`', '``').replace('\"', '`\"')\n"
        "    return f'\"{text}\"'\n"
        "\n"
        "def terminal_command(script_path, args):\n"
        "    return ' '.join(['&', ps_quote(sys.executable), '-u', ps_quote(script_path), *[ps_quote(arg) for arg in args]])\n"
        "\n"
        "def write_command_script(label, script_path, args):\n"
        "    script_path = Path(script_path)\n"
        "    if not script_path.exists():\n"
        "        raise FileNotFoundError(\n"
        "            f'Missing {label} script: {script_path}. Run the extraction cell above before generating commands.'\n"
        "        )\n"
        "    command = terminal_command(script_path, args)\n"
        "    ps1_path = LOCAL_CODE_ROOT / f'run_{label}.ps1'\n"
        "    log_path = RUN_LOG_DIR / f'{VERSION}_{label}.log'\n"
        "    ps1_path.parent.mkdir(parents=True, exist_ok=True)\n"
        "    log_path.parent.mkdir(parents=True, exist_ok=True)\n"
        "    py_path = str(LOCAL_CODE_ROOT).replace(\"'\", \"''\")\n"
        "    ps1 = (\n"
        "        \"$ErrorActionPreference = 'Stop'\\n\"\n"
        "        \"$env:PYTHONUNBUFFERED = '1'\\n\"\n"
        "        f\"$env:PYTHONPATH = '{py_path}' + [System.IO.Path]::PathSeparator + $env:PYTHONPATH\\n\"\n"
        "        f\"{command} 2>&1 | Tee-Object -FilePath {ps_quote(log_path)}\\n\"\n"
        "        \"if ($LASTEXITCODE -ne 0) { throw \\\"Command failed with exit code $LASTEXITCODE\\\" }\\n\"\n"
        "    )\n"
        "    ps1_path.write_text(ps1, encoding='utf-8')\n"
        "    return ps1_path, log_path, command\n"
        "\n"
        "def print_script(label, ps1_path, log_path, command):\n"
        "    print('=' * 96)\n"
        "    print(f'{label.upper()} SCRIPT')\n"
        "    print('PowerShell script:', ps1_path)\n"
        "    print('Log file:', log_path)\n"
        "    print('Run this in PowerShell:')\n"
        "    print('& ' + ps_quote(ps1_path))\n"
        "    print('Direct command equivalent:')\n"
        "    print(command)\n"
        "    print('=' * 96)\n"
        "\n"
        "def add_rebuild_flag(args, enabled):\n"
        "    if enabled:\n"
        "        args.append('--rebuild-cache')\n"
        "\n"
        "def add_build_chunks_flag(args, enabled):\n"
        "    if enabled:\n"
        "        args.append('--build-chunks')\n"
        "\n"
        "def add_verbose_worker_steps_flag(args, enabled):\n"
        "    if enabled:\n"
        "        args.append('--verbose-worker-steps')\n"
        "\n"
        f"PROFILE_ENABLED = {bool(profile_script)!r}\n"
        "\n"
        "install_ps1 = LOCAL_CODE_ROOT / 'run_install_deps.ps1'\n"
        "install_log = RUN_LOG_DIR / f'{VERSION}_install_deps.log'\n"
        "install_command = f'& {ps_quote(sys.executable)} -m pip install \"polars[rt64]\" pyarrow wandb torchinfo torchview graphviz'\n"
        "install_ps1.parent.mkdir(parents=True, exist_ok=True)\n"
        "install_log.parent.mkdir(parents=True, exist_ok=True)\n"
        "install_ps1.write_text(\n"
        "    \"$ErrorActionPreference = 'Stop'\\n\"\n"
        "    f\"{install_command} 2>&1 | Tee-Object -FilePath {ps_quote(install_log)}\\n\"\n"
        "    \"if ($LASTEXITCODE -ne 0) { throw \\\"Command failed with exit code $LASTEXITCODE\\\" }\\n\",\n"
        "    encoding='utf-8',\n"
        ")\n"
        "print_script('install_deps', install_ps1, install_log, install_command)\n"
        "\n"
        f"{profile_block}"
        "\n"
        f"preprocess_py = LOCAL_CODE_ROOT / 'research' / 'inhouse_transformer' / {version!r} / {preprocess_script!r}\n"
        "preprocess_args = [\n"
        "    '--flatfiles-root', str(FLATFILES_ROOT),\n"
        f"{canonical_arg}"
        "    '--cache-root', str(CACHE_ROOT),\n"
        "    '--start-date', manifest['train_start_date'],\n"
        "    '--end-date', manifest['test_end_date'],\n"
        "    '--tickers', manifest.get('tickers', 'ALL'),\n"
        f"{extra_preprocess_args}"
        "    '--processes', str(PREPROCESS_PROCESSES),\n"
        "    '--normalize-processes', str(NORMALIZE_PROCESSES),\n"
        "    '--quote-normalize-processes', str(QUOTE_NORMALIZE_PROCESSES),\n"
        "    '--trade-normalize-processes', str(TRADE_NORMALIZE_PROCESSES),\n"
        "    '--canonical-processes', str(CANONICAL_PROCESSES),\n"
        "    '--chunk-processes', str(CHUNK_PROCESSES),\n"
        "    '--heartbeat-seconds', str(PREPROCESS_HEARTBEAT_SECONDS),\n"
        "    '--max-pending', str(PREPROCESS_MAX_PENDING),\n"
        "    '--polars-threads-per-process', str(POLARS_THREADS_PER_PROCESS),\n"
        "]\n"
        "add_rebuild_flag(preprocess_args, REBUILD_PREPROCESS_CACHE)\n"
        "add_build_chunks_flag(preprocess_args, BUILD_EVENT_CHUNKS)\n"
        "add_verbose_worker_steps_flag(preprocess_args, VERBOSE_WORKER_STEPS)\n"
        "\n"
        f"train_py = LOCAL_CODE_ROOT / 'research' / 'inhouse_transformer' / {version!r} / 'train.py'\n"
        "args = [\n"
        "    '--flatfiles-root', str(FLATFILES_ROOT),\n"
        f"{canonical_arg}"
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
        "generated = {}\n"
        "if PROFILE_ENABLED:\n"
        "    generated['profile'] = write_command_script('profile', profile_py, profile_args)\n"
        "generated['preprocess'] = write_command_script('preprocess', preprocess_py, preprocess_args)\n"
        "generated['train'] = write_command_script('train', train_py, args)\n"
        "commands_md = LOCAL_CODE_ROOT / 'WORKSTATION_COMMANDS.md'\n"
        "lines = [\n"
        "    f'# {VERSION} workstation commands',\n"
        "    '',\n"
        "    'Run these from PowerShell in order:',\n"
        "    '',\n"
        "    f'1. `& {ps_quote(install_ps1)}` if dependencies are missing.',\n"
        "]\n"
        "for index, key in enumerate(['profile', 'preprocess', 'train'], start=2):\n"
        "    if key in generated:\n"
        "        lines.append(f'{index}. `& {ps_quote(generated[key][0])}`')\n"
        "lines.extend(['', f'Logs: `{RUN_LOG_DIR}`', ''])\n"
        "commands_md.write_text('\\n'.join(lines), encoding='utf-8')\n"
        "print('\\n'.join(lines))\n"
        "print('\\nCommand summary written to:', commands_md)\n"
        "for label, (ps1_path, log_path, command) in generated.items():\n"
        "    print_script(label, ps1_path, log_path, command)\n"
    )


def write_workstation_readme(path: Path, version: str, manifest: dict[str, Any]) -> None:
    path.write_text(
        f"# Workstation package for {version}\n\n"
        "Open `train_workstation.ipynb` from the same Drive folder. The notebook extracts "
        "the zip to a local runtime directory and generates PowerShell scripts for profiling, "
        "preprocessing, and training.\n\n"
        "Preferred workflow: run the generated `run_profile.ps1`, `run_preprocess.ps1`, and "
        "`run_train.ps1` scripts from a terminal so output is fully visible and restartable.\n\n"
        "The notebook does not start long-running jobs. It only extracts the code and generates scripts. "
        "Logs are written to `workstation_logs/*.log` under the model output root.\n\n"
        f"Default W&B project: `{manifest['wandb_project']}`\n\n"
        "For performance, keep flatfiles and cache output on local SSD/NVMe and update `FLATFILES_ROOT` "
        "in the notebook if needed.\n",
        encoding="utf-8",
    )


def write_package_zip(zip_path: Path, version: str, notebook_path: Path, manifest_path: Path, readme_path: Path) -> None:
    include_files = [
        REPO_ROOT / "src" / "data_provider" / "config.py",
    ]
    if version == "v21":
        include_files.extend(
            [
                REPO_ROOT / "research" / "inhouse_transformer" / "model_artifacts.py",
                REPO_ROOT / "research" / "inhouse_transformer" / "v14" / "data.py",
                REPO_ROOT / "research" / "inhouse_transformer" / "v14" / "metrics.py",
                REPO_ROOT / "research" / "inhouse_transformer" / "v14" / "config.py",
            ]
        )
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

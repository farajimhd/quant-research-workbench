from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.runtime_paths import frontend_review_root, frontend_runtime_root


SOURCE_ROOT = PROJECT_ROOT / "frontend"
SYNCED_DIRECTORIES = ("src", "scripts")
SYNCED_FILES = (
    "index.html",
    "package-lock.json",
    "package.json",
    "tsconfig.json",
    "vite.config.ts",
)
LOCK_MARKER = ".package-lock.sha256"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the frontend from its external dependency/build workspace."
    )
    parser.add_argument(
        "command",
        choices=("install", "dev", "build", "preview", "ui:review", "ui:review:full"),
    )
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=frontend_runtime_root(),
        help="External frontend workspace (default: %(default)s).",
    )
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help="Use the existing external node_modules without checking the lockfile marker.",
    )
    args, command_args = parser.parse_known_args()
    args.command_args = command_args
    return args


def validate_runtime_root(runtime_root: Path) -> Path:
    resolved = runtime_root.expanduser().resolve()
    project = PROJECT_ROOT.resolve()
    if resolved == project or project in resolved.parents:
        raise ValueError(f"Frontend runtime must be outside the repository: {resolved}")
    return resolved


def sync_frontend(runtime_root: Path) -> None:
    runtime_root.mkdir(parents=True, exist_ok=True)
    for name in SYNCED_FILES:
        shutil.copy2(SOURCE_ROOT / name, runtime_root / name)
    for name in SYNCED_DIRECTORIES:
        target = runtime_root / name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(SOURCE_ROOT / name, target)


def lock_digest() -> str:
    return hashlib.sha256((SOURCE_ROOT / "package-lock.json").read_bytes()).hexdigest()


def npm_executable() -> str:
    executable = shutil.which("npm.cmd") or shutil.which("npm")
    if not executable:
        raise FileNotFoundError("npm was not found on PATH.")
    return executable


def ensure_dependencies(runtime_root: Path, *, force: bool = False) -> None:
    marker = runtime_root / LOCK_MARKER
    expected = lock_digest()
    current = marker.read_text(encoding="utf-8").strip() if marker.exists() else ""
    if not force and (runtime_root / "node_modules").is_dir() and current == expected:
        return
    subprocess.run([npm_executable(), "ci"], cwd=runtime_root, check=True)
    marker.write_text(expected + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    runtime_root = validate_runtime_root(args.runtime_root)
    sync_frontend(runtime_root)
    print(f"Frontend source:  {SOURCE_ROOT}")
    print(f"Frontend runtime: {runtime_root}")

    if args.command == "install":
        ensure_dependencies(runtime_root, force=True)
        return 0
    if not args.skip_install:
        ensure_dependencies(runtime_root)

    command_args = list(args.command_args)
    if command_args[:1] == ["--"]:
        command_args = command_args[1:]
    environment = os.environ.copy()
    environment.setdefault("QW_FRONTEND_REVIEW_ROOT", str(frontend_review_root()))
    command = [npm_executable(), "run", args.command]
    if command_args:
        command.extend(["--", *command_args])
    print("Running:", subprocess.list2cmdline(command))
    return subprocess.run(
        command,
        cwd=runtime_root,
        env=environment,
        check=False,
    ).returncode


if __name__ == "__main__":
    sys.exit(main())

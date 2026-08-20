from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

sys.dont_write_bytecode = True

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
DEV_SYNC_INTERVAL_SECONDS = 0.35


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


def _files_match(source: Path, target: Path) -> bool:
    if not target.is_file():
        return False
    source_stat = source.stat()
    target_stat = target.stat()
    return (
        source_stat.st_size == target_stat.st_size
        and source_stat.st_mtime_ns == target_stat.st_mtime_ns
    )


def _sync_file_if_changed(source: Path, target: Path) -> bool:
    if _files_match(source, target):
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return True


def _sync_directory_incrementally(source_root: Path, target_root: Path) -> int:
    copied = 0
    source_files = {
        source.relative_to(source_root)
        for source in source_root.rglob("*")
        if source.is_file()
    }
    target_files = {
        target.relative_to(target_root)
        for target in target_root.rglob("*")
        if target.is_file()
    } if target_root.is_dir() else set()

    for relative_path in sorted(target_files - source_files, reverse=True):
        (target_root / relative_path).unlink()
    for relative_path in sorted(source_files):
        copied += int(
            _sync_file_if_changed(
                source_root / relative_path,
                target_root / relative_path,
            )
        )
    if target_root.is_dir():
        for directory in sorted(
            (path for path in target_root.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        ):
            try:
                directory.rmdir()
            except OSError:
                pass
    return copied + len(target_files - source_files)


def sync_frontend_incrementally(runtime_root: Path) -> int:
    """Mirror source changes without disturbing external dependencies or Vite state."""
    changed = 0
    for name in SYNCED_FILES:
        changed += int(
            _sync_file_if_changed(SOURCE_ROOT / name, runtime_root / name)
        )
    for name in SYNCED_DIRECTORIES:
        changed += _sync_directory_incrementally(
            SOURCE_ROOT / name,
            runtime_root / name,
        )
    return changed


def mirror_frontend_source(runtime_root: Path, stop_event: threading.Event) -> None:
    while not stop_event.wait(DEV_SYNC_INTERVAL_SECONDS):
        try:
            changed = sync_frontend_incrementally(runtime_root)
        except OSError as exc:
            print(
                f"Frontend source mirror failed; retrying: {exc}",
                file=sys.stderr,
                flush=True,
            )
            continue
        if changed:
            print(
                f"Frontend source mirror refreshed {changed} path(s).",
                flush=True,
            )


def lock_digest() -> str:
    return hashlib.sha256((SOURCE_ROOT / "package-lock.json").read_bytes()).hexdigest()


def npm_command() -> list[str]:
    executable = shutil.which("npm.cmd") or shutil.which("npm")
    if not executable:
        raise FileNotFoundError("npm was not found on PATH.")
    npm_path = Path(executable).resolve()
    if os.name != "nt" or npm_path.suffix.lower() not in {".cmd", ".bat"}:
        return [str(npm_path)]

    node = shutil.which("node.exe") or shutil.which("node")
    if not node:
        adjacent_node = npm_path.parent / "node.exe"
        if adjacent_node.is_file():
            node = str(adjacent_node)
    if not node:
        raise FileNotFoundError("node.exe was not found for the Windows npm CLI.")

    npm_cli = npm_path.parent / "node_modules" / "npm" / "bin" / "npm-cli.js"
    npm_prefix = npm_path.parent / "node_modules" / "npm" / "bin" / "npm-prefix.js"
    if npm_prefix.is_file():
        prefix_result = subprocess.run(
            [node, str(npm_prefix)],
            check=False,
            capture_output=True,
            text=True,
        )
        if prefix_result.returncode == 0 and prefix_result.stdout.strip():
            prefixed_cli = (
                Path(prefix_result.stdout.strip())
                / "node_modules"
                / "npm"
                / "bin"
                / "npm-cli.js"
            )
            if prefixed_cli.is_file():
                npm_cli = prefixed_cli
    if not npm_cli.is_file():
        raise FileNotFoundError(
            f"npm-cli.js was not found beside the Windows npm wrapper: {npm_path}"
        )
    # Invoking npm.cmd would insert cmd.exe into the service tree. On Ctrl+C,
    # that batch wrapper prompts `Terminate batch job (Y/N)?` and blocks the
    # registered graceful shutdown. Direct Node execution preserves npm's CLI
    # semantics without an interactive batch boundary.
    return [str(Path(node).resolve()), str(npm_cli.resolve())]


def ensure_dependencies(runtime_root: Path, *, force: bool = False) -> None:
    marker = runtime_root / LOCK_MARKER
    expected = lock_digest()
    current = marker.read_text(encoding="utf-8").strip() if marker.exists() else ""
    if not force and (runtime_root / "node_modules").is_dir() and current == expected:
        return
    subprocess.run([*npm_command(), "ci"], cwd=runtime_root, check=True)
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
    command = [*npm_command(), "run", args.command]
    if command_args:
        command.extend(["--", *command_args])
    print("Running:", subprocess.list2cmdline(command))
    mirror_stop = threading.Event()
    mirror_thread: threading.Thread | None = None
    if args.command == "dev":
        mirror_thread = threading.Thread(
            target=mirror_frontend_source,
            args=(runtime_root, mirror_stop),
            daemon=True,
            name="frontend-source-mirror",
        )
        mirror_thread.start()
    try:
        return subprocess.run(
            command,
            cwd=runtime_root,
            env=environment,
            check=False,
        ).returncode
    finally:
        mirror_stop.set()
        if mirror_thread is not None:
            mirror_thread.join(timeout=2.0)


if __name__ == "__main__":
    sys.exit(main())

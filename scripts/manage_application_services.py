"""Start, stop, restart, or inspect the application-facing service stack.

This composes the existing ownership-aware PowerShell launchers. It does not
replace the separately managed News/SEC/Reference/IBKR gateway bundle.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


sys.dont_write_bytecode = True

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
DEFAULT_RUNTIME_ROOT = Path(os.environ.get("BAR_GPT_RUNTIME_ROOT", r"D:\TradingML\runtimes\bar_gpt_service"))
DEFAULT_RELEASE_MANIFEST = Path(
    os.environ.get(
        "BAR_GPT_RELEASE_MANIFEST",
        str(DEFAULT_RUNTIME_ROOT / "configuration" / "releases.json"),
    )
)
SERVICES = (
    ("QMD Live", 8795, "http://127.0.0.1:8795/health"),
    ("QMD History", 8801, "http://127.0.0.1:8801/health"),
    ("Backend", 8000, "http://127.0.0.1:8000/api/health"),
    ("Frontend", 5173, "http://127.0.0.1:5173/"),
    ("BarGPT", 8805, "http://127.0.0.1:8805/health"),
)


def _powershell() -> str:
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    candidate = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if candidate.is_file():
        return str(candidate)
    return "powershell.exe"


def _run_script(name: str, arguments: Iterable[str]) -> None:
    command = [
        _powershell(),
        "-NoLogo",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(SCRIPTS / name),
        *arguments,
    ]
    completed = subprocess.run(command, cwd=REPO_ROOT, check=False)
    if completed.returncode:
        raise RuntimeError(f"{name} failed with exit code {completed.returncode}")


def _port_open(port: int, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _http_ready(url: str, timeout: float = 2.0) -> tuple[bool, str]:
    try:
        request = Request(url, headers={"User-Agent": "workspace-service-manager/1"})
        with urlopen(request, timeout=timeout) as response:
            response.read(256)
            return 200 <= response.status < 400, f"HTTP {response.status}"
    except HTTPError as error:
        return False, f"HTTP {error.code}"
    except (URLError, TimeoutError, OSError) as error:
        return False, str(error.reason if isinstance(error, URLError) else error)


def _wait_ready(names: set[str], timeout_seconds: int) -> None:
    pending = {name: url for name, _, url in SERVICES if name in names}
    deadline = time.monotonic() + timeout_seconds
    last_report = 0.0
    while pending and time.monotonic() < deadline:
        for name, url in list(pending.items()):
            ready, _ = _http_ready(url)
            if ready:
                print(f"[ready]   {name}")
                del pending[name]
        now = time.monotonic()
        if pending and now - last_report >= 10:
            print(f"[waiting] {', '.join(pending)}")
            last_report = now
        if pending:
            time.sleep(1)
    if pending:
        raise RuntimeError(
            f"readiness timeout after {timeout_seconds}s: {', '.join(pending)}"
        )


def _validate_manifest(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise RuntimeError(
            "approved BarGPT release manifest is missing: "
            f"{resolved}. Create the promoted catalog or pass --release-manifest."
        )
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"cannot read BarGPT release manifest {resolved}: {error}") from error
    if not isinstance(payload, list) or not payload:
        raise RuntimeError(f"BarGPT release manifest must be a non-empty JSON array: {resolved}")
    required = {"model_id", "version", "checkpoint", "checkpoint_sha256", "contract_hash", "role"}
    for index, row in enumerate(payload):
        missing = required - set(row) if isinstance(row, dict) else required
        if missing:
            raise RuntimeError(
                f"BarGPT release manifest row {index} is missing immutable identity fields: "
                + ", ".join(sorted(missing))
            )
    return resolved


def _prepare_manifest_if_needed(path: Path, checkpoint_root: Path | None) -> None:
    if path.is_file() or checkpoint_root is None:
        return
    print(f"[prepare] BarGPT release manifest from checkpoint root {checkpoint_root}")
    completed = subprocess.run(
        [
            sys.executable,
            "-B",
            str(SCRIPTS / "prepare_bar_gpt_release_manifest.py"),
            "--checkpoint-root",
            str(checkpoint_root),
            "--output",
            str(path),
        ],
        cwd=REPO_ROOT,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError("BarGPT release-manifest preparation failed")


def status() -> bool:
    all_ready = True
    print("Application service status")
    for name, port, url in SERVICES:
        listening = _port_open(port)
        ready, detail = _http_ready(url) if listening else (False, "port closed")
        state = "ready" if ready else ("listening, not ready" if listening else "stopped")
        print(f"  {name:<12} {state:<20} port={port} {detail}")
        all_ready &= ready
    return all_ready


def start(
    release_manifest: Path,
    timeout_seconds: int,
    terminal_target: str,
    checkpoint_root: Path | None = None,
    python_exe: Path | None = None,
) -> None:
    _prepare_manifest_if_needed(release_manifest, checkpoint_root)
    manifest = _validate_manifest(release_manifest)
    qmd_started_here = False
    workspace_started_here = False
    try:
        qmd_ready, _ = _http_ready(SERVICES[0][2]) if _port_open(8795) else (False, "")
        if qmd_ready:
            print("[active]  QMD Live is already healthy; preserving its stream.")
        elif _port_open(8795):
            raise RuntimeError("QMD Live port 8795 is occupied but /health is not ready")
        else:
            print("[start]   QMD Live")
            _run_script("start_qmd_live_gateway.ps1", ["-TerminalTarget", terminal_target])
            qmd_started_here = True
            _wait_ready({"QMD Live"}, timeout_seconds)

        workspace_ports = [port for _, port, _ in SERVICES[1:]]
        conflicts = [str(port) for port in workspace_ports if _port_open(port)]
        if conflicts:
            raise RuntimeError(
                "workspace startup requires ports 8801, 8000, 5173, and 8805 to be free; "
                f"occupied: {', '.join(conflicts)}. Run this script with 'restart'."
            )
        print("[start]   QMD History, Backend, Frontend, and BarGPT")
        _run_script(
            "start_workspace_services.ps1",
            [
                "-NoBackendReload",
                "-WithBarGpt",
                "-BarGptReleaseManifest",
                str(manifest),
                *(["-PythonExe", str(python_exe.expanduser().resolve())] if python_exe else []),
                "-TerminalTarget",
                terminal_target,
            ],
        )
        workspace_started_here = True
        _wait_ready({name for name, _, _ in SERVICES[1:]}, timeout_seconds)
        print("[complete] Application stack is ready.")
    except Exception:
        if workspace_started_here:
            print("[rollback] Stopping the partially started workspace stack.")
            _run_script("stop_workspace_services.ps1", [])
        if qmd_started_here:
            print("[rollback] Stopping QMD Live started by this attempt.")
            _run_script("stop_qmd_live_gateway.ps1", [])
        raise


def stop(*, keep_qmd_live: bool) -> None:
    print("[stop]    Workspace services, including BarGPT")
    _run_script("stop_workspace_services.ps1", [])
    if keep_qmd_live:
        print("[active]  QMD Live preserved by request.")
    else:
        print("[stop]    QMD Live")
        _run_script("stop_qmd_live_gateway.ps1", [])
    print("[complete] Stop sequence finished; foreign port owners were not adopted.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Manage QMD Live plus the workspace and BarGPT service stack."
    )
    parser.add_argument("action", choices=("start", "stop", "restart", "status"))
    parser.add_argument("--release-manifest", type=Path, default=DEFAULT_RELEASE_MANIFEST)
    parser.add_argument(
        "--checkpoint-root",
        type=Path,
        default=Path(os.environ["BAR_GPT_CHECKPOINT_ROOT"]) if os.environ.get("BAR_GPT_CHECKPOINT_ROOT") else None,
        help="Authoritative BarGPT runtime root used only to create a missing immutable release manifest.",
    )
    parser.add_argument(
        "--python-exe",
        type=Path,
        default=Path(os.environ["WORKSPACE_PYTHON_EXE"]) if os.environ.get("WORKSPACE_PYTHON_EXE") else None,
        help="Python runtime for Backend and BarGPT; use a CUDA-capable environment for GPU serving.",
    )
    parser.add_argument("--timeout-seconds", type=int, default=600)
    parser.add_argument("--terminal-target", choices=("Auto", "Caller", "Named"), default="Named")
    parser.add_argument(
        "--keep-qmd-live",
        action="store_true",
        help="On stop, preserve QMD Live. Restart preserves a healthy QMD Live by default.",
    )
    args = parser.parse_args()
    try:
        if args.action == "status":
            return 0 if status() else 1
        if args.action == "stop":
            stop(keep_qmd_live=args.keep_qmd_live)
            return 0
        if args.action == "start":
            start(args.release_manifest, args.timeout_seconds, args.terminal_target, args.checkpoint_root, args.python_exe)
            return 0
        if args.action == "restart":
            _prepare_manifest_if_needed(args.release_manifest, args.checkpoint_root)
            _validate_manifest(args.release_manifest)
            stop(keep_qmd_live=True)
            start(args.release_manifest, args.timeout_seconds, args.terminal_target, args.checkpoint_root, args.python_exe)
        return 0
    except (RuntimeError, ValueError) as error:
        print(f"[failed]  {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

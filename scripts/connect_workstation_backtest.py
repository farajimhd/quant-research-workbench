"""Keep a loopback-only SSH connection to the workstation Backtest API.

Strategy 1's credentials and Keeper remain on the workstation. This launcher
does not read or copy either authority to the laptop, and owns only its SSH
tunnel. Start the workstation backend separately with scripts/run_backend.py.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
from time import monotonic, sleep
from urllib.error import URLError
from urllib.request import urlopen


sys.dont_write_bytecode = True
DEFAULT_KEY = Path.home() / ".ssh" / "id_ed25519_codex_workstation"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Connect the laptop frontend to the workstation-owned Backtest API."
    )
    parser.add_argument("--ssh-target", default="mehdi@DESKTOP-SAAI85T")
    parser.add_argument("--identity-file", type=Path, default=DEFAULT_KEY)
    parser.add_argument("--local-port", type=int, default=8000)
    parser.add_argument("--remote-port", type=int, default=8000)
    parser.add_argument("--check-only", action="store_true",
                        help="Verify the tunnel and API, then disconnect.")
    return parser.parse_args(argv)


def _validate(options: argparse.Namespace) -> None:
    if (not options.ssh_target or options.ssh_target.startswith("-")
            or any(char.isspace() for char in options.ssh_target)
            or not 1 <= options.local_port <= 65535
            or not 1 <= options.remote_port <= 65535):
        raise ValueError("SSH target and API ports must be explicit and valid")
    if not options.identity_file.is_file():
        raise FileNotFoundError(
            f"Dedicated workstation SSH key is unavailable: {options.identity_file}")


def _api_ready(port: int) -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1.0) as response:
            return response.status == 200
    except (OSError, URLError):
        return False


def _stop_tunnel(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main(argv: list[str] | None = None) -> int:
    options = parse_args(argv)
    try:
        _validate(options)
        command = [
            "ssh", "-N", "-i", str(options.identity_file),
            "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
            "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=15",
            "-L", f"127.0.0.1:{options.local_port}:127.0.0.1:{options.remote_port}",
            options.ssh_target,
        ]
        # On Windows, Ctrl+C belongs to this launcher. The tunnel is stopped
        # in finally, rather than receiving an independent console interrupt.
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, creationflags=flags)
        try:
            deadline = monotonic() + 15.0
            while monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("SSH tunnel exited before the Backtest API became ready")
                if _api_ready(options.local_port):
                    break
                sleep(0.25)
            else:
                raise RuntimeError(
                    "Workstation Backtest API is not responding through SSH. "
                    "Start scripts/run_backend.py on the workstation and retry.")
            print(f"Workstation Backtest API connected at 127.0.0.1:{options.local_port}.",
                  flush=True)
            if options.check_only:
                return 0
            print("Start the laptop frontend in another terminal:", flush=True)
            print(f"  $env:VITE_API_PROXY_TARGET='http://127.0.0.1:{options.local_port}'",
                  flush=True)
            print("  python -B scripts/run_frontend.py dev", flush=True)
            print("Press Ctrl+C here to disconnect. No workstation service is stopped.",
                  flush=True)
            while process.poll() is None:
                sleep(1.0)
            raise RuntimeError("SSH tunnel disconnected; the laptop Backtest API is unavailable")
        finally:
            _stop_tunnel(process)
    except KeyboardInterrupt:
        print("Workstation Backtest connection closed.")
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Backtest connection failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

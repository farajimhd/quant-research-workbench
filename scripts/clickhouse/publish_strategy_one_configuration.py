"""One-time Candidate 350 -> normalized ARTE Strategy 1 release.

Run on the laptop with --apply after pushing and syncing this same commit.
The old SQLite candidate is read exactly once on the laptop; its compiled
payload travels through authenticated SSH stdin, never a transfer file.
The workstation receiver has a narrow two-table INSERT principal. Dry run
performs no network or ClickHouse writes.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

from pipelines.strategy_one.configuration_migration import SOURCE_CANDIDATE_ID
from pipelines.strategy_one.configuration_publisher import (
    publication_envelope, publish_configuration,
)


WORKSTATION = "DESKTOP-SAAI85T"
WORKSTATION_REPO = r"D:\TradingML\codes\quant-research-workbench-certificate-d8b09dff"
WORKSTATION_PYTHON = r"C:\Users\Mehdi\miniconda3\envs\ml4t\python.exe"
SSH_KEY = Path(r"C:\Users\g835l\.ssh\id_ed25519_codex_workstation")


def _source_envelope() -> dict:
    from src.backend.trading_configuration_service import (
        candidate_runtime_configuration_snapshot,
    )

    source = candidate_runtime_configuration_snapshot(
        "backtest", candidate_id=SOURCE_CANDIDATE_ID)
    return publication_envelope(source)


def _receive() -> None:
    if platform.node().upper() != WORKSTATION:
        raise RuntimeError("Strategy 1 typed publisher is workstation-only")
    raw = sys.stdin.buffer.read(2_000_001)
    if not raw or len(raw) > 2_000_000:
        raise ValueError("Strategy 1 transfer exceeded its bounded input")
    envelope = json.loads(raw)
    from research.mlops.clickhouse import ClickHouseHttpClient
    from scripts.clickhouse.install_market_day_certificate_layout import (
        workstation_clickhouse_url,
    )
    from scripts.clickhouse.provision_strategy_one_configuration_publisher import (
        PRINCIPAL, SECRET_PATH,
    )
    from src.trading_runtime.keeper_session import open_workstation_keeper_session

    values = dict(line.split("=", 1) for line in
                  SECRET_PATH.read_text(encoding="utf-8").splitlines()
                  if "=" in line)
    if (values.get("STRATEGY_ONE_CONFIGURATION_USER") != PRINCIPAL
            or len(values.get("STRATEGY_ONE_CONFIGURATION_PASSWORD", "")) < 40):
        raise RuntimeError("Strategy 1 publisher credential is incomplete")
    with closing(ClickHouseHttpClient(
            workstation_clickhouse_url(), PRINCIPAL,
            values["STRATEGY_ONE_CONFIGURATION_PASSWORD"],
            timeout_seconds=90, persistent=True)) as client, closing(
                open_workstation_keeper_session()) as keeper:
        if client.execute("SELECT currentUser()").strip() != PRINCIPAL:
            raise RuntimeError("Strategy 1 publisher authenticated as another user")
        token = publish_configuration(client, keeper.client, envelope)
    print(f"Strategy 1 typed release certified: {token}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--receive-stdin", action="store_true",
                        help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.receive_stdin:
        if args.apply:
            raise ValueError("Receiver has one explicit invocation mode")
        try:
            _receive()
        except Exception as exc:
            # Preserve the diagnostic class without exposing an HTTP request,
            # ClickHouse response, transfer payload, or workstation secret.
            safe = getattr(exc, "safe_diagnostic", None)
            status = getattr(exc, "status_code", None)
            suffix = (f":{safe}" if isinstance(safe, str) else
                      f":HTTP{status}" if type(status) is int else "")
            print(f"Strategy 1 receiver failed: {type(exc).__name__}{suffix}",
                  file=sys.stderr, flush=True)
            raise SystemExit(1) from None
        return
    if platform.node().upper() == WORKSTATION:
        raise RuntimeError("The Candidate 350 source must be read on the laptop")
    envelope = _source_envelope()
    print("Strategy 1 source Candidate 350; "
          f"{envelope['node_count']} typed nodes; "
          f"payload seal {envelope['payload_hash']}", flush=True)
    if not args.apply:
        print("Plan only: no workstation transfer or ClickHouse write", flush=True)
        return
    if not SSH_KEY.is_file():
        raise RuntimeError("Dedicated workstation SSH key is missing")
    remote_command = (
        f"cd /d {WORKSTATION_REPO} && {WORKSTATION_PYTHON} -B "
        r"scripts\clickhouse\publish_strategy_one_configuration.py "
        "--receive-stdin"
    )
    command = ["ssh", "-i", str(SSH_KEY),
               f"mehdi@{WORKSTATION}", remote_command]
    result = subprocess.run(
        command, input=json.dumps(envelope, separators=(",", ":"),
                                  ensure_ascii=True, allow_nan=False).encode("utf-8"),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=180, check=False)
    if result.returncode:
        diagnostic = result.stderr.decode("utf-8", errors="replace").strip()
        match = re.search(r"Strategy 1 receiver failed: ([A-Za-z]+)"
                          r"(:[A-Za-z0-9_:]+)?", diagnostic)
        if match is not None:
            diagnostic = "".join(part or "" for part in match.groups())
        else:
            diagnostic = "workstation transport or pre-receiver failure " \
                f"(stderr_bytes={len(result.stderr)}, stdout_bytes={len(result.stdout)})"
        raise RuntimeError(f"Workstation Strategy 1 publication failed: {diagnostic}")
    print(result.stdout.decode("utf-8", errors="replace").strip(), flush=True)


if __name__ == "__main__":
    main()

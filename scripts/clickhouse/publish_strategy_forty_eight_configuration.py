"""Plan or publish immutable Backtest-only Strategy 48 using existing typed tables.

Requires a clean, committed, approved source revision on both machines. Dry-run
is the default. Publication is coverage-last with the existing narrow principal.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from pipelines.strategy_one.configuration_publisher import publish_configuration, _verified_numbered_envelope
from pipelines.strategy_one.strategy_forty_eight_configuration import compile_strategy_forty_eight_configuration
from src.backend.backtest_strategy_one_configuration import certify_numbered_configuration
from src.backend.historical_runtime_versions import backend_source_fingerprint
from src.backend.backtest_fixed_v4_certification import certify_numbered_fixed_v4_projection


def approved_source(commit: str) -> str:
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if commit != head:
        raise ValueError("Approved code commit must equal this checkout HEAD")
    dirty = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all", "--",
                                    "src/backend", "src/trading_runtime", "pipelines/strategy_one",
                                    "scripts/clickhouse/publish_strategy_forty_eight_configuration.py"],
                                   cwd=ROOT, text=True)
    if dirty.strip():
        raise ValueError("Commit the reviewed implementation before constructing the approved release")
    certify_numbered_fixed_v4_projection(48)
    return backend_source_fingerprint()


def receive() -> None:
    if platform.node().upper() != "DESKTOP-SAAI85T":
        raise RuntimeError("Publication receiver is workstation-only")
    raw = sys.stdin.buffer.read(2_000_001)
    if not raw or len(raw) > 2_000_000:
        raise ValueError("Configuration transfer exceeds its bounded input")
    envelope = json.loads(raw)
    payload, _ = _verified_numbered_envelope(envelope)
    if payload["strategy"]["strategy_number"] != 48:
        raise ValueError("Strategy 48 receiver requires number 48")
    manifest = payload["strategy"]["numbered_release"]
    if approved_source(manifest["approved_code_commit"]) != manifest["approved_code_fingerprint"]:
        raise ValueError("Workstation source differs from approved Strategy 48 code")
    from research.mlops.clickhouse import ClickHouseHttpClient
    from scripts.clickhouse.install_market_day_certificate_layout import workstation_clickhouse_url
    from scripts.clickhouse.provision_strategy_one_configuration_publisher import PRINCIPAL, SECRET_PATH, _grant_set, _GRANTS
    from src.trading_runtime.keeper_session import open_workstation_keeper_session
    values = dict(line.split("=", 1) for line in SECRET_PATH.read_text(encoding="utf-8").splitlines() if "=" in line)
    if values.get("STRATEGY_ONE_CONFIGURATION_USER") != PRINCIPAL:
        raise ValueError("Configuration publisher identity differs")
    with closing(ClickHouseHttpClient(workstation_clickhouse_url(), PRINCIPAL,
            values["STRATEGY_ONE_CONFIGURATION_PASSWORD"], timeout_seconds=90, persistent=True)) as client, closing(open_workstation_keeper_session()) as keeper:
        if client.execute("SELECT currentUser()").strip() != PRINCIPAL or _grant_set(client) != _GRANTS:
            raise ValueError("Configuration publisher lacks exact narrow grants")
        token = publish_configuration(client, keeper.client, envelope)
    print(f"Strategy 48 published and certified: {token}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approved-code-commit")
    parser.add_argument("--approval-reference")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--workstation-repo", help="already-synchronized checkout, required for publication")
    parser.add_argument("--receive-stdin", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.receive_stdin:
            if args.apply:
                raise ValueError("Receiver has a single explicit mode")
            receive()
            return 0
        if not args.approved_code_commit or not args.approval_reference:
            raise ValueError("Both --approved-code-commit and --approval-reference are required")
        fingerprint = approved_source(args.approved_code_commit)
        from scripts.clickhouse.smoke_strategy_one_backtest import _load_private_credentials
        from src.backend.backtest_v3_clients import v3_client
        _load_private_credentials()
        with closing(v3_client("read")) as reader:
            source = certify_numbered_configuration(reader, 42)
        envelope = compile_strategy_forty_eight_configuration(source,
            approved_code_commit=args.approved_code_commit,
            approved_code_fingerprint=fingerprint, approval_reference=args.approval_reference)
        print(f"Strategy 48 plan: {envelope['node_count']} typed nodes; payload {envelope['payload_hash']}", flush=True)
        if not args.apply:
            print("Plan complete; no publication performed.")
            return 0
        # The exact paths are explicit and constrained before Windows SSH shell use.
        import re
        if not args.workstation_repo or not re.fullmatch(r"[A-Za-z]:[\\/][A-Za-z0-9_\\/.-]+", args.workstation_repo):
            raise ValueError("--workstation-repo must be an explicit safe absolute Windows path without spaces")
        from scripts.clickhouse.publish_strategy_one_configuration import SSH_KEY, WORKSTATION_PYTHON
        if not SSH_KEY.is_file():
            raise ValueError("Dedicated workstation SSH key is unavailable")
        command = (f"cd /d {args.workstation_repo} && {WORKSTATION_PYTHON} -B "
                   "scripts\\clickhouse\\publish_strategy_forty_eight_configuration.py --receive-stdin")
        result = subprocess.run(["ssh", "-i", str(SSH_KEY), "mehdi@DESKTOP-SAAI85T", command],
            input=json.dumps(envelope, allow_nan=False).encode(), capture_output=True, timeout=180)
        if result.returncode:
            raise RuntimeError("Workstation publication failed; reconcile the immutable release before retry")
        print(result.stdout.decode("utf-8").strip())
        return 0
    except Exception as exc:
        # Avoid printing raw server/request exceptions carrying credentials.
        message = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        print(f"Strategy 48 publication failed: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

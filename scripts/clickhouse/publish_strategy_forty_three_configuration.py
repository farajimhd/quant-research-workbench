"""Publish the independent Strategy 43 release from a verified committed archive."""
import argparse
from contextlib import closing
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import sys

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.backend.backtest_strategy_forty_three_configuration import compile_configuration
from src.backend.backtest_strategy_forty_three_certification import certify_projection
from src.backend.historical_runtime_versions import backend_source_fingerprint
from pipelines.strategy_one.configuration_publisher import publish_configuration


def approved_archive(path):
    receipt = json.loads(path.read_text(encoding="utf-8"))
    if not receipt.get("commit") or len(receipt["commit"]) != 40 or not receipt.get("files"):
        raise ValueError("Deployment receipt lacks its committed source and file hashes")
    expected = receipt["files"]
    directories = ("src", "pipelines", "scripts", "services", "research/mlops", "research/reaction_levels")
    actual = {p.relative_to(ROOT).as_posix(): sha256(p.read_bytes()).hexdigest()
              for directory in directories for p in (ROOT / directory).rglob("*") if p.is_file()}
    if actual != expected:
        raise ValueError("Workstation source differs from the exact committed archive receipt")
    certify_projection()
    return receipt["commit"], backend_source_fingerprint()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployment-receipt", required=True, type=Path)
    parser.add_argument("--approval-reference", default="User-approved Strategy 43 Sep3 premarket comparison")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if platform.node().upper() != "DESKTOP-SAAI85T":
        raise RuntimeError("Strategy 43 release publication requires the workstation")
    if not Path("D:/TradingML/runtimes").is_dir():
        raise RuntimeError("Required runtime root is unavailable")
    commit, fingerprint = approved_archive(args.deployment_receipt)
    envelope = compile_configuration(approved_code_commit=commit,
        approved_code_fingerprint=fingerprint, approval_reference=args.approval_reference)
    print(f"Strategy 43 | immutable release | {envelope['node_count']} typed nodes | {'APPLY' if args.apply else 'CHECK'}", flush=True)
    if not args.apply:
        print("Verified committed source; no publication performed.", flush=True)
        return
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
    print(f"Strategy 43 release certified | completed 1 | active 0 | queued 0 | failed 0 | {token}", flush=True)


if __name__ == "__main__":
    main()

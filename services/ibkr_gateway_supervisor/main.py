from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from research.mlops.env import discover_env_files, load_env_files
from services.ibkr_gateway_supervisor.config import IbkrGatewayConfig
from services.ibkr_gateway_supervisor.login import run_playwright_login
from services.ibkr_gateway_supervisor.supervisor import IbkrGatewaySupervisor


REPO_ROOT = Path(__file__).resolve().parents[2]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="IBKR Client Portal Gateway supervisor.")
    parser.add_argument("--account", default="paper", help="Configured account key. Starts with paper.")
    parser.add_argument("--check-only", action="store_true", help="Verify config, gateway reachability, auth status, and account access once.")
    parser.add_argument("--login-once", action="store_true", help="Use headed Playwright to log in once, then verify auth/account access.")
    parser.add_argument("--no-launch", action="store_true", help="Do not start bin/run.bat if the gateway is unavailable.")
    parser.add_argument("--headless", action="store_true", help="Run the Playwright login helper headless.")
    return parser.parse_args()


def main() -> None:
    load_env_files(discover_env_files(REPO_ROOT))
    args = parse_args()
    if args.no_launch:
        import os

        os.environ["IBKR_GATEWAY_LAUNCH"] = "false"
    if args.headless:
        import os

        os.environ["IBKR_GATEWAY_LOGIN_HEADLESS"] = "true"
    config = IbkrGatewayConfig.from_env(account_key=args.account)
    try:
        if args.check_only:
            print(json.dumps(config.public_dict(), indent=2, sort_keys=True), flush=True)
            raise SystemExit(IbkrGatewaySupervisor(config).check_once())
        if args.login_once:
            raise SystemExit(0 if asyncio.run(run_playwright_login(config)) else 1)
        IbkrGatewaySupervisor(config).run_forever()
    except RuntimeError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, sort_keys=True), flush=True)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import json
import sys

from research.mlops.clickhouse import discover_clickhouse_env_files
from research.mlops.env import load_env_files, secret_status
from services.reference_gateway.audit import run_reference_audit, write_report
from services.reference_gateway.config import ReferenceGatewayConfig
from services.reference_gateway.policy import evaluate_write_policy
from services.reference_gateway.tradability import tradability_rule_markdown


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reference gateway audit and sync planner.")
    parser.add_argument("--write-report", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--print-rules", action="store_true", help="Print the hard tradability blocking rules.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_env_files(discover_clickhouse_env_files())
    if args.print_rules:
        print(tradability_rule_markdown())
        return
    config = ReferenceGatewayConfig.from_env()
    write_policy = evaluate_write_policy(config)
    print("=" * 96, flush=True)
    print("Reference Gateway audit", flush=True)
    print(f"database={config.clickhouse_database} execute={config.execute} report_root={config.report_root_win}", flush=True)
    print(
        "write_policy="
        f"allowed={write_policy.writes_allowed} "
        f"active_window={write_policy.active_collection_window} "
        f"window={write_policy.window_label} "
        f"reason={write_policy.reason}",
        flush=True,
    )
    print(
        "secret_status="
        + json.dumps(
            secret_status(
                [
                    "REFERENCE_CLICKHOUSE_URL",
                    "REAL_LIVE_CLICKHOUSE_WRITE_URL",
                    "REAL_LIVE_CLICKHOUSE_WRITE_USER",
                    "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD",
                    "CLICKHOUSE_WORKSTATION_USER",
                    "CLICKHOUSE_WORKSTATION_PASSWORD",
                    "MASSIVE_API_KEY",
                ]
            ),
            sort_keys=True,
        ),
        flush=True,
    )
    print("=" * 96, flush=True)
    if config.execute and not write_policy.writes_allowed:
        print(
            "Reference writes are blocked during active market collection hours. "
            "Run after hours or set REFERENCE_GATEWAY_MARKET_HOURS_WRITE_OVERRIDE=true "
            "with REFERENCE_GATEWAY_MARKET_HOURS_WRITE_REASON for a required market-hours operation.",
            flush=True,
        )
        sys.exit(2)
    report = run_reference_audit(config)
    report_path = write_report(report, config.report_root_win) if args.write_report else None
    for check in report.checks:
        print(
            f"{check.status.upper():7} {check.severity:7} {check.name:45} count={check.count:,} {check.message}",
            flush=True,
        )
    if report_path:
        print(f"report={report_path}", flush=True)
    print(f"status={report.status} wall_seconds={report.wall_seconds:.2f}", flush=True)
    if report.status == "failed":
        sys.exit(2)


if __name__ == "__main__":
    main()

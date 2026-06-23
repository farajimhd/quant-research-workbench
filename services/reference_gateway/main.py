from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict

from research.mlops.clickhouse import discover_clickhouse_env_files
from research.mlops.env import load_env_files, secret_status
from services.gateway_policy import active_collection_window
from services.reference_gateway.active_tickers import run_active_ticker_plan, write_active_ticker_plan
from services.reference_gateway.audit import run_reference_audit, write_report
from services.reference_gateway.canonical_graph_writer import write_canonical_graph_candidates
from services.reference_gateway.config import ReferenceGatewayConfig
from services.reference_gateway.daemon import run_reference_daemon
from services.reference_gateway.issue_resolution import resolve_stale_active_ticker_issues
from services.reference_gateway.issue_writer import write_active_ticker_mapping_issues, write_graph_mapping_issues
from services.reference_gateway.market_publications import ensure_market_publication_schema
from services.reference_gateway.policy import evaluate_write_policy
from services.reference_gateway.publication_maintenance import run_recent_publication_gap_fill
from services.reference_gateway.publication_rebuild import rebuild_tradable_publications
from services.reference_gateway.table_groups import table_group_markdown
from services.reference_gateway.tradability import tradability_rule_markdown


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reference gateway audit and sync planner.")
    parser.add_argument("--write-report", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--execute", action=argparse.BooleanOptionalAction, default=None, help="Override REFERENCE_GATEWAY_EXECUTE for this run.")
    parser.add_argument("--read-database", default="", help="Canonical source database. Defaults to REFERENCE_* read DB or q_live.")
    parser.add_argument("--write-database", default="", help="Target database for writes. Defaults to REFERENCE_* write DB or q_live.")
    parser.add_argument("--test-write-database", default="", help="Shortcut for temp testing: read from q_live/read DB and write to this temp DB.")
    parser.add_argument(
        "--market-hours-write-override",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Allow execute-mode writes during the active collection window. Requires --market-hours-write-reason.",
    )
    parser.add_argument(
        "--market-hours-write-reason",
        default="",
        help="Auditable reason for market-hours write override.",
    )
    parser.add_argument("--print-rules", action="store_true", help="Print the hard tradability blocking rules.")
    parser.add_argument("--print-table-groups", action="store_true", help="Print reference gateway table ownership groups.")
    parser.add_argument(
        "--active-ticker-check",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Fetch Massive active tickers and compare them with q_live symbols.",
    )
    parser.add_argument(
        "--ensure-market-publication-schema",
        action="store_true",
        help="Create/alter market reference publication and coverage tables before auditing.",
    )
    parser.add_argument(
        "--write-discovered-issues",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="In execute mode, write discovered provider/reference issues to id_mapping_issue_v1.",
    )
    parser.add_argument(
        "--write-canonical-graph",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="In execute mode, insert clean new Massive ticker candidates into the canonical issuer/security/listing/symbol graph.",
    )
    parser.add_argument(
        "--resolve-stale-issues",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="In execute mode, close reference-gateway active ticker issues once the canonical symbol exists.",
    )
    parser.add_argument(
        "--rebuild-tradable",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="In execute mode, rebuild tradable/scanner feature publications before audit and after issue writes.",
    )
    parser.add_argument(
        "--rebuild-tradable-in-test-mode",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Allow step 6 tradable rebuild when read and write databases differ. Only use after cloning required source tables.",
    )
    parser.add_argument("--market-publication-gap-fill", action=argparse.BooleanOptionalAction, default=None, help="Run recent coverage-aware reference publication gap fill after audit in execute mode.")
    parser.add_argument("--daemon", action=argparse.BooleanOptionalAction, default=None, help="Run repeated audit/sync cycles. Active-window cycles are read-only unless an override is supplied.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_env_files(discover_clickhouse_env_files())
    if args.execute is not None:
        os.environ["REFERENCE_GATEWAY_EXECUTE"] = "true" if args.execute else "false"
    if args.market_hours_write_override and not args.market_hours_write_reason.strip():
        print("--market-hours-write-reason is required with --market-hours-write-override.", flush=True)
        sys.exit(2)
    if args.read_database:
        os.environ["REFERENCE_CLICKHOUSE_READ_DATABASE"] = args.read_database
    if args.test_write_database:
        os.environ["REFERENCE_CLICKHOUSE_WRITE_DATABASE"] = args.test_write_database
    elif args.write_database:
        os.environ["REFERENCE_CLICKHOUSE_WRITE_DATABASE"] = args.write_database
    if args.market_hours_write_override is not None:
        os.environ["REFERENCE_GATEWAY_MARKET_HOURS_WRITE_OVERRIDE"] = "true" if args.market_hours_write_override else "false"
    if args.market_hours_write_reason:
        os.environ["REFERENCE_GATEWAY_MARKET_HOURS_WRITE_REASON"] = args.market_hours_write_reason
    if args.write_discovered_issues is not None:
        os.environ["REFERENCE_GATEWAY_WRITE_DISCOVERED_ISSUES"] = "true" if args.write_discovered_issues else "false"
    if args.write_canonical_graph is not None:
        os.environ["REFERENCE_GATEWAY_WRITE_CANONICAL_GRAPH"] = "true" if args.write_canonical_graph else "false"
    if args.resolve_stale_issues is not None:
        os.environ["REFERENCE_GATEWAY_RESOLVE_STALE_ISSUES"] = "true" if args.resolve_stale_issues else "false"
    if args.rebuild_tradable is not None:
        os.environ["REFERENCE_GATEWAY_REBUILD_TRADABLE_ON_EXECUTE"] = "true" if args.rebuild_tradable else "false"
    if args.rebuild_tradable_in_test_mode is not None:
        os.environ["REFERENCE_GATEWAY_REBUILD_TRADABLE_IN_TEST_MODE"] = "true" if args.rebuild_tradable_in_test_mode else "false"
    if args.market_publication_gap_fill is not None:
        os.environ["REFERENCE_GATEWAY_MARKET_PUBLICATION_GAP_FILL_ENABLED"] = "true" if args.market_publication_gap_fill else "false"
    if args.daemon is not None:
        os.environ["REFERENCE_GATEWAY_DAEMON"] = "true" if args.daemon else "false"
    if args.print_rules:
        print(tradability_rule_markdown())
        return
    if args.print_table_groups:
        print(table_group_markdown())
        return
    config = ReferenceGatewayConfig.from_env()
    if config.daemon_loop_enabled:
        run_reference_daemon(config, sys.argv[1:])
        return
    write_policy = evaluate_write_policy(config)
    print("=" * 96, flush=True)
    print("Reference Gateway audit", flush=True)
    print(
        f"read_database={config.clickhouse_read_database} "
        f"write_database={config.clickhouse_write_database} "
        f"test_write_mode={config.test_write_mode} "
        f"execute={config.execute} report_root={config.report_root_win}",
        flush=True,
    )
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
                    "REFERENCE_CLICKHOUSE_READ_DATABASE",
                    "REFERENCE_CLICKHOUSE_WRITE_DATABASE",
                    "REFERENCE_GATEWAY_READ_DATABASE",
                    "REFERENCE_GATEWAY_WRITE_DATABASE",
                    "CLICKHOUSE_WORKSTATION_USER",
                    "CLICKHOUSE_WORKSTATION_PASSWORD",
                    "MASSIVE_API_KEY",
                    "IBKR_CPAPI_BASE_URL",
                    "REFERENCE_GATEWAY_WRITE_DISCOVERED_ISSUES",
                    "REFERENCE_GATEWAY_WRITE_CANONICAL_GRAPH",
                    "REFERENCE_GATEWAY_RESOLVE_STALE_ISSUES",
                    "REFERENCE_GATEWAY_REBUILD_TRADABLE_ON_EXECUTE",
                    "REFERENCE_GATEWAY_REBUILD_TRADABLE_IN_TEST_MODE",
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
    if args.ensure_market_publication_schema:
        if not write_policy.writes_allowed:
            print("market_publication_schema=blocked reason=" + write_policy.reason, flush=True)
            sys.exit(2)
        from research.mlops.clickhouse import ClickHouseHttpClient, default_clickhouse_password

        ensure_market_publication_schema(
            ClickHouseHttpClient(config.clickhouse_url, config.clickhouse_user, default_clickhouse_password()),
            database=config.clickhouse_write_database,
            read_database=config.clickhouse_read_database,
            storage_policy=os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY") or "",
        )
        print(
            "market_publication_schema=ensured "
            f"read_database={config.clickhouse_read_database} "
            f"write_database={config.clickhouse_write_database}",
            flush=True,
        )
    if config.execute and config.resolve_stale_issues:
        resolution = resolve_stale_active_ticker_issues(config)
        print("stale_issue_resolution=" + json.dumps(asdict(resolution), sort_keys=True), flush=True)
    if config.execute and config.rebuild_tradable_on_execute:
        rebuild = rebuild_tradable_publications(config, reason="pre_audit_source_truth_refresh")
        print("tradable_publication_rebuild=" + json.dumps(asdict(rebuild), sort_keys=True), flush=True)
    report = run_reference_audit(config)
    report_path = write_report(report, config.report_root_win) if args.write_report else None
    for check in report.checks:
        print(
            f"{check.status.upper():7} {check.severity:7} {check.name:45} count={check.count:,} {check.message}",
            flush=True,
        )
    if report_path:
        print(f"report={report_path}", flush=True)
    should_check_tickers = args.active_ticker_check if args.active_ticker_check is not None else config.active_ticker_check_enabled
    if should_check_tickers:
        if config.active_ticker_check_market_hours_only and not active_collection_window(service_prefix="REFERENCE"):
            print("active_ticker_check=skipped reason=outside_reference_collection_window", flush=True)
        elif not config.source_massive_enabled:
            print("active_ticker_check=skipped reason=massive_disabled", flush=True)
        else:
            plan = run_active_ticker_plan(config)
            plan_path = write_active_ticker_plan(plan, config.report_root_win) if args.write_report else None
            print(
                "active_ticker_check=done "
                f"provider_rows={plan.provider_rows:,} known_symbols={plan.known_active_symbols:,} "
                f"missing={plan.missing_tickers:,} overview={plan.overview_fetched:,} ibkr={plan.ibkr_searched:,} "
                f"saturated={plan.provider_saturated} wall_seconds={plan.wall_seconds:.2f}",
                flush=True,
            )
            if plan_path:
                print(f"active_ticker_report={plan_path}", flush=True)
            if config.execute:
                issue_write = None
                graph_write = None
                graph_issue_write = None
                if config.write_discovered_issues:
                    issue_write = write_active_ticker_mapping_issues(config, plan)
                    print("active_ticker_issue_write=" + json.dumps(asdict(issue_write), sort_keys=True), flush=True)
                else:
                    print("active_ticker_issue_write=skipped reason=REFERENCE_GATEWAY_WRITE_DISCOVERED_ISSUES_FALSE", flush=True)
                if config.write_canonical_graph:
                    graph_write = write_canonical_graph_candidates(config, plan)
                    print("canonical_graph_write=" + json.dumps(asdict(graph_write), sort_keys=True, default=str), flush=True)
                    if graph_write.issues and config.write_discovered_issues:
                        graph_issue_write = write_graph_mapping_issues(config, graph_write.issues)
                        print("canonical_graph_issue_write=" + json.dumps(asdict(graph_issue_write), sort_keys=True), flush=True)
                    elif graph_write.issues:
                        print("canonical_graph_issue_write=skipped reason=REFERENCE_GATEWAY_WRITE_DISCOVERED_ISSUES_FALSE", flush=True)
                else:
                    print("canonical_graph_write=skipped reason=REFERENCE_GATEWAY_WRITE_CANONICAL_GRAPH_FALSE", flush=True)
                if config.resolve_stale_issues:
                    resolution = resolve_stale_active_ticker_issues(config)
                    print("stale_issue_resolution=" + json.dumps(asdict(resolution), sort_keys=True), flush=True)
                changed_rows = issue_write.written if issue_write is not None else 0
                if graph_write is not None:
                    changed_rows += graph_write.inserted_rows
                if graph_issue_write is not None:
                    changed_rows += graph_issue_write.written
                if changed_rows > 0 and config.rebuild_tradable_on_execute:
                    rebuild = rebuild_tradable_publications(config, reason="post_active_ticker_issue_write")
                    print("tradable_publication_rebuild=" + json.dumps(asdict(rebuild), sort_keys=True), flush=True)
                    report = run_reference_audit(config)
                    report_path = write_report(report, config.report_root_win) if args.write_report else None
                    if report_path:
                        print(f"post_issue_report={report_path}", flush=True)
    if (
        config.execute
        and write_policy.writes_allowed
        and config.market_publication_gap_fill_enabled
        and (not config.test_write_mode or args.market_publication_gap_fill is True)
    ):
        maintenance = run_recent_publication_gap_fill(config)
        print("market_publication_gap_fill=" + json.dumps(asdict(maintenance), sort_keys=True), flush=True)
    elif config.execute and config.test_write_mode and config.market_publication_gap_fill_enabled:
        print("market_publication_gap_fill=skipped reason=test_write_mode_requires_explicit_flag", flush=True)
    print(f"status={report.status} wall_seconds={report.wall_seconds:.2f}", flush=True)
    if report.status == "failed":
        sys.exit(2)


if __name__ == "__main__":
    main()

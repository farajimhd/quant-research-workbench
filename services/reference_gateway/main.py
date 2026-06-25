from __future__ import annotations

import argparse
import json
import os
import sys
import time
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
from services.reference_gateway.terminal import OperationRecord, ReferenceRunRecord, render_reference_run
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
    rich_output = config.terminal_rich_enabled
    run_started = time.perf_counter()
    record = ReferenceRunRecord(config=config, write_policy=write_policy)

    def emit(message: str) -> None:
        if not rich_output:
            print(message, flush=True)

    def add_operation(name: str, status: str, detail: str = "", rows: int | None = None, seconds: float | None = None) -> None:
        record.operations.append(OperationRecord(name=name, status=status, detail=detail, rows=rows, seconds=seconds))

    emit("=" * 96)
    emit("Reference Gateway audit")
    emit(
        f"read_database={config.clickhouse_read_database} "
        f"write_database={config.clickhouse_write_database} "
        f"test_write_mode={config.test_write_mode} "
        f"execute={config.execute} report_root={config.report_root_win}"
    )
    emit(
        "write_policy="
        f"allowed={write_policy.writes_allowed} "
        f"active_window={write_policy.active_collection_window} "
        f"window={write_policy.window_label} "
        f"reason={write_policy.reason}"
    )
    emit(
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
        )
    )
    emit("=" * 96)
    if config.execute and not write_policy.writes_allowed:
        add_operation("Promotion write policy", "skipped", write_policy.reason)
    if args.ensure_market_publication_schema:
        if not write_policy.writes_allowed:
            add_operation("Market publication schema", "skipped", write_policy.reason)
            emit("market_publication_schema=blocked reason=" + write_policy.reason)
        else:
            from research.mlops.clickhouse import ClickHouseHttpClient, default_clickhouse_password

            started = time.perf_counter()
            ensure_market_publication_schema(
                ClickHouseHttpClient(config.clickhouse_url, config.clickhouse_user, default_clickhouse_password()),
                database=config.clickhouse_write_database,
                read_database=config.clickhouse_read_database,
                storage_policy=os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY") or "",
            )
            add_operation(
                "Market publication schema",
                "completed",
                f"read={config.clickhouse_read_database} write={config.clickhouse_write_database}",
                seconds=time.perf_counter() - started,
            )
            emit(
                "market_publication_schema=ensured "
                f"read_database={config.clickhouse_read_database} "
                f"write_database={config.clickhouse_write_database}"
            )
    if config.execute and config.resolve_stale_issues:
        started = time.perf_counter()
        resolution = resolve_stale_active_ticker_issues(config)
        add_operation("Resolve issues", "completed", resolution_detail(resolution), rows=resolution.resolved, seconds=time.perf_counter() - started)
        emit("stale_issue_resolution=" + json.dumps(asdict(resolution), sort_keys=True))
    if config.execute and config.rebuild_tradable_on_execute and write_policy.writes_allowed:
        started = time.perf_counter()
        rebuild = rebuild_tradable_publications(config, reason="pre_audit_source_truth_refresh")
        add_operation("Rebuild tradable publications", rebuild.status, rebuild.reason, seconds=time.perf_counter() - started)
        emit("tradable_publication_rebuild=" + json.dumps(asdict(rebuild), sort_keys=True))
    elif config.execute and config.rebuild_tradable_on_execute:
        add_operation("Rebuild tradable publications", "skipped", write_policy.reason)
    audit_started = time.perf_counter()
    report = run_reference_audit(config)
    record.audit = report
    add_operation("Reference audit", report.status, f"{len(report.checks)} checks", seconds=time.perf_counter() - audit_started)
    report_path = write_report(report, config.report_root_win) if args.write_report else None
    record.report_path = str(report_path or "")
    for check in report.checks:
        emit(
            f"{check.status.upper():7} {check.severity:7} {check.name:45} count={check.count:,} {check.message}",
        )
    if report_path:
        emit(f"report={report_path}")
    should_check_tickers = args.active_ticker_check if args.active_ticker_check is not None else config.active_ticker_check_enabled
    if should_check_tickers:
        if config.active_ticker_check_market_hours_only and not active_collection_window(service_prefix="REFERENCE"):
            add_operation("Active ticker check", "skipped", "outside_reference_collection_window")
            emit("active_ticker_check=skipped reason=outside_reference_collection_window")
        elif not config.source_massive_enabled:
            add_operation("Active ticker check", "skipped", "massive_disabled")
            emit("active_ticker_check=skipped reason=massive_disabled")
        else:
            started = time.perf_counter()
            plan = run_active_ticker_plan(config)
            plan_path = write_active_ticker_plan(plan, config.report_root_win) if args.write_report else None
            add_operation(
                "Active ticker check",
                "completed",
                f"provider={plan.provider_rows:,} missing={plan.missing_tickers:,} overview={plan.overview_fetched:,} ibkr={plan.ibkr_searched:,}",
                rows=plan.missing_tickers,
                seconds=time.perf_counter() - started,
            )
            emit(
                "active_ticker_check=done "
                f"provider_rows={plan.provider_rows:,} known_symbols={plan.known_active_symbols:,} "
                f"missing={plan.missing_tickers:,} overview={plan.overview_fetched:,} ibkr={plan.ibkr_searched:,} "
                f"saturated={plan.provider_saturated} wall_seconds={plan.wall_seconds:.2f}"
            )
            if plan_path:
                emit(f"active_ticker_report={plan_path}")
            if config.execute:
                issue_write = None
                graph_write = None
                graph_issue_write = None
                if config.write_discovered_issues:
                    started = time.perf_counter()
                    issue_write = write_active_ticker_mapping_issues(config, plan)
                    add_operation("Write active ticker issues", "completed", issue_write.reason, rows=issue_write.written, seconds=time.perf_counter() - started)
                    emit("active_ticker_issue_write=" + json.dumps(asdict(issue_write), sort_keys=True))
                else:
                    add_operation("Write active ticker issues", "skipped", "REFERENCE_GATEWAY_WRITE_DISCOVERED_ISSUES_FALSE")
                    emit("active_ticker_issue_write=skipped reason=REFERENCE_GATEWAY_WRITE_DISCOVERED_ISSUES_FALSE")
                if config.write_canonical_graph and write_policy.writes_allowed:
                    started = time.perf_counter()
                    graph_write = write_canonical_graph_candidates(config, plan)
                    add_operation("Write canonical graph", "completed", graph_write.reason, rows=graph_write.inserted_rows, seconds=time.perf_counter() - started)
                    emit("canonical_graph_write=" + json.dumps(asdict(graph_write), sort_keys=True, default=str))
                    if graph_write.issues and config.write_discovered_issues:
                        started = time.perf_counter()
                        graph_issue_write = write_graph_mapping_issues(config, graph_write.issues)
                        add_operation("Write graph issues", "completed", graph_issue_write.reason, rows=graph_issue_write.written, seconds=time.perf_counter() - started)
                        emit("canonical_graph_issue_write=" + json.dumps(asdict(graph_issue_write), sort_keys=True))
                    elif graph_write.issues:
                        add_operation("Write graph issues", "skipped", "REFERENCE_GATEWAY_WRITE_DISCOVERED_ISSUES_FALSE")
                        emit("canonical_graph_issue_write=skipped reason=REFERENCE_GATEWAY_WRITE_DISCOVERED_ISSUES_FALSE")
                else:
                    reason = "REFERENCE_GATEWAY_WRITE_CANONICAL_GRAPH_FALSE" if not config.write_canonical_graph else write_policy.reason
                    add_operation("Write canonical graph", "skipped", reason)
                    emit("canonical_graph_write=skipped reason=" + reason)
                if config.resolve_stale_issues:
                    started = time.perf_counter()
                    resolution = resolve_stale_active_ticker_issues(config)
                    add_operation("Resolve issues", "completed", resolution_detail(resolution), rows=resolution.resolved, seconds=time.perf_counter() - started)
                    emit("stale_issue_resolution=" + json.dumps(asdict(resolution), sort_keys=True))
                changed_rows = issue_write.written if issue_write is not None else 0
                if graph_write is not None:
                    changed_rows += graph_write.inserted_rows
                if graph_issue_write is not None:
                    changed_rows += graph_issue_write.written
                if changed_rows > 0 and config.rebuild_tradable_on_execute and write_policy.writes_allowed:
                    started = time.perf_counter()
                    rebuild = rebuild_tradable_publications(config, reason="post_active_ticker_issue_write")
                    add_operation("Rebuild tradable publications", rebuild.status, rebuild.reason, seconds=time.perf_counter() - started)
                    emit("tradable_publication_rebuild=" + json.dumps(asdict(rebuild), sort_keys=True))
                    audit_started = time.perf_counter()
                    report = run_reference_audit(config)
                    record.audit = report
                    add_operation("Post-write reference audit", report.status, f"{len(report.checks)} checks", seconds=time.perf_counter() - audit_started)
                    report_path = write_report(report, config.report_root_win) if args.write_report else None
                    record.report_path = str(report_path or record.report_path)
                    if report_path:
                        emit(f"post_issue_report={report_path}")
                elif changed_rows > 0 and config.rebuild_tradable_on_execute:
                    add_operation("Post-issue tradable rebuild", "skipped", write_policy.reason)
    if (
        config.execute
        and write_policy.writes_allowed
        and config.market_publication_gap_fill_enabled
        and (not config.test_write_mode or args.market_publication_gap_fill is True)
    ):
        started = time.perf_counter()
        maintenance = run_recent_publication_gap_fill(config)
        add_operation(
            "Market publication gap fill",
            "completed" if maintenance.returncode == 0 else "failed",
            f"{maintenance.start_date}->{maintenance.end_date}; {maintenance.reason}",
            seconds=time.perf_counter() - started,
        )
        emit("market_publication_gap_fill=" + json.dumps(asdict(maintenance), sort_keys=True))
    elif config.execute and config.test_write_mode and config.market_publication_gap_fill_enabled:
        add_operation("Market publication gap fill", "skipped", "test_write_mode_requires_explicit_flag")
        emit("market_publication_gap_fill=skipped reason=test_write_mode_requires_explicit_flag")
    record.final_status = report.status
    record.wall_seconds = time.perf_counter() - run_started
    if rich_output:
        render_reference_run(record)
    emit(f"status={report.status} wall_seconds={report.wall_seconds:.2f}")
    if report.status == "failed":
        sys.exit(2)


def resolution_detail(resolution: object) -> str:
    return (
        f"{getattr(resolution, 'reason', '-')}; "
        f"auto_block={getattr(resolution, 'auto_block_until_resolved', 0):,} "
        f"review={getattr(resolution, 'human_review_required', 0):,} "
        f"historical={getattr(resolution, 'historical_repair', 0):,}"
    )


if __name__ == "__main__":
    main()

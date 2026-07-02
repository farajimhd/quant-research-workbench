from __future__ import annotations

import argparse
import atexit
import json
import os
import sys
import time
from dataclasses import asdict

from research.mlops.clickhouse import discover_clickhouse_env_files
from research.mlops.env import load_env_files, secret_status
from services.reference_gateway.active_tickers import run_active_ticker_plan, write_active_ticker_plan
from services.reference_gateway.alerts import (
    ReferenceAlert,
    build_audit_alerts,
    build_graph_issue_alerts,
    build_publication_maintenance_alert,
    build_source_sync_alerts,
    build_tradability_block_alert,
    ensure_alert_schema,
    write_alerts,
)
from services.reference_gateway.audit import run_reference_audit, write_report
from services.reference_gateway.canonical_graph_writer import write_canonical_graph_candidates
from services.reference_gateway.config import ReferenceGatewayConfig, ReferenceGatewayConfigOverrides
from services.reference_gateway.daemon import run_reference_daemon
from services.reference_gateway.facts import ensure_fact_schema
from services.reference_gateway.issue_resolution import resolve_stale_active_ticker_issues
from services.reference_gateway.issue_writer import write_active_ticker_mapping_issues, write_graph_mapping_issues
from services.reference_gateway.market_publications import ensure_market_publication_schema
from services.reference_gateway.policy import evaluate_write_policy
from services.reference_gateway.preflight import run_preflight
from services.reference_gateway.publication_maintenance import run_recent_publication_gap_fill
from services.reference_gateway.publication_rebuild import rebuild_tradable_publications
from services.reference_gateway.runtime_log import RuntimeLogger
from services.reference_gateway.table_groups import table_group_markdown
from services.reference_gateway.terminal import OperationRecord, ReferenceRunRecord, ReferenceTerminalSession
from services.reference_gateway.tradable_blocker import block_latest_universe_for_open_issues
from services.reference_gateway.tradability import tradability_rule_markdown


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Reference gateway audit and sync service. Operational runs always "
            "perform source sync and integrity checks; the public knobs only "
            "select operator mode, lifetime, integrity strictness, maintenance "
            "policy, and diagnostics."
        )
    )
    parser.add_argument("--mode", choices=["prod", "temp"], default="prod", help="prod writes to q_live; temp reads q_live and writes q_reference_tmp.")
    parser.add_argument("--run", choices=["daemon", "once"], default="", help="Process lifetime. Empty means prod=daemon and temp=once.")
    parser.add_argument("--integrity", choices=["strict", "report-only"], default="strict", help="strict writes issues/blocks tradability; report-only audits without guardrail writes.")
    parser.add_argument("--maintenance", choices=["auto", "skip", "force"], default="auto", help="auto defers heavy work during market hours; skip disables maintenance; force allows it with a reason.")
    parser.add_argument("--maintenance-reason", default="", help="Auditable reason required when --maintenance force is used.")
    parser.add_argument("--diagnostics", choices=["none", "rules", "table-groups", "config"], default="none", help="Print diagnostics and exit without operational writes.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    load_env_files(discover_clickhouse_env_files())
    if args.maintenance == "force" and not args.maintenance_reason.strip():
        print("--maintenance-reason is required with --maintenance force.", flush=True)
        sys.exit(2)
    if args.diagnostics == "rules":
        print(tradability_rule_markdown())
        return
    if args.diagnostics == "table-groups":
        print(table_group_markdown())
        return
    config = ReferenceGatewayConfig.from_env(config_overrides_from_args(args))
    if config.maintenance_mode == "force" and not config.market_hours_write_reason.strip():
        print("--maintenance-reason or REFERENCE_GATEWAY_MAINTENANCE_REASON is required when maintenance mode is force.", flush=True)
        sys.exit(2)
    if args.diagnostics == "config":
        print(json.dumps(config.public_dict(), indent=2, sort_keys=True))
        return
    if config.daemon_loop_enabled:
        run_reference_daemon(config, sys.argv[1:])
        return
    write_policy = evaluate_write_policy(config)
    rich_output = config.terminal_rich_enabled
    run_started = time.perf_counter()
    logger = RuntimeLogger.from_env()
    record = ReferenceRunRecord(config=config, write_policy=write_policy)
    terminal = ReferenceTerminalSession(record) if rich_output else None
    if terminal is not None:
        terminal.start()
        atexit.register(terminal.stop)

    def emit(message: str) -> None:
        if not rich_output:
            print(message, flush=True)

    def add_operation(name: str, status: str, detail: str = "", rows: int | None = None, seconds: float | None = None) -> None:
        record.operations.append(OperationRecord(name=name, status=status, detail=detail, rows=rows, seconds=seconds))
        logger.event("operation", name=name, status=status, detail=detail, rows=rows, seconds=seconds)
        refresh_terminal()

    def update_latest_operation(name: str, status: str, detail: str = "", rows: int | None = None, seconds: float | None = None) -> None:
        for op in reversed(record.operations):
            if op.name == name:
                op.status = status
                op.detail = detail
                op.rows = rows
                op.seconds = seconds
                break
        else:
            record.operations.append(OperationRecord(name=name, status=status, detail=detail, rows=rows, seconds=seconds))
        logger.event("operation_progress", name=name, status=status, detail=detail, rows=rows, seconds=seconds)
        refresh_terminal()

    def write_alert_batch(alerts: list[ReferenceAlert], reason: str) -> None:
        if not config.execute:
            return
        started = time.perf_counter()
        result = write_alerts(config, alerts, reason=reason)
        if result.attempted == 0:
            return
        add_operation("Write reference alerts", "completed", result.reason, rows=result.written, seconds=time.perf_counter() - started)
        logger.event("alerts_written", reason=reason, attempted=result.attempted, written=result.written, table=result.table)
        emit("reference_alert_write=" + json.dumps(asdict(result), sort_keys=True))

    def refresh_terminal() -> None:
        if terminal is not None:
            terminal.update()

    def stop_terminal() -> None:
        if terminal is not None:
            terminal.stop()

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
                    "REFERENCE_GATEWAY_MODE",
                    "REFERENCE_GATEWAY_RUN",
                    "REFERENCE_GATEWAY_INTEGRITY",
                    "REFERENCE_GATEWAY_MAINTENANCE",
                    "REFERENCE_GATEWAY_MAINTENANCE_REASON",
                    "REFERENCE_GATEWAY_DIAGNOSTICS",
                    "CLICKHOUSE_WORKSTATION_USER",
                    "CLICKHOUSE_WORKSTATION_PASSWORD",
                    "MASSIVE_API_KEY",
                    "IBKR_CPAPI_BASE_URL",
                ]
            ),
            sort_keys=True,
        )
    )
    emit("=" * 96)
    logger.event(
        "run_started",
        config=config.public_dict(),
        write_policy=asdict(write_policy),
        argv=sys.argv[1:],
    )
    should_sync_sources = True
    if config.preflight_enabled:
        started = time.perf_counter()
        preflight = run_preflight(config, require_source_sync_dependencies=should_sync_sources, logger=logger)
        add_operation(
            "Dependency preflight",
            preflight.status,
            "; ".join(f"{check.name}={check.status}" for check in preflight.checks),
            seconds=time.perf_counter() - started,
        )
        emit("preflight=" + json.dumps(preflight.public_dict(), sort_keys=True))
        if preflight.status != "ok":
            record.final_status = "failed"
            record.wall_seconds = time.perf_counter() - run_started
            logger.event("run_failed", reason="preflight_failed", wall_seconds=record.wall_seconds)
            refresh_terminal()
            stop_terminal()
            sys.exit(2)
    else:
        add_operation("Dependency preflight", "skipped", "REFERENCE_GATEWAY_PREFLIGHT_ENABLED_FALSE")
    if config.execute and not write_policy.writes_allowed:
        add_operation("Promotion write policy", "skipped", write_policy.reason)
    if config.execute:
        from research.mlops.clickhouse import ClickHouseHttpClient, default_clickhouse_password

        started = time.perf_counter()
        ensure_alert_schema(
            ClickHouseHttpClient(config.clickhouse_url, config.clickhouse_user, default_clickhouse_password()),
            database=config.clickhouse_write_database,
            storage_policy=os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY") or "",
        )
        add_operation("Reference alert schema", "completed", f"write={config.clickhouse_write_database}", seconds=time.perf_counter() - started)
        started = time.perf_counter()
        ensure_fact_schema(
            ClickHouseHttpClient(config.clickhouse_url, config.clickhouse_user, default_clickhouse_password()),
            database=config.clickhouse_write_database,
            storage_policy=os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY") or "",
        )
        add_operation("Reference fact schema", "completed", f"write={config.clickhouse_write_database}", seconds=time.perf_counter() - started)
    if config.execute and config.market_publication_gap_fill_enabled:
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
    report_path = write_report(report, config.report_root_win)
    record.report_path = str(report_path or "")
    logger.event("audit_completed", **audit_log_summary(report, report_path=record.report_path))
    write_alert_batch(build_audit_alerts(report, report_path=record.report_path), "reference_audit")
    refresh_terminal()
    for check in report.checks:
        emit(
            f"{check.status.upper():7} {check.severity:7} {check.name:45} count={check.count:,} {check.message}",
        )
    if report_path:
        emit(f"report={report_path}")
    if should_sync_sources:
        started = time.perf_counter()
        add_operation("Source sync", "running", "Starting Massive active ticker reconciliation.", seconds=0.0)

        def source_sync_progress(message: str, rows: int | None) -> None:
            update_latest_operation("Source sync", "running", truncate_detail(message), rows=rows, seconds=time.perf_counter() - started)

        plan = run_active_ticker_plan(config, on_progress=source_sync_progress)
        plan_path = write_active_ticker_plan(plan, config.report_root_win)
        logger.event(
            "source_sync_completed",
            provider_rows=plan.provider_rows,
            provider_pages=plan.provider_pages,
            provider_saturated=plan.provider_saturated,
            known_active_symbols=plan.known_active_symbols,
            missing_tickers=plan.missing_tickers,
            overview_fetched=plan.overview_fetched,
            ibkr_searched=plan.ibkr_searched,
            candidate_limit=plan.candidate_limit,
            wall_seconds=plan.wall_seconds,
            report_path=str(plan_path or ""),
        )
        write_alert_batch(build_source_sync_alerts(plan), "source_sync")
        add_operation(
            "Source sync",
            "completed",
            f"provider={plan.provider_rows:,} missing={plan.missing_tickers:,} overview={plan.overview_fetched:,} ibkr={plan.ibkr_searched:,}",
            rows=plan.missing_tickers,
            seconds=time.perf_counter() - started,
        )
        emit(
            "source_sync=done "
            f"provider_rows={plan.provider_rows:,} known_symbols={plan.known_active_symbols:,} "
            f"missing={plan.missing_tickers:,} overview={plan.overview_fetched:,} ibkr={plan.ibkr_searched:,} "
            f"saturated={plan.provider_saturated} wall_seconds={plan.wall_seconds:.2f}"
        )
        if plan_path:
            emit(f"source_sync_report={plan_path}")
        if config.execute:
            issue_write = None
            graph_write = None
            graph_issue_write = None
            if config.write_discovered_issues:
                started = time.perf_counter()
                issue_write = write_active_ticker_mapping_issues(config, plan)
                add_operation("Write source-sync issues", "completed", issue_write.reason, rows=issue_write.written, seconds=time.perf_counter() - started)
                emit("source_sync_issue_write=" + json.dumps(asdict(issue_write), sort_keys=True))
                if config.immediate_tradability_block_enabled:
                    started = time.perf_counter()
                    block_result = block_latest_universe_for_open_issues(config, reason="source_sync_issue_write")
                    add_operation(
                        "Immediate tradability block",
                        block_result.status,
                        block_result.reason,
                        rows=block_result.rows_blocked,
                        seconds=time.perf_counter() - started,
                    )
                    emit("immediate_tradability_block=" + json.dumps(asdict(block_result), sort_keys=True))
                    write_alert_batch(build_tradability_block_alert(block_result, reason="source_sync_issue_write"), "tradability_block")
            else:
                add_operation("Write source-sync issues", "skipped", "REFERENCE_GATEWAY_WRITE_DISCOVERED_ISSUES_FALSE")
                emit("source_sync_issue_write=skipped reason=REFERENCE_GATEWAY_WRITE_DISCOVERED_ISSUES_FALSE")
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
                    write_alert_batch(build_graph_issue_alerts(graph_write.issues), "canonical_graph_issues")
                    if config.immediate_tradability_block_enabled:
                        started = time.perf_counter()
                        block_result = block_latest_universe_for_open_issues(config, reason="canonical_graph_issue_write")
                        add_operation(
                            "Immediate tradability block",
                            block_result.status,
                            block_result.reason,
                            rows=block_result.rows_blocked,
                            seconds=time.perf_counter() - started,
                        )
                        emit("immediate_tradability_block=" + json.dumps(asdict(block_result), sort_keys=True))
                        write_alert_batch(build_tradability_block_alert(block_result, reason="canonical_graph_issue_write"), "tradability_block")
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
                rebuild = rebuild_tradable_publications(config, reason="post_source_sync_issue_write")
                add_operation("Rebuild tradable publications", rebuild.status, rebuild.reason, seconds=time.perf_counter() - started)
                emit("tradable_publication_rebuild=" + json.dumps(asdict(rebuild), sort_keys=True))
                audit_started = time.perf_counter()
                report = run_reference_audit(config)
                record.audit = report
                add_operation("Post-write reference audit", report.status, f"{len(report.checks)} checks", seconds=time.perf_counter() - audit_started)
                report_path = write_report(report, config.report_root_win)
                record.report_path = str(report_path or record.report_path)
                logger.event("audit_completed", **audit_log_summary(report, report_path=record.report_path, post_write=True))
                write_alert_batch(build_audit_alerts(report, report_path=record.report_path, post_write=True), "post_write_reference_audit")
                refresh_terminal()
                if report_path:
                    emit(f"post_issue_report={report_path}")
            elif changed_rows > 0 and config.rebuild_tradable_on_execute:
                add_operation("Post-issue tradable rebuild", "skipped", write_policy.reason)
    if (
        config.execute
        and config.market_publication_gap_fill_enabled
        and (not config.test_write_mode or config.maintenance_mode == "force")
    ):
        started = time.perf_counter()
        add_operation(
            "Market publication gap fill",
            "running",
            f"Starting source sync for recent publication window; days={config.market_publication_gap_fill_days}",
            seconds=0.0,
        )

        def publication_progress(line: str) -> None:
            update_latest_operation(
                "Market publication gap fill",
                "running",
                truncate_detail(line),
                seconds=time.perf_counter() - started,
            )

        maintenance = run_recent_publication_gap_fill(config, on_progress=publication_progress)
        update_latest_operation(
            "Market publication gap fill",
            "completed" if maintenance.returncode == 0 else "failed",
            f"{maintenance.start_date}->{maintenance.end_date}; {maintenance.reason}; {last_nonempty_line(maintenance.stdout_tail)}",
            seconds=time.perf_counter() - started,
        )
        emit("market_publication_gap_fill=" + json.dumps(asdict(maintenance), sort_keys=True))
        write_alert_batch(
            build_publication_maintenance_alert(
                "completed" if maintenance.returncode == 0 else "failed",
                maintenance.reason,
                asdict(maintenance),
            ),
            "market_publication_gap_fill",
        )
    elif config.execute and config.test_write_mode and config.market_publication_gap_fill_enabled:
        add_operation("Market publication gap fill", "skipped", "temp_mode_requires_maintenance_force")
        emit("market_publication_gap_fill=skipped reason=temp_mode_requires_maintenance_force")
    record.final_status = report.status
    record.wall_seconds = time.perf_counter() - run_started
    logger.event("run_finished", status=record.final_status, wall_seconds=record.wall_seconds, report_path=record.report_path)
    refresh_terminal()
    stop_terminal()
    emit(f"status={report.status} wall_seconds={report.wall_seconds:.2f}")
    if report.status == "failed":
        sys.exit(2)


def config_overrides_from_args(args: argparse.Namespace) -> ReferenceGatewayConfigOverrides:
    return ReferenceGatewayConfigOverrides(
        operator_mode=args.mode,
        run_mode=args.run or None,
        integrity_mode=args.integrity,
        maintenance_mode=args.maintenance,
        diagnostics_mode=args.diagnostics,
        market_hours_write_reason=args.maintenance_reason or None,
    )


def resolution_detail(resolution: object) -> str:
    return (
        f"{getattr(resolution, 'reason', '-')}; "
        f"auto_block={getattr(resolution, 'auto_block_until_resolved', 0):,} "
        f"review={getattr(resolution, 'human_review_required', 0):,} "
        f"historical={getattr(resolution, 'historical_repair', 0):,}"
    )


def audit_log_summary(report: object, *, report_path: str, post_write: bool = False) -> dict[str, object]:
    checks = list(getattr(report, "checks", []) or [])
    failed = [check for check in checks if getattr(check, "status", "") != "ok"]
    return {
        "post_write": post_write,
        "status": getattr(report, "status", ""),
        "checked_at_utc": getattr(report, "checked_at_utc", ""),
        "read_database": getattr(report, "read_database", ""),
        "write_database": getattr(report, "write_database", ""),
        "check_count": len(checks),
        "failed_check_count": len(failed),
        "failed_checks": [
            {
                "name": getattr(check, "name", ""),
                "severity": getattr(check, "severity", ""),
                "status": getattr(check, "status", ""),
                "count": getattr(check, "count", 0),
            }
            for check in failed[:20]
        ],
        "report_path": report_path,
    }


def truncate_detail(value: str, limit: int = 500) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "..."


def last_nonempty_line(value: str) -> str:
    for line in reversed(str(value or "").splitlines()):
        text = line.strip()
        if text:
            return truncate_detail(text, 300)
    return "no subprocess output"


if __name__ == "__main__":
    main()

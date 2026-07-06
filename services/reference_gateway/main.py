from __future__ import annotations

import argparse
import atexit
import json
import os
import sys
import time
from dataclasses import asdict
from datetime import UTC, datetime

from research.mlops.clickhouse import discover_clickhouse_env_files
from research.mlops.env import load_env_files, secret_status
from services.reference_gateway.active_tickers import ActiveTickerPlan, run_active_ticker_plan, write_active_ticker_plan
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
from services.reference_gateway.country_assertions import write_country_assertions
from services.reference_gateway.current_ticker_detail_sync import CurrentTickerDetailSyncResult, run_current_ticker_detail_sync
from services.reference_gateway.daemon import run_reference_daemon
from services.reference_gateway.fact_fillers import fill_reference_tradability_and_routing_facts
from services.reference_gateway.facts import ensure_fact_schema
from services.reference_gateway.issue_resolution import resolve_stale_active_ticker_issues
from services.reference_gateway.issue_writer import write_active_ticker_mapping_issues, write_graph_mapping_issues
from services.reference_gateway.ibkr_borrow_sync import IbkrBorrowSyncResult, run_startup_ibkr_borrow_sync
from services.reference_gateway.market_publications import ensure_market_publication_schema
from services.reference_gateway.memory import memory_snapshot, start_memory_trace
from services.reference_gateway.policy import evaluate_write_policy
from services.reference_gateway.preflight import run_preflight
from services.reference_gateway.publication_bootstrap import bootstrap_existing_publication_coverage
from services.reference_gateway.publication_maintenance import run_recent_publication_gap_fill
from services.reference_gateway.publication_rebuild import rebuild_tradable_publications
from services.reference_gateway.runtime_log import RuntimeLogger
from services.reference_gateway.source_schedule import ensure_source_schedule_schema, record_source_schedule, schedule_decision
from services.reference_gateway.state import collect_reference_state
from services.reference_gateway.status import build_reference_status_snapshot
from services.reference_gateway.table_groups import table_group_markdown
from services.reference_gateway.terminal import OperationRecord, ReferenceRunRecord, ReferenceTerminalSession
from services.reference_gateway.tradable_blocker import block_latest_universe_for_open_issues
from services.reference_gateway.tradability import tradability_rule_markdown


SOURCE_SYNC_OPERATION_NAMES = {
    "massive_active_tickers": "Source: Massive /v3/reference/tickers",
    "canonical_symbols": "Source: q_live canonical symbols",
    "massive_overview": "Source: Massive /v3/reference/tickers/{ticker}",
    "massive_ticker_details": "Source: Massive current snapshot/float",
    "ibkr_conids": "Source: IBKR /iserver/secdef/search",
    "ibkr_borrow_availability": "Source: IBKR /iserver/marketdata/snapshot",
    "country_assertions": "Source: country assertions",
    "ticker_reconciliation": "Source: ticker reconciliation",
}

PUBLICATION_OPERATION_NAMES = {
    "finra_short_volume:CNMS": "Source: FINRA CNMS short volume",
    "massive_short_interest": "Source: Massive short interest",
    "sec_fails_to_deliver": "Source: SEC fails-to-deliver zip",
    "reg_sho_threshold": "Source: Reg SHO threshold lists",
    "massive_splits": "Source: Massive /v3/reference/splits",
    "massive_dividends": "Source: Massive /v3/reference/dividends",
    "massive_ipos": "Source: Massive /v3/reference/ipos",
    "massive_presentation_assets": "Source: Massive presentation assets",
    "massive_ticker_details": "Source: Massive ticker details snapshot",
    "ibkr_borrow_availability": "Source: IBKR borrow availability",
    "sec_country_assertions": "Source: country assertions",
}


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
    start_memory_trace()
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
    terminal = ReferenceTerminalSession(record, screen=config.terminal_screen_enabled) if rich_output else None
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

    def ensure_operation(name: str, status: str, detail: str = "", rows: int | None = None, seconds: float | None = None) -> None:
        if any(op.name == name for op in record.operations):
            update_latest_operation(name, status, detail, rows=rows, seconds=seconds)
            return
        add_operation(name, status, detail, rows=rows, seconds=seconds)

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

    def refresh_reference_state(reason: str) -> None:
        try:
            from research.mlops.clickhouse import ClickHouseHttpClient, default_clickhouse_password

            client = ClickHouseHttpClient(config.clickhouse_url, config.clickhouse_user, default_clickhouse_password())
            source_states, table_states = collect_reference_state(client, database=config.clickhouse_write_database)
            record.source_states = source_states
            record.table_states = table_states
            logger.event(
                "reference_state_collected",
                reason=reason,
                source_states=len(source_states),
                table_states=len(table_states),
                source_failed=sum(1 for state in source_states if state.status == "failed"),
                table_missing=sum(1 for state in table_states if state.status == "missing"),
            )
        except Exception as exc:  # noqa: BLE001
            logger.event("reference_state_failed", reason=reason, error=repr(exc))
        refresh_terminal()

    def fill_reference_facts(reason: str) -> None:
        started = time.perf_counter()
        result = fill_reference_tradability_and_routing_facts(config, reason=reason)
        status = result.status if result.status != "completed" or result.total_rows > 0 else "completed"
        add_operation(
            "Fill reference facts",
            status,
            (
                f"{result.reason}; tradability={result.tradability_rows:,} routing={result.routing_rows:,} "
                f"source={result.source_database} issues={result.issue_database} target={result.target_database}"
            ),
            rows=result.total_rows,
            seconds=time.perf_counter() - started,
        )
        logger.event(
            "reference_fact_fill_completed",
            status=result.status,
            reason=result.reason,
            tradability_rows=result.tradability_rows,
            routing_rows=result.routing_rows,
            source_database=result.source_database,
            issue_database=result.issue_database,
            target_database=result.target_database,
            source_run_id=result.source_run_id,
        )

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
        memory=memory_snapshot("run_started").public_dict(),
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
        refresh_reference_state("after_preflight")
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
        started = time.perf_counter()
        ensure_source_schedule_schema(
            ClickHouseHttpClient(config.clickhouse_url, config.clickhouse_user, default_clickhouse_password()),
            database=config.clickhouse_write_database,
            storage_policy=os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY") or "",
        )
        add_operation("Source schedule schema", "completed", f"write={config.clickhouse_write_database}", seconds=time.perf_counter() - started)
        refresh_reference_state("after_fact_schema")
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
        started = time.perf_counter()
        bootstrap = bootstrap_existing_publication_coverage(config, reason="startup_schema_coverage_bootstrap")
        add_operation(
            "Bootstrap publication coverage",
            bootstrap.status,
            f"checked={bootstrap.sources_checked:,} covered={bootstrap.sources_covered:,} reason={bootstrap.reason}",
            rows=bootstrap.rows_written,
            seconds=time.perf_counter() - started,
        )
        logger.event("publication_coverage_bootstrap_completed", **asdict(bootstrap))
        refresh_reference_state("after_market_publication_schema")
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
        source_sync_started = time.perf_counter()
        add_operation("Source sync", "running", "Starting Massive active ticker reconciliation.", seconds=0.0)
        for operation_name in SOURCE_SYNC_OPERATION_NAMES.values():
            ensure_operation(operation_name, "waiting", "not started", seconds=0.0)

        def source_sync_progress(source: str, status: str, message: str, rows: int | None) -> None:
            elapsed = time.perf_counter() - source_sync_started
            operation_name = SOURCE_SYNC_OPERATION_NAMES.get(source, "Source: " + source.replace("_", " "))
            update_latest_operation(operation_name, status, truncate_detail(message), rows=rows, seconds=elapsed)
            update_latest_operation("Source sync", "running", truncate_detail(message), rows=rows, seconds=elapsed)

        from research.mlops.clickhouse import ClickHouseHttpClient, default_clickhouse_password

        schedule_client = ClickHouseHttpClient(config.clickhouse_url, config.clickhouse_user, default_clickhouse_password())
        active_schedule = schedule_decision(
            schedule_client,
            config,
            source_name="massive_active_tickers",
            frequency_seconds=config.active_ticker_sync_frequency_seconds,
            force=config.maintenance_mode == "force",
        )
        if active_schedule.should_run:
            plan = run_active_ticker_plan(config, on_progress=source_sync_progress)
            record_source_schedule(
                schedule_client,
                config,
                source_name="massive_active_tickers",
                status="completed",
                rows_written=0,
                details=plan.public_dict() | {"schedule_reason": active_schedule.reason},
                frequency_seconds=config.active_ticker_sync_frequency_seconds,
            )
        else:
            now_text = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            plan = ActiveTickerPlan(now_text, 0, 0, False, 0, 0, 0, 0, config.active_ticker_new_candidate_limit, [], 0.0)
            update_latest_operation(
                SOURCE_SYNC_OPERATION_NAMES["massive_active_tickers"],
                "skipped",
                f"{active_schedule.reason}; next_due={active_schedule.next_due_at_utc or '-'}",
                rows=0,
                seconds=0.0,
            )
            update_latest_operation(
                "Source sync",
                "running",
                f"Massive active ticker sync skipped: {active_schedule.reason}; next_due={active_schedule.next_due_at_utc or '-'}",
                rows=0,
                seconds=0.0,
            )
        plan_path = write_active_ticker_plan(plan, config.report_root_win)
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
            if config.write_canonical_graph:
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
                reason = "REFERENCE_GATEWAY_WRITE_CANONICAL_GRAPH_FALSE"
                add_operation("Write canonical graph", "skipped", reason)
                emit("canonical_graph_write=skipped reason=" + reason)
        else:
            graph_write = None

        accepted_tickers = list(getattr(graph_write, "accepted_tickers", []) or [])
        current_details_started = time.perf_counter()

        def current_details_progress(source: str, status: str, message: str, rows: int | None) -> None:
            elapsed = time.perf_counter() - current_details_started
            operation_name = SOURCE_SYNC_OPERATION_NAMES.get(source, "Source: " + source.replace("_", " "))
            update_latest_operation(operation_name, status, truncate_detail(message), rows=rows, seconds=elapsed)
            update_latest_operation("Source sync", "running", truncate_detail(message), rows=rows, seconds=time.perf_counter() - source_sync_started)

        current_detail_schedule = schedule_decision(
            schedule_client,
            config,
            source_name="massive_ticker_details",
            frequency_seconds=config.current_ticker_detail_frequency_seconds,
            force=bool(accepted_tickers),
        )
        if current_detail_schedule.should_run:
            current_details = run_current_ticker_detail_sync(config, tickers=accepted_tickers, on_progress=current_details_progress)
            record_source_schedule(
                schedule_client,
                config,
                source_name="massive_ticker_details",
                status=current_details.status,
                rows_written=current_details.written,
                details=asdict(current_details) | {"schedule_reason": current_detail_schedule.reason},
                frequency_seconds=config.current_ticker_detail_frequency_seconds,
            )
        else:
            current_details = CurrentTickerDetailSyncResult(
                False,
                "skipped",
                requested=len(accepted_tickers),
                wall_seconds=0.0,
                details={
                    "reason": current_detail_schedule.reason,
                    "next_due_at_utc": current_detail_schedule.next_due_at_utc,
                },
            )
        current_details_status = "completed" if current_details.status in {"completed", "covered_empty", "skipped"} else current_details.status
        update_latest_operation(
            SOURCE_SYNC_OPERATION_NAMES["massive_ticker_details"],
            current_details_status,
            (
                f"requested={current_details.requested:,} matched={current_details.matched:,} "
                f"written={current_details.written:,} failed={current_details.failed:,} status={current_details.status}"
            ),
            rows=current_details.written,
            seconds=current_details.wall_seconds,
        )
        logger.event(
            "current_ticker_detail_source_sync_completed",
            attempted=current_details.attempted,
            status=current_details.status,
            requested=current_details.requested,
            matched=current_details.matched,
            written=current_details.written,
            failed=current_details.failed,
            wall_seconds=current_details.wall_seconds,
            details=current_details.details,
        )

        borrow_started = time.perf_counter()

        def ibkr_borrow_progress(source: str, status: str, message: str, rows: int | None) -> None:
            elapsed = time.perf_counter() - borrow_started
            operation_name = SOURCE_SYNC_OPERATION_NAMES.get(source, "Source: " + source.replace("_", " "))
            update_latest_operation(operation_name, status, truncate_detail(message), rows=rows, seconds=elapsed)
            update_latest_operation("Source sync", "running", truncate_detail(message), rows=rows, seconds=time.perf_counter() - source_sync_started)

        borrow_schedule = schedule_decision(
            schedule_client,
            config,
            source_name="ibkr_borrow_availability",
            frequency_seconds=config.ibkr_borrow_frequency_seconds,
        )
        if borrow_schedule.should_run:
            borrow_sync = run_startup_ibkr_borrow_sync(config, on_progress=ibkr_borrow_progress)
            record_source_schedule(
                schedule_client,
                config,
                source_name="ibkr_borrow_availability",
                status=borrow_sync.status,
                rows_written=borrow_sync.written,
                details=asdict(borrow_sync) | {"schedule_reason": borrow_schedule.reason},
                frequency_seconds=config.ibkr_borrow_frequency_seconds,
            )
        else:
            borrow_sync = IbkrBorrowSyncResult(
                False,
                "skipped",
                wall_seconds=0.0,
                details={
                    "reason": borrow_schedule.reason,
                    "next_due_at_utc": borrow_schedule.next_due_at_utc,
                },
            )
        borrow_status = "completed" if borrow_sync.status in {"completed", "covered_empty", "skipped"} else borrow_sync.status
        update_latest_operation(
            SOURCE_SYNC_OPERATION_NAMES["ibkr_borrow_availability"],
            borrow_status,
            (
                f"eligible={borrow_sync.eligible:,} written={borrow_sync.written:,} "
                f"failed={borrow_sync.failed:,} status={borrow_sync.status}"
            ),
            rows=borrow_sync.written,
            seconds=borrow_sync.wall_seconds,
        )
        country_schedule = schedule_decision(
            schedule_client,
            config,
            source_name="country_assertions",
            frequency_seconds=config.country_assertion_frequency_seconds,
        )
        if country_schedule.should_run:
            country_started = time.perf_counter()
            update_latest_operation(SOURCE_SYNC_OPERATION_NAMES["country_assertions"], "running", "Writing country assertions from canonical exchange evidence.", seconds=0.0)
            country_result = write_country_assertions(config, reason="source_sync")
            record_source_schedule(
                schedule_client,
                config,
                source_name="country_assertions",
                status=country_result.status,
                rows_written=country_result.rows_written,
                details=asdict(country_result) | {"schedule_reason": country_schedule.reason},
                frequency_seconds=config.country_assertion_frequency_seconds,
            )
            update_latest_operation(
                SOURCE_SYNC_OPERATION_NAMES["country_assertions"],
                country_result.status,
                country_result.reason,
                rows=country_result.rows_written,
                seconds=time.perf_counter() - country_started,
            )
        else:
            update_latest_operation(
                SOURCE_SYNC_OPERATION_NAMES["country_assertions"],
                "skipped",
                f"{country_schedule.reason}; next_due={country_schedule.next_due_at_utc or '-'}",
                rows=0,
                seconds=0.0,
            )
            country_result = None
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
            current_detail_attempted=current_details.attempted,
            current_detail_status=current_details.status,
            current_detail_requested=current_details.requested,
            current_detail_matched=current_details.matched,
            current_detail_written=current_details.written,
            current_detail_failed=current_details.failed,
            ibkr_borrow_attempted=borrow_sync.attempted,
            ibkr_borrow_status=borrow_sync.status,
            ibkr_borrow_eligible=borrow_sync.eligible,
            ibkr_borrow_written=borrow_sync.written,
            ibkr_borrow_failed=borrow_sync.failed,
            country_assertion_status=getattr(country_result, "status", "skipped"),
            country_assertion_written=getattr(country_result, "rows_written", 0),
            wall_seconds=time.perf_counter() - source_sync_started,
            report_path=str(plan_path or ""),
        )
        logger.event(
            "ibkr_borrow_source_sync_completed",
            attempted=borrow_sync.attempted,
            status=borrow_sync.status,
            eligible=borrow_sync.eligible,
            written=borrow_sync.written,
            failed=borrow_sync.failed,
            wall_seconds=borrow_sync.wall_seconds,
            details=borrow_sync.details,
        )
        source_sync_status = "completed" if borrow_status == "completed" and current_details_status == "completed" else "warning"
        write_alert_batch(build_source_sync_alerts(plan), "source_sync")
        fill_reference_facts("after_source_sync")
        add_operation(
            "Source sync",
            source_sync_status,
            (
                f"provider={plan.provider_rows:,} missing={plan.missing_tickers:,} "
                f"overview={plan.overview_fetched:,} ibkr={plan.ibkr_searched:,} "
                f"current_detail_written={current_details.written:,} current_detail_failed={current_details.failed:,} "
                f"borrow_written={borrow_sync.written:,} borrow_failed={borrow_sync.failed:,} "
                f"country_written={getattr(country_result, 'rows_written', 0):,}"
            ),
            rows=plan.missing_tickers,
            seconds=time.perf_counter() - source_sync_started,
        )
        emit(
            "source_sync=done "
            f"provider_rows={plan.provider_rows:,} known_symbols={plan.known_active_symbols:,} "
            f"missing={plan.missing_tickers:,} overview={plan.overview_fetched:,} ibkr={plan.ibkr_searched:,} "
            f"current_detail_written={current_details.written:,} current_detail_failed={current_details.failed:,} "
            f"borrow_written={borrow_sync.written:,} borrow_failed={borrow_sync.failed:,} "
            f"country_written={getattr(country_result, 'rows_written', 0):,} "
            f"saturated={plan.provider_saturated} wall_seconds={time.perf_counter() - source_sync_started:.2f}"
        )
        refresh_reference_state("after_source_sync_writes")

    maintenance_allowed = config.execute and config.maintenance_mode != "skip" and write_policy.writes_allowed
    maintenance_skip_reason = "maintenance_disabled" if config.maintenance_mode == "skip" else write_policy.reason
    if maintenance_allowed:
        if config.resolve_stale_issues:
            started = time.perf_counter()
            resolution = resolve_stale_active_ticker_issues(config)
            add_operation("Resolve issues", "completed", resolution_detail(resolution), rows=resolution.resolved, seconds=time.perf_counter() - started)
            emit("stale_issue_resolution=" + json.dumps(asdict(resolution), sort_keys=True))
        if config.rebuild_tradable_on_execute:
            started = time.perf_counter()
            rebuild = rebuild_tradable_publications(config, reason="maintenance_cycle")
            add_operation("Rebuild SEC bridge and tradable publications", rebuild.status, rebuild.reason, seconds=time.perf_counter() - started)
            emit("tradable_publication_rebuild=" + json.dumps(asdict(rebuild), sort_keys=True))
            audit_started = time.perf_counter()
            report = run_reference_audit(config)
            record.audit = report
            add_operation("Post-maintenance reference audit", report.status, f"{len(report.checks)} checks", seconds=time.perf_counter() - audit_started)
            report_path = write_report(report, config.report_root_win)
            record.report_path = str(report_path or record.report_path)
            logger.event("audit_completed", **audit_log_summary(report, report_path=record.report_path, post_write=True))
            write_alert_batch(build_audit_alerts(report, report_path=record.report_path, post_write=True), "post_maintenance_reference_audit")
            fill_reference_facts("after_tradable_publication_rebuild")
            refresh_terminal()
            if report_path:
                emit(f"post_maintenance_report={report_path}")
    else:
        if config.execute and config.resolve_stale_issues:
            add_operation("Resolve issues", "skipped", maintenance_skip_reason)
        if config.execute and config.rebuild_tradable_on_execute:
            add_operation("Rebuild SEC bridge and tradable publications", "skipped", maintenance_skip_reason)

    if maintenance_allowed and config.market_publication_gap_fill_enabled and (not config.test_write_mode or config.maintenance_mode == "force"):
        started = time.perf_counter()
        from research.mlops.clickhouse import ClickHouseHttpClient, default_clickhouse_password

        schedule_client = ClickHouseHttpClient(config.clickhouse_url, config.clickhouse_user, default_clickhouse_password())
        publication_schedule = schedule_decision(
            schedule_client,
            config,
            source_name="market_publication_gap_fill",
            frequency_seconds=config.market_publication_gap_fill_frequency_seconds,
            force=config.maintenance_mode == "force",
        )
        add_operation(
            "Market publication gap fill",
            "running" if publication_schedule.should_run else "skipped",
            (
                f"schedule={publication_schedule.reason}; next_due={publication_schedule.next_due_at_utc or '-'}; "
                f"recent_days={config.market_publication_gap_fill_days}; deep_start={config.market_publication_deep_backfill_start_date}"
            ),
            seconds=0.0,
        )
        if not publication_schedule.should_run:
            record_source_schedule(
                schedule_client,
                config,
                source_name="market_publication_gap_fill",
                status="skipped_not_due",
                rows_written=0,
                details=asdict(publication_schedule),
                frequency_seconds=config.market_publication_gap_fill_frequency_seconds,
            )
            maintenance = None
        else:
            maintenance = None
        for operation_name in PUBLICATION_OPERATION_NAMES.values():
            ensure_operation(operation_name, "waiting", "not started", seconds=0.0)

        def publication_progress(line: str) -> None:
            publication_source = publication_source_from_line(line)
            if publication_source:
                update_latest_operation(
                    PUBLICATION_OPERATION_NAMES.get(publication_source, "Source: " + publication_source.replace("_", " ")),
                    publication_status_from_line(line),
                    truncate_detail(line),
                    rows=publication_rows_from_line(line),
                    seconds=time.perf_counter() - started,
                )
            update_latest_operation(
                "Market publication gap fill",
                "running",
                truncate_detail(line),
                seconds=time.perf_counter() - started,
            )

        if publication_schedule.should_run:
            maintenance = run_recent_publication_gap_fill(
                config,
                on_progress=publication_progress,
                deep=config.market_publication_deep_backfill_enabled,
            )
        for operation_name in PUBLICATION_OPERATION_NAMES.values():
            latest = next((op for op in reversed(record.operations) if op.name == operation_name), None)
            if latest is not None and latest.status == "waiting":
                update_latest_operation(operation_name, "skipped", "No uncovered recent window reported for this source.", seconds=time.perf_counter() - started)
        if maintenance is not None:
            maintenance_status = "completed" if maintenance.returncode == 0 else "failed"
            record_source_schedule(
                schedule_client,
                config,
                source_name="market_publication_gap_fill",
                status=maintenance_status,
                rows_written=publication_written_rows(maintenance.stdout_tail),
                details=asdict(maintenance),
                frequency_seconds=config.market_publication_gap_fill_frequency_seconds,
            )
            update_latest_operation(
                "Market publication gap fill",
                maintenance_status,
                f"{maintenance.start_date}->{maintenance.end_date}; {maintenance.reason}; {last_nonempty_line(maintenance.stdout_tail)}",
                seconds=time.perf_counter() - started,
            )
            emit("market_publication_gap_fill=" + json.dumps(asdict(maintenance), sort_keys=True))
            refresh_reference_state("after_market_publication_gap_fill")
            write_alert_batch(
                build_publication_maintenance_alert(
                    maintenance_status,
                    maintenance.reason,
                    asdict(maintenance),
                ),
                "market_publication_gap_fill",
            )
    elif config.execute and config.market_publication_gap_fill_enabled:
        reason = "temp_mode_requires_maintenance_force" if config.test_write_mode and config.maintenance_mode != "force" else maintenance_skip_reason
        add_operation("Market publication gap fill", "skipped", reason)
        emit("market_publication_gap_fill=skipped reason=" + reason)
    refresh_reference_state("final")
    record.final_status = report.status
    record.wall_seconds = time.perf_counter() - run_started
    final_memory = memory_snapshot("run_finished")
    if config.daemon_child_max_rss_mb > 0 and final_memory.rss_bytes is not None:
        max_bytes = config.daemon_child_max_rss_mb * 1024 * 1024
        if final_memory.rss_bytes > max_bytes:
            record.final_status = "failed"
            add_operation(
                "Memory guardrail",
                "failed",
                f"rss={final_memory.rss_bytes:,} max={max_bytes:,}",
                rows=final_memory.rss_bytes,
            )
            logger.event("memory_guardrail_failed", max_rss_bytes=max_bytes, memory=final_memory.public_dict())
    logger.event("standard_status_snapshot", snapshot=build_reference_status_snapshot(record))
    logger.event("run_finished", status=record.final_status, wall_seconds=record.wall_seconds, report_path=record.report_path, memory=final_memory.public_dict())
    refresh_terminal()
    stop_terminal()
    emit(f"status={record.final_status} wall_seconds={record.wall_seconds:.2f}")
    if record.final_status == "failed":
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


def publication_source_from_line(line: str) -> str:
    first = str(line or "").strip().split(" ", 1)[0]
    return first if first in PUBLICATION_OPERATION_NAMES else ""


def publication_status_from_line(line: str) -> str:
    text = str(line or "").lower()
    if " status=failed" in text or (" rows_failed=" in text and " rows_failed=0" not in text):
        return "failed"
    if " status=source_not_yet_available" in text or " status=source_not_historical" in text:
        return "skipped"
    if " status=covered_empty" in text or " status=non_publication_day" in text:
        return "completed"
    if " status=completed" in text or " status=success" in text:
        return "completed"
    if "status=" in text:
        return "completed"
    return "running"


def publication_rows_from_line(line: str) -> int | None:
    import re

    match = re.search(r"\bwritten=([0-9,]+)", line)
    if not match:
        match = re.search(r"\brows=([0-9,]+)", line)
    if not match:
        return None
    try:
        return int(match.group(1).replace(",", ""))
    except ValueError:
        return None


def publication_written_rows(output_tail: str) -> int:
    total = 0
    for line in str(output_tail or "").splitlines():
        rows = publication_rows_from_line(line)
        if rows is not None and "written=" in line:
            total += rows
    return total


if __name__ == "__main__":
    main()

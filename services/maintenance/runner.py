from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines.news.benzinga.core.coverage_manifest import (  # noqa: E402
    CoverageManifestConfig,
    find_coverage_gaps,
    load_coverage_intervals as load_news_coverage_intervals,
)
from pipelines.news.benzinga.news_pipeline.config import ClickHouseTargetConfig  # noqa: E402
from pipelines.sec.edgar.sec_pipeline.coverage import (  # noqa: E402
    SecCoverageConfig,
    ensure_coverage_table as ensure_sec_coverage_table,
    plan_coverage_gaps,
)
from pipelines.sec.edgar.sec_pipeline.historical_fill import (  # noqa: E402
    build_historical_fill_plan,
    write_multi_plan_script,
)
from research.mlops.clickhouse import (  # noqa: E402
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    quote_ident,
    sql_string,
)
from research.mlops.env import discover_env_files, load_env_files, secret_status  # noqa: E402
from services.gateway_policy import active_collection_window  # noqa: E402
from services.reference_gateway.market_publications import (  # noqa: E402
    ensure_market_publication_schema,
    find_publication_gaps as find_reference_publication_gaps,
    table_exists as reference_table_exists,
)


WORKSTATION_CODE_ROOT_WIN = Path(r"D:/TradingML/codes/quant_research_workbench_pipelines")
WORKSTATION_SHARE_CODE_ROOT_WIN = Path(r"\\DESKTOP-SAAI85T\Workstation-D\TradingML\codes\quant_research_workbench_pipelines")
WORKSTATION_DATA_ROOT_WIN = Path(r"D:/market-data")
WORKSTATION_SHARE_DATA_ROOT_WIN = Path(r"\\DESKTOP-SAAI85T\Workstation-D\market-data")
WORKSTATION_NAME = "DESKTOP-SAAI85T"

MAINTENANCE_TABLE = "service_maintenance_task_v1"
MAINTENANCE_RUN_TABLE = "service_maintenance_run_v1"

REFERENCE_IMPLEMENTED_PUBLICATION_SPECS: tuple[tuple[str, str, str], ...] = (
    ("finra_short_volume:CNMS", "finra", "daily FINRA consolidated NMS short-volume publication"),
    ("sec_fails_to_deliver", "sec", "SEC fails-to-deliver publication"),
)

REFERENCE_PLANNED_PUBLICATION_SPECS: tuple[tuple[str, str, str], ...] = (
    ("finra_short_interest", "finra", "FINRA exchange-published short-interest source"),
    ("reg_sho_threshold", "exchange_or_sec", "Reg SHO threshold-list source"),
    ("ibkr_borrow_availability", "ibkr", "IBKR point-in-time borrow availability"),
    ("massive_market_snapshot", "massive", "Massive daily market snapshot publication"),
    ("massive_splits", "massive", "Massive stock split publication"),
    ("massive_dividends", "massive", "Massive cash dividend publication"),
    ("massive_ipos", "massive", "Massive IPO publication"),
    ("massive_presentation_assets", "massive", "Massive presentation assets"),
    ("sec_country_assertions", "sec", "SEC/XBRL-derived country assertions"),
)


@dataclass(frozen=True, slots=True)
class TaskRecord:
    task_id: str
    run_id: str
    service: str
    task_kind: str
    status: str
    source_truth: str
    window_start_utc: datetime | None = None
    window_end_utc: datetime | None = None
    rows_checked: int = 0
    rows_missing: int = 0
    rows_repaired: int = 0
    command: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    error: str = ""


@dataclass(frozen=True, slots=True)
class MaintenanceContext:
    run_id: str
    now_utc: datetime
    execute: bool
    auto_run: bool
    services: set[str]
    client: ClickHouseHttpClient
    database: str
    storage_policy: str
    output_root: Path
    code_root_win: Path
    is_workstation: bool
    qmd_api_url: str
    max_auto_gap_days: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "After-hours maintenance coordinator for QMD, Benzinga news, SEC, and reference-publication gateways. "
            "It checks durable coverage/source tables, records action items, and generates "
            "the existing service-specific gap-fill commands without stopping live services."
        )
    )
    parser.add_argument("--services", default="qmd,news,sec,reference", help="Comma-separated subset: qmd,news,sec,reference.")
    parser.add_argument("--execute", action="store_true", help="Write maintenance rows and run allowed auto tasks.")
    parser.add_argument("--auto-run", action="store_true", help="Run generated commands when this host is the workstation and collection is closed.")
    parser.add_argument("--force-active-window", action="store_true", help="Allow auto-run even during 04:00-20:00 ET collection window.")
    parser.add_argument("--database", default=os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_DATABASE") or "q_live")
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--storage-policy", default=os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY") or "")
    parser.add_argument("--output-root-win", default=os.environ.get("SERVICE_MAINTENANCE_OUTPUT_ROOT_WIN") or str(default_output_root()))
    parser.add_argument("--code-root-win", default=os.environ.get("SERVICE_MAINTENANCE_CODE_ROOT_WIN") or str(default_code_root()))
    parser.add_argument("--qmd-api-url", default=os.environ.get("QMD_GATEWAY_API_URL") or "http://127.0.0.1:8795")
    parser.add_argument("--max-auto-gap-days", type=float, default=float(os.environ.get("SERVICE_MAINTENANCE_MAX_AUTO_GAP_DAYS") or "3"))
    parser.add_argument("--news-trailing-lookback-seconds", type=int, default=int(os.environ.get("NEWS_MAINTENANCE_TRAILING_LOOKBACK_SECONDS") or "600"))
    parser.add_argument("--news-merge-tolerance-seconds", type=int, default=int(os.environ.get("NEWS_MAINTENANCE_MERGE_TOLERANCE_SECONDS") or "0"))
    parser.add_argument("--sec-min-date", default=os.environ.get("SEC_MAINTENANCE_MIN_DATE") or "2019-01-01")
    parser.add_argument("--reference-min-date", default=os.environ.get("REFERENCE_MAINTENANCE_MIN_DATE") or "2019-01-01")
    parser.add_argument("--reference-read-database", default=os.environ.get("REFERENCE_CLICKHOUSE_READ_DATABASE") or os.environ.get("REFERENCE_GATEWAY_READ_DATABASE") or "")
    parser.add_argument("--reference-write-database", default=os.environ.get("REFERENCE_CLICKHOUSE_WRITE_DATABASE") or os.environ.get("REFERENCE_GATEWAY_WRITE_DATABASE") or "")
    return parser.parse_args()


def main() -> None:
    loaded_env = load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args()
    services = {item.strip().lower() for item in args.services.split(",") if item.strip()}
    invalid = services - {"qmd", "news", "sec", "reference"}
    if invalid:
        raise SystemExit(f"Invalid services: {sorted(invalid)}")
    now = datetime.now(UTC)
    run_id = f"maintenance_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    output_root = Path(args.output_root_win) / run_id
    output_root.mkdir(parents=True, exist_ok=True)
    ctx = MaintenanceContext(
        run_id=run_id,
        now_utc=now,
        execute=bool(args.execute),
        auto_run=bool(args.auto_run and (args.force_active_window or not active_collection_window(service_prefix="QMD"))),
        services=services,
        client=client,
        database=args.database,
        storage_policy=args.storage_policy,
        output_root=output_root,
        code_root_win=Path(args.code_root_win),
        is_workstation=is_workstation(),
        qmd_api_url=args.qmd_api_url.rstrip("/"),
        max_auto_gap_days=max(0.0, args.max_auto_gap_days),
    )
    if ctx.execute:
        ensure_tables(ctx)
        insert_run_row(ctx, status="running", loaded_env=[str(path) for path in loaded_env])
    tasks: list[TaskRecord] = []
    try:
        if "qmd" in services:
            tasks.extend(check_qmd(ctx))
        if "news" in services:
            tasks.extend(check_news(ctx, args))
        if "sec" in services:
            tasks.extend(check_sec(ctx, args))
        if "reference" in services:
            tasks.extend(check_reference_publications(ctx, args))
        if ctx.execute:
            insert_tasks(ctx, tasks)
            run_allowed_tasks(ctx, tasks)
            insert_run_row(ctx, status="completed", loaded_env=[str(path) for path in loaded_env])
    except Exception as exc:
        if ctx.execute:
            insert_run_row(ctx, status="failed", error=str(exc), loaded_env=[str(path) for path in loaded_env])
        raise
    write_summary(ctx, tasks, loaded_env)
    print_summary(ctx, tasks)


def check_qmd(ctx: MaintenanceContext) -> list[TaskRecord]:
    tasks: list[TaskRecord] = []
    source = qmd_market_sip_source_window(ctx)
    live = qmd_live_coverage_window(ctx)
    rows = qmd_row_counts(ctx)
    tasks.append(
        TaskRecord(
            task_id=new_task_id("qmd_source"),
            run_id=ctx.run_id,
            service="qmd",
            task_kind="historical_source_audit",
            status="ok" if source else "missing_source",
            source_truth="market_sip_compact.events",
            window_start_utc=source.get("start") if source else None,
            window_end_utc=source.get("end") if source else None,
            rows_checked=int(source.get("rows") or 0) if source else 0,
            details={
                "message": "QMD historical repair source is market_sip_compact.events/events_ordinal_continuity, not raw flatfiles.",
                "source_database": os.environ.get("QMD_HISTORICAL_CLICKHOUSE_DATABASE") or "market_sip_compact",
                "row_counts": rows,
            },
        )
    )
    if live:
        tasks.append(
            TaskRecord(
                task_id=new_task_id("qmd_live"),
                run_id=ctx.run_id,
                service="qmd",
                task_kind="q_live_coverage_audit",
                status="ok",
                source_truth=f"{ctx.database}.qmd_live_event_coverage_v1",
                window_start_utc=live.get("start"),
                window_end_utc=live.get("end"),
                rows_checked=int(live.get("coverage_rows") or 0),
                details={
                    "message": "QMD gateway repairs live gaps through REST replay fanout so compact events and bars remain coherent.",
                    "event_rows": rows.get("live_market_events_v1", 0),
                    "bar_rows": rows.get("live_market_bars", 0),
                },
            )
        )
    else:
        tasks.append(
            TaskRecord(
                task_id=new_task_id("qmd_live_missing"),
                run_id=ctx.run_id,
                service="qmd",
                task_kind="q_live_coverage_audit",
                status="needs_gateway_start",
                source_truth=f"{ctx.database}.qmd_live_event_coverage_v1",
                command=f"Start qmd-gateway; it will open q_live coverage and repair recent live gaps through {ctx.qmd_api_url}/snapshot/maintenance.",
                details={
                    "message": "No q_live event coverage rows were found. The QMD gateway must create live coverage while ingesting.",
                    "row_counts": rows,
                },
            )
        )
    qmd_api = probe_qmd_api(ctx)
    tasks.append(
        TaskRecord(
            task_id=new_task_id("qmd_api"),
            run_id=ctx.run_id,
            service="qmd",
            task_kind="gateway_api_probe",
            status="ok" if qmd_api.get("ok") else "unreachable",
            source_truth=ctx.qmd_api_url,
            details=qmd_api,
        )
    )
    return tasks


def check_news(ctx: MaintenanceContext, args: argparse.Namespace) -> list[TaskRecord]:
    target = ClickHouseTargetConfig.from_env()
    config = CoverageManifestConfig(
        database=target.database,
        coverage_table=target.coverage_table,
        normalized_table=target.normalized_table,
        storage_policy=ctx.storage_policy,
    )
    intervals = load_news_coverage_intervals(ctx.client, config)
    gaps = find_coverage_gaps(
        intervals,
        end_utc=ctx.now_utc,
        merge_tolerance_seconds=args.news_merge_tolerance_seconds,
        trailing_live_lookback_seconds=args.news_trailing_lookback_seconds,
    )
    row_count = safe_int_query(ctx.client, f"SELECT count() FROM {qn(config.database)}.{qn(config.normalized_table)} FINAL")
    tasks = [
        TaskRecord(
            task_id=new_task_id("news_coverage"),
            run_id=ctx.run_id,
            service="news",
            task_kind="coverage_audit",
            status="ok" if not gaps else "gap_detected",
            source_truth=f"{config.database}.{config.coverage_table}",
            rows_checked=len(intervals),
            rows_missing=len(gaps),
            details={
                "normalized_rows": row_count,
                "coverage_intervals": len(intervals),
                "gap_seconds": sum(max(0.0, gap.seconds) for gap in gaps),
            },
        )
    ]
    if gaps:
        start = min(gap.start_utc for gap in gaps)
        end = max(gap.end_utc for gap in gaps)
        command = news_gap_fill_command(ctx, start, end, config)
        tasks.append(
            TaskRecord(
                task_id=new_task_id("news_gap"),
                run_id=ctx.run_id,
                service="news",
                task_kind="gap_fill",
                status="auto_runnable" if should_auto_run(ctx, "NEWS", start, end) else "manual_required",
                source_truth="Benzinga provider via Massive",
                window_start_utc=start,
                window_end_utc=end,
                rows_missing=len(gaps),
                command=command,
                details={
                    "gap_count": len(gaps),
                    "gap_days": round((end - start).total_seconds() / 86400.0, 3),
                    "policy": "run after hours on workstation; live news gateway keeps polling separately",
                },
            )
        )
    return tasks


def check_sec(ctx: MaintenanceContext, args: argparse.Namespace) -> list[TaskRecord]:
    coverage_table = os.environ.get("SEC_COVERAGE_TABLE") or "sec_coverage_manifest_v1"
    config = SecCoverageConfig(database=os.environ.get("SEC_CLICKHOUSE_WRITE_DATABASE") or ctx.database, coverage_table=coverage_table, storage_policy=ctx.storage_policy)
    ensure_sec_coverage_table(ctx.client, config)
    plan = plan_coverage_gaps(
        ctx.client,
        config,
        read_database=os.environ.get("SEC_CLICKHOUSE_READ_DATABASE") or ctx.database,
        now_utc=ctx.now_utc,
    )
    tasks = [
        TaskRecord(
            task_id=new_task_id("sec_coverage"),
            run_id=ctx.run_id,
            service="sec",
            task_kind="coverage_audit",
            status="ok" if not plan.gaps else "gap_detected",
            source_truth=f"{config.database}.{config.coverage_table}",
            rows_checked=plan.interval_count,
            rows_missing=len(plan.gaps),
            details={
                "kinds_checked": plan.kinds_checked,
                "coverage_intervals": plan.interval_count,
                "min_date": args.sec_min_date,
            },
        )
    ]
    if plan.gaps:
        start = max(parse_date(args.sec_min_date), min(gap.start_utc.date() for gap in plan.gaps))
        end = max(gap.end_utc.date() for gap in plan.gaps) + timedelta(days=1)
        fill_plan = build_historical_fill_plan(
            start_date=start,
            end_date=end,
            code_root_win=ctx.code_root_win,
            python_executable="python",
            execute=True,
            read_database=os.environ.get("SEC_CLICKHOUSE_READ_DATABASE") or ctx.database,
            write_database=config.database,
            extra_args=[
                "--coverage-table",
                config.coverage_table,
                "--output-root-win",
                str(default_data_root() / "prepared" / "sec_historical_gap_fill"),
                "--resume-from-coverage",
            ],
        )
        script_path = ctx.output_root / "sec_gap_fill" / f"{ctx.run_id}_sec_gap_fill.ps1"
        write_multi_plan_script([fill_plan], script_path)
        status = "auto_runnable" if should_auto_run(ctx, "SEC", datetime.combine(start, time.min, tzinfo=UTC), datetime.combine(end, time.min, tzinfo=UTC)) else "manual_required"
        tasks.append(
            TaskRecord(
                task_id=new_task_id("sec_gap"),
                run_id=ctx.run_id,
                service="sec",
                task_kind="gap_fill",
                status=status,
                source_truth="SEC EDGAR feed, daily archives, submissions, companyfacts",
                window_start_utc=datetime.combine(start, time.min, tzinfo=UTC),
                window_end_utc=datetime.combine(end, time.min, tzinfo=UTC),
                rows_missing=len(plan.gaps),
                command=str(script_path),
                details={
                    "gap_count": len(plan.gaps),
                    "gap_kinds": sorted({gap.coverage_kind for gap in plan.gaps}),
                    "script_path": str(script_path),
                    "policy": "unified SEC historical gap fill writes filing parents, text, XBRL, repair, audit, and coverage",
                },
            )
        )
    return tasks


def check_reference_publications(ctx: MaintenanceContext, args: argparse.Namespace) -> list[TaskRecord]:
    read_database = args.reference_read_database or ctx.database
    write_database = args.reference_write_database or ctx.database
    if ctx.execute:
        ensure_market_publication_schema(
            ctx.client,
            database=write_database,
            read_database=read_database,
            storage_policy=ctx.storage_policy,
        )
    elif not reference_table_exists(ctx.client, write_database, "market_reference_publication_coverage_v1"):
        return [
            TaskRecord(
                task_id=new_task_id("reference_publications_schema"),
                run_id=ctx.run_id,
                service="reference",
                task_kind="market_publication_schema",
                status="schema_missing",
                source_truth=f"{write_database}.market_reference_publication_coverage_v1",
                command="python -m services.reference_gateway.main --ensure-market-publication-schema",
                details={
                    "message": "Market-publication coverage table is missing. Run the schema ensure step before dry-run gap discovery.",
                    "policy": "dry-run maintenance does not create tables",
                    "read_database": read_database,
                    "write_database": write_database,
                },
            )
        ]
    start = parse_date(args.reference_min_date)
    end = ctx.now_utc.date() + timedelta(days=1)
    source_specs = list(REFERENCE_IMPLEMENTED_PUBLICATION_SPECS)
    all_gaps = []
    details: dict[str, Any] = {}
    for coverage_kind, source_system, label in source_specs:
        gaps = find_reference_publication_gaps(
            ctx.client,
            database=write_database,
            coverage_kind=coverage_kind,
            source_system=source_system,
            start_date=start,
            end_date=end,
        )
        all_gaps.extend(gaps)
        details[coverage_kind] = {"source": label, "gaps": len(gaps), "days": sum(gap.missing_days for gap in gaps)}
    tasks = [
        TaskRecord(
            task_id=new_task_id("reference_publications"),
            run_id=ctx.run_id,
            service="reference",
            task_kind="market_publication_coverage_audit",
            status="ok" if not all_gaps else "gap_detected",
            source_truth=f"{write_database}.market_reference_publication_coverage_v1",
            rows_checked=len(source_specs),
            rows_missing=len(all_gaps),
            details={**details, "read_database": read_database, "write_database": write_database},
        )
    ]
    if all_gaps:
        window_start = min(gap.start_date for gap in all_gaps)
        window_end = max(gap.end_date for gap in all_gaps)
        command = reference_gap_fill_command(ctx, window_start, window_end, read_database=read_database, write_database=write_database)
        tasks.append(
            TaskRecord(
                task_id=new_task_id("reference_publication_gap"),
                run_id=ctx.run_id,
                service="reference",
                task_kind="market_publication_gap_fill",
                status="auto_runnable" if should_auto_run(ctx, "REFERENCE", datetime.combine(window_start, time.min, tzinfo=UTC), datetime.combine(window_end, time.min, tzinfo=UTC)) else "manual_required",
                source_truth="FINRA daily short volume and SEC fails-to-deliver publications",
                window_start_utc=datetime.combine(window_start, time.min, tzinfo=UTC),
                window_end_utc=datetime.combine(window_end, time.min, tzinfo=UTC),
                rows_missing=len(all_gaps),
                command=command,
                details={
                    "gap_days": sum(gap.missing_days for gap in all_gaps),
                    "gap_kinds": sorted({gap.coverage_kind for gap in all_gaps}),
                    "read_database": read_database,
                    "write_database": write_database,
                    "policy": "run after hours on workstation; publication coverage prevents repeat fills",
                },
            )
        )
    tasks.append(
        TaskRecord(
            task_id=new_task_id("reference_publication_planned"),
            run_id=ctx.run_id,
            service="reference",
            task_kind="market_publication_planned_sources",
            status="planned_not_implemented",
            source_truth="reference publication source catalog",
            rows_checked=len(REFERENCE_PLANNED_PUBLICATION_SPECS),
            details={
                "message": "These source kinds are modeled in schema/table groups but do not have enabled historical writers yet.",
                "sources": [
                    {"coverage_kind": kind, "source_system": source, "label": label}
                    for kind, source, label in REFERENCE_PLANNED_PUBLICATION_SPECS
                ],
            },
        )
    )
    return tasks


def ensure_tables(ctx: MaintenanceContext) -> None:
    settings = ["index_granularity = 8192"]
    if ctx.storage_policy.strip():
        settings.append(f"storage_policy = {sql_string(ctx.storage_policy.strip())}")
    ctx.client.execute(f"CREATE DATABASE IF NOT EXISTS {qn(ctx.database)}")
    ctx.client.execute(
        f"""
CREATE TABLE IF NOT EXISTS {qn(ctx.database)}.{qn(MAINTENANCE_RUN_TABLE)}
(
    run_id String,
    status LowCardinality(String),
    services Array(String),
    host String,
    execute Bool,
    auto_run Bool,
    started_at_utc DateTime64(3, 'UTC'),
    updated_at_utc DateTime64(3, 'UTC'),
    completed_at_utc Nullable(DateTime64(3, 'UTC')),
    error String,
    metadata_json String
)
ENGINE = ReplacingMergeTree(updated_at_utc)
PARTITION BY toYYYYMM(started_at_utc)
ORDER BY (run_id)
SETTINGS {", ".join(settings)}
""".strip()
    )
    ctx.client.execute(
        f"""
CREATE TABLE IF NOT EXISTS {qn(ctx.database)}.{qn(MAINTENANCE_TABLE)}
(
    task_id String,
    run_id String,
    service LowCardinality(String),
    task_kind LowCardinality(String),
    status LowCardinality(String),
    source_truth String,
    window_start_utc Nullable(DateTime64(3, 'UTC')),
    window_end_utc Nullable(DateTime64(3, 'UTC')),
    rows_checked UInt64,
    rows_missing UInt64,
    rows_repaired UInt64,
    command String,
    error String,
    details_json String,
    created_at_utc DateTime64(3, 'UTC'),
    updated_at_utc DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(updated_at_utc)
PARTITION BY toYYYYMM(created_at_utc)
ORDER BY (service, task_kind, run_id, task_id)
SETTINGS {", ".join(settings)}
""".strip()
    )


def insert_run_row(ctx: MaintenanceContext, *, status: str, error: str = "", loaded_env: list[str] | None = None) -> None:
    now = datetime.now(UTC)
    row = {
        "run_id": ctx.run_id,
        "status": status,
        "services": sorted(ctx.services),
        "host": socket.gethostname(),
        "execute": ctx.execute,
        "auto_run": ctx.auto_run,
        "started_at_utc": dt64(ctx.now_utc),
        "updated_at_utc": dt64(now),
        "completed_at_utc": dt64(now) if status in {"completed", "failed"} else None,
        "error": error,
        "metadata_json": json.dumps(
            {
                "output_root": str(ctx.output_root),
                "code_root_win": str(ctx.code_root_win),
                "is_workstation": ctx.is_workstation,
                "loaded_env_files": loaded_env or [],
                "secret_status": secret_status(["MASSIVE_API_KEY", "REAL_LIVE_CLICKHOUSE_WRITE_URL", "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD"]),
            },
            sort_keys=True,
        ),
    }
    ctx.client.execute(f"INSERT INTO {qn(ctx.database)}.{qn(MAINTENANCE_RUN_TABLE)} FORMAT JSONEachRow\n{json.dumps(row, default=str)}")


def insert_tasks(ctx: MaintenanceContext, tasks: list[TaskRecord]) -> None:
    if not tasks:
        return
    now = datetime.now(UTC)
    rows = []
    for task in tasks:
        rows.append(
            {
                "task_id": task.task_id,
                "run_id": task.run_id,
                "service": task.service,
                "task_kind": task.task_kind,
                "status": task.status,
                "source_truth": task.source_truth,
                "window_start_utc": dt64(task.window_start_utc) if task.window_start_utc else None,
                "window_end_utc": dt64(task.window_end_utc) if task.window_end_utc else None,
                "rows_checked": max(0, int(task.rows_checked)),
                "rows_missing": max(0, int(task.rows_missing)),
                "rows_repaired": max(0, int(task.rows_repaired)),
                "command": task.command,
                "error": task.error,
                "details_json": json.dumps(task.details, sort_keys=True, default=str),
                "created_at_utc": dt64(now),
                "updated_at_utc": dt64(now),
            }
        )
    payload = "\n".join(json.dumps(row, default=str) for row in rows)
    ctx.client.execute(f"INSERT INTO {qn(ctx.database)}.{qn(MAINTENANCE_TABLE)} FORMAT JSONEachRow\n{payload}")


def run_allowed_tasks(ctx: MaintenanceContext, tasks: list[TaskRecord]) -> None:
    if not ctx.auto_run:
        return
    for task in tasks:
        if task.status != "auto_runnable" or not task.command:
            continue
        if task.service == "sec" and task.command.lower().endswith(".ps1"):
            subprocess.Popen(["powershell", "-ExecutionPolicy", "Bypass", "-File", task.command], cwd=str(ctx.code_root_win))
        elif task.service in {"news", "reference"}:
            subprocess.Popen(task.command, cwd=str(ctx.code_root_win), shell=True)


def qmd_market_sip_source_window(ctx: MaintenanceContext) -> dict[str, Any]:
    database = os.environ.get("QMD_HISTORICAL_CLICKHOUSE_DATABASE") or "market_sip_compact"
    sql = (
        "SELECT min(source_date), max(source_date), sum(event_count) "
        f"FROM {qn(database)}.events_ordinal_continuity "
        "WHERE source_date >= toDate('2019-01-01') FORMAT TSV"
    )
    try:
        raw = ctx.client.execute(sql).strip().split("\t")
    except Exception:
        return {}
    if len(raw) != 3 or not raw[0] or raw[0].startswith("\\N"):
        return {}
    start_date = parse_date(raw[0])
    end_date = parse_date(raw[1])
    return {
        "start": datetime.combine(start_date, time.min, tzinfo=UTC),
        "end": datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=UTC),
        "rows": int(float(raw[2] or "0")),
    }


def qmd_live_coverage_window(ctx: MaintenanceContext) -> dict[str, Any]:
    table = os.environ.get("QMD_LIVE_EVENT_COVERAGE_TABLE") or "qmd_live_event_coverage_v1"
    sql = (
        "SELECT min(coverage_start_utc), max(coverage_end_utc), count() "
        f"FROM {qn(ctx.database)}.{qn(table)} FINAL "
        "WHERE coverage_kind = 'q_live_events' "
        "AND status IN ('repair_completed','coverage_bootstrap','compact_persisted','bars_persisted','running') FORMAT TSV"
    )
    try:
        raw = ctx.client.execute(sql).strip().split("\t")
    except Exception:
        return {}
    if len(raw) != 3 or not raw[0] or raw[0].startswith("\\N"):
        return {}
    return {"start": parse_dt(raw[0]), "end": parse_dt(raw[1]), "coverage_rows": int(raw[2] or "0")}


def qmd_row_counts(ctx: MaintenanceContext) -> dict[str, int]:
    output: dict[str, int] = {}
    for table in ["live_market_events_v1", "live_event_ordinal_continuity", "live_market_bars", "qmd_gap_fill_symbol_universe_v1"]:
        output[table] = safe_int_query(ctx.client, f"SELECT count() FROM {qn(ctx.database)}.{qn(table)}")
    return output


def probe_qmd_api(ctx: MaintenanceContext) -> dict[str, Any]:
    try:
        from urllib import request

        with request.urlopen(ctx.qmd_api_url + "/snapshot/maintenance", timeout=3) as response:
            body = response.read().decode("utf-8", errors="replace")
        payload = json.loads(body)
        return {"ok": True, "snapshot": payload}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def news_gap_fill_command(ctx: MaintenanceContext, start: datetime, end: datetime, config: CoverageManifestConfig) -> str:
    script = ctx.code_root_win / "pipelines" / "news" / "benzinga" / "news_benzinga_provider_gap_fill.py"
    parts = [
        "python",
        str(script),
        "--start-utc",
        iso_z(start),
        "--end-utc",
        iso_z(end),
        "--workers",
        os.environ.get("NEWS_MAINTENANCE_WORKERS") or "4",
        "--database",
        config.database,
        "--normalized-table",
        config.normalized_table,
        "--coverage-table",
        config.coverage_table,
        "--execute",
    ]
    return " ".join(ps_quote(part) for part in parts)


def reference_gap_fill_command(ctx: MaintenanceContext, start: date, end: date, *, read_database: str, write_database: str) -> str:
    script = ctx.code_root_win / "pipelines" / "reference_data" / "market_publications_historical_gap_fill.py"
    parts = [
        "python",
        str(script),
        "--start-date",
        start.isoformat(),
        "--end-date",
        end.isoformat(),
        "--read-database",
        read_database,
        "--write-database",
        write_database,
        "--sources",
        os.environ.get("REFERENCE_MAINTENANCE_PUBLICATION_SOURCES") or "finra_short_volume,sec_fails_to_deliver",
        "--finra-venues",
        os.environ.get("REFERENCE_MAINTENANCE_FINRA_VENUES") or "CNMS",
        "--output-root-win",
        str(default_data_root() / "prepared" / "reference_market_publications"),
        "--resume-from-coverage",
        "--execute",
    ]
    return " ".join(ps_quote(part) for part in parts)


def should_auto_run(ctx: MaintenanceContext, service_prefix: str, start: datetime, end: datetime) -> bool:
    days = max(0.0, (end - start).total_seconds() / 86400.0)
    return bool(ctx.is_workstation and ctx.auto_run and days <= ctx.max_auto_gap_days and not active_collection_window(service_prefix=service_prefix))


def write_summary(ctx: MaintenanceContext, tasks: list[TaskRecord], loaded_env: list[Path]) -> None:
    payload = {
        "run_id": ctx.run_id,
        "created_at_utc": iso_z(ctx.now_utc),
        "execute": ctx.execute,
        "auto_run": ctx.auto_run,
        "services": sorted(ctx.services),
        "output_root": str(ctx.output_root),
        "tasks": [asdict(task) for task in tasks],
        "loaded_env_files": [str(path) for path in loaded_env],
    }
    (ctx.output_root / "maintenance_summary.json").write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    lines = ["# Service Maintenance Summary", "", f"- run_id: `{ctx.run_id}`", f"- execute: `{ctx.execute}`", f"- auto_run: `{ctx.auto_run}`", ""]
    for task in tasks:
        lines.append(f"## {task.service} / {task.task_kind}")
        lines.append(f"- status: `{task.status}`")
        lines.append(f"- source_truth: `{task.source_truth}`")
        if task.window_start_utc and task.window_end_utc:
            lines.append(f"- window: `{iso_z(task.window_start_utc)}` -> `{iso_z(task.window_end_utc)}`")
        if task.command:
            lines.append(f"- command: `{task.command}`")
        if task.error:
            lines.append(f"- error: `{task.error}`")
        lines.append("")
    (ctx.output_root / "maintenance_summary.md").write_text("\n".join(lines), encoding="utf-8")


def print_summary(ctx: MaintenanceContext, tasks: list[TaskRecord]) -> None:
    print("=" * 96, flush=True)
    print("After-hours service maintenance", flush=True)
    print(f"run_id={ctx.run_id}", flush=True)
    print(f"execute={ctx.execute} auto_run={ctx.auto_run} output_root={ctx.output_root}", flush=True)
    for task in tasks:
        window = ""
        if task.window_start_utc and task.window_end_utc:
            window = f" [{iso_z(task.window_start_utc)} -> {iso_z(task.window_end_utc)}]"
        print(f"{task.service:4s} {task.task_kind:28s} {task.status:16s} source={task.source_truth}{window}", flush=True)
        if task.command:
            print(f"  command: {task.command}", flush=True)
    print("=" * 96, flush=True)


def safe_int_query(client: ClickHouseHttpClient, sql: str) -> int:
    try:
        return int((client.execute(sql).strip() or "0").splitlines()[0])
    except Exception:
        return 0


def default_output_root() -> Path:
    return default_data_root() / "prepared" / "service_maintenance"


def default_data_root() -> Path:
    if is_workstation() and path_exists(WORKSTATION_DATA_ROOT_WIN):
        return WORKSTATION_DATA_ROOT_WIN
    if path_exists(WORKSTATION_SHARE_DATA_ROOT_WIN):
        return WORKSTATION_SHARE_DATA_ROOT_WIN
    return Path("D:/market-data")


def default_code_root() -> Path:
    if is_workstation() and path_exists(WORKSTATION_CODE_ROOT_WIN):
        return WORKSTATION_CODE_ROOT_WIN
    if path_exists(WORKSTATION_SHARE_CODE_ROOT_WIN):
        return WORKSTATION_SHARE_CODE_ROOT_WIN
    return REPO_ROOT


def path_exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def is_workstation() -> bool:
    return os.environ.get("COMPUTERNAME", "").strip().upper() == WORKSTATION_NAME


def qn(value: str) -> str:
    return quote_ident(value)


def parse_date(value: str) -> date:
    return date.fromisoformat(value[:10])


def parse_dt(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    if "T" not in text and " " in text:
        text = text.replace(" ", "T") + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def dt64(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def iso_z(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def new_task_id(prefix: str) -> str:
    return f"{prefix}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


def ps_quote(value: str) -> str:
    if not value or any(ch.isspace() for ch in value):
        return '"' + value.replace('"', '\\"') + '"'
    return value


if __name__ == "__main__":
    main()

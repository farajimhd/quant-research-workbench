from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time as dt_time, timedelta
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines.sec.edgar.sec_pipeline.config import SecPipelineConfig, env_string  # noqa: E402
from pipelines.sec.edgar.sec_bulk_sources import DEFAULT_BULK_SOURCES, require_complete_bulk_sources  # noqa: E402
from pipelines.sec.edgar.sec_pipeline.coverage import (  # noqa: E402
    KIND_BULK_COMPANYFACTS,
    KIND_BULK_SUBMISSIONS,
    KIND_DAILY_ARCHIVE,
    KIND_INTEGRITY_AUDIT,
    KIND_LIVE_FEED,
    KIND_TEXT_EXTRACTION,
    SecCoverageConfig,
    ensure_coverage_table,
    insert_coverage,
    new_coverage_id,
)
from research.mlops.clickhouse import ClickHouseHttpClient  # noqa: E402
from research.mlops.env import discover_env_files, load_env_files, secret_status  # noqa: E402


DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_historical_gap_fill")
DEFAULT_PARTS_ROOT_WIN = Path("D:/market-data")
DEFAULT_PARTS_ROOT_CH = "/mnt/d/market-data"
DEFAULT_SEC_BRIDGE_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/q_live_migration/step_06_bridge_features")
DEFAULT_SEC_CONTEXT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_context")
DEFAULT_START_DATE = "2019-01-01"
FINALIZE_ONLY_STAGES = {
    "filing-entity-backfill",
    "missing-document-repair",
    "filing-parent-reconcile",
    "acceptance-submissions-enrichment",
    "acceptance-raw-metadata-repair",
    "acceptance-archive-repair",
    "archive-identity-audit",
    "sec-bridge-rebuild",
    "sec-context-build",
    "integrity-audit",
}


@dataclass(frozen=True, slots=True)
class StageCommand:
    stage: str
    command: list[str]
    log_path: Path
    mutates_database: bool
    coverage_kinds: tuple[str, ...] = ()
    start_date: str = ""
    end_date: str = ""


@dataclass(frozen=True, slots=True)
class StageResult:
    stage: str
    status: str
    returncode: int
    elapsed_seconds: float
    log_path: str
    command: list[str]


class HistoricalFillProgress:
    def __init__(self, layout: str, commands: list[StageCommand], run_root: Path) -> None:
        self.layout = layout
        self.commands = commands
        self.run_root = run_root
        self.enabled = False
        self.status_by_stage = {command.stage: "pending" for command in commands}
        self.elapsed_by_stage: dict[str, float] = {}
        self.current_stage = ""
        self.current_command = ""
        self.output_tail: list[str] = []
        self.archive_worker_states: dict[int, dict[str, object]] = {}
        self.archive_overall: dict[str, object] = {}
        self.archive_failure: dict[str, object] = {}
        self.archive_cleanup: dict[str, object] = {}
        self._live = None
        self._render = None

    def __enter__(self) -> "HistoricalFillProgress":
        if self.layout == "text" or (self.layout == "auto" and not sys.stdout.isatty()):
            return self
        try:
            from rich.console import Group
            from rich.live import Live
            from rich.panel import Panel
            from rich.table import Table
            from rich.text import Text
        except Exception:
            return self

        def render() -> object:
            table = Table(title="SEC Historical Gap Fill", expand=True)
            table.add_column("#", justify="right", width=4)
            table.add_column("Stage")
            table.add_column("Status", width=16)
            table.add_column("Elapsed", justify="right", width=10)
            table.add_column("Log")
            for index, command in enumerate(self.commands, start=1):
                status = self.status_by_stage.get(command.stage, "pending")
                elapsed = self.elapsed_by_stage.get(command.stage)
                table.add_row(
                    str(index),
                    command.stage,
                    status,
                    f"{elapsed:.1f}s" if elapsed is not None else "-",
                    str(command.log_path),
                )
            archive_table = None
            if self.current_stage == "archive-text-rebuild" and self.archive_worker_states:
                archive_table = Table(title="Archive Worker Lanes", expand=True)
                archive_table.add_column("Lane", justify="right", width=5)
                archive_table.add_column("Archive", width=22)
                archive_table.add_column("Extract", justify="right", width=9)
                archive_table.add_column("Preflight", justify="right", width=10)
                archive_table.add_column("Insert", justify="right", width=9)
                archive_table.add_column("Verify", justify="right", width=9)
                archive_table.add_column("Cleanup", justify="right", width=9)
                archive_table.add_column("Progress", width=24)
                archive_table.add_column("Rows", justify="right", width=10)
                archive_table.add_column("Temp", justify="right", width=9)
                archive_table.add_column("Status", width=10)
                for lane in sorted(self.archive_worker_states):
                    state = self.archive_worker_states[lane]
                    durations = state.get("durations") if isinstance(state.get("durations"), dict) else {}
                    stage = str(state.get("stage") or "")
                    position = int(state.get("position") or 0)
                    total = int(state.get("total") or 0)
                    archive_table.add_row(
                        str(lane),
                        str(state.get("archive") or "-"),
                        archive_stage_cell("extract", stage, durations),
                        archive_stage_cell("preflight", stage, durations),
                        archive_stage_cell("insert", stage, durations),
                        archive_stage_cell("verify", stage, durations),
                        archive_stage_cell("cleanup", stage, durations),
                        archive_progress_bar(position, total),
                        f"{int(state.get('rows') or 0):,}",
                        format_bytes(int(state.get("temp_bytes") or 0)),
                        str(state.get("status") or "pending"),
                    )
            tail_text = Text("\n".join(self.output_tail[-18:]) or "No stage output yet.", no_wrap=False)
            current = Text(f"run_root={self.run_root}\ncurrent_stage={self.current_stage or '-'}\ncommand={self.current_command or '-'}", no_wrap=False)
            sections = [table, Panel(current, title="Current")]
            if self.current_stage == "archive-text-rebuild" and self.archive_cleanup:
                cleanup = self.archive_cleanup
                cleanup_text = Text(
                    f"stage={cleanup.get('stage', '-')} failed_attempts={int(cleanup.get('attempts') or 0):,} "
                    f"rows_removed={int(cleanup.get('rows') or 0):,} batches={int(cleanup.get('batches') or 0):,}",
                    no_wrap=False,
                )
                sections.append(Panel(cleanup_text, title="Failed Insert Cleanup"))
            if self.archive_failure:
                failure = self.archive_failure
                failure_text = Text(
                    f"lane={failure.get('lane', '-')} archive={failure.get('archive', '-')}\n"
                    f"{failure.get('error') or 'Archive worker failed without an error message.'}",
                    no_wrap=False,
                )
                sections.append(Panel(failure_text, title="Archive Failure - Stopping", border_style="red"))
            if archive_table is not None:
                overall_total = int(self.archive_overall.get("total") or 0)
                already = int(self.archive_overall.get("already_completed") or 0)
                lane_completed = sum(
                    int(state.get("position") or 0)
                    if str(state.get("stage") or "") == "done"
                    else max(0, int(state.get("position") or 0) - 1)
                    for state in self.archive_worker_states.values()
                )
                sections.append(Panel(archive_progress_bar(already + lane_completed, overall_total), title="Overall Archive Progress"))
                sections.append(archive_table)
            else:
                sections.append(Panel(tail_text, title="Recent Output"))
            return Group(*sections)

        self._render = render
        self._live = Live(render(), refresh_per_second=4, screen=True)
        self._live.__enter__()
        self.enabled = True
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._live is not None:
            self._live.__exit__(exc_type, exc, tb)

    def start_stage(self, command: StageCommand, actual_command: list[str]) -> None:
        self.current_stage = command.stage
        self.current_command = format_command(actual_command)
        self.status_by_stage[command.stage] = "running"
        self.refresh()

    def log_line(self, line: str) -> None:
        clean = line.rstrip()
        if clean.startswith("SEC_ARCHIVE_EVENT="):
            try:
                payload = json.loads(clean.removeprefix("SEC_ARCHIVE_EVENT="))
            except json.JSONDecodeError:
                payload = {}
            if payload.get("kind") == "init":
                self.archive_overall = payload
                for item in payload.get("lanes") or []:
                    lane = int(item.get("lane") or 0)
                    if lane:
                        self.archive_worker_states[lane] = {"lane": lane, "total": int(item.get("total") or 0), "status": "pending"}
            elif payload.get("kind") == "lane":
                lane = int(payload.get("lane") or 0)
                if lane:
                    self.archive_worker_states[lane] = payload
                if payload.get("stage") == "failed" and not self.archive_failure:
                    self.archive_failure = payload
                    if self.current_stage:
                        self.status_by_stage[self.current_stage] = "stopping: failed"
            elif payload.get("kind") == "cleanup":
                self.archive_cleanup = payload
            self.refresh()
            return
        if clean:
            self.output_tail.append(clean)
            self.output_tail = self.output_tail[-50:]
            self.refresh()

    def finish_stage(self, result: StageResult) -> None:
        self.status_by_stage[result.stage] = result.status
        self.elapsed_by_stage[result.stage] = result.elapsed_seconds
        self.current_stage = ""
        self.current_command = ""
        self.refresh()

    def mark_skipped(self, result: StageResult) -> None:
        self.finish_stage(result)

    def refresh(self) -> None:
        if self.enabled and self._live is not None and self._render is not None:
            self._live.update(self._render())


def archive_stage_cell(name: str, active_stage: str, durations: dict[str, object]) -> str:
    if name in durations:
        return f"{float(durations[name]):.1f}s"
    order = {"extract": 0, "preflight": 1, "insert": 2, "verify": 3, "cleanup": 4, "done": 5, "failed": -1, "cancelled": -1}
    if active_stage == name:
        return "active"
    if order.get(active_stage, -1) > order[name]:
        return "done"
    return "-"


def archive_progress_bar(completed: int, total: int, width: int = 12) -> str:
    total = max(0, int(total))
    completed = max(0, min(int(completed), total)) if total else 0
    filled = int(round(width * completed / total)) if total else 0
    return f"[{'#' * filled}{'-' * (width - filled)}] {completed:,}/{total:,}"


def format_bytes(value: int) -> str:
    value = max(0, int(value))
    if value >= 1024**3:
        return f"{value / 1024**3:.1f}G"
    if value >= 1024**2:
        return f"{value / 1024**2:.1f}M"
    if value >= 1024:
        return f"{value / 1024:.1f}K"
    return f"{value}B"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Unified SEC historical gap fill. This is the gateway-facing historical "
            "entry point: it downloads missing daily archives, extracts normalized "
            "filing/document/text rows, inserts them, catches up XBRL companyfacts, "
            "repairs XBRL relationships, audits the result, and writes coverage."
        )
    )
    parser.add_argument(
        "--start-date",
        default=env_string("SEC_HISTORICAL_GAP_FILL_START_DATE", DEFAULT_START_DATE),
        help="Inclusive UTC/archive date, YYYY-MM-DD. Defaults to 2019-01-01.",
    )
    parser.add_argument(
        "--end-date",
        default=env_string("SEC_HISTORICAL_GAP_FILL_END_DATE", default_end_date()),
        help="Exclusive UTC/archive date, YYYY-MM-DD. Defaults to tomorrow UTC so today's archives are included.",
    )
    parser.add_argument("--execute", action="store_true", help="Execute writes. Without this, only dry-run stage commands execute.")
    parser.add_argument(
        "--finalize-only",
        action="store_true",
        help="Run only source inventory, targeted document/timestamp repair, derived refresh, and final audit.",
    )
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--python-executable", default=sys.executable)
    parser.add_argument("--read-database", default=env_string("SEC_CLICKHOUSE_READ_DATABASE", "q_live"))
    parser.add_argument("--write-database", default=env_string("SEC_CLICKHOUSE_WRITE_DATABASE", env_string("SEC_GATEWAY_WRITE_DATABASE", "q_live")))
    parser.add_argument("--coverage-table", default=env_string("SEC_COVERAGE_TABLE", "sec_coverage_manifest_v3"))
    parser.add_argument("--bulk-mirror-database", default=env_string("SEC_BULK_MIRROR_DATABASE", "sec_core"))
    parser.add_argument("--artifact-root-win", default=env_string("SEC_CORE_ARTIFACT_ROOT_WIN", "D:/market-data/sec_core"))
    parser.add_argument("--core-output-root-win", default=env_string("SEC_CORE_OUTPUT_ROOT_WIN", "D:/market-data/prepared/sec_core"))
    parser.add_argument("--output-root-win", default=env_string("SEC_HISTORICAL_GAP_FILL_OUTPUT_ROOT_WIN", str(DEFAULT_OUTPUT_ROOT_WIN)))
    parser.add_argument("--daily-archive-output-root-win", default=env_string("SEC_DAILY_FEED_OUTPUT_ROOT_WIN", "D:/market-data/prepared/sec_daily_feed_archives"))
    parser.add_argument("--archive-validation-output-root-win", default=env_string("SEC_DOWNLOADED_ARCHIVE_VALIDATION_OUTPUT_ROOT_WIN", "D:/market-data/prepared/sec_downloaded_archive_validation"))
    parser.add_argument("--text-parts-output-root-win", default=env_string("SEC_FILING_TEXT_PARTS_OUTPUT_ROOT_WIN", "D:/market-data/prepared/sec_filing_text_parts"))
    parser.add_argument("--xbrl-output-root-win", default=env_string("SEC_XBRL_CATCHUP_OUTPUT_ROOT_WIN", "D:/market-data/prepared/sec_xbrl_companyfacts_catchup"))
    parser.add_argument("--xbrl-repair-output-root-win", default=env_string("SEC_XBRL_REPAIR_OUTPUT_ROOT_WIN", "D:/market-data/prepared/sec_xbrl_integrity_repair"))
    parser.add_argument("--integrity-audit-output-root-win", default=env_string("SEC_INTEGRITY_AUDIT_OUTPUT_ROOT_WIN", "D:/market-data/prepared/sec_integrity_audit"))
    parser.add_argument("--missing-document-repair-output-root-win", default=env_string("SEC_MISSING_DOCUMENT_REPAIR_OUTPUT_ROOT_WIN", "D:/market-data/prepared/sec_missing_document_repair"))
    parser.add_argument("--acceptance-archive-repair-output-root-win", default=env_string("SEC_ACCEPTANCE_ARCHIVE_REPAIR_OUTPUT_ROOT_WIN", "D:/market-data/prepared/sec_acceptance_archive_repair"))
    parser.add_argument("--sec-bridge-output-root-win", default=env_string("SEC_BRIDGE_OUTPUT_ROOT_WIN", str(DEFAULT_SEC_BRIDGE_OUTPUT_ROOT_WIN)))
    parser.add_argument("--sec-bridge-table", default=env_string("SEC_BRIDGE_TABLE", "id_sec_market_bridge_v3"))
    parser.add_argument("--context-database", default=env_string("SEC_CONTEXT_DATABASE", "market_sip_compact"))
    parser.add_argument("--context-filing-table", default=env_string("SEC_CONTEXT_FILING_TABLE", "sec_filing_context_v3"))
    parser.add_argument("--context-text-table", default=env_string("SEC_CONTEXT_TEXT_TABLE", "sec_filing_text_context_v3"))
    parser.add_argument("--context-xbrl-table", default=env_string("SEC_CONTEXT_XBRL_TABLE", "sec_xbrl_context_v3"))
    parser.add_argument("--context-output-root-win", default=env_string("SEC_CONTEXT_OUTPUT_ROOT_WIN", str(DEFAULT_SEC_CONTEXT_OUTPUT_ROOT_WIN)))
    parser.add_argument("--context-sec-text-buckets", type=int, default=int(os.environ.get("SEC_CONTEXT_TEXT_BUCKETS", "64")))
    parser.add_argument("--context-render-batch-rows", type=int, default=int(os.environ.get("SEC_CONTEXT_RENDER_BATCH_ROWS", "256")))
    parser.add_argument("--parts-root-win", default=env_string("SEC_TEXT_PARTS_ROOT_WIN", str(DEFAULT_PARTS_ROOT_WIN)))
    parser.add_argument("--parts-root-ch", default=env_string("SEC_TEXT_PARTS_ROOT_CH", DEFAULT_PARTS_ROOT_CH))
    parser.add_argument("--bulk-sources", default=DEFAULT_BULK_SOURCES)
    parser.add_argument("--bulk-download-concurrency", type=int, default=2)
    parser.add_argument("--bulk-file-root-ch", default=env_string("SEC_CORE_ARTIFACT_ROOT_CH", "/mnt/d/market-data"))
    parser.add_argument("--bulk-ingest-max-threads", type=int, default=int(os.environ.get("SEC_BULK_CLICKHOUSE_MAX_THREADS", "32")))
    parser.add_argument("--bulk-ingest-max-memory", default=os.environ.get("SEC_BULK_CLICKHOUSE_MAX_MEMORY", "96G"))
    parser.add_argument("--bulk-minimum-row-ratio", type=float, default=float(os.environ.get("SEC_BULK_MINIMUM_ROW_RATIO", "0.95")))
    parser.add_argument("--bulk-insert-max-retries", type=int, default=int(os.environ.get("SEC_BULK_INSERT_MAX_RETRIES", "12")))
    parser.add_argument("--bulk-insert-retry-base-seconds", type=float, default=float(os.environ.get("SEC_BULK_INSERT_RETRY_BASE_SECONDS", "5.0")))
    parser.add_argument("--bulk-insert-retry-max-seconds", type=float, default=float(os.environ.get("SEC_BULK_INSERT_RETRY_MAX_SECONDS", "120.0")))
    parser.add_argument("--bulk-limit-ciks", type=int, default=0)
    parser.add_argument("--archive-download-concurrency", type=int, default=3)
    parser.add_argument("--archive-validation-workers", type=int, default=32)
    parser.add_argument("--text-extract-workers", type=int, default=int(os.environ.get("SEC_ARCHIVE_REBUILD_WORKERS", "32")))
    parser.add_argument("--entity-backfill-workers", type=int, default=int(os.environ.get("SEC_ENTITY_BACKFILL_WORKERS", "32")))
    parser.add_argument("--entity-archive-manifest-table", default=env_string("SEC_ENTITY_ARCHIVE_MANIFEST_TABLE", "sec_filing_entity_archive_manifest_v3"))
    parser.add_argument("--xbrl-workers", type=int, default=8)
    parser.add_argument("--sec-request-min-interval-seconds", type=float, default=0.12)
    parser.add_argument("--request-timeout-seconds", type=float, default=30.0)
    parser.add_argument("--max-retries", type=int, default=8)
    parser.add_argument("--retry-base-seconds", type=float, default=30.0)
    parser.add_argument("--pending-multiplier", type=int, default=2)
    parser.add_argument("--sample-limit", type=int, default=1000)
    parser.add_argument("--sample-text-chars", type=int, default=2000)
    parser.add_argument("--min-text-chars", type=int, default=40)
    parser.add_argument("--max-text-chars", type=int, default=0, help="Optional normalized text storage cap. 0 means unlimited.")
    parser.add_argument("--text-limit-parts", type=int, default=0)
    parser.add_argument("--text-ingest-max-threads", type=int, default=int(os.environ.get("SEC_TEXT_FILE_INGEST_MAX_THREADS", "96")))
    parser.add_argument("--text-ingest-max-memory-usage", default=os.environ.get("SEC_TEXT_FILE_INGEST_MAX_MEMORY", "64G"))
    parser.add_argument("--text-worker-insert-max-threads", type=int, default=int(os.environ.get("SEC_ARCHIVE_INSERT_MAX_THREADS", "8")))
    parser.add_argument("--text-worker-insert-max-memory-usage", default=os.environ.get("SEC_ARCHIVE_INSERT_MAX_MEMORY", "16G"))
    parser.add_argument("--text-insert-concurrency", type=int, default=int(os.environ.get("SEC_ARCHIVE_INSERT_CONCURRENCY", "8")))
    parser.add_argument("--text-parquet-row-group-mb", type=int, default=int(os.environ.get("SEC_TEXT_PARQUET_ROW_GROUP_MB", "256")))
    parser.add_argument("--text-parquet-file-mb", type=int, default=int(os.environ.get("SEC_TEXT_PARQUET_FILE_MB", "1024")))
    parser.add_argument("--text-parquet-compression-level", type=int, default=int(os.environ.get("SEC_TEXT_PARQUET_ZSTD_LEVEL", "1")))
    parser.add_argument("--text-archive-manifest-table", default=env_string("SEC_ARCHIVE_INGEST_MANIFEST_TABLE", "sec_filing_archive_ingest_manifest_v3"))
    parser.add_argument("--limit-days", type=int, default=0)
    parser.add_argument("--limit-archives", type=int, default=0)
    parser.add_argument("--max-filings-per-archive", type=int, default=0)
    parser.add_argument("--force-download", action="store_true")
    parser.add_argument("--allow-g-drive", action="store_true")
    parser.add_argument(
        "--resume-from-coverage",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Skip stages whose stage-level coverage already covers the requested range.",
    )
    parser.add_argument("--progress-layout", choices=["auto", "rich", "text"], default=env_string("SEC_HISTORICAL_GAP_FILL_PROGRESS_LAYOUT", "rich"))
    return parser.parse_args()


def default_end_date() -> str:
    return (datetime.now(UTC).date() + timedelta(days=1)).isoformat()


def required_archive_through_date(end_date: str, *, today_utc: date | None = None) -> date:
    today = today_utc or datetime.now(UTC).date()
    candidate = min(parse_date(end_date) - timedelta(days=1), today - timedelta(days=1))
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def required_archive_is_local(args: argparse.Namespace) -> bool:
    required = required_archive_through_date(args.end_date)
    quarter = ((required.month - 1) // 3) + 1
    path = Path(args.artifact_root_win) / "daily_archives" / str(required.year) / f"QTR{quarter}" / f"{required:%Y%m%d}.nc.tar.gz"
    return path.is_file() and path.stat().st_size > 0


def main() -> None:
    args = parse_args()
    try:
        args.bulk_sources = require_complete_bulk_sources(args.bulk_sources)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    start_date = parse_date(args.start_date)
    end_date = parse_date(args.end_date)
    if end_date <= start_date:
        raise SystemExit("--end-date must be later than --start-date")
    if start_date < date(2019, 1, 1):
        raise SystemExit("SEC historical gap fill is intentionally bounded at 2019-01-01 or later")

    loaded_env = load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    run_id = f"sec_historical_gap_fill_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    run_root = Path(args.output_root_win) / run_id
    logs_root = run_root / "logs"
    logs_root.mkdir(parents=True, exist_ok=True)

    commands = build_commands(args, logs_root)
    if args.finalize_only:
        commands = [command for command in commands if command.stage in FINALIZE_ONLY_STAGES]
    manifest = {
        "run_id": run_id,
        "created_at_utc": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "script": str(Path(__file__).resolve()),
        "execute": bool(args.execute),
        "start_date": args.start_date,
        "end_date": args.end_date,
        "read_database": args.read_database,
        "write_database": args.write_database,
        "bulk_mirror_database": args.bulk_mirror_database,
        "run_root": str(run_root),
        "loaded_env_files": [str(path) for path in loaded_env],
        "secret_status": secret_status(
            [
                "SEC_USER_AGENT",
                "SEC_EDGAR_USER_AGENT",
                "REAL_LIVE_CLICKHOUSE_WRITE_URL",
                "REAL_LIVE_CLICKHOUSE_WRITE_USER",
                "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD",
                "CLICKHOUSE_LIVE_STORAGE_POLICY",
            ]
        ),
        "commands": [asdict(command) for command in commands],
    }
    (run_root / "sec_historical_gap_fill_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str), encoding="utf-8")
    write_plan(run_root / "sec_historical_gap_fill_plan.ps1", commands)

    print("=" * 96, flush=True)
    print("SEC unified historical gap fill", flush=True)
    print(f"run_root={run_root}", flush=True)
    print(f"execute={args.execute} range=[{args.start_date},{args.end_date})", flush=True)
    print(f"read_database={args.read_database} write_database={args.write_database}", flush=True)
    print("=" * 96, flush=True)
    for index, command in enumerate(commands, start=1):
        print(f"[{index}/{len(commands)}] {command.stage}: {format_command(command.command)}", flush=True)
    if not args.execute:
        print("dry_run=true; wrote manifest and plan only. Pass --execute to run SEC requests and ClickHouse writes.", flush=True)
        write_summary(run_root / "sec_historical_gap_fill_summary.md", args, [], coverage_written=False)
        return

    results: list[StageResult] = []
    results_path = run_root / "sec_historical_gap_fill_results.jsonl"
    if args.execute:
        ensure_historical_coverage_table(args)
    with HistoricalFillProgress(args.progress_layout, commands, run_root) as progress:
        for command in commands:
            if args.execute and args.resume_from_coverage and stage_already_completed(args, command):
                result = skipped_stage_result(command)
                results.append(result)
                append_jsonl(results_path, asdict(result))
                progress.mark_skipped(result)
                if not progress.enabled:
                    print(f"stage={command.stage} status=skipped_covered reason=coverage already covers requested range", flush=True)
                continue
            result = run_stage(command, progress=progress)
            results.append(result)
            append_jsonl(results_path, asdict(result))
            if result.returncode != 0 and not args.continue_on_error:
                write_summary(run_root / "sec_historical_gap_fill_summary.md", args, results, coverage_written=False)
                raise SystemExit(result.returncode)
            if args.execute and result.returncode == 0:
                write_stage_coverage(args, run_id, command)

    failed = [result for result in results if result.returncode != 0]
    coverage_written = False
    if args.execute and not failed:
        write_coverage(args, run_id)
        coverage_written = True
    write_summary(run_root / "sec_historical_gap_fill_summary.md", args, results, coverage_written=coverage_written)
    if failed:
        raise SystemExit(1)


def build_commands(args: argparse.Namespace, logs_root: Path) -> list[StageCommand]:
    archive_root = str(Path(args.artifact_root_win) / "daily_archives")
    context_start_date = DEFAULT_START_DATE
    context_end_date = (parse_date(args.end_date) - timedelta(days=1)).isoformat()
    required_archive_date = required_archive_through_date(args.end_date).isoformat()
    return [
        StageCommand(
            "bulk-download",
            add_runtime_flags(
                [
                    args.python_executable,
                    script("pipelines/sec/edgar/sec_initial_fill_download.py"),
                    "--sources",
                    args.bulk_sources,
                    "--artifact-root-win",
                    args.artifact_root_win,
                    "--output-root-win",
                    args.core_output_root_win,
                    "--download-concurrency",
                    str(max(1, args.bulk_download_concurrency)),
                    "--sec-request-min-interval-seconds",
                    str(max(0.0, args.sec_request_min_interval_seconds)),
                    "--request-timeout-seconds",
                    str(max(1.0, args.request_timeout_seconds)),
                    "--max-retries",
                    str(max(0, args.max_retries)),
                    "--retry-base-seconds",
                    str(max(0.0, args.retry_base_seconds)),
                    "--continue-on-429",
                    "--max-429-before-stop",
                    "20",
                    "--progress-layout",
                    "rich",
                ],
                args,
                dry_run_flag="--dry-run",
            ),
            logs_root / "bulk-download.log",
            False,
            (stage_coverage_kind("bulk-download"),),
        ),
        StageCommand(
            "bulk-ingest",
            add_runtime_flags(
                [
                    args.python_executable,
                    script("pipelines/sec/edgar/sec_bulk_clickhouse_ingest.py"),
                    "--sources",
                    args.bulk_sources,
                    "--database",
                    args.bulk_mirror_database,
                    "--artifact-root-win",
                    args.artifact_root_win,
                    "--output-root-win",
                    args.core_output_root_win,
                    "--clickhouse-file-root",
                    args.bulk_file_root_ch,
                    "--max-threads",
                    str(max(1, args.bulk_ingest_max_threads)),
                    "--max-memory-usage",
                    args.bulk_ingest_max_memory,
                    "--minimum-row-ratio",
                    str(args.bulk_minimum_row_ratio),
                    "--insert-max-retries",
                    str(max(0, args.bulk_insert_max_retries)),
                    "--insert-retry-base-seconds",
                    str(max(0.0, args.bulk_insert_retry_base_seconds)),
                    "--insert-retry-max-seconds",
                    str(max(0.0, args.bulk_insert_retry_max_seconds)),
                ],
                args,
                dry_run_flag="--dry-run",
            ),
            logs_root / "bulk-ingest.log",
            True,
            (stage_coverage_kind("bulk-ingest"),),
        ),
        StageCommand(
            "bulk-canonicalize",
            add_required_execute_flag(
                [
                    args.python_executable,
                    script("pipelines/sec/edgar/sec_bulk_to_canonical.py"),
                    "--source-database",
                    args.bulk_mirror_database,
                    "--schema-source-database",
                    args.read_database,
                    "--target-database",
                    args.write_database,
                    "--start-date",
                    args.start_date,
                    "--end-date",
                    args.end_date,
                    "--stages",
                    "xbrl",
                ],
                args,
            ),
            logs_root / "bulk-canonicalize.log",
            True,
            (stage_coverage_kind("bulk-canonicalize"),),
        ),
        StageCommand(
            "daily-archive-download",
            add_runtime_flags(
                [
                    args.python_executable,
                    script("pipelines/sec/edgar/sec_daily_feed_archive_download.py"),
                    "--start-date",
                    args.start_date,
                    "--end-date",
                    args.end_date,
                    "--require-through-date",
                    required_archive_date,
                    "--artifact-root-win",
                    args.artifact_root_win,
                    "--output-root-win",
                    args.daily_archive_output_root_win,
                    "--download-concurrency",
                    str(max(1, args.archive_download_concurrency)),
                    "--sec-request-min-interval-seconds",
                    str(max(0.0, args.sec_request_min_interval_seconds)),
                    "--request-timeout-seconds",
                    str(max(1.0, args.request_timeout_seconds)),
                    "--max-retries",
                    str(max(0, args.max_retries)),
                    "--retry-base-seconds",
                    str(max(0.0, args.retry_base_seconds)),
                    "--continue-on-429",
                    "--max-429-before-stop",
                    "20",
                    "--progress-layout",
                    "rich",
                ],
                args,
                dry_run_flag="--dry-run",
            ),
            logs_root / "daily-archive-download.log",
            False,
            (stage_coverage_kind("daily-archive-download"),),
        ),
        StageCommand(
            "validate-downloaded",
            [
                args.python_executable,
                script("pipelines/sec/edgar/sec_validate_downloaded_archives.py"),
                "--downloader-output-root-win",
                args.daily_archive_output_root_win,
                "--output-root-win",
                args.archive_validation_output_root_win,
                "--archive-workers",
                str(max(1, args.archive_validation_workers)),
                "--pending-multiplier",
                str(max(1, args.pending_multiplier)),
                "--sample-limit",
                str(max(0, args.sample_limit)),
                "--sample-text-chars",
                str(max(0, args.sample_text_chars)),
                "--status",
                "downloaded",
                "--repair-failed-archives",
                "--sec-request-min-interval-seconds",
                str(max(0.0, args.sec_request_min_interval_seconds)),
                "--request-timeout-seconds",
                str(max(1.0, args.request_timeout_seconds)),
                "--max-retries",
                str(max(0, args.max_retries)),
                "--retry-base-seconds",
                str(max(0.0, args.retry_base_seconds)),
            ],
            logs_root / "validate-downloaded.log",
            False,
            (stage_coverage_kind("validate-downloaded"),),
        ),
        StageCommand(
            "filing-entity-backfill",
            add_required_execute_flag(
                [
                    args.python_executable,
                    script("pipelines/sec/edgar/sec_filing_entity_backfill.py"),
                    "--database",
                    args.write_database,
                    "--archive-root-win",
                    archive_root,
                    "--start-date",
                    DEFAULT_START_DATE,
                    "--end-date",
                    args.end_date,
                    "--workers",
                    str(max(1, args.entity_backfill_workers)),
                    "--manifest-table",
                    args.entity_archive_manifest_table,
                ],
                args,
            ),
            logs_root / "filing-entity-backfill.log",
            True,
            (stage_coverage_kind("filing-entity-backfill"),),
        ),
        StageCommand(
            "archive-text-rebuild",
            add_required_execute_flag(
                [
                    args.python_executable,
                    script("pipelines/sec/edgar/sec_filing_archive_rebuild.py"),
                    "--database",
                    args.write_database,
                    "--archive-root-win",
                    archive_root,
                    "--output-root-win",
                    args.text_parts_output_root_win,
                    "--start-date",
                    args.start_date,
                    "--end-date",
                    args.end_date,
                    "--workers",
                    str(max(1, args.text_extract_workers)),
                    "--sample-limit-per-archive",
                    "1" if args.sample_limit else "0",
                    "--sample-text-chars",
                    str(max(0, args.sample_text_chars)),
                    "--min-text-chars",
                    str(max(0, args.min_text_chars)),
                    "--max-text-chars",
                    str(max(0, args.max_text_chars)),
                    "--parts-root-win",
                    args.parts_root_win,
                    "--parts-root-ch",
                    args.parts_root_ch,
                    "--historical-output-root-win",
                    args.output_root_win,
                    "--insert-max-threads",
                    str(max(1, args.text_worker_insert_max_threads)),
                    "--insert-max-memory-usage",
                    str(args.text_worker_insert_max_memory_usage),
                    "--insert-concurrency",
                    str(max(1, args.text_insert_concurrency)),
                    "--parquet-row-group-mb",
                    str(max(1, args.text_parquet_row_group_mb)),
                    "--parquet-file-mb",
                    str(max(1, args.text_parquet_file_mb)),
                    "--parquet-compression-level",
                    str(args.text_parquet_compression_level),
                    "--archive-manifest-table",
                    args.text_archive_manifest_table,
                    "--cleanup-parts",
                    "--recover-incomplete-runs",
                    "--progress-layout",
                    "events",
                ],
                args,
            ),
            logs_root / "archive-text-rebuild.log",
            True,
            (stage_coverage_kind("archive-text-rebuild"),),
        ),
        StageCommand(
            "sec-revision-reconcile",
            add_required_execute_flag(
                [
                    args.python_executable,
                    script("pipelines/sec/edgar/sec_revision_audit_repair.py"),
                    "--database",
                    args.write_database,
                    "--archive-root-win",
                    archive_root,
                    "--start-date",
                    args.start_date,
                    "--end-date",
                    args.end_date,
                    "--apply-stored-pac",
                ],
                args,
            ),
            logs_root / "sec-revision-reconcile.log",
            True,
            (stage_coverage_kind("sec-revision-reconcile"),),
        ),
        StageCommand(
            "missing-document-repair",
            add_required_execute_flag(
                [
                    args.python_executable,
                    script("pipelines/sec/edgar/sec_missing_document_repair.py"),
                    "--database",
                    args.write_database,
                    "--output-root-win",
                    args.missing_document_repair_output_root_win,
                    "--parts-root-win",
                    args.parts_root_win,
                    "--parts-root-ch",
                    args.parts_root_ch,
                    "--workers",
                    str(max(1, args.text_extract_workers)),
                    "--min-text-chars",
                    str(max(0, args.min_text_chars)),
                    "--max-text-chars",
                    str(max(0, args.max_text_chars)),
                ],
                args,
            ),
            logs_root / "missing-document-repair.log",
            True,
            (stage_coverage_kind("missing-document-repair"),),
        ),
        StageCommand(
            "filing-parent-reconcile",
            add_required_execute_flag(
                [
                    args.python_executable,
                    script("pipelines/sec/edgar/sec_filing_parent_reconcile.py"),
                    "--database",
                    args.write_database,
                ],
                args,
            ),
            logs_root / "filing-parent-reconcile.log",
            True,
            (stage_coverage_kind("filing-parent-reconcile"),),
        ),
        StageCommand(
            "acceptance-submissions-enrichment",
            add_required_execute_flag(
                [
                    args.python_executable,
                    script("pipelines/sec/edgar/sec_acceptance_fragment_fill.py"),
                    "--target-database",
                    args.write_database,
                    "--target-table",
                    "sec_filing_v3",
                    "--stage-database",
                    args.bulk_mirror_database,
                    "--stage-table",
                    "sec_submissions_filing_overlay_v3",
                    "--artifact-root-win",
                    args.artifact_root_win,
                    "--output-root-win",
                    str(Path(args.core_output_root_win) / "sec_acceptance_fragment_fill"),
                    "--download-workers",
                    "8",
                    "--sec-request-min-interval-seconds",
                    str(max(0.0, args.sec_request_min_interval_seconds)),
                    "--request-timeout-seconds",
                    str(max(1.0, args.request_timeout_seconds)),
                ],
                args,
            ),
            logs_root / "acceptance-submissions-enrichment.log",
            True,
            (stage_coverage_kind("acceptance-submissions-enrichment"),),
        ),
        StageCommand(
            "acceptance-raw-metadata-repair",
            add_required_execute_flag(
                [
                    args.python_executable,
                    script("pipelines/sec/edgar/sec_acceptance_raw_metadata_repair.py"),
                    "--target-database",
                    args.write_database,
                    "--target-table",
                    "sec_filing_v3",
                    "--mirror-database",
                    args.bulk_mirror_database,
                    "--mirror-table",
                    "sec_bulk_mirror_filing_v3",
                    "--enriched-table",
                    "sec_submissions_filing_overlay_v3",
                ],
                args,
            ),
            logs_root / "acceptance-raw-metadata-repair.log",
            True,
            (stage_coverage_kind("acceptance-raw-metadata-repair"),),
        ),
        StageCommand(
            "acceptance-archive-repair",
            add_required_execute_flag(
                [
                    args.python_executable,
                    script("pipelines/sec/edgar/sec_acceptance_archive_repair.py"),
                    "--database",
                    args.write_database,
                    "--archive-root-win",
                    archive_root,
                    "--output-root-win",
                    args.acceptance_archive_repair_output_root_win,
                    "--start-date",
                    DEFAULT_START_DATE,
                    "--end-date",
                    args.end_date,
                    "--archive-workers",
                    str(max(1, args.text_extract_workers)),
                ],
                args,
            ),
            logs_root / "acceptance-archive-repair.log",
            True,
            (stage_coverage_kind("acceptance-archive-repair"),),
        ),
        StageCommand(
            "archive-identity-audit",
            [
                args.python_executable,
                script("pipelines/sec/edgar/sec_archive_identity_audit.py"),
                "--database",
                args.write_database,
                "--submissions-database",
                args.bulk_mirror_database,
                "--output-root-win",
                str(Path(args.core_output_root_win) / "sec_archive_identity_audit"),
                "--workers",
                str(max(1, args.text_extract_workers)),
            ],
            logs_root / "archive-identity-audit.log",
            True,
            (stage_coverage_kind("archive-identity-audit"),),
        ),
        StageCommand(
            "xbrl-companyfacts-catchup",
            add_required_execute_flag(
                [
                    args.python_executable,
                    script("pipelines/sec/edgar/sec_xbrl_companyfacts_catchup.py"),
                    "--read-database",
                    args.write_database,
                    "--write-database",
                    args.write_database,
                    "--start-date",
                    args.start_date,
                    "--end-date",
                    args.end_date,
                    "--output-root-win",
                    args.xbrl_output_root_win,
                    "--workers",
                    str(max(1, args.xbrl_workers)),
                ],
                args,
            ),
            logs_root / "xbrl-companyfacts-catchup.log",
            True,
            (stage_coverage_kind("xbrl-companyfacts-catchup"),),
        ),
        StageCommand(
            "xbrl-integrity-repair",
            add_required_execute_flag(
                [
                    args.python_executable,
                    script("pipelines/sec/edgar/sec_xbrl_integrity_repair.py"),
                    "--database",
                    args.write_database,
                    "--scope-start-date",
                    "2019-01-01",
                    "--stages",
                    "drop-legacy,filing-parents,frame-parents",
                    "--output-root-win",
                    args.xbrl_repair_output_root_win,
                ],
                args,
            ),
            logs_root / "xbrl-integrity-repair.log",
            True,
            (stage_coverage_kind("xbrl-integrity-repair"),),
        ),
        StageCommand(
            "sec-bridge-rebuild",
            add_required_execute_flag(
                [
                    args.python_executable,
                    script("pipelines/reference_data/migration/step_06_build_q_live_bridge_features.py"),
                    "--target-database",
                    args.write_database,
                    "--output-root-win",
                    args.sec_bridge_output_root_win,
                    "--feature-date",
                    context_end_date,
                    "--specs",
                    "sec_market_bridge",
                    "--sec-bridge-table",
                    args.sec_bridge_table,
                    "--allow-non-empty-targets",
                ],
                args,
            ),
            logs_root / "sec-bridge-rebuild.log",
            True,
            (stage_coverage_kind("sec-bridge-rebuild"),),
        ),
        StageCommand(
            "sec-context-build",
            add_runtime_flags(
                [
                    args.python_executable,
                    script("pipelines/market_sip/events/clickhouse_build_sec_context.py"),
                    "--source-database",
                    args.write_database,
                    "--target-database",
                    args.context_database,
                    "--filing-table",
                    args.context_filing_table,
                    "--text-table",
                    args.context_text_table,
                    "--xbrl-table",
                    args.context_xbrl_table,
                    "--source-filing-table",
                    "sec_filing_v3",
                    "--source-text-table",
                    "sec_filing_text_v3",
                    "--source-bridge-table",
                    args.sec_bridge_table,
                    "--source-xbrl-company-fact-table",
                    "sec_xbrl_company_fact_v3",
                    "--source-xbrl-frame-observation-table",
                    "sec_xbrl_frame_observation_v3",
                    "--start-date",
                    context_start_date,
                    "--end-date",
                    context_end_date,
                    "--sec-text-buckets",
                    str(max(1, args.context_sec_text_buckets)),
                    "--render-batch-rows",
                    str(max(1, args.context_render_batch_rows)),
                    "--skip-text",
                    "--output-root-win",
                    args.context_output_root_win,
                ],
                args,
                dry_run_flag="--dry-run",
            ),
            logs_root / "sec-context-build.log",
            True,
            (stage_coverage_kind("sec-context-build"),),
        ),
        StageCommand(
            "integrity-audit",
            [
                args.python_executable,
                script("pipelines/sec/edgar/sec_integrity_audit.py"),
                "--database",
                args.write_database,
                "--output-root-win",
                args.integrity_audit_output_root_win,
                "--archive-root-win",
                archive_root,
                "--archive-start-date",
                args.start_date,
                "--archive-end-date",
                args.end_date,
                "--require-v3-tables",
            ],
            logs_root / "integrity-audit.log",
            False,
            (stage_coverage_kind("integrity-audit"),),
        ),
    ]


def add_runtime_flags(command: list[str], args: argparse.Namespace, *, dry_run_flag: str = "") -> list[str]:
    out = list(command)
    command_text = " ".join(out)
    if "sec_initial_fill_download.py" in command_text:
        if args.force_download:
            out.append("--force")
        if args.allow_g_drive:
            out.append("--allow-g-drive")
    if "sec_bulk_clickhouse_ingest.py" in command_text:
        if args.bulk_limit_ciks:
            out.extend(["--limit-members", str(args.bulk_limit_ciks)])
        if args.allow_g_drive:
            out.append("--allow-g-drive")
    if "sec_daily_feed_archive_download.py" in command_text:
        if args.force_download:
            out.append("--force")
        if args.allow_g_drive:
            out.append("--allow-g-drive")
        if args.limit_days:
            out.extend(["--limit-days", str(args.limit_days)])
    if "sec_filing_text_extract_parts.py" in command_text or "sec_filing_archive_rebuild.py" in command_text:
        if args.limit_archives:
            out.extend(["--limit-archives", str(args.limit_archives)])
        if args.max_filings_per_archive:
            out.extend(["--max-filings-per-archive", str(args.max_filings_per_archive)])
    if "sec_filing_text_clickhouse_file_ingest.py" in command_text and args.text_limit_parts:
        out.extend(["--limit-parts", str(args.text_limit_parts)])
    if not args.execute and dry_run_flag:
        out.append(dry_run_flag)
    return out


def add_required_execute_flag(command: list[str], args: argparse.Namespace) -> list[str]:
    """Build a command whose component script explicitly gates writes behind --execute."""
    out = add_runtime_flags(command, args)
    if args.execute:
        out.append("--execute")
    return out


def run_stage(command: StageCommand, *, progress: HistoricalFillProgress | None = None) -> StageResult:
    actual_command = resolve_runtime_command(command)
    started = time.perf_counter()
    rich_enabled = bool(progress and progress.enabled)
    if progress:
        progress.start_stage(command, actual_command)
    if not rich_enabled:
        print("=" * 96, flush=True)
        print(f"stage={command.stage}", flush=True)
        print(format_command(actual_command), flush=True)
        print(f"log={command.log_path}", flush=True)
        print("=" * 96, flush=True)
    command.log_path.parent.mkdir(parents=True, exist_ok=True)
    with command.log_path.open("w", encoding="utf-8") as log:
        log.write(f"command={format_command(actual_command)}\n\n")
        process = subprocess.Popen(
            actual_command,
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            if rich_enabled and progress:
                progress.log_line(line)
            else:
                print(line, end="", flush=True)
            log.write(line)
            log.flush()
        returncode = int(process.wait())
    elapsed = round(time.perf_counter() - started, 3)
    status = "ok" if returncode == 0 else "failed"
    result = StageResult(command.stage, status, returncode, elapsed, str(command.log_path), actual_command)
    if progress:
        progress.finish_stage(result)
    if not rich_enabled:
        print(f"stage={command.stage} status={status} returncode={returncode} elapsed_seconds={elapsed}", flush=True)
    return result


def skipped_stage_result(command: StageCommand) -> StageResult:
    return StageResult(command.stage, "skipped_covered", 0, 0.0, str(command.log_path), resolve_runtime_command(command))


def resolve_runtime_command(command: StageCommand) -> list[str]:
    placeholder = next((item for item in command.command if item.startswith("<latest-text-manifest:")), "")
    if not placeholder:
        return command.command
    root = placeholder.removeprefix("<latest-text-manifest:").removesuffix(">")
    manifest = latest_text_manifest(Path(root), start_date=command.start_date, end_date=command.end_date)
    return [str(manifest) if item == placeholder else item for item in command.command]


def latest_text_manifest(root: Path, *, start_date: str = "", end_date: str = "") -> Path:
    candidates = sorted(root.glob("*/sec_filing_text_extract_manifest.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not candidates:
        raise SystemExit(f"no sec_filing_text_extract_manifest.json found under {root}")
    if start_date and end_date:
        for candidate in candidates:
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
            except Exception:
                continue
            summary = payload.get("summary") if isinstance(payload, dict) else {}
            if not isinstance(summary, dict):
                continue
            if summary.get("start_date") == start_date and summary.get("end_date") == end_date:
                return candidate
    return candidates[0]


def ensure_historical_coverage_table(args: argparse.Namespace) -> None:
    config = SecPipelineConfig.from_env()
    client = ClickHouseHttpClient(config.clickhouse.url, config.clickhouse.user, config.clickhouse.password)
    ensure_coverage_table(client, coverage_config(args))


def coverage_config(args: argparse.Namespace) -> SecCoverageConfig:
    return SecCoverageConfig(
        database=args.write_database,
        coverage_table=args.coverage_table,
        storage_policy=os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY") or "",
    )


def stage_coverage_kind(stage: str) -> str:
    return "sec_stage_" + stage.replace("-", "_")


def stage_already_completed(args: argparse.Namespace, command: StageCommand) -> bool:
    if command.stage in {
        "bulk-download",
        "bulk-ingest",
        "archive-text-rebuild",
        "filing-entity-backfill",
        "missing-document-repair",
        "filing-parent-reconcile",
        "validate-downloaded",
        "acceptance-submissions-enrichment",
        "acceptance-raw-metadata-repair",
        "acceptance-archive-repair",
        "archive-identity-audit",
        "sec-bridge-rebuild",
        "sec-context-build",
        "integrity-audit",
    }:
        # These stages reconcile mutable source state and are intentionally idempotent.
        return False
    if command.stage == "daily-archive-download" and not required_archive_is_local(args):
        # Requested-range coverage is not authoritative when the terminal archive was not present.
        return False
    if not command.coverage_kinds:
        return False
    config = SecPipelineConfig.from_env()
    client = ClickHouseHttpClient(config.clickhouse.url, config.clickhouse.user, config.clickhouse.password)
    start = datetime.combine(parse_date(args.start_date), dt_time.min, tzinfo=UTC)
    end = datetime.combine(parse_date(args.end_date), dt_time.min, tzinfo=UTC)
    for kind in command.coverage_kinds:
        out = client.execute(
            f"""
            SELECT count()
            FROM {quote_ident(args.write_database)}.{quote_ident(args.coverage_table)} FINAL
            WHERE source = 'sec'
              AND coverage_kind = {sql_string(kind)}
              AND status IN ('completed', 'covered_empty', 'coverage_bootstrap')
              AND coverage_start_utc <= toDateTime64({sql_string(dt_text(start))}, 3, 'UTC')
              AND coverage_end_utc >= toDateTime64({sql_string(dt_text(end))}, 3, 'UTC')
            FORMAT TSV
            """
        )
        if int(out.strip() or "0") == 0:
            return False
    return True


def write_stage_coverage(args: argparse.Namespace, run_id: str, command: StageCommand) -> None:
    if not command.coverage_kinds:
        return
    config = SecPipelineConfig.from_env()
    client = ClickHouseHttpClient(config.clickhouse.url, config.clickhouse.user, config.clickhouse.password)
    coverage = coverage_config(args)
    start = datetime.combine(parse_date(args.start_date), dt_time.min, tzinfo=UTC)
    end = datetime.combine(parse_date(args.end_date), dt_time.min, tzinfo=UTC)
    for kind in command.coverage_kinds:
        insert_coverage(
            client,
            coverage,
            coverage_id=new_coverage_id(f"{kind}_completed"),
            coverage_kind=kind,
            start_utc=start,
            end_utc=end,
            status="completed",
            run_id=run_id,
            host_role="workstation",
            metadata={
                "source": "sec_historical_gap_fill",
                "stage": command.stage,
                "mutates_database": command.mutates_database,
                "command": resolve_runtime_command(command),
                "start_date": args.start_date,
                "end_date": args.end_date,
            },
            completed=True,
        )


def write_coverage(args: argparse.Namespace, run_id: str) -> None:
    config = SecPipelineConfig.from_env()
    client = ClickHouseHttpClient(config.clickhouse.url, config.clickhouse.user, config.clickhouse.password)
    coverage = coverage_config(args)
    start = datetime.combine(parse_date(args.start_date), dt_time.min, tzinfo=UTC)
    end = datetime.combine(parse_date(args.end_date), dt_time.min, tzinfo=UTC)
    for kind in [KIND_BULK_SUBMISSIONS, KIND_DAILY_ARCHIVE, KIND_LIVE_FEED, KIND_TEXT_EXTRACTION, KIND_BULK_COMPANYFACTS, KIND_INTEGRITY_AUDIT]:
        insert_coverage(
            client,
            coverage,
            coverage_id=new_coverage_id(f"{kind}_historical_fill"),
            coverage_kind=kind,
            start_utc=start,
            end_utc=end,
            status="completed",
            run_id=run_id,
            host_role="workstation",
            metadata={"source": "sec_historical_gap_fill", "start_date": args.start_date, "end_date": args.end_date},
            completed=True,
        )


def write_plan(path: Path, commands: list[StageCommand]) -> None:
    lines = ["$ErrorActionPreference = 'Stop'", ""]
    for index, command in enumerate(commands, start=1):
        lines.append(f"Write-Host 'SEC gap-fill task {index}/{len(commands)}: {command.stage}'")
        lines.append(format_command(command.command))
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_summary(path: Path, args: argparse.Namespace, results: list[StageResult], *, coverage_written: bool) -> None:
    lines = [
        "# SEC Historical Gap Fill Summary",
        "",
        f"- Range: `{args.start_date}` to `{args.end_date}` exclusive",
        f"- Execute: `{args.execute}`",
        f"- Read database: `{args.read_database}`",
        f"- Write database: `{args.write_database}`",
        f"- Coverage written: `{coverage_written}`",
        "",
        "## Stages",
        "",
    ]
    for result in results:
        lines.append(f"- `{result.stage}`: `{result.status}` returncode `{result.returncode}` elapsed `{result.elapsed_seconds}s` log `{result.log_path}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def append_jsonl(path: Path, row: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, default=str, separators=(",", ":")) + "\n")


def script(relative: str) -> str:
    return str(REPO_ROOT / relative)


def format_command(command: list[str]) -> str:
    return " ".join(powershell_quote(item) for item in command)


def powershell_quote(value: object) -> str:
    text = str(value)
    if not text or any(char.isspace() for char in text) or "'" in text:
        return "'" + text.replace("'", "''") + "'"
    return text


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def quote_ident(value: str) -> str:
    return "`" + value.replace("`", "``") + "`"


def sql_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def dt_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "")


if __name__ == "__main__":
    main()

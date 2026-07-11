from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.env import discover_env_files, load_env_files, secret_status  # noqa: E402


DEFAULT_ARTIFACT_ROOT_WIN = Path("D:/market-data/sec_core")
DEFAULT_CORE_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_core")
DEFAULT_DAILY_ARCHIVE_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_daily_feed_archives")
DEFAULT_ARCHIVE_VALIDATION_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_downloaded_archive_validation")
DEFAULT_ARCHIVE_DISCOVERY_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_archive_content_discovery")
DEFAULT_TEXT_PARTS_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_filing_text_parts")
DEFAULT_TIMESTAMP_REPAIR_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_acceptance_fallback_submissions_repair")
DEFAULT_INTEGRITY_AUDIT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_integrity_audit")
DEFAULT_ORCHESTRATOR_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_historical_backfill_orchestrator")
DEFAULT_PARTS_ROOT_WIN = Path("D:/market-data")
DEFAULT_PARTS_ROOT_CH = "/mnt/d/market-data"

STAGES = (
    "bulk-download",
    "bulk-ingest",
    "daily-archive-download",
    "validate-downloaded",
    "archive-content-discovery",
    "text-extract",
    "text-ingest-preflight",
    "text-ingest-execute",
    "timestamp-repair",
    "integrity-audit",
)

GAP_FILL_STAGES = (
    "daily-archive-download",
    "validate-downloaded",
    "text-extract",
    "text-ingest-preflight",
    "text-ingest-execute",
    "timestamp-repair",
    "integrity-audit",
)

DEFAULT_STAGES = (
    "bulk-download",
    "bulk-ingest",
    *GAP_FILL_STAGES,
)

INITIAL_FILL_STAGES = DEFAULT_STAGES

ARCHIVE_TO_TEXT_STAGES = (
    "daily-archive-download",
    "validate-downloaded",
    "text-extract",
    "text-ingest-preflight",
    "text-ingest-execute",
)


@dataclass(frozen=True, slots=True)
class StageCommand:
    stage: str
    command: list[str]
    cwd: str
    mutates_database: bool
    downloads_data: bool
    log_path: str


@dataclass(frozen=True, slots=True)
class StageResult:
    stage: str
    status: str
    returncode: int
    elapsed_seconds: float
    command: list[str]
    log_path: str
    started_at_utc: str
    ended_at_utc: str


@dataclass(slots=True)
class RunContext:
    run_root: Path
    logs_root: Path
    latest_text_manifest: Path | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Orchestrate the current SEC historical fill pipeline for a date period. "
            "The script calls the stage scripts we use today: SEC bulk download/ingest, "
            "daily archive download/validation, v2 filing text part extraction, ClickHouse "
            "file ingest, submissions-bulk timestamp repair, and integrity audit."
        )
    )
    parser.add_argument("--start-date", required=True, help="Inclusive SEC daily archive date, YYYY-MM-DD.")
    parser.add_argument("--end-date", required=True, help="Exclusive SEC daily archive date, YYYY-MM-DD.")
    parser.add_argument(
        "--stages",
        default=",".join(DEFAULT_STAGES),
        help=(
            "Comma-separated stages, or a preset: default, initial-fill, gap-fill, archive-to-text, all. "
            f"Valid stages: {', '.join(STAGES)}"
        ),
    )
    parser.add_argument("--execute", action="store_true", help="Run child stages. Without this flag, only a plan is written.")
    parser.add_argument("--continue-on-error", action="store_true", help="Continue after a stage fails.")
    parser.add_argument("--python-executable", default=sys.executable)
    parser.add_argument("--artifact-root-win", default=os.environ.get("SEC_CORE_ARTIFACT_ROOT_WIN", str(DEFAULT_ARTIFACT_ROOT_WIN)))
    parser.add_argument("--core-output-root-win", default=os.environ.get("SEC_CORE_OUTPUT_ROOT_WIN", str(DEFAULT_CORE_OUTPUT_ROOT_WIN)))
    parser.add_argument("--daily-archive-output-root-win", default=os.environ.get("SEC_DAILY_FEED_OUTPUT_ROOT_WIN", str(DEFAULT_DAILY_ARCHIVE_OUTPUT_ROOT_WIN)))
    parser.add_argument("--archive-validation-output-root-win", default=os.environ.get("SEC_DOWNLOADED_ARCHIVE_VALIDATION_OUTPUT_ROOT_WIN", str(DEFAULT_ARCHIVE_VALIDATION_OUTPUT_ROOT_WIN)))
    parser.add_argument("--archive-discovery-output-root-win", default=os.environ.get("SEC_ARCHIVE_DISCOVERY_OUTPUT_ROOT_WIN", str(DEFAULT_ARCHIVE_DISCOVERY_OUTPUT_ROOT_WIN)))
    parser.add_argument("--text-parts-output-root-win", default=os.environ.get("SEC_FILING_TEXT_PARTS_OUTPUT_ROOT_WIN", str(DEFAULT_TEXT_PARTS_OUTPUT_ROOT_WIN)))
    parser.add_argument("--timestamp-repair-output-root-win", default=os.environ.get("SEC_TIMESTAMP_REPAIR_OUTPUT_ROOT_WIN", str(DEFAULT_TIMESTAMP_REPAIR_OUTPUT_ROOT_WIN)))
    parser.add_argument("--integrity-audit-output-root-win", default=os.environ.get("SEC_INTEGRITY_AUDIT_OUTPUT_ROOT_WIN", str(DEFAULT_INTEGRITY_AUDIT_OUTPUT_ROOT_WIN)))
    parser.add_argument("--orchestrator-output-root-win", default=os.environ.get("SEC_HISTORICAL_ORCHESTRATOR_OUTPUT_ROOT_WIN", str(DEFAULT_ORCHESTRATOR_OUTPUT_ROOT_WIN)))
    parser.add_argument("--parts-root-win", default=os.environ.get("SEC_FILING_TEXT_PARTS_ROOT_WIN", str(DEFAULT_PARTS_ROOT_WIN)))
    parser.add_argument("--parts-root-ch", default=os.environ.get("SEC_FILING_TEXT_PARTS_ROOT_CH", DEFAULT_PARTS_ROOT_CH))
    parser.add_argument(
        "--text-manifest-json",
        default="",
        help="Existing sec_filing_text_extract_manifest.json. Required only when ingest stages run without text-extract.",
    )
    parser.add_argument("--bulk-sources", default="company_tickers,company_tickers_exchange,company_tickers_mf,submissions,companyfacts")
    parser.add_argument("--bulk-download-concurrency", type=int, default=2)
    parser.add_argument("--bulk-ingest-batch-size", type=int, default=50000)
    parser.add_argument("--bulk-limit-ciks", type=int, default=0)
    parser.add_argument("--archive-download-concurrency", type=int, default=2)
    parser.add_argument("--sec-request-min-interval-seconds", type=float, default=0.11)
    parser.add_argument("--daily-archive-request-min-interval-seconds", type=float, default=0.2)
    parser.add_argument("--daily-archive-request-timeout-seconds", type=float, default=60.0)
    parser.add_argument("--daily-archive-max-retries", type=int, default=8)
    parser.add_argument("--daily-archive-retry-base-seconds", type=float, default=30.0)
    parser.add_argument("--daily-archive-max-429-before-stop", type=int, default=20)
    parser.add_argument("--archive-validation-workers", type=int, default=4)
    parser.add_argument("--archive-discovery-workers", type=int, default=4)
    parser.add_argument("--text-extract-workers", type=int, default=4)
    parser.add_argument("--pending-multiplier", type=int, default=2)
    parser.add_argument("--sample-limit", type=int, default=1000)
    parser.add_argument("--sample-text-chars", type=int, default=2000)
    parser.add_argument("--min-text-chars", type=int, default=40)
    parser.add_argument("--max-text-chars", type=int, default=0, help="Optional normalized text storage cap. 0 means unlimited.")
    parser.add_argument("--limit-days", type=int, default=0)
    parser.add_argument("--limit-archives", type=int, default=0)
    parser.add_argument("--max-filings-per-archive", type=int, default=0)
    parser.add_argument("--text-limit-parts", type=int, default=0)
    parser.add_argument("--timestamp-limit-rows", type=int, default=0)
    parser.add_argument("--timestamp-limit-ciks", type=int, default=0)
    parser.add_argument("--timestamp-limit-zip-entries", type=int, default=0)
    parser.add_argument("--timestamp-progress-interval", type=int, default=100000)
    parser.add_argument("--timestamp-row-progress-interval", type=int, default=10000)
    parser.add_argument("--timestamp-status-interval-seconds", type=float, default=30.0)
    parser.add_argument("--validation-status", default="downloaded", help="Downloader manifest status selected by validate-downloaded.")
    parser.add_argument("--progress-layout", choices=["auto", "rich", "text"], default="auto")
    parser.add_argument("--integrity-skip-xbrl-sample", action="store_true")
    parser.add_argument("--force-download", action="store_true", help="Pass --force to SEC downloader stages.")
    parser.add_argument("--allow-g-drive", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    loaded_env = load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    stages = parse_stages(args.stages)
    validate_args(args, stages)

    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    run_root = Path(args.orchestrator_output_root_win) / run_id
    logs_root = run_root / "logs"
    logs_root.mkdir(parents=True, exist_ok=True)
    context = RunContext(run_root=run_root, logs_root=logs_root)

    planned_commands = build_all_commands(args, stages, context, dry_plan=True)
    manifest = {
        "run_id": run_id,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "script": str(Path(__file__).resolve()),
        "repo_root": str(REPO_ROOT),
        "execute": bool(args.execute),
        "stages": stages,
        "start_date": args.start_date,
        "end_date": args.end_date,
        "run_root": str(run_root),
        "loaded_env_files": [str(path) for path in loaded_env],
        "secret_status": secret_status(
            [
                "SEC_USER_AGENT",
                "SEC_EDGAR_USER_AGENT",
                "NEWS_SEC_USER_AGENT",
                "REAL_LIVE_CLICKHOUSE_WRITE_URL",
                "REAL_LIVE_CLICKHOUSE_WRITE_USER",
                "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD",
                "SEC_CLICKHOUSE_URL",
                "SEC_CLICKHOUSE_USER",
                "SEC_CLICKHOUSE_PASSWORD",
                "CLICKHOUSE_LIVE_STORAGE_POLICY",
            ]
        ),
        "commands": [command_manifest(row) for row in planned_commands],
    }
    (run_root / "sec_historical_backfill_orchestrator_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    write_plan(run_root / "sec_historical_backfill_orchestrator_plan.ps1", planned_commands)

    print("=" * 96, flush=True)
    print("SEC historical backfill orchestrator", flush=True)
    print(f"run_root={run_root}", flush=True)
    print(f"execute={args.execute} stages={','.join(stages)}", flush=True)
    print(f"start_date={args.start_date} end_date={args.end_date}", flush=True)
    print("=" * 96, flush=True)
    for index, command in enumerate(planned_commands, start=1):
        print(f"[{index}/{len(planned_commands)}] {command.stage}: {format_command(command.command)}", flush=True)

    if not args.execute:
        print("dry_run=true; no child stage was executed", flush=True)
        print(f"plan={run_root / 'sec_historical_backfill_orchestrator_plan.ps1'}", flush=True)
        return

    results_path = run_root / "sec_historical_backfill_orchestrator_results.jsonl"
    results: list[StageResult] = []
    for stage in stages:
        command = build_stage_command(args, stage, context, dry_plan=False)
        result = run_stage(command)
        if stage == "text-extract" and result.returncode == 0:
            context.latest_text_manifest = latest_text_manifest(Path(args.text_parts_output_root_win))
        results.append(result)
        append_jsonl(results_path, asdict(result))
        if result.returncode != 0 and not args.continue_on_error:
            write_summary(run_root / "sec_historical_backfill_orchestrator_summary.md", args, planned_commands, results)
            raise SystemExit(result.returncode)

    write_summary(run_root / "sec_historical_backfill_orchestrator_summary.md", args, planned_commands, results)
    failed = [row for row in results if row.returncode != 0]
    if failed:
        raise SystemExit(1)


def parse_stages(text: str) -> list[str]:
    normalized = text.strip().lower()
    if normalized in {"default", ""}:
        return list(DEFAULT_STAGES)
    if normalized in {"initial-fill", "initial_all", "initial-all"}:
        return list(INITIAL_FILL_STAGES)
    if normalized in {"gap-fill", "gap_fill"}:
        return list(GAP_FILL_STAGES)
    if normalized in {"archive-to-text", "archive_text"}:
        return list(ARCHIVE_TO_TEXT_STAGES)
    if normalized == "all":
        return list(STAGES)
    stages = [item.strip().lower().replace("_", "-") for item in text.split(",") if item.strip()]
    invalid = sorted(set(stages) - set(STAGES))
    if invalid:
        raise SystemExit(f"invalid stages: {invalid}; valid stages are: {', '.join(STAGES)}")
    if not stages:
        raise SystemExit("--stages produced no stages")
    return stages


def validate_args(args: argparse.Namespace, stages: list[str]) -> None:
    start = parse_iso_date(args.start_date)
    end = parse_iso_date(args.end_date)
    if end <= start:
        raise SystemExit("--end-date must be later than --start-date")
    non_negative = [
        "limit_days",
        "limit_archives",
        "max_filings_per_archive",
        "text_limit_parts",
        "timestamp_limit_rows",
        "timestamp_limit_ciks",
        "timestamp_limit_zip_entries",
        "bulk_limit_ciks",
    ]
    for name in non_negative:
        if getattr(args, name) < 0:
            raise SystemExit(f"--{name.replace('_', '-')} must be >= 0")
    if {"text-ingest-preflight", "text-ingest-execute"}.intersection(stages):
        if "text-extract" not in stages and not args.text_manifest_json:
            existing = latest_text_manifest(Path(args.text_parts_output_root_win), required=False)
            if existing is None:
                raise SystemExit("ingest stages require --text-manifest-json when text-extract is not part of this run")


def build_all_commands(args: argparse.Namespace, stages: list[str], context: RunContext, *, dry_plan: bool) -> list[StageCommand]:
    return [build_stage_command(args, stage, context, dry_plan=dry_plan) for stage in stages]


def build_stage_command(args: argparse.Namespace, stage: str, context: RunContext, *, dry_plan: bool) -> StageCommand:
    log_path = str(context.logs_root / f"{stage}.log")
    if stage == "bulk-download":
        command = [
            args.python_executable,
            script("pipelines/sec/edgar/sec_initial_fill_download.py"),
            "--sources",
            "all" if args.bulk_sources.strip().lower() == "all" else args.bulk_sources,
            "--artifact-root-win",
            args.artifact_root_win,
            "--output-root-win",
            args.core_output_root_win,
            "--download-concurrency",
            str(args.bulk_download_concurrency),
            "--sec-request-min-interval-seconds",
            str(args.sec_request_min_interval_seconds),
            "--progress-layout",
            args.progress_layout,
        ]
        add_common_download_flags(command, args)
        if not args.execute:
            command.append("--dry-run")
        return StageCommand(stage, command, str(REPO_ROOT), False, True, log_path)

    if stage == "bulk-ingest":
        command = [
            args.python_executable,
            script("pipelines/sec/edgar/sec_bulk_clickhouse_ingest.py"),
            "--artifact-root-win",
            args.artifact_root_win,
            "--output-root-win",
            args.core_output_root_win,
            "--sources",
            args.bulk_sources,
            "--batch-size",
            str(args.bulk_ingest_batch_size),
        ]
        add_positive(command, "--limit-ciks", args.bulk_limit_ciks)
        if not args.execute:
            command.append("--dry-run")
        return StageCommand(stage, command, str(REPO_ROOT), True, False, log_path)

    if stage == "daily-archive-download":
        command = [
            args.python_executable,
            script("pipelines/sec/edgar/sec_daily_feed_archive_download.py"),
            "--start-date",
            args.start_date,
            "--end-date",
            args.end_date,
            "--artifact-root-win",
            args.artifact_root_win,
            "--output-root-win",
            args.daily_archive_output_root_win,
            "--download-concurrency",
            str(args.archive_download_concurrency),
            "--sec-request-min-interval-seconds",
            str(args.daily_archive_request_min_interval_seconds),
            "--request-timeout-seconds",
            str(args.daily_archive_request_timeout_seconds),
            "--max-retries",
            str(args.daily_archive_max_retries),
            "--retry-base-seconds",
            str(args.daily_archive_retry_base_seconds),
            "--continue-on-429",
            "--max-429-before-stop",
            str(args.daily_archive_max_429_before_stop),
            "--progress-layout",
            args.progress_layout,
        ]
        add_positive(command, "--limit-days", args.limit_days)
        add_common_download_flags(command, args)
        if not args.execute:
            command.append("--dry-run")
        return StageCommand(stage, command, str(REPO_ROOT), False, True, log_path)

    if stage == "validate-downloaded":
        command = [
            args.python_executable,
            script("pipelines/sec/edgar/sec_validate_downloaded_archives.py"),
            "--downloader-output-root-win",
            args.daily_archive_output_root_win,
            "--output-root-win",
            args.archive_validation_output_root_win,
            "--archive-workers",
            str(args.archive_validation_workers),
            "--pending-multiplier",
            str(args.pending_multiplier),
            "--sample-limit",
            str(args.sample_limit),
            "--sample-text-chars",
            str(args.sample_text_chars),
            "--status",
            args.validation_status,
        ]
        add_positive(command, "--limit-archives", args.limit_archives)
        add_positive(command, "--max-filings-per-archive", args.max_filings_per_archive)
        return StageCommand(stage, command, str(REPO_ROOT), False, False, log_path)

    if stage == "archive-content-discovery":
        command = [
            args.python_executable,
            script("pipelines/sec/edgar/sec_archive_content_discovery.py"),
            "--artifact-root-win",
            args.artifact_root_win,
            "--archive-subdir",
            "daily_archives",
            "--output-root-win",
            args.archive_discovery_output_root_win,
            "--start-date",
            args.start_date,
            "--end-date",
            args.end_date,
            "--archive-workers",
            str(args.archive_discovery_workers),
            "--sample-limit",
            str(args.sample_limit),
            "--pending-multiplier",
            str(args.pending_multiplier),
        ]
        add_positive(command, "--limit-archives", args.limit_archives)
        add_positive(command, "--max-filings-per-archive", args.max_filings_per_archive)
        return StageCommand(stage, command, str(REPO_ROOT), False, False, log_path)

    if stage == "text-extract":
        command = [
            args.python_executable,
            script("pipelines/sec/edgar/sec_filing_text_extract_parts.py"),
            "--archive-root-win",
            str(Path(args.artifact_root_win) / "daily_archives"),
            "--output-root-win",
            args.text_parts_output_root_win,
            "--start-date",
            args.start_date,
            "--end-date",
            args.end_date,
            "--archive-workers",
            str(args.text_extract_workers),
            "--pending-multiplier",
            str(args.pending_multiplier),
            "--sample-limit",
            str(args.sample_limit),
            "--sample-text-chars",
            str(args.sample_text_chars),
            "--min-text-chars",
            str(args.min_text_chars),
            "--max-text-chars",
            str(args.max_text_chars),
            "--progress-every",
            "1",
        ]
        add_positive(command, "--limit-archives", args.limit_archives)
        add_positive(command, "--max-filings-per-archive", args.max_filings_per_archive)
        if not args.execute:
            command.append("--dry-run")
        return StageCommand(stage, command, str(REPO_ROOT), False, False, log_path)

    if stage == "text-ingest-preflight":
        command = build_text_ingest_command(args, context, dry_plan=dry_plan)
        command.append("--preflight-only")
        add_positive(command, "--limit-parts", args.text_limit_parts)
        return StageCommand(stage, command, str(REPO_ROOT), False, False, log_path)

    if stage == "text-ingest-execute":
        command = build_text_ingest_command(args, context, dry_plan=dry_plan)
        command.extend(["--execute", "--skip-preflight"])
        add_positive(command, "--limit-parts", args.text_limit_parts)
        return StageCommand(stage, command, str(REPO_ROOT), True, False, log_path)

    if stage == "timestamp-repair":
        command = [
            args.python_executable,
            script("pipelines/sec/edgar/sec_acceptance_fallback_submissions_repair.py"),
            "--artifact-root-win",
            args.artifact_root_win,
            "--output-root-win",
            args.timestamp_repair_output_root_win,
            "--progress-interval",
            str(args.timestamp_progress_interval),
            "--row-progress-interval",
            str(args.timestamp_row_progress_interval),
            "--status-interval-seconds",
            str(args.timestamp_status_interval_seconds),
        ]
        add_positive(command, "--limit-rows", args.timestamp_limit_rows)
        add_positive(command, "--limit-ciks", args.timestamp_limit_ciks)
        add_positive(command, "--limit-zip-entries", args.timestamp_limit_zip_entries)
        if args.execute:
            command.append("--execute")
        return StageCommand(stage, command, str(REPO_ROOT), True, False, log_path)

    if stage == "integrity-audit":
        command = [
            args.python_executable,
            script("pipelines/sec/edgar/sec_integrity_audit.py"),
            "--output-root-win",
            args.integrity_audit_output_root_win,
            "--archive-root-win",
            str(Path(args.artifact_root_win) / "daily_archives"),
            "--archive-start-date",
            args.start_date,
            "--archive-end-date",
            args.end_date,
            "--require-v2-tables",
        ]
        if args.integrity_skip_xbrl_sample:
            command.append("--skip-xbrl-sample")
        return StageCommand(stage, command, str(REPO_ROOT), False, False, log_path)

    raise AssertionError(stage)


def build_text_ingest_command(args: argparse.Namespace, context: RunContext, *, dry_plan: bool) -> list[str]:
    manifest = text_manifest_for_stage(args, context, dry_plan=dry_plan)
    return [
        args.python_executable,
        script("pipelines/sec/edgar/sec_filing_text_clickhouse_file_ingest.py"),
        "--manifest-json",
        str(manifest),
        "--parts-root-win",
        args.parts_root_win,
        "--parts-root-ch",
        args.parts_root_ch,
    ]


def text_manifest_for_stage(args: argparse.Namespace, context: RunContext, *, dry_plan: bool) -> Path | str:
    if args.text_manifest_json:
        return Path(args.text_manifest_json)
    if context.latest_text_manifest is not None:
        return context.latest_text_manifest
    existing = latest_text_manifest(Path(args.text_parts_output_root_win), required=False)
    if existing is not None:
        return existing
    if dry_plan:
        return "<latest sec_filing_text_extract_manifest.json from text-extract>"
    raise SystemExit("could not find sec_filing_text_extract_manifest.json; run text-extract first or pass --text-manifest-json")


def run_stage(command: StageCommand) -> StageResult:
    started = time.perf_counter()
    started_at = datetime.now(UTC).isoformat()
    Path(command.log_path).parent.mkdir(parents=True, exist_ok=True)
    print("=" * 96, flush=True)
    print(f"stage={command.stage} started_at_utc={started_at}", flush=True)
    print(format_command(command.command), flush=True)
    print(f"log={command.log_path}", flush=True)
    print("=" * 96, flush=True)
    with Path(command.log_path).open("w", encoding="utf-8") as log:
        log.write(f"started_at_utc={started_at}\n")
        log.write(f"command={format_command(command.command)}\n\n")
        process = subprocess.Popen(
            command.command,
            cwd=command.cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
            log.write(line)
            log.flush()
        returncode = int(process.wait())
        ended_at = datetime.now(UTC).isoformat()
        elapsed = round(time.perf_counter() - started, 3)
        status = "ok" if returncode == 0 else "failed"
        log.write(f"\nended_at_utc={ended_at}\n")
        log.write(f"status={status} returncode={returncode} elapsed_seconds={elapsed}\n")
    print(f"stage={command.stage} status={status} returncode={returncode} elapsed_seconds={elapsed}", flush=True)
    return StageResult(command.stage, status, returncode, elapsed, command.command, command.log_path, started_at, ended_at)


def latest_text_manifest(root: Path, *, required: bool = True) -> Path | None:
    if not root.exists():
        if required:
            raise SystemExit(f"text parts output root does not exist: {root}")
        return None
    candidates = sorted(root.glob("*/sec_filing_text_extract_manifest.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not candidates:
        if required:
            raise SystemExit(f"no sec_filing_text_extract_manifest.json found under {root}")
        return None
    return candidates[0]


def add_common_download_flags(command: list[str], args: argparse.Namespace) -> None:
    if args.allow_g_drive:
        command.append("--allow-g-drive")
    if args.force_download:
        command.append("--force")


def write_plan(path: Path, commands: list[StageCommand]) -> None:
    lines = [
        "# Generated SEC historical backfill plan.",
        "# Review before running commands manually.",
        "$ErrorActionPreference = 'Stop'",
        "",
    ]
    for command in commands:
        lines.append(f"# stage: {command.stage}")
        lines.append(format_command(command.command))
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_summary(path: Path, args: argparse.Namespace, commands: list[StageCommand], results: list[StageResult]) -> None:
    result_by_stage = {row.stage: row for row in results}
    lines = [
        "# SEC Historical Backfill Orchestrator Summary",
        "",
        f"- execute: `{args.execute}`",
        f"- start_date: `{args.start_date}`",
        f"- end_date: `{args.end_date}`",
        "",
        "| Stage | Status | Return Code | Seconds | Log |",
        "| --- | --- | ---: | ---: | --- |",
    ]
    for command in commands:
        result = result_by_stage.get(command.stage)
        if result is None:
            lines.append(f"| `{command.stage}` | not_run |  |  | `{command.log_path}` |")
        else:
            lines.append(f"| `{command.stage}` | {result.status} | {result.returncode} | {result.elapsed_seconds:.3f} | `{result.log_path}` |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def append_jsonl(path: Path, row: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")


def command_manifest(command: StageCommand) -> dict[str, object]:
    return {
        "stage": command.stage,
        "command": command.command,
        "cwd": command.cwd,
        "mutates_database": command.mutates_database,
        "downloads_data": command.downloads_data,
        "log_path": command.log_path,
    }


def add_positive(command: list[str], flag: str, value: int) -> None:
    if value > 0:
        command.extend([flag, str(value)])


def script(relative_path: str) -> str:
    return str(REPO_ROOT / relative_path)


def format_command(command: Iterable[str | Path]) -> str:
    return " ".join(shlex.quote(str(part)) for part in command)


def parse_iso_date(value: str) -> date:
    return date.fromisoformat(value)


if __name__ == "__main__":
    main()

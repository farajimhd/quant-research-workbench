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
DEFAULT_ACCEPTANCE_BACKFILL_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_acceptance_backfill")
DEFAULT_ACCEPTANCE_FRAGMENT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_acceptance_fragment_fill")
DEFAULT_ACCEPTANCE_HEADER_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_acceptance_header_fill")
DEFAULT_ACCEPTANCE_DATE_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_acceptance_date_fallback_fill")
DEFAULT_Q_LIVE_STEP_07_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/q_live_migration/step_07_sec_accepted_timestamps")
DEFAULT_DAILY_ARCHIVE_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_daily_feed_archives")
DEFAULT_ARCHIVE_VALIDATION_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_downloaded_archive_validation")
DEFAULT_ARCHIVE_DISCOVERY_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_archive_content_discovery")
DEFAULT_ORCHESTRATOR_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_historical_backfill_orchestrator")

PHASES = (
    "bulk-download",
    "acceptance-recent",
    "acceptance-fragment",
    "acceptance-header",
    "acceptance-date-fallback",
    "q-live-accepted-backfill",
    "daily-archive-download",
    "validate-downloaded",
    "archive-content-discovery",
)
DEFAULT_PHASES = (
    "bulk-download",
    "acceptance-recent",
    "acceptance-fragment",
    "acceptance-header",
    "acceptance-date-fallback",
    "q-live-accepted-backfill",
    "daily-archive-download",
    "validate-downloaded",
)


@dataclass(frozen=True, slots=True)
class PhaseCommand:
    phase: str
    command: list[str]
    cwd: str
    mutates_database: bool
    downloads_data: bool


@dataclass(frozen=True, slots=True)
class PhaseResult:
    phase: str
    status: str
    returncode: int
    elapsed_seconds: float
    command: list[str]
    started_at_utc: str
    ended_at_utc: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the SEC historical backfill stages that currently exist: SEC bulk source download, "
            "accepted timestamp staging/backfill, daily archive download, targeted validation, and "
            "optional archive content discovery. This orchestrates stage scripts; it does not merge "
            "their logic into one large implementation."
        )
    )
    parser.add_argument("--start-date", default="", help="Inclusive daily archive date, YYYY-MM-DD. Required for archive phases.")
    parser.add_argument("--end-date", default="", help="Exclusive daily archive date, YYYY-MM-DD. Required for archive phases.")
    parser.add_argument("--phases", default=",".join(DEFAULT_PHASES), help=f"Comma-separated phases, or 'all'. Valid: {', '.join(PHASES)}")
    parser.add_argument("--execute", action="store_true", help="Run stages for real. Without this flag the orchestrator writes a plan only.")
    parser.add_argument("--continue-on-error", action="store_true", help="Continue later phases after a phase fails.")
    parser.add_argument("--python-executable", default=sys.executable)
    parser.add_argument("--artifact-root-win", default=os.environ.get("SEC_CORE_ARTIFACT_ROOT_WIN", str(DEFAULT_ARTIFACT_ROOT_WIN)))
    parser.add_argument("--orchestrator-output-root-win", default=os.environ.get("SEC_HISTORICAL_ORCHESTRATOR_OUTPUT_ROOT_WIN", str(DEFAULT_ORCHESTRATOR_OUTPUT_ROOT_WIN)))
    parser.add_argument("--core-output-root-win", default=os.environ.get("SEC_CORE_OUTPUT_ROOT_WIN", str(DEFAULT_CORE_OUTPUT_ROOT_WIN)))
    parser.add_argument("--acceptance-backfill-output-root-win", default=os.environ.get("SEC_ACCEPTANCE_BACKFILL_OUTPUT_ROOT_WIN", str(DEFAULT_ACCEPTANCE_BACKFILL_OUTPUT_ROOT_WIN)))
    parser.add_argument("--acceptance-fragment-output-root-win", default=os.environ.get("SEC_ACCEPTANCE_FRAGMENT_OUTPUT_ROOT_WIN", str(DEFAULT_ACCEPTANCE_FRAGMENT_OUTPUT_ROOT_WIN)))
    parser.add_argument("--acceptance-header-output-root-win", default=os.environ.get("SEC_ACCEPTANCE_HEADER_OUTPUT_ROOT_WIN", str(DEFAULT_ACCEPTANCE_HEADER_OUTPUT_ROOT_WIN)))
    parser.add_argument("--acceptance-date-output-root-win", default=os.environ.get("SEC_ACCEPTANCE_DATE_OUTPUT_ROOT_WIN", str(DEFAULT_ACCEPTANCE_DATE_OUTPUT_ROOT_WIN)))
    parser.add_argument("--q-live-step-07-output-root-win", default=os.environ.get("QLIVE_MIGRATION_STEP_07_OUTPUT_ROOT_WIN", str(DEFAULT_Q_LIVE_STEP_07_OUTPUT_ROOT_WIN)))
    parser.add_argument("--daily-archive-output-root-win", default=os.environ.get("SEC_DAILY_FEED_OUTPUT_ROOT_WIN", str(DEFAULT_DAILY_ARCHIVE_OUTPUT_ROOT_WIN)))
    parser.add_argument("--archive-validation-output-root-win", default=os.environ.get("SEC_DOWNLOADED_ARCHIVE_VALIDATION_OUTPUT_ROOT_WIN", str(DEFAULT_ARCHIVE_VALIDATION_OUTPUT_ROOT_WIN)))
    parser.add_argument("--archive-discovery-output-root-win", default=os.environ.get("SEC_ARCHIVE_DISCOVERY_OUTPUT_ROOT_WIN", str(DEFAULT_ARCHIVE_DISCOVERY_OUTPUT_ROOT_WIN)))
    parser.add_argument("--bulk-download-concurrency", type=int, default=2)
    parser.add_argument("--archive-download-concurrency", type=int, default=1)
    parser.add_argument("--sec-request-min-interval-seconds", type=float, default=0.11)
    parser.add_argument("--daily-archive-request-min-interval-seconds", type=float, default=1.0)
    parser.add_argument("--archive-validation-workers", type=int, default=4)
    parser.add_argument("--archive-discovery-workers", type=int, default=4)
    parser.add_argument("--acceptance-fragment-download-workers", type=int, default=8)
    parser.add_argument("--acceptance-header-download-workers", type=int, default=8)
    parser.add_argument("--limit-days", type=int, default=0, help="Smoke-test cap for archive download/discovery day selection.")
    parser.add_argument("--limit-missing-keys", type=int, default=0, help="Smoke-test cap for acceptance-recent.")
    parser.add_argument("--limit-fragments", type=int, default=0, help="Smoke-test cap for acceptance-fragment.")
    parser.add_argument("--limit-accessions", type=int, default=0, help="Smoke-test cap for acceptance-header.")
    parser.add_argument("--date-fallback-max-rows", type=int, default=0, help="Safety cap for acceptance-date-fallback.")
    parser.add_argument("--limit-archives", type=int, default=0, help="Smoke-test cap for validation/discovery archive count.")
    parser.add_argument("--max-filings-per-archive", type=int, default=0, help="Smoke-test cap for validation/discovery per archive.")
    parser.add_argument("--validation-status", default="downloaded", help="Downloader manifest status selected by validate-downloaded.")
    parser.add_argument("--progress-layout", choices=["auto", "rich", "text"], default="auto")
    parser.add_argument("--allow-g-drive", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    loaded_env = load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    phases = parse_phases(args.phases)
    validate_args(args, phases)
    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    run_root = Path(args.orchestrator_output_root_win) / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    commands = build_commands(args, phases)

    manifest = {
        "run_id": run_id,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "script": str(Path(__file__).resolve()),
        "repo_root": str(REPO_ROOT),
        "execute": bool(args.execute),
        "phases": phases,
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
                "CLICKHOUSE_LIVE_STORAGE_POLICY",
            ]
        ),
        "commands": [command_manifest(row) for row in commands],
    }
    (run_root / "sec_historical_backfill_orchestrator_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    write_plan(run_root / "sec_historical_backfill_orchestrator_plan.ps1", commands)

    print("=" * 96, flush=True)
    print("SEC historical backfill orchestrator", flush=True)
    print(f"run_root={run_root}", flush=True)
    print(f"execute={args.execute} phases={','.join(phases)}", flush=True)
    print(f"start_date={args.start_date or '<none>'} end_date={args.end_date or '<none>'}", flush=True)
    print("=" * 96, flush=True)
    for index, command in enumerate(commands, start=1):
        print(f"[{index}/{len(commands)}] {command.phase}: {format_command(command.command)}", flush=True)

    results_path = run_root / "sec_historical_backfill_orchestrator_results.jsonl"
    if not args.execute:
        print("dry_run=true; plan written but no phase commands were executed", flush=True)
        print(f"plan={run_root / 'sec_historical_backfill_orchestrator_plan.ps1'}", flush=True)
        return

    results: list[PhaseResult] = []
    for command in commands:
        result = run_phase(command)
        results.append(result)
        append_jsonl(results_path, asdict(result))
        if result.returncode != 0 and not args.continue_on_error:
            write_summary(run_root / "sec_historical_backfill_orchestrator_summary.md", args, commands, results)
            raise SystemExit(result.returncode)
    write_summary(run_root / "sec_historical_backfill_orchestrator_summary.md", args, commands, results)
    failed = [row for row in results if row.returncode != 0]
    if failed:
        raise SystemExit(1)


def parse_phases(text: str) -> list[str]:
    if text.strip().lower() == "all":
        return list(PHASES)
    phases = [item.strip() for item in text.split(",") if item.strip()]
    invalid = sorted(set(phases) - set(PHASES))
    if invalid:
        raise SystemExit(f"invalid phases: {invalid}; valid phases are: {', '.join(PHASES)}")
    if not phases:
        raise SystemExit("--phases produced no phases")
    return phases


def validate_args(args: argparse.Namespace, phases: list[str]) -> None:
    archive_phases = {"daily-archive-download", "validate-downloaded", "archive-content-discovery"}
    if archive_phases.intersection(phases):
        if not args.start_date or not args.end_date:
            raise SystemExit("--start-date and --end-date are required for archive phases")
        start = parse_iso_date(args.start_date)
        end = parse_iso_date(args.end_date)
        if end <= start:
            raise SystemExit("--end-date must be later than --start-date")
    if args.limit_days < 0 or args.limit_archives < 0 or args.max_filings_per_archive < 0:
        raise SystemExit("limit arguments must be >= 0")


def build_commands(args: argparse.Namespace, phases: list[str]) -> list[PhaseCommand]:
    commands: list[PhaseCommand] = []
    for phase in phases:
        if phase == "bulk-download":
            cmd = [
                args.python_executable,
                script("pipelines/sec/edgar/sec_initial_fill_download.py"),
                "--sources",
                "all",
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
            if args.allow_g_drive:
                cmd.append("--allow-g-drive")
            if not args.execute:
                cmd.append("--dry-run")
            commands.append(PhaseCommand(phase, cmd, str(REPO_ROOT), mutates_database=False, downloads_data=True))
        elif phase == "acceptance-recent":
            cmd = [
                args.python_executable,
                script("pipelines/sec/edgar/sec_acceptance_backfill_build.py"),
                "--artifact-root-win",
                args.artifact_root_win,
                "--output-root-win",
                args.acceptance_backfill_output_root_win,
            ]
            add_positive(cmd, "--limit-missing-keys", args.limit_missing_keys)
            if args.execute:
                cmd.append("--execute")
            commands.append(PhaseCommand(phase, cmd, str(REPO_ROOT), mutates_database=True, downloads_data=False))
        elif phase == "acceptance-fragment":
            cmd = [
                args.python_executable,
                script("pipelines/sec/edgar/sec_acceptance_fragment_fill.py"),
                "--artifact-root-win",
                args.artifact_root_win,
                "--output-root-win",
                args.acceptance_fragment_output_root_win,
                "--download-workers",
                str(args.acceptance_fragment_download_workers),
                "--sec-request-min-interval-seconds",
                str(args.sec_request_min_interval_seconds),
            ]
            add_positive(cmd, "--limit-fragments", args.limit_fragments)
            if args.execute:
                cmd.append("--execute")
            commands.append(PhaseCommand(phase, cmd, str(REPO_ROOT), mutates_database=True, downloads_data=True))
        elif phase == "acceptance-header":
            cmd = [
                args.python_executable,
                script("pipelines/sec/edgar/sec_acceptance_header_fill.py"),
                "--artifact-root-win",
                args.artifact_root_win,
                "--output-root-win",
                args.acceptance_header_output_root_win,
                "--download-workers",
                str(args.acceptance_header_download_workers),
                "--sec-request-min-interval-seconds",
                str(args.sec_request_min_interval_seconds),
            ]
            add_positive(cmd, "--limit-accessions", args.limit_accessions)
            if args.execute:
                cmd.append("--execute")
            commands.append(PhaseCommand(phase, cmd, str(REPO_ROOT), mutates_database=True, downloads_data=True))
        elif phase == "acceptance-date-fallback":
            cmd = [
                args.python_executable,
                script("pipelines/sec/edgar/sec_acceptance_date_fallback_fill.py"),
                "--output-root-win",
                args.acceptance_date_output_root_win,
            ]
            add_positive(cmd, "--max-rows", args.date_fallback_max_rows)
            if args.execute:
                cmd.append("--execute")
            commands.append(PhaseCommand(phase, cmd, str(REPO_ROOT), mutates_database=True, downloads_data=False))
        elif phase == "q-live-accepted-backfill":
            cmd = [
                args.python_executable,
                script("pipelines/reference_data/migration/step_07_backfill_sec_accepted_timestamps.py"),
                "--output-root-win",
                args.q_live_step_07_output_root_win,
            ]
            if args.execute:
                cmd.append("--execute")
            commands.append(PhaseCommand(phase, cmd, str(REPO_ROOT), mutates_database=True, downloads_data=False))
        elif phase == "daily-archive-download":
            cmd = [
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
                "--progress-layout",
                args.progress_layout,
            ]
            add_positive(cmd, "--limit-days", args.limit_days)
            if args.allow_g_drive:
                cmd.append("--allow-g-drive")
            if not args.execute:
                cmd.append("--dry-run")
            commands.append(PhaseCommand(phase, cmd, str(REPO_ROOT), mutates_database=False, downloads_data=True))
        elif phase == "validate-downloaded":
            cmd = [
                args.python_executable,
                script("pipelines/sec/edgar/sec_validate_downloaded_archives.py"),
                "--downloader-output-root-win",
                args.daily_archive_output_root_win,
                "--output-root-win",
                args.archive_validation_output_root_win,
                "--archive-workers",
                str(args.archive_validation_workers),
                "--status",
                args.validation_status,
            ]
            add_positive(cmd, "--limit-archives", args.limit_archives)
            add_positive(cmd, "--max-filings-per-archive", args.max_filings_per_archive)
            commands.append(PhaseCommand(phase, cmd, str(REPO_ROOT), mutates_database=False, downloads_data=False))
        elif phase == "archive-content-discovery":
            cmd = [
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
            ]
            add_positive(cmd, "--limit-archives", args.limit_archives)
            add_positive(cmd, "--max-filings-per-archive", args.max_filings_per_archive)
            commands.append(PhaseCommand(phase, cmd, str(REPO_ROOT), mutates_database=False, downloads_data=False))
        else:  # pragma: no cover - guarded by parse_phases
            raise AssertionError(phase)
    return commands


def run_phase(command: PhaseCommand) -> PhaseResult:
    started = time.perf_counter()
    started_at = datetime.now(UTC).isoformat()
    print("=" * 96, flush=True)
    print(f"phase={command.phase} started_at_utc={started_at}", flush=True)
    print(format_command(command.command), flush=True)
    print("=" * 96, flush=True)
    completed = subprocess.run(command.command, cwd=command.cwd, check=False)
    ended_at = datetime.now(UTC).isoformat()
    elapsed = round(time.perf_counter() - started, 3)
    status = "ok" if completed.returncode == 0 else "failed"
    print(f"phase={command.phase} status={status} returncode={completed.returncode} elapsed_seconds={elapsed}", flush=True)
    return PhaseResult(command.phase, status, int(completed.returncode), elapsed, command.command, started_at, ended_at)


def write_plan(path: Path, commands: list[PhaseCommand]) -> None:
    lines = [
        "# Generated SEC historical backfill plan.",
        "# Review before running commands manually.",
        "$ErrorActionPreference = 'Stop'",
        "",
    ]
    for command in commands:
        lines.append(f"# phase: {command.phase}")
        lines.append(format_command(command.command))
        lines.append("")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_summary(path: Path, args: argparse.Namespace, commands: list[PhaseCommand], results: list[PhaseResult]) -> None:
    result_by_phase = {row.phase: row for row in results}
    lines = [
        "# SEC Historical Backfill Orchestrator Summary",
        "",
        f"- execute: `{args.execute}`",
        f"- start_date: `{args.start_date or ''}`",
        f"- end_date: `{args.end_date or ''}`",
        "",
        "| Phase | Status | Return Code | Seconds |",
        "| --- | --- | ---: | ---: |",
    ]
    for command in commands:
        result = result_by_phase.get(command.phase)
        if result is None:
            lines.append(f"| `{command.phase}` | not_run |  |  |")
        else:
            lines.append(f"| `{command.phase}` | {result.status} | {result.returncode} | {result.elapsed_seconds:.3f} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def append_jsonl(path: Path, row: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")


def command_manifest(command: PhaseCommand) -> dict[str, object]:
    return {
        "phase": command.phase,
        "command": command.command,
        "cwd": command.cwd,
        "mutates_database": command.mutates_database,
        "downloads_data": command.downloads_data,
    }


def add_positive(command: list[str], flag: str, value: int) -> None:
    if value > 0:
        command.extend([flag, str(value)])


def script(relative_path: str) -> str:
    return str(REPO_ROOT / relative_path)


def format_command(command: Iterable[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def parse_iso_date(value: str) -> date:
    return date.fromisoformat(value)


if __name__ == "__main__":
    main()

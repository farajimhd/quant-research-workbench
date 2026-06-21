from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time as dt_time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines.sec.edgar.sec_pipeline.config import SecPipelineConfig, env_string  # noqa: E402
from pipelines.sec.edgar.sec_pipeline.coverage import (  # noqa: E402
    KIND_BULK_COMPANYFACTS,
    KIND_DAILY_ARCHIVE,
    KIND_INTEGRITY_AUDIT,
    KIND_LIVE_FEED,
    KIND_TEXT_EXTRACTION,
    SecCoverageConfig,
    insert_coverage,
    new_coverage_id,
)
from research.mlops.clickhouse import ClickHouseHttpClient  # noqa: E402
from research.mlops.env import discover_env_files, load_env_files, secret_status  # noqa: E402


DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_historical_gap_fill")
DEFAULT_PARTS_ROOT_WIN = Path("D:/market-data")
DEFAULT_PARTS_ROOT_CH = "/mnt/d/market-data"


@dataclass(frozen=True, slots=True)
class StageCommand:
    stage: str
    command: list[str]
    log_path: Path
    mutates_database: bool


@dataclass(frozen=True, slots=True)
class StageResult:
    stage: str
    status: str
    returncode: int
    elapsed_seconds: float
    log_path: str
    command: list[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Unified SEC historical gap fill. This is the gateway-facing historical "
            "entry point: it downloads missing daily archives, extracts normalized "
            "filing/document/text rows, inserts them, catches up XBRL companyfacts, "
            "repairs XBRL relationships, audits the result, and writes coverage."
        )
    )
    parser.add_argument("--start-date", required=True, help="Inclusive UTC/archive date, YYYY-MM-DD.")
    parser.add_argument("--end-date", required=True, help="Exclusive UTC/archive date, YYYY-MM-DD.")
    parser.add_argument("--execute", action="store_true", help="Execute writes. Without this, only dry-run stage commands execute.")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--python-executable", default=sys.executable)
    parser.add_argument("--read-database", default=env_string("SEC_CLICKHOUSE_READ_DATABASE", "q_live"))
    parser.add_argument("--write-database", default=env_string("SEC_CLICKHOUSE_WRITE_DATABASE", env_string("SEC_GATEWAY_WRITE_DATABASE", "q_sec_tmp")))
    parser.add_argument("--coverage-table", default=env_string("SEC_COVERAGE_TABLE", "sec_coverage_manifest_v1"))
    parser.add_argument("--artifact-root-win", default=env_string("SEC_CORE_ARTIFACT_ROOT_WIN", "D:/market-data/sec_core"))
    parser.add_argument("--output-root-win", default=env_string("SEC_HISTORICAL_GAP_FILL_OUTPUT_ROOT_WIN", str(DEFAULT_OUTPUT_ROOT_WIN)))
    parser.add_argument("--daily-archive-output-root-win", default=env_string("SEC_DAILY_FEED_OUTPUT_ROOT_WIN", "D:/market-data/prepared/sec_daily_feed_archives"))
    parser.add_argument("--archive-validation-output-root-win", default=env_string("SEC_DOWNLOADED_ARCHIVE_VALIDATION_OUTPUT_ROOT_WIN", "D:/market-data/prepared/sec_downloaded_archive_validation"))
    parser.add_argument("--text-parts-output-root-win", default=env_string("SEC_FILING_TEXT_PARTS_OUTPUT_ROOT_WIN", "D:/market-data/prepared/sec_filing_text_parts"))
    parser.add_argument("--xbrl-output-root-win", default=env_string("SEC_XBRL_CATCHUP_OUTPUT_ROOT_WIN", "D:/market-data/prepared/sec_xbrl_companyfacts_catchup"))
    parser.add_argument("--xbrl-repair-output-root-win", default=env_string("SEC_XBRL_REPAIR_OUTPUT_ROOT_WIN", "D:/market-data/prepared/sec_xbrl_integrity_repair"))
    parser.add_argument("--integrity-audit-output-root-win", default=env_string("SEC_INTEGRITY_AUDIT_OUTPUT_ROOT_WIN", "D:/market-data/prepared/sec_integrity_audit"))
    parser.add_argument("--parts-root-win", default=env_string("SEC_TEXT_PARTS_ROOT_WIN", str(DEFAULT_PARTS_ROOT_WIN)))
    parser.add_argument("--parts-root-ch", default=env_string("SEC_TEXT_PARTS_ROOT_CH", DEFAULT_PARTS_ROOT_CH))
    parser.add_argument("--archive-download-concurrency", type=int, default=2)
    parser.add_argument("--archive-validation-workers", type=int, default=4)
    parser.add_argument("--text-extract-workers", type=int, default=4)
    parser.add_argument("--xbrl-workers", type=int, default=4)
    parser.add_argument("--sec-request-min-interval-seconds", type=float, default=0.2)
    parser.add_argument("--request-timeout-seconds", type=float, default=60.0)
    parser.add_argument("--max-retries", type=int, default=8)
    parser.add_argument("--retry-base-seconds", type=float, default=30.0)
    parser.add_argument("--pending-multiplier", type=int, default=2)
    parser.add_argument("--sample-limit", type=int, default=1000)
    parser.add_argument("--sample-text-chars", type=int, default=2000)
    parser.add_argument("--min-text-chars", type=int, default=40)
    parser.add_argument("--max-text-chars", type=int, default=250000)
    parser.add_argument("--text-limit-parts", type=int, default=0)
    parser.add_argument("--limit-days", type=int, default=0)
    parser.add_argument("--limit-archives", type=int, default=0)
    parser.add_argument("--max-filings-per-archive", type=int, default=0)
    parser.add_argument("--force-download", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
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
    manifest = {
        "run_id": run_id,
        "created_at_utc": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "script": str(Path(__file__).resolve()),
        "execute": bool(args.execute),
        "start_date": args.start_date,
        "end_date": args.end_date,
        "read_database": args.read_database,
        "write_database": args.write_database,
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
    for command in commands:
        result = run_stage(command)
        results.append(result)
        append_jsonl(results_path, asdict(result))
        if result.returncode != 0 and not args.continue_on_error:
            write_summary(run_root / "sec_historical_gap_fill_summary.md", args, results, coverage_written=False)
            raise SystemExit(result.returncode)

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
    text_manifest = f"<latest-text-manifest:{args.text_parts_output_root_win}>"
    text_ingest_execute = [
        args.python_executable,
        script("pipelines/sec/edgar/sec_filing_text_clickhouse_file_ingest.py"),
        "--manifest-json",
        text_manifest,
        "--database",
        args.write_database,
        "--parts-root-win",
        args.parts_root_win,
        "--parts-root-ch",
        args.parts_root_ch,
    ]
    if args.execute:
        text_ingest_execute.extend(["--execute", "--skip-preflight"])
    return [
        StageCommand(
            "daily-archive-download",
            add_execute_flag(
                [
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
            ],
            logs_root / "validate-downloaded.log",
            False,
        ),
        StageCommand(
            "text-extract",
            add_execute_flag(
                [
                    args.python_executable,
                    script("pipelines/sec/edgar/sec_filing_text_extract_parts.py"),
                    "--archive-root-win",
                    archive_root,
                    "--output-root-win",
                    args.text_parts_output_root_win,
                    "--start-date",
                    args.start_date,
                    "--end-date",
                    args.end_date,
                    "--archive-workers",
                    str(max(1, args.text_extract_workers)),
                    "--pending-multiplier",
                    str(max(1, args.pending_multiplier)),
                    "--sample-limit",
                    str(max(0, args.sample_limit)),
                    "--sample-text-chars",
                    str(max(0, args.sample_text_chars)),
                    "--min-text-chars",
                    str(max(0, args.min_text_chars)),
                    "--max-text-chars",
                    str(max(1, args.max_text_chars)),
                    "--progress-every",
                    "1",
                ],
                args,
                dry_run_flag="--dry-run",
            ),
            logs_root / "text-extract.log",
            False,
        ),
        StageCommand(
            "text-ingest-preflight",
            [
                args.python_executable,
                script("pipelines/sec/edgar/sec_filing_text_clickhouse_file_ingest.py"),
                "--manifest-json",
                text_manifest,
                "--database",
                args.write_database,
                "--parts-root-win",
                args.parts_root_win,
                "--parts-root-ch",
                args.parts_root_ch,
                "--preflight-only",
            ],
            logs_root / "text-ingest-preflight.log",
            False,
        ),
        StageCommand(
            "text-ingest-execute",
            text_ingest_execute,
            logs_root / "text-ingest-execute.log",
            True,
        ),
        StageCommand(
            "xbrl-companyfacts-catchup",
            add_execute_flag(
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
        ),
        StageCommand(
            "xbrl-integrity-repair",
            add_execute_flag(
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
                "--require-v2-tables",
            ],
            logs_root / "integrity-audit.log",
            False,
        ),
    ]


def add_execute_flag(command: list[str], args: argparse.Namespace, *, dry_run_flag: str = "") -> list[str]:
    out = list(command)
    command_text = " ".join(out)
    if "sec_daily_feed_archive_download.py" in command_text:
        if args.force_download:
            out.append("--force")
        if args.limit_days:
            out.extend(["--limit-days", str(args.limit_days)])
    if "sec_filing_text_extract_parts.py" in command_text:
        if args.limit_archives:
            out.extend(["--limit-archives", str(args.limit_archives)])
        if args.max_filings_per_archive:
            out.extend(["--max-filings-per-archive", str(args.max_filings_per_archive)])
    if "sec_filing_text_clickhouse_file_ingest.py" in command_text and args.text_limit_parts:
        out.extend(["--limit-parts", str(args.text_limit_parts)])
    if args.execute:
        if "sec_xbrl_companyfacts_catchup.py" in command_text or "sec_xbrl_integrity_repair.py" in command_text:
            out.append("--execute")
    elif dry_run_flag:
        out.append(dry_run_flag)
    return out


def run_stage(command: StageCommand) -> StageResult:
    actual_command = resolve_runtime_command(command)
    started = time.perf_counter()
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
            print(line, end="", flush=True)
            log.write(line)
            log.flush()
        returncode = int(process.wait())
    elapsed = round(time.perf_counter() - started, 3)
    status = "ok" if returncode == 0 else "failed"
    print(f"stage={command.stage} status={status} returncode={returncode} elapsed_seconds={elapsed}", flush=True)
    return StageResult(command.stage, status, returncode, elapsed, str(command.log_path), actual_command)


def resolve_runtime_command(command: StageCommand) -> list[str]:
    placeholder = next((item for item in command.command if item.startswith("<latest-text-manifest:")), "")
    if not placeholder:
        return command.command
    root = placeholder.removeprefix("<latest-text-manifest:").removesuffix(">")
    manifest = latest_text_manifest(Path(root))
    return [str(manifest) if item == placeholder else item for item in command.command]


def latest_text_manifest(root: Path) -> Path:
    candidates = sorted(root.glob("*/sec_filing_text_extract_manifest.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not candidates:
        raise SystemExit(f"no sec_filing_text_extract_manifest.json found under {root}")
    return candidates[0]


def write_coverage(args: argparse.Namespace, run_id: str) -> None:
    config = SecPipelineConfig.from_env()
    client = ClickHouseHttpClient(config.clickhouse.url, config.clickhouse.user, config.clickhouse.password)
    coverage = SecCoverageConfig(
        database=args.write_database,
        coverage_table=args.coverage_table,
        storage_policy=os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY") or "",
    )
    start = datetime.combine(parse_date(args.start_date), dt_time.min, tzinfo=UTC)
    end = datetime.combine(parse_date(args.end_date), dt_time.min, tzinfo=UTC)
    for kind in [KIND_DAILY_ARCHIVE, KIND_LIVE_FEED, KIND_TEXT_EXTRACTION, KIND_BULK_COMPANYFACTS, KIND_INTEGRITY_AUDIT]:
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


if __name__ == "__main__":
    main()

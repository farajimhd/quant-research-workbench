from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.clickhouse import (  # noqa: E402
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    quote_ident,
    sql_string,
)
from research.mlops.env import discover_env_files, load_env_files, secret_status  # noqa: E402


DEFAULT_DATABASE = "q_live"
DEFAULT_ARCHIVE_ROOT_WIN = Path("D:/market-data/sec_core/daily_archives")
DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_filing_text_repair_rebuild")
DEFAULT_TEXT_PARTS_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_filing_text_parts_repair")
DEFAULT_PARTS_ROOT_WIN = Path("D:/market-data")
DEFAULT_PARTS_ROOT_CH = "/mnt/d/market-data"


@dataclass(frozen=True, slots=True)
class RepairCommand:
    stage: str
    command: list[str]
    log_path: str


@dataclass(frozen=True, slots=True)
class CommandResult:
    stage: str
    returncode: int
    elapsed_seconds: float
    log_path: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Rebuild SEC filing document/text rows from daily archive raw submissions with "
            "the current parser. This is intended for repairing previously capped or "
            "misclassified sec_filing_text_v2 rows. Dry-run writes a plan only."
        )
    )
    parser.add_argument("--start-date", required=True, help="Inclusive archive date, YYYY-MM-DD.")
    parser.add_argument("--end-date", required=True, help="Exclusive archive date, YYYY-MM-DD.")
    parser.add_argument("--execute", action="store_true", help="Run extraction and ClickHouse ingest. Without this, only write a plan.")
    parser.add_argument("--python-executable", default=sys.executable)
    parser.add_argument("--clickhouse-url", default=default_sec_clickhouse_url())
    parser.add_argument("--user", default=default_sec_clickhouse_user())
    parser.add_argument("--password", default=default_sec_clickhouse_password())
    parser.add_argument("--database", default=os.environ.get("SEC_CLICKHOUSE_DATABASE") or DEFAULT_DATABASE)
    parser.add_argument("--archive-root-win", default=os.environ.get("SEC_DAILY_ARCHIVE_ROOT_WIN", str(DEFAULT_ARCHIVE_ROOT_WIN)))
    parser.add_argument("--output-root-win", default=os.environ.get("SEC_TEXT_REPAIR_OUTPUT_ROOT_WIN", str(DEFAULT_OUTPUT_ROOT_WIN)))
    parser.add_argument("--text-parts-output-root-win", default=os.environ.get("SEC_TEXT_REPAIR_PARTS_OUTPUT_ROOT_WIN", str(DEFAULT_TEXT_PARTS_OUTPUT_ROOT_WIN)))
    parser.add_argument("--parts-root-win", default=os.environ.get("SEC_TEXT_PARTS_ROOT_WIN") or str(DEFAULT_PARTS_ROOT_WIN))
    parser.add_argument("--parts-root-ch", default=os.environ.get("SEC_TEXT_PARTS_ROOT_CH") or os.environ.get("TD__DATABASE__CLICKHOUSE__FILE_ROOT") or DEFAULT_PARTS_ROOT_CH)
    parser.add_argument("--archive-workers", type=int, default=int(os.environ.get("SEC_TEXT_REPAIR_ARCHIVE_WORKERS", "4")))
    parser.add_argument("--pending-multiplier", type=int, default=int(os.environ.get("SEC_TEXT_REPAIR_PENDING_MULTIPLIER", "2")))
    parser.add_argument("--sample-limit", type=int, default=1000)
    parser.add_argument("--sample-text-chars", type=int, default=2000)
    parser.add_argument("--min-text-chars", type=int, default=40)
    parser.add_argument("--max-text-chars", type=int, default=0, help="Optional normalized text storage cap. 0 means unlimited.")
    parser.add_argument("--limit-archives", type=int, default=0)
    parser.add_argument("--max-filings-per-archive", type=int, default=0)
    parser.add_argument("--limit-parts", type=int, default=0)
    parser.add_argument("--max-threads", type=int, default=int(os.environ.get("SEC_TEXT_FILE_INGEST_MAX_THREADS", "24")))
    parser.add_argument("--max-memory-usage", default=os.environ.get("SEC_TEXT_FILE_INGEST_MAX_MEMORY", "0"))
    parser.add_argument("--skip-preflight", action="store_true")
    parser.add_argument(
        "--cleanup-stale-skips",
        action="store_true",
        help=(
            "After replacement text rows are inserted, delete old sec_filing_document_skip_v1 "
            "rows for document_ids that now have text in this repair run."
        ),
    )
    parser.add_argument("--cleanup-mutations-sync", type=int, default=0, choices=[0, 1, 2])
    return parser.parse_args()


def main() -> None:
    loaded_env_files = load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args()
    validate_args(args)

    run_id = f"sec_filing_text_repair_rebuild_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    run_root = Path(args.output_root_win) / run_id
    logs_root = run_root / "logs"
    logs_root.mkdir(parents=True, exist_ok=True)

    commands = build_commands(args, logs_root)
    manifest_path = run_root / "sec_filing_text_repair_rebuild_manifest.json"
    summary_path = run_root / "sec_filing_text_repair_rebuild_summary.md"
    manifest: dict[str, Any] = {
        "run_id": run_id,
        "created_at_utc": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "execute": bool(args.execute),
        "start_date": args.start_date,
        "end_date": args.end_date,
        "database": args.database,
        "archive_root_win": args.archive_root_win,
        "text_parts_output_root_win": args.text_parts_output_root_win,
        "commands": [asdict(command) for command in commands],
        "loaded_env_files": [str(path) for path in loaded_env_files],
        "secret_status": secret_status(
            [
                "SEC_CLICKHOUSE_URL",
                "SEC_CLICKHOUSE_USER",
                "SEC_CLICKHOUSE_PASSWORD",
                "REAL_LIVE_CLICKHOUSE_WRITE_URL",
                "REAL_LIVE_CLICKHOUSE_WRITE_USER",
                "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD",
            ]
        ),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str), encoding="utf-8")
    write_plan(run_root / "sec_filing_text_repair_rebuild_plan.ps1", commands)

    print("=" * 96, flush=True)
    print("SEC filing text repair rebuild", flush=True)
    print(f"run_root={run_root}", flush=True)
    print(f"execute={args.execute} range=[{args.start_date},{args.end_date})", flush=True)
    print("This repair rebuilds document/text rows only; run acceptance timestamp repair separately if needed.", flush=True)
    print("=" * 96, flush=True)
    for index, command in enumerate(commands, start=1):
        print(f"[{index}/{len(commands)}] {command.stage}: {format_command(command.command)}", flush=True)

    if not args.execute:
        write_summary(summary_path, manifest, [], None, {"status": "dry_run"})
        print(f"dry_run=true manifest={manifest_path}", flush=True)
        return

    results: list[CommandResult] = []
    started_at_epoch = time.time()
    run_command(commands[0], results)
    extract_manifest = latest_extract_manifest(Path(args.text_parts_output_root_win), started_at_epoch)
    if not extract_manifest:
        raise SystemExit(f"could not find sec_filing_text_extract_manifest.json under {args.text_parts_output_root_win}")
    extract_payload = json.loads(extract_manifest.read_text(encoding="utf-8"))
    ingest_command = build_ingest_command(args, logs_root, extract_manifest)
    run_command(ingest_command, results)
    cleanup_summary = {"status": "skipped", "reason": "cleanup_stale_skips_not_requested"}
    if args.cleanup_stale_skips:
        cleanup_summary = cleanup_stale_skip_rows(args, extract_payload)

    manifest["execute_results"] = [asdict(result) for result in results]
    manifest["extract_manifest"] = str(extract_manifest)
    manifest["extract_source_run_id"] = extract_payload.get("source_run_id")
    manifest["cleanup_summary"] = cleanup_summary
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str), encoding="utf-8")
    write_summary(summary_path, manifest, results, extract_manifest, cleanup_summary)
    print(f"manifest={manifest_path}", flush=True)
    print(f"summary={summary_path}", flush=True)


def default_sec_clickhouse_url() -> str:
    return os.environ.get("SEC_CLICKHOUSE_URL") or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_URL") or default_clickhouse_url()


def default_sec_clickhouse_user() -> str:
    return os.environ.get("SEC_CLICKHOUSE_USER") or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_USER") or default_clickhouse_user()


def default_sec_clickhouse_password() -> str:
    return os.environ.get("SEC_CLICKHOUSE_PASSWORD") or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD") or default_clickhouse_password()


def validate_args(args: argparse.Namespace) -> None:
    validate_identifier(args.database, "--database")
    if args.start_date >= args.end_date:
        raise SystemExit("--end-date must be later than --start-date")
    if not Path(args.archive_root_win).exists():
        raise SystemExit(f"archive root does not exist: {args.archive_root_win}")
    if args.archive_workers <= 0:
        raise SystemExit("--archive-workers must be positive")
    if args.pending_multiplier <= 0:
        raise SystemExit("--pending-multiplier must be positive")
    if args.max_text_chars < 0:
        raise SystemExit("--max-text-chars must be >= 0")


def validate_identifier(value: str, label: str) -> None:
    if not value or not all(part.replace("_", "").isalnum() for part in value.split(".")):
        raise SystemExit(f"{label} must be a simple ClickHouse identifier, got {value!r}")


def build_commands(args: argparse.Namespace, logs_root: Path) -> list[RepairCommand]:
    extract_command = [
        args.python_executable,
        script("pipelines/sec/edgar/sec_filing_text_extract_parts.py"),
        "--database",
        args.database,
        "--archive-root-win",
        args.archive_root_win,
        "--output-root-win",
        args.text_parts_output_root_win,
        "--start-date",
        args.start_date,
        "--end-date",
        args.end_date,
        "--archive-workers",
        str(max(1, args.archive_workers)),
        "--pending-multiplier",
        str(max(1, args.pending_multiplier)),
        "--sample-limit",
        str(max(0, args.sample_limit)),
        "--sample-text-chars",
        str(max(0, args.sample_text_chars)),
        "--min-text-chars",
        str(max(0, args.min_text_chars)),
        "--max-text-chars",
        str(max(0, args.max_text_chars)),
        "--progress-every",
        "1",
    ]
    if args.limit_archives:
        extract_command.extend(["--limit-archives", str(max(0, args.limit_archives))])
    if args.max_filings_per_archive:
        extract_command.extend(["--max-filings-per-archive", str(max(0, args.max_filings_per_archive))])
    return [RepairCommand("text-extract", extract_command, str(logs_root / "text-extract.log"))]


def build_ingest_command(args: argparse.Namespace, logs_root: Path, manifest_path: Path) -> RepairCommand:
    command = [
        args.python_executable,
        script("pipelines/sec/edgar/sec_filing_text_clickhouse_file_ingest.py"),
        "--manifest-json",
        str(manifest_path),
        "--database",
        args.database,
        "--parts-root-win",
        args.parts_root_win,
        "--parts-root-ch",
        args.parts_root_ch,
        "--max-threads",
        str(max(1, args.max_threads)),
        "--max-memory-usage",
        str(args.max_memory_usage),
        "--execute",
        "--force",
    ]
    if args.limit_parts:
        command.extend(["--limit-parts", str(max(0, args.limit_parts))])
    if args.skip_preflight:
        command.append("--skip-preflight")
    return RepairCommand("text-ingest-execute", command, str(logs_root / "text-ingest-execute.log"))


def run_command(command: RepairCommand, results: list[CommandResult]) -> None:
    started = time.perf_counter()
    log_path = Path(command.log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    print("=" * 96, flush=True)
    print(f"stage={command.stage}", flush=True)
    print(format_command(command.command), flush=True)
    print(f"log={log_path}", flush=True)
    print("=" * 96, flush=True)
    with log_path.open("w", encoding="utf-8") as log:
        log.write(f"command={format_command(command.command)}\n\n")
        process = subprocess.Popen(
            command.command,
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
        returncode = process.wait()
    result = CommandResult(command.stage, returncode, round(time.perf_counter() - started, 3), str(log_path))
    results.append(result)
    if returncode:
        raise RuntimeError(f"stage failed: {command.stage} rc={returncode} log={log_path}")


def latest_extract_manifest(root: Path, started_at_epoch: float) -> Path | None:
    manifests = sorted(
        (path for path in root.glob("*/sec_filing_text_extract_manifest.json") if path.stat().st_mtime >= started_at_epoch - 1.0),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return manifests[0] if manifests else None


def cleanup_stale_skip_rows(args: argparse.Namespace, extract_manifest: dict[str, Any]) -> dict[str, Any]:
    source_run_id = str(extract_manifest.get("source_run_id") or "")
    if not source_run_id:
        raise RuntimeError("extract manifest has no source_run_id; cannot scope stale skip cleanup")
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    text_table = f"{quote_ident(args.database)}.sec_filing_text_v2"
    skip_table = f"{quote_ident(args.database)}.sec_filing_document_skip_v1"
    count_sql = f"""
SELECT count()
FROM {skip_table} FINAL
WHERE document_id IN (
    SELECT document_id
    FROM {text_table} FINAL
    WHERE source_run_id = {sql_string(source_run_id)}
)
"""
    stale_count = int((client.execute(count_sql).strip() or "0").splitlines()[0])
    if stale_count <= 0:
        return {"status": "ok", "source_run_id": source_run_id, "stale_skip_rows": 0, "mutation_submitted": False}
    delete_sql = f"""
ALTER TABLE {skip_table}
DELETE WHERE document_id IN (
    SELECT document_id
    FROM {text_table} FINAL
    WHERE source_run_id = {sql_string(source_run_id)}
)
SETTINGS mutations_sync = {int(args.cleanup_mutations_sync)}
"""
    client.execute(delete_sql)
    return {"status": "ok", "source_run_id": source_run_id, "stale_skip_rows": stale_count, "mutation_submitted": True}


def script(relative: str) -> str:
    return str(REPO_ROOT / relative)


def write_plan(path: Path, commands: list[RepairCommand]) -> None:
    lines = ["$ErrorActionPreference = 'Stop'", f"Set-Location -LiteralPath {ps_quote(str(REPO_ROOT))}", ""]
    for command in commands:
        lines.append(f"# {command.stage}")
        lines.append(format_powershell_command(command.command))
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_summary(
    path: Path,
    manifest: dict[str, Any],
    results: list[CommandResult],
    extract_manifest: Path | None,
    cleanup_summary: dict[str, Any],
) -> None:
    lines = [
        "# SEC Filing Text Repair Rebuild",
        "",
        f"- run_id: `{manifest.get('run_id')}`",
        f"- execute: `{manifest.get('execute')}`",
        f"- range: `{manifest.get('start_date')}` to `{manifest.get('end_date')}`",
        f"- database: `{manifest.get('database')}`",
        f"- extract_manifest: `{extract_manifest or ''}`",
        f"- cleanup: `{json.dumps(cleanup_summary, sort_keys=True)}`",
        "",
        "## Stages",
        "",
    ]
    if not results:
        lines.append("- dry run only; no stages executed")
    for result in results:
        lines.append(f"- {result.stage}: rc={result.returncode} elapsed={result.elapsed_seconds}s log=`{result.log_path}`")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def format_command(command: list[str]) -> str:
    return " ".join(command)


def format_powershell_command(command: list[str]) -> str:
    return " ".join(ps_quote(part) for part in command)


def ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath
from typing import Any


DEFAULT_DISCOVERY_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_archive_content_discovery")
DEFAULT_SOURCE_ARCHIVE_ROOT_WIN = "D:/market-data/sec_core/daily_archives"
DEFAULT_TARGET_ARCHIVE_ROOT_WIN = Path("G:/market-data/sec_core/daily_archives")
DEFAULT_DELETE_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_archive_failed_archive_delete")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Delete SEC daily archives that a sec_archive_content_discovery.py run marked "
            "as failed. The script defaults to dry-run and validates every target path "
            "under --archive-root-win before deleting."
        )
    )
    parser.add_argument(
        "--discovery-run-root",
        default="",
        help=(
            "Discovery run folder containing archive_summary.jsonl. If omitted, the latest "
            "run under --discovery-output-root-win is used."
        ),
    )
    parser.add_argument(
        "--discovery-output-root-win",
        default=os.environ.get("SEC_ARCHIVE_DISCOVERY_OUTPUT_ROOT_WIN", str(DEFAULT_DISCOVERY_OUTPUT_ROOT_WIN)),
        help="Parent folder used only when --discovery-run-root is omitted.",
    )
    parser.add_argument(
        "--archive-summary-jsonl",
        default="",
        help="Explicit archive_summary.jsonl path. Overrides --discovery-run-root.",
    )
    parser.add_argument(
        "--source-archive-root-win",
        default=os.environ.get("SEC_DELETE_FAILED_SOURCE_ARCHIVE_ROOT_WIN", DEFAULT_SOURCE_ARCHIVE_ROOT_WIN),
        help="Archive root recorded in discovery rows. Used to derive relative target paths.",
    )
    parser.add_argument(
        "--archive-root-win",
        default=os.environ.get("SEC_DELETE_FAILED_ARCHIVE_ROOT_WIN", str(DEFAULT_TARGET_ARCHIVE_ROOT_WIN)),
        help="Local archive root to delete from. For the workstation HDD backup this is usually G:/market-data/sec_core/daily_archives.",
    )
    parser.add_argument(
        "--output-root-win",
        default=os.environ.get("SEC_DELETE_FAILED_ARCHIVES_OUTPUT_ROOT_WIN", str(DEFAULT_DELETE_OUTPUT_ROOT_WIN)),
        help="Where to write the deletion audit report.",
    )
    parser.add_argument("--status", default="failed", help="archive_summary status to delete. Default: failed.")
    parser.add_argument("--expected-count", type=int, default=0, help="Abort if the selected row count differs from this value.")
    parser.add_argument("--execute", action="store_true", help="Actually delete files. Without this flag the script is a dry-run.")
    parser.add_argument(
        "--windows-fix-acl",
        action="store_true",
        help=(
            "On Windows, if deletion is denied, clear the read-only attribute and grant the "
            "current user full control on the exact target file, then retry deletion."
        ),
    )
    parser.add_argument(
        "--windows-take-ownership",
        action="store_true",
        help=(
            "With --windows-fix-acl, run takeown on the exact target file before granting "
            "ACL rights. This usually requires an elevated terminal."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    started = time.perf_counter()

    archive_root = Path(args.archive_root_win)
    archive_root_resolved = resolve_existing_dir(archive_root, "--archive-root-win")
    summary_path = resolve_summary_path(args)
    output_root = Path(args.output_root_win)
    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    run_root = output_root / run_id
    run_root.mkdir(parents=True, exist_ok=True)

    print("=" * 96, flush=True)
    print("SEC failed archive delete", flush=True)
    print(f"execute={bool(args.execute)}", flush=True)
    print(f"archive_summary={summary_path}", flush=True)
    print(f"source_archive_root={args.source_archive_root_win}", flush=True)
    print(f"target_archive_root={archive_root_resolved}", flush=True)
    print(f"run_root={run_root}", flush=True)
    print("=" * 96, flush=True)

    candidates = load_candidates(summary_path, args.status)
    if args.expected_count and len(candidates) != args.expected_count:
        raise SystemExit(f"expected {args.expected_count:,} candidate rows but found {len(candidates):,}")
    if not candidates:
        raise SystemExit(f"no archive_summary rows found with status={args.status!r}")

    report_rows: list[dict[str, Any]] = []
    deleted_count = 0
    missing_count = 0
    error_count = 0
    candidate_bytes = 0
    deleted_bytes = 0

    for index, row in enumerate(candidates, start=1):
        source_path = str(row.get("archive_path", ""))
        rel_path = derive_relative_archive_path(source_path, args.source_archive_root_win, row)
        target_path = archive_root / rel_path
        target_resolved = target_path.resolve(strict=False)
        ensure_under_root(target_resolved, archive_root_resolved)

        exists_before = target_path.exists()
        size_before = target_path.stat().st_size if exists_before else 0
        candidate_bytes += int(row.get("archive_bytes") or 0)
        action = "dry_run"
        error = ""
        acl_actions: list[dict[str, Any]] = []

        if not exists_before:
            missing_count += 1
            action = "missing"
        elif args.execute:
            try:
                target_path.unlink()
            except PermissionError as exc:  # pragma: no cover - depends on local ACL/runtime
                error = repr(exc)
                if args.windows_fix_acl:
                    acl_actions = repair_windows_file_acl(target_path, take_ownership=bool(args.windows_take_ownership))
                    try:
                        target_path.unlink()
                    except Exception as retry_exc:  # pragma: no cover - depends on local ACL/runtime
                        error_count += 1
                        action = "error"
                        error = f"{error}; retry_after_acl={retry_exc!r}"
                    else:
                        deleted_count += 1
                        deleted_bytes += size_before
                        action = "deleted_after_acl"
                        error = ""
                else:
                    error_count += 1
                    action = "error"
            except Exception as exc:  # pragma: no cover - depends on local runtime
                error_count += 1
                action = "error"
                error = repr(exc)
            else:
                deleted_count += 1
                deleted_bytes += size_before
                action = "deleted"

        report_rows.append(
            {
                "index": index,
                "archive_date": row.get("archive_date", ""),
                "source_archive_path": source_path,
                "target_archive_path": str(target_path),
                "relative_path": str(rel_path),
                "status": row.get("status", ""),
                "discovery_error": row.get("error", ""),
                "archive_bytes_reported": int(row.get("archive_bytes") or 0),
                "size_before": size_before,
                "exists_before": exists_before,
                "exists_after": target_path.exists(),
                "action": action,
                "acl_actions": acl_actions,
                "error": error,
            }
        )

        if index == 1 or index % 10 == 0 or index == len(candidates):
            print(
                f"processed={index:,}/{len(candidates):,} deleted={deleted_count:,} "
                f"missing={missing_count:,} errors={error_count:,}",
                flush=True,
            )

    summary = {
        "run_id": run_id,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "script": str(Path(__file__).resolve()),
        "execute": bool(args.execute),
        "windows_fix_acl": bool(args.windows_fix_acl),
        "windows_take_ownership": bool(args.windows_take_ownership),
        "archive_summary_jsonl": str(summary_path),
        "source_archive_root": args.source_archive_root_win,
        "target_archive_root": str(archive_root_resolved),
        "status_filter": args.status,
        "candidate_count": len(candidates),
        "candidate_bytes_reported": candidate_bytes,
        "deleted_count": deleted_count,
        "deleted_bytes": deleted_bytes,
        "missing_count": missing_count,
        "error_count": error_count,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }

    report_path = run_root / "failed_archive_delete_report.json"
    rows_path = run_root / "failed_archive_delete_rows.jsonl"
    report_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    write_jsonl(rows_path, report_rows)

    print("=" * 96, flush=True)
    print(f"candidate_count={len(candidates):,}", flush=True)
    print(f"deleted_count={deleted_count:,} deleted_bytes={deleted_bytes:,}", flush=True)
    print(f"missing_count={missing_count:,} error_count={error_count:,}", flush=True)
    print(f"report={report_path}", flush=True)
    print(f"rows={rows_path}", flush=True)
    print("=" * 96, flush=True)

    if error_count:
        raise SystemExit(2)


def resolve_existing_dir(path: Path, arg_name: str) -> Path:
    if not path.exists():
        raise SystemExit(f"{arg_name} does not exist: {path}")
    if not path.is_dir():
        raise SystemExit(f"{arg_name} is not a directory: {path}")
    return path.resolve()


def resolve_summary_path(args: argparse.Namespace) -> Path:
    if args.archive_summary_jsonl:
        summary_path = Path(args.archive_summary_jsonl)
    else:
        run_root = Path(args.discovery_run_root) if args.discovery_run_root else latest_discovery_run(Path(args.discovery_output_root_win))
        summary_path = run_root / "archive_summary.jsonl"
    if not summary_path.exists():
        raise SystemExit(f"archive summary does not exist: {summary_path}")
    if not summary_path.is_file():
        raise SystemExit(f"archive summary is not a file: {summary_path}")
    return summary_path.resolve()


def latest_discovery_run(output_root: Path) -> Path:
    if not output_root.exists():
        raise SystemExit(f"discovery output root does not exist: {output_root}")
    runs = [path for path in output_root.iterdir() if path.is_dir() and (path / "archive_summary.jsonl").exists()]
    if not runs:
        raise SystemExit(f"no discovery runs with archive_summary.jsonl found under: {output_root}")
    return max(runs, key=lambda path: path.stat().st_mtime)


def load_candidates(summary_path: Path, status: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    with summary_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"invalid JSON in {summary_path} line {line_number}: {exc}") from exc
            if row.get("status") == status:
                candidates.append(row)
    return candidates


def derive_relative_archive_path(source_path: str, source_root: str, row: dict[str, Any]) -> Path:
    if source_path:
        relative = relative_to_windows_root(PureWindowsPath(source_path), PureWindowsPath(source_root))
        if relative is not None:
            return Path(*relative.parts)
        parts = PureWindowsPath(source_path).parts
        lowered = [part.lower() for part in parts]
        if "daily_archives" in lowered:
            index = lowered.index("daily_archives") + 1
            return Path(*parts[index:])

    archive_date = str(row.get("archive_date", ""))
    if len(archive_date) >= 10:
        year = archive_date[:4]
        month = int(archive_date[5:7])
        quarter = f"QTR{((month - 1) // 3) + 1}"
        filename = f"{archive_date.replace('-', '')}.nc.tar.gz"
        return Path(year) / quarter / filename

    raise SystemExit(f"cannot derive relative archive path from row: {row}")


def relative_to_windows_root(path: PureWindowsPath, root: PureWindowsPath) -> PureWindowsPath | None:
    path_parts = [part.lower() for part in path.parts]
    root_parts = [part.lower() for part in root.parts]
    if len(path_parts) < len(root_parts):
        return None
    if path_parts[: len(root_parts)] != root_parts:
        return None
    return PureWindowsPath(*path.parts[len(root_parts) :])


def ensure_under_root(path: Path, root: Path) -> None:
    path_norm = os.path.normcase(str(path))
    root_norm = os.path.normcase(str(root))
    try:
        common = os.path.commonpath([path_norm, root_norm])
    except ValueError as exc:
        raise SystemExit(f"refusing target outside archive root: {path}") from exc
    if common != root_norm:
        raise SystemExit(f"refusing target outside archive root: {path}")


def repair_windows_file_acl(path: Path, take_ownership: bool) -> list[dict[str, Any]]:
    if os.name != "nt":
        return [{"command": "windows_acl_repair", "returncode": 1, "stderr": "not running on Windows"}]

    identity = current_windows_identity()
    actions: list[dict[str, Any]] = []
    actions.append(run_command(["attrib", "-R", str(path)]))
    if take_ownership:
        actions.append(run_command(["takeown", "/F", str(path)]))
    actions.append(run_command(["icacls", str(path), "/grant", f"{identity}:F"]))
    return actions


def current_windows_identity() -> str:
    username = os.environ.get("USERNAME") or os.environ.get("USER") or "Users"
    domain = os.environ.get("USERDOMAIN", "")
    if domain:
        return f"{domain}\\{username}"
    return username


def run_command(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return {
        "command": command,
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-2000:],
        "stderr_tail": completed.stderr[-2000:],
    }


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()

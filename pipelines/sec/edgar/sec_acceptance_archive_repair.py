from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import sys
import tarfile
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


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
from pipelines.sec.edgar.sec_filing_text_extract_parts import (  # noqa: E402
    FILING_COLUMNS,
    archive_date_from_name,
    parse_filing,
)


DEFAULT_ARCHIVE_ROOT_WIN = Path("D:/market-data/sec_core/daily_archives")
DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_acceptance_archive_repair")
DEFAULT_DATABASE = "q_live"
DEFAULT_TARGET_TABLE = "sec_filing_v2"
SEC_ET = ZoneInfo("America/New_York")
DEFAULT_REPAIR_SOURCES = (
    "archive_acceptance_datetime",
    "archive_filing_date_midnight",
    "archive_date_midnight",
    "filing_date_midnight_fallback",
)


@dataclass(frozen=True, slots=True)
class CandidateRow:
    row: dict[str, Any]

    @property
    def cik(self) -> str:
        return str(self.row.get("cik") or "")

    @property
    def accession_number(self) -> str:
        return normalize_accession(str(self.row.get("accession_number") or ""))

    @property
    def accepted_at_source(self) -> str:
        return str(self.row.get("accepted_at_source") or "")


@dataclass(frozen=True, slots=True)
class ArchiveJob:
    archive_path: str
    archive_date: str
    candidates: list[CandidateRow]


@dataclass(frozen=True, slots=True)
class ArchiveRepairResult:
    archive_path: str
    archive_date: str
    status: str
    candidates: int
    members_scanned: int
    repaired_rows: int
    unresolved_rows: int
    source_counts: dict[str, int]
    elapsed_seconds: float
    error: str = ""


@dataclass(frozen=True, slots=True)
class RunPaths:
    run_root: Path
    parts_root: Path
    repair_parts_root: Path
    archive_results_jsonl: Path
    unresolved_jsonl: Path
    manifest_json: Path
    summary_md: Path

    @classmethod
    def create(cls, output_root: Path, run_id: str) -> "RunPaths":
        run_root = output_root / run_id
        parts_root = run_root / "parts"
        repair_parts_root = parts_root / "sec_filing_v2_acceptance_repair_parts"
        repair_parts_root.mkdir(parents=True, exist_ok=True)
        return cls(
            run_root=run_root,
            parts_root=parts_root,
            repair_parts_root=repair_parts_root,
            archive_results_jsonl=run_root / "archive_results.jsonl",
            unresolved_jsonl=run_root / "unresolved_rows.jsonl",
            manifest_json=run_root / "sec_acceptance_archive_repair_manifest.json",
            summary_md=run_root / "sec_acceptance_archive_repair_summary.md",
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Repair SEC filing accepted_at_utc rows that were created from daily archive fallback "
            "dates. The script scans local .nc.tar.gz archives, extracts EDGAR "
            "ACCEPTANCE-DATETIME from the matching accession container, converts EDGAR Eastern "
            "time to UTC, writes ReplacingMergeTree replacement rows, and optionally inserts "
            "those rows into q_live.sec_filing_v2."
        )
    )
    parser.add_argument("--archive-root-win", default=os.environ.get("SEC_DAILY_ARCHIVE_ROOT_WIN", str(DEFAULT_ARCHIVE_ROOT_WIN)))
    parser.add_argument("--output-root-win", default=os.environ.get("SEC_ACCEPTANCE_ARCHIVE_REPAIR_OUTPUT_ROOT_WIN", str(DEFAULT_OUTPUT_ROOT_WIN)))
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--database", default=os.environ.get("SEC_CLICKHOUSE_DATABASE", DEFAULT_DATABASE))
    parser.add_argument("--target-table", default=os.environ.get("SEC_FILING_TABLE", DEFAULT_TARGET_TABLE))
    parser.add_argument("--start-date", default="", help="Inclusive archive date, YYYY-MM-DD. Defaults to all archives.")
    parser.add_argument("--end-date", default="", help="Exclusive archive date, YYYY-MM-DD. Defaults to all archives.")
    parser.add_argument("--repair-sources", default=",".join(DEFAULT_REPAIR_SOURCES))
    parser.add_argument("--source-run-id", default="", help="Optional source_run_id filter, e.g. sec_text_extract_20260617_141532.")
    parser.add_argument("--archive-workers", type=int, default=int(os.environ.get("SEC_ACCEPTANCE_ARCHIVE_REPAIR_WORKERS", "4")))
    parser.add_argument("--pending-multiplier", type=int, default=2)
    parser.add_argument("--rows-per-part", type=int, default=int(os.environ.get("SEC_ACCEPTANCE_ARCHIVE_REPAIR_ROWS_PER_PART", "50000")))
    parser.add_argument("--limit-archives", type=int, default=0)
    parser.add_argument("--limit-candidates-per-archive", type=int, default=0)
    parser.add_argument("--execute", action="store_true", help="Insert replacement rows into ClickHouse. Without this, only part files are written.")
    parser.add_argument("--skip-insert", action="store_true", help="Build parts and manifest only, even if --execute is set.")
    return parser.parse_args()


def main() -> None:
    loaded_env_files = load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args()
    validate_args(args)
    repair_sources = parse_repair_sources(args.repair_sources)
    run_id = f"sec_acceptance_archive_repair_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    paths = RunPaths.create(Path(args.output_root_win), run_id)
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    archives = select_archives(Path(args.archive_root_win), args)
    print_header(args, paths, loaded_env_files, repair_sources, archives, run_id)

    started = time.perf_counter()
    jobs = build_jobs(client, args, archives, repair_sources)
    results = process_jobs(args, paths, jobs, run_id)
    part_files = sorted(paths.repair_parts_root.glob("*.jsonl"))
    if args.execute and not args.skip_insert and part_files:
        insert_parts(client, args, part_files)
    summary = summarize(args, repair_sources, archives, jobs, results, part_files, time.perf_counter() - started)
    write_manifest(paths.manifest_json, args, paths, loaded_env_files, run_id, repair_sources, part_files, summary)
    write_summary(paths.summary_md, args, paths, run_id, summary)
    print("summary=" + json.dumps(summary, sort_keys=True), flush=True)
    print(f"manifest={paths.manifest_json}", flush=True)
    print(f"summary_md={paths.summary_md}", flush=True)


def validate_args(args: argparse.Namespace) -> None:
    validate_identifier(args.database, "--database")
    validate_identifier(args.target_table, "--target-table")
    if args.archive_workers < 1:
        raise SystemExit("--archive-workers must be >= 1")
    if args.pending_multiplier < 1:
        raise SystemExit("--pending-multiplier must be >= 1")
    if args.rows_per_part < 1:
        raise SystemExit("--rows-per-part must be >= 1")
    if args.limit_archives < 0 or args.limit_candidates_per_archive < 0:
        raise SystemExit("limit arguments must be >= 0")
    if args.start_date:
        parse_iso_date(args.start_date, "--start-date")
    if args.end_date:
        parse_iso_date(args.end_date, "--end-date")
    if args.start_date and args.end_date and parse_iso_date(args.end_date, "--end-date") <= parse_iso_date(args.start_date, "--start-date"):
        raise SystemExit("--end-date must be later than --start-date")


def print_header(
    args: argparse.Namespace,
    paths: RunPaths,
    loaded_env_files: list[Path],
    repair_sources: list[str],
    archives: list[Path],
    run_id: str,
) -> None:
    print("=" * 96, flush=True)
    print("SEC acceptance archive repair", flush=True)
    print(f"run_id={run_id}", flush=True)
    print(f"run_root={paths.run_root}", flush=True)
    print(f"archive_root={args.archive_root_win}", flush=True)
    print(f"archive_count={len(archives):,}", flush=True)
    print(f"target={args.database}.{args.target_table}", flush=True)
    print(f"repair_sources={','.join(repair_sources)}", flush=True)
    print(f"source_run_id={args.source_run_id or '<any>'}", flush=True)
    print(f"execute={args.execute} skip_insert={args.skip_insert}", flush=True)
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    print("secret_status=" + json.dumps(secret_status(secret_keys()), sort_keys=True), flush=True)
    print("=" * 96, flush=True)


def select_archives(root: Path, args: argparse.Namespace) -> list[Path]:
    if not root.exists():
        raise SystemExit(f"archive root does not exist: {root}")
    start = parse_iso_date(args.start_date, "--start-date") if args.start_date else None
    end = parse_iso_date(args.end_date, "--end-date") if args.end_date else None
    archives: list[Path] = []
    for path in sorted(root.rglob("*.nc.tar.gz")):
        archive_date = archive_date_from_name(path.name)
        if start and archive_date < start:
            continue
        if end and archive_date >= end:
            continue
        archives.append(path)
    if args.limit_archives:
        archives = archives[: args.limit_archives]
    if not archives:
        raise SystemExit("no SEC daily archives selected")
    return archives


def build_jobs(client: ClickHouseHttpClient, args: argparse.Namespace, archives: list[Path], repair_sources: list[str]) -> list[ArchiveJob]:
    jobs: list[ArchiveJob] = []
    started = time.perf_counter()
    for index, archive in enumerate(archives, start=1):
        archive_date = archive_date_from_name(archive.name)
        candidates = load_candidates_for_archive(client, args, archive_date, repair_sources)
        if args.limit_candidates_per_archive:
            candidates = candidates[: args.limit_candidates_per_archive]
        if candidates:
            jobs.append(ArchiveJob(str(archive), archive_date.isoformat(), candidates))
        if index == 1 or index % 50 == 0 or index == len(archives):
            print(
                f"candidate_scan={index:,}/{len(archives):,} jobs={len(jobs):,} "
                f"last_date={archive_date.isoformat()} elapsed={time.perf_counter() - started:.1f}s",
                flush=True,
            )
    print(f"candidate_scan_done archives={len(archives):,} jobs_with_candidates={len(jobs):,}", flush=True)
    return jobs


def load_candidates_for_archive(client: ClickHouseHttpClient, args: argparse.Namespace, archive_date: date, repair_sources: list[str]) -> list[CandidateRow]:
    target = f"{quote_ident(args.database)}.{quote_ident(args.target_table)}"
    sources = ", ".join(sql_string(source) for source in repair_sources)
    source_run_clause = f" AND source_run_id = {sql_string(args.source_run_id)}" if args.source_run_id else ""
    archive_date_sql = sql_string(archive_date.isoformat())
    sql = f"""
SELECT {", ".join(FILING_COLUMNS)}
FROM {target} FINAL
WHERE accepted_at_source IN ({sources})
  AND (filing_date = toDate({archive_date_sql}) OR toDate(accepted_at_utc) = toDate({archive_date_sql}))
  {source_run_clause}
FORMAT JSONEachRow
"""
    text = client.execute(sql)
    return [CandidateRow(json.loads(line)) for line in text.splitlines() if line.strip()]


def process_jobs(args: argparse.Namespace, paths: RunPaths, jobs: list[ArchiveJob], run_id: str) -> list[ArchiveRepairResult]:
    if not jobs:
        print("no candidate jobs selected; nothing to repair", flush=True)
        return []
    max_pending = max(1, args.archive_workers * args.pending_multiplier)
    results: list[ArchiveRepairResult] = []
    part_state = {"part_index": 0, "rows_in_part": 0, "current_path": None}
    started = time.perf_counter()
    submitted = completed = 0

    def submit_one(pool: concurrent.futures.ProcessPoolExecutor, futures: dict[concurrent.futures.Future[dict[str, Any]], ArchiveJob]) -> bool:
        nonlocal submitted
        if submitted >= len(jobs):
            return False
        job = jobs[submitted]
        submitted += 1
        futures[pool.submit(process_archive_worker, asdict(job), run_id)] = job
        return True

    with (
        paths.archive_results_jsonl.open("w", encoding="utf-8") as results_out,
        paths.unresolved_jsonl.open("w", encoding="utf-8") as unresolved_out,
        concurrent.futures.ProcessPoolExecutor(max_workers=args.archive_workers) as pool,
    ):
        futures: dict[concurrent.futures.Future[dict[str, Any]], ArchiveJob] = {}
        while len(futures) < max_pending and submit_one(pool, futures):
            pass
        try:
            while futures:
                done, _ = concurrent.futures.wait(futures, timeout=5, return_when=concurrent.futures.FIRST_COMPLETED)
                if not done:
                    print(
                        f"repair_active={len(futures):,} submitted={submitted:,}/{len(jobs):,} "
                        f"completed={completed:,} elapsed={time.perf_counter() - started:.1f}s",
                        flush=True,
                    )
                    continue
                for future in done:
                    job = futures.pop(future)
                    completed += 1
                    try:
                        payload = future.result()
                    except Exception as exc:  # noqa: BLE001
                        payload = {
                            "result": asdict(
                                ArchiveRepairResult(
                                    archive_path=job.archive_path,
                                    archive_date=job.archive_date,
                                    status="failed",
                                    candidates=len(job.candidates),
                                    members_scanned=0,
                                    repaired_rows=0,
                                    unresolved_rows=len(job.candidates),
                                    source_counts={},
                                    elapsed_seconds=0.0,
                                    error=repr(exc),
                                )
                            ),
                            "repaired_rows": [],
                            "unresolved_rows": [unresolved_from_candidate(candidate.row, "worker_exception", repr(exc)) for candidate in job.candidates],
                        }
                    result = ArchiveRepairResult(**payload["result"])
                    results.append(result)
                    results_out.write(json.dumps(payload["result"], ensure_ascii=False, sort_keys=True) + "\n")
                    results_out.flush()
                    write_repaired_rows(paths, part_state, payload["repaired_rows"], args.rows_per_part)
                    for row in payload["unresolved_rows"]:
                        unresolved_out.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                    unresolved_out.flush()
                    while len(futures) < max_pending and submit_one(pool, futures):
                        pass
                    if completed == 1 or completed % 10 == 0 or completed == len(jobs):
                        print(
                            f"repair_completed={completed:,}/{len(jobs):,} submitted={submitted:,} "
                            f"active={len(futures):,} repaired={sum(row.repaired_rows for row in results):,} "
                            f"unresolved={sum(row.unresolved_rows for row in results):,} "
                            f"last={Path(job.archive_path).name} status={result.status} elapsed={time.perf_counter() - started:.1f}s",
                            flush=True,
                        )
        except KeyboardInterrupt:
            print("KeyboardInterrupt received; cancelling archive repair workers.", flush=True)
            for future in futures:
                future.cancel()
            raise
    return results


def process_archive_worker(job_payload: dict[str, Any], run_id: str) -> dict[str, Any]:
    started = time.perf_counter()
    archive_path = Path(job_payload["archive_path"])
    archive_date = str(job_payload["archive_date"])
    candidates = [CandidateRow(row=item["row"]) for item in job_payload["candidates"]]
    pending = {candidate.accession_number: candidate for candidate in candidates}
    repaired_rows: list[dict[str, Any]] = []
    unresolved_rows: list[dict[str, Any]] = []
    source_counts: Counter[str] = Counter()
    members_scanned = 0
    status = "ok"
    error_text = ""
    try:
        with tarfile.open(archive_path, "r:gz") as tar:
            for member in tar:
                if not pending:
                    break
                if not member.isfile() or not member.name.lower().endswith(".nc"):
                    continue
                members_scanned += 1
                handle = tar.extractfile(member)
                if handle is None:
                    continue
                raw = handle.read()
                try:
                    filing = parse_filing(raw, member.name)
                except Exception:  # noqa: BLE001
                    continue
                accession = normalize_accession(str(filing.get("accession_number") or ""))
                candidate = pending.pop(accession, None)
                if candidate is None:
                    continue
                accepted_raw = str(filing.get("acceptance_datetime_raw") or "")
                accepted_at_utc = acceptance_datetime_to_utc(accepted_raw)
                if accepted_at_utc:
                    replacement = dict(candidate.row)
                    replacement["accepted_at_utc"] = accepted_at_utc
                    replacement["acceptance_datetime_raw"] = re.sub(r"\D+", "", accepted_raw)[:14]
                    replacement["accepted_at_source"] = repaired_source(candidate.accepted_at_source)
                    replacement["source_run_id"] = run_id
                    replacement["inserted_at"] = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")
                    repaired_rows.append(clean_filing_row(replacement))
                    source_counts[replacement["accepted_at_source"]] += 1
                else:
                    unresolved_rows.append(unresolved_from_candidate(candidate.row, "acceptance_datetime_missing_in_archive_member", ""))
        for candidate in pending.values():
            unresolved_rows.append(unresolved_from_candidate(candidate.row, "accession_not_found_in_archive", ""))
    except Exception as exc:  # noqa: BLE001
        status = "failed"
        error_text = repr(exc)
        unresolved_rows.extend(unresolved_from_candidate(candidate.row, "archive_error", error_text) for candidate in pending.values())
    result = ArchiveRepairResult(
        archive_path=str(archive_path),
        archive_date=archive_date,
        status=status,
        candidates=len(candidates),
        members_scanned=members_scanned,
        repaired_rows=len(repaired_rows),
        unresolved_rows=len(unresolved_rows),
        source_counts=dict(source_counts),
        elapsed_seconds=round(time.perf_counter() - started, 3),
        error=error_text,
    )
    return {"result": asdict(result), "repaired_rows": repaired_rows, "unresolved_rows": unresolved_rows}


def write_repaired_rows(paths: RunPaths, part_state: dict[str, Any], rows: list[dict[str, Any]], rows_per_part: int) -> None:
    for row in rows:
        if part_state["current_path"] is None or int(part_state["rows_in_part"]) >= rows_per_part:
            part_state["part_index"] = int(part_state["part_index"]) + 1
            part_state["rows_in_part"] = 0
            part_state["current_path"] = paths.repair_parts_root / f"sec_filing_v2_acceptance_repair_part_{int(part_state['part_index']):06d}.jsonl"
        path = Path(part_state["current_path"])
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n")
        part_state["rows_in_part"] = int(part_state["rows_in_part"]) + 1


def insert_parts(client: ClickHouseHttpClient, args: argparse.Namespace, part_files: list[Path]) -> None:
    target = f"{quote_ident(args.database)}.{quote_ident(args.target_table)}"
    columns = ", ".join(quote_ident(column) for column in FILING_COLUMNS)
    started = time.perf_counter()
    for index, path in enumerate(part_files, start=1):
        body = path.read_text(encoding="utf-8")
        if not body.strip():
            continue
        before = time.perf_counter()
        client.execute(f"INSERT INTO {target} ({columns}) FORMAT JSONEachRow\n{body}")
        print(f"inserted_part={index:,}/{len(part_files):,} path={path.name} elapsed={time.perf_counter() - before:.2f}s", flush=True)
    print(f"insert_done parts={len(part_files):,} elapsed={time.perf_counter() - started:.1f}s", flush=True)


def summarize(
    args: argparse.Namespace,
    repair_sources: list[str],
    archives: list[Path],
    jobs: list[ArchiveJob],
    results: list[ArchiveRepairResult],
    part_files: list[Path],
    elapsed_seconds: float,
) -> dict[str, Any]:
    source_counts: Counter[str] = Counter()
    for result in results:
        source_counts.update(result.source_counts)
    return {
        "archive_root": args.archive_root_win,
        "archive_count": len(archives),
        "job_count": len(jobs),
        "candidate_rows": sum(len(job.candidates) for job in jobs),
        "repaired_rows": sum(result.repaired_rows for result in results),
        "unresolved_rows": sum(result.unresolved_rows for result in results),
        "failed_archives": sum(1 for result in results if result.status != "ok"),
        "part_files": len(part_files),
        "part_rows": sum(count_lines(path) for path in part_files),
        "repair_sources": repair_sources,
        "new_source_counts": dict(source_counts),
        "execute": bool(args.execute),
        "skip_insert": bool(args.skip_insert),
        "elapsed_seconds": round(elapsed_seconds, 3),
    }


def write_manifest(
    path: Path,
    args: argparse.Namespace,
    paths: RunPaths,
    loaded_env_files: list[Path],
    run_id: str,
    repair_sources: list[str],
    part_files: list[Path],
    summary: dict[str, Any],
) -> None:
    payload = {
        "run_id": run_id,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "script": str(Path(__file__).resolve()),
        "repo_root": str(REPO_ROOT),
        "target": f"{args.database}.{args.target_table}",
        "parts_root": str(paths.parts_root),
        "part_files": [
            {
                "path": str(part),
                "rows": count_lines(part),
                "bytes": part.stat().st_size,
                "columns": FILING_COLUMNS,
                "format": "JSONEachRow",
            }
            for part in part_files
        ],
        "args": vars(args),
        "repair_sources": repair_sources,
        "loaded_env_files": [str(path) for path in loaded_env_files],
        "secret_status": secret_status(secret_keys()),
        "summary": summary,
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def write_summary(path: Path, args: argparse.Namespace, paths: RunPaths, run_id: str, summary: dict[str, Any]) -> None:
    lines = [
        "# SEC Acceptance Archive Repair",
        "",
        f"- run_id: `{run_id}`",
        f"- target: `{args.database}.{args.target_table}`",
        f"- run_root: `{paths.run_root}`",
        f"- execute: `{args.execute}`",
        f"- candidate_rows: `{summary['candidate_rows']:,}`",
        f"- repaired_rows: `{summary['repaired_rows']:,}`",
        f"- unresolved_rows: `{summary['unresolved_rows']:,}`",
        f"- failed_archives: `{summary['failed_archives']:,}`",
        f"- part_files: `{summary['part_files']:,}`",
        f"- elapsed_seconds: `{summary['elapsed_seconds']}`",
        "",
        "## New Source Counts",
        "",
    ]
    for source, count in sorted((summary.get("new_source_counts") or {}).items()):
        lines.append(f"- `{source}`: `{count:,}`")
    if not summary.get("new_source_counts"):
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- This script inserts replacement rows only when `--execute` is used.",
            "- Replacements rely on ClickHouse `ReplacingMergeTree`; query `sec_filing_v2 FINAL` to see repaired rows.",
            "- Unresolved rows stay in `unresolved_rows.jsonl` and should remain excluded from timestamp-sensitive training.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def clean_filing_row(row: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for column in FILING_COLUMNS:
        value = row.get(column)
        if value == "\\N":
            value = None
        output[column] = value
    return output


def unresolved_from_candidate(row: dict[str, Any], reason: str, error: str) -> dict[str, Any]:
    return {
        "cik": row.get("cik"),
        "accession_number": row.get("accession_number"),
        "form_type": row.get("form_type"),
        "filing_date": row.get("filing_date"),
        "accepted_at_utc": row.get("accepted_at_utc"),
        "accepted_at_source": row.get("accepted_at_source"),
        "source_file_name": row.get("source_file_name"),
        "source_run_id": row.get("source_run_id"),
        "reason": reason,
        "error": error,
    }


def acceptance_datetime_to_utc(raw: str) -> str:
    digits = re.sub(r"\D+", "", str(raw or ""))
    if len(digits) < 14:
        return ""
    try:
        parsed = datetime.strptime(digits[:14], "%Y%m%d%H%M%S").replace(tzinfo=SEC_ET)
    except ValueError:
        return ""
    return parsed.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")


def repaired_source(old_source: str) -> str:
    if old_source == "archive_acceptance_datetime":
        return "archive_acceptance_datetime_utc_repaired"
    return "archive_acceptance_datetime_recovered_utc"


def normalize_accession(value: str) -> str:
    text = str(value or "").strip()
    compact = re.sub(r"[^0-9]", "", text)
    if len(compact) == 18:
        return f"{compact[:10]}-{compact[10:12]}-{compact[12:]}"
    return text


def parse_repair_sources(text: str) -> list[str]:
    sources = [item.strip() for item in text.split(",") if item.strip()]
    if not sources:
        raise SystemExit("--repair-sources produced no values")
    return sources


def parse_iso_date(value: str, label: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise SystemExit(f"{label} must be YYYY-MM-DD: {value!r}") from exc


def validate_identifier(value: str, label: str) -> None:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value or ""):
        raise SystemExit(f"{label} must be a simple ClickHouse identifier: {value!r}")


def count_lines(path: Path) -> int:
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for _ in handle)


def secret_keys() -> list[str]:
    return [
        "SEC_CLICKHOUSE_URL",
        "SEC_CLICKHOUSE_USER",
        "SEC_CLICKHOUSE_PASSWORD",
        "QMD_CLICKHOUSE_URL",
        "QMD_CLICKHOUSE_USER",
        "QMD_CLICKHOUSE_PASSWORD",
        "REAL_LIVE_CLICKHOUSE_WRITE_URL",
        "REAL_LIVE_CLICKHOUSE_WRITE_USER",
        "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD",
    ]


if __name__ == "__main__":
    main()

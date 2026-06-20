from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import zipfile
from collections import Counter
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib import error, request


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
from pipelines.sec.edgar.sec_bulk_clickhouse_ingest import accepted_at_utc, clean_string, recent_value  # noqa: E402
from pipelines.sec.edgar.sec_filing_text_extract_parts import FILING_COLUMNS  # noqa: E402
from pipelines.sec.edgar.sec_initial_fill_download import sha256_file  # noqa: E402


DEFAULT_ARTIFACT_ROOT_WIN = Path("D:/market-data/sec_core")
DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_acceptance_fallback_submissions_repair")
DEFAULT_DATABASE = "q_live"
DEFAULT_TARGET_TABLE = "sec_filing_v2"
DEFAULT_FALLBACK_SOURCES = (
    "archive_filing_date_midnight",
    "archive_date_midnight",
    "filing_date_midnight_fallback",
)


@dataclass(frozen=True, slots=True)
class RunPaths:
    run_root: Path
    candidate_root: Path
    parts_root: Path
    accepted_jsonl: Path
    unresolved_jsonl: Path
    source_results_jsonl: Path
    scan_status_json: Path
    manifest_json: Path
    summary_md: Path

    @classmethod
    def create(cls, output_root: Path, run_id: str) -> "RunPaths":
        run_root = output_root / run_id
        candidate_root = run_root / "candidate_cik_buckets"
        parts_root = run_root / "parts" / "filing"
        candidate_root.mkdir(parents=True, exist_ok=True)
        parts_root.mkdir(parents=True, exist_ok=True)
        return cls(
            run_root=run_root,
            candidate_root=candidate_root,
            parts_root=parts_root,
            accepted_jsonl=run_root / "accepted_rows.jsonl",
            unresolved_jsonl=run_root / "unresolved_rows.jsonl",
            source_results_jsonl=run_root / "source_results.jsonl",
            scan_status_json=run_root / "scan_status.json",
            manifest_json=run_root / "sec_acceptance_fallback_submissions_repair_manifest.json",
            summary_md=run_root / "sec_acceptance_fallback_submissions_repair_summary.md",
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Repair SEC filing rows whose accepted_at_utc is a date-only fallback by scanning "
            "local SEC submissions.zip main and fragment JSON files for acceptanceDateTime. "
            "The script writes replacement sec_filing_v2 JSONEachRow parts and optionally "
            "inserts them into ClickHouse."
        )
    )
    parser.add_argument("--clickhouse-url", default=default_migration_clickhouse_url())
    parser.add_argument("--user", default=default_migration_clickhouse_user())
    parser.add_argument("--password", default=default_migration_clickhouse_password())
    parser.add_argument("--database", default=os.environ.get("SEC_CLICKHOUSE_DATABASE", DEFAULT_DATABASE))
    parser.add_argument("--target-table", default=os.environ.get("SEC_FILING_TABLE", DEFAULT_TARGET_TABLE))
    parser.add_argument("--artifact-root-win", default=os.environ.get("SEC_CORE_ARTIFACT_ROOT_WIN", str(DEFAULT_ARTIFACT_ROOT_WIN)))
    parser.add_argument("--submissions-zip-win", default=os.environ.get("SEC_SUBMISSIONS_ZIP_WIN", ""))
    parser.add_argument("--output-root-win", default=os.environ.get("SEC_ACCEPTANCE_FALLBACK_SUBMISSIONS_REPAIR_OUTPUT_ROOT_WIN", str(DEFAULT_OUTPUT_ROOT_WIN)))
    parser.add_argument("--fallback-sources", default=",".join(DEFAULT_FALLBACK_SOURCES))
    parser.add_argument("--rows-per-part", type=int, default=int(os.environ.get("SEC_ACCEPTANCE_FALLBACK_REPAIR_ROWS_PER_PART", "50000")))
    parser.add_argument("--insert-batch-size", type=int, default=int(os.environ.get("SEC_ACCEPTANCE_FALLBACK_REPAIR_INSERT_BATCH_SIZE", "50000")))
    parser.add_argument("--limit-rows", type=int, default=0)
    parser.add_argument("--limit-ciks", type=int, default=0)
    parser.add_argument("--limit-zip-entries", type=int, default=0)
    parser.add_argument("--progress-interval", type=int, default=10000)
    parser.add_argument("--row-progress-interval", type=int, default=10000)
    parser.add_argument("--status-interval-seconds", type=float, default=30.0)
    parser.add_argument("--execute", action="store_true", help="Insert replacement rows into ClickHouse. Without this, only part files are written.")
    parser.add_argument("--skip-insert", action="store_true", help="Build parts even when --execute is passed, but do not insert.")
    return parser.parse_args()


def main() -> None:
    loaded_env_files = load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args()
    validate_args(args)
    fallback_sources = parse_sources(args.fallback_sources)
    run_id = f"sec_acceptance_fallback_submissions_repair_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    paths = RunPaths.create(Path(args.output_root_win), run_id)
    submissions_zip = resolve_submissions_zip(args)
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    print_header(args, paths, loaded_env_files, run_id, submissions_zip, fallback_sources)

    started = time.perf_counter()
    source_sha256 = sha256_file(submissions_zip)
    candidate_stats = write_candidate_buckets(client, args, paths, fallback_sources)
    scan_stats = scan_submissions_zip(args, paths, submissions_zip, run_id)
    part_files = sorted(paths.parts_root.glob("*.jsonl"))
    inserted_rows = 0
    if args.execute and not args.skip_insert and part_files:
        inserted_rows = insert_part_files(client, args, part_files)
    summary = build_summary(args, fallback_sources, candidate_stats, scan_stats, part_files, inserted_rows, time.perf_counter() - started)
    write_manifest(paths, args, loaded_env_files, run_id, submissions_zip, source_sha256, fallback_sources, part_files, summary)
    write_summary(paths.summary_md, args, run_id, submissions_zip, summary)
    print("summary=" + json.dumps(summary, sort_keys=True, default=str), flush=True)
    print(f"manifest={paths.manifest_json}", flush=True)
    print(f"summary_md={paths.summary_md}", flush=True)


def default_migration_clickhouse_url() -> str:
    return os.environ.get("QLIVE_MIGRATION_CLICKHOUSE_URL") or os.environ.get("QMD_CLICKHOUSE_URL") or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_URL") or os.environ.get("REAL_LIVE_CLICKHOUSE_READ_URL") or default_clickhouse_url()


def default_migration_clickhouse_user() -> str:
    return os.environ.get("QLIVE_MIGRATION_CLICKHOUSE_USER") or os.environ.get("QMD_CLICKHOUSE_USER") or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_USER") or os.environ.get("CLICKHOUSE_USER") or os.environ.get("CLICKHOUSE_WORKSTATION_USER") or default_clickhouse_user()


def default_migration_clickhouse_password() -> str:
    return (
        os.environ.get("QLIVE_MIGRATION_CLICKHOUSE_PASSWORD")
        or os.environ.get("QMD_CLICKHOUSE_PASSWORD")
        or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD")
        or os.environ.get("CLICKHOUSE_PASSWORD")
        or os.environ.get("CLICKHOUSE_WORKSTATION_PASSWORD")
        or default_clickhouse_password()
    )


def validate_args(args: argparse.Namespace) -> None:
    validate_identifier(args.database, "--database")
    validate_identifier(args.target_table, "--target-table")
    if args.rows_per_part < 1:
        raise SystemExit("--rows-per-part must be >= 1")
    if args.insert_batch_size < 1:
        raise SystemExit("--insert-batch-size must be >= 1")
    for name in ("limit_rows", "limit_ciks", "limit_zip_entries"):
        if int(getattr(args, name)) < 0:
            raise SystemExit(f"--{name.replace('_', '-')} must be >= 0")
    if int(args.row_progress_interval) < 1:
        raise SystemExit("--row-progress-interval must be >= 1")
    if float(args.status_interval_seconds) < 1.0:
        raise SystemExit("--status-interval-seconds must be >= 1")


def resolve_submissions_zip(args: argparse.Namespace) -> Path:
    path = Path(args.submissions_zip_win) if args.submissions_zip_win else Path(args.artifact_root_win) / "bulk" / "submissions" / "submissions.zip"
    if not path.exists():
        raise SystemExit(f"SEC submissions zip not found: {path}")
    return path


def print_header(
    args: argparse.Namespace,
    paths: RunPaths,
    loaded_env_files: list[Path],
    run_id: str,
    submissions_zip: Path,
    fallback_sources: list[str],
) -> None:
    print("=" * 96, flush=True)
    print("SEC fallback acceptance submissions repair", flush=True)
    print(f"run_id={run_id}", flush=True)
    print(f"run_root={paths.run_root}", flush=True)
    print(f"target={args.database}.{args.target_table}", flush=True)
    print(f"submissions_zip={submissions_zip}", flush=True)
    print(f"fallback_sources={','.join(fallback_sources)}", flush=True)
    print(f"execute={args.execute} skip_insert={args.skip_insert}", flush=True)
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    print("secret_status=" + json.dumps(secret_status(secret_keys()), sort_keys=True), flush=True)
    print("=" * 96, flush=True)


def write_candidate_buckets(
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    paths: RunPaths,
    fallback_sources: list[str],
) -> dict[str, Any]:
    target = f"{quote_ident(args.database)}.{quote_ident(args.target_table)}"
    sources = ", ".join(sql_string(source) for source in fallback_sources)
    limit_clause = f"\nLIMIT {int(args.limit_rows)}" if args.limit_rows else ""
    sql = f"""
SELECT {", ".join(FILING_COLUMNS)}
FROM {target} FINAL
WHERE accepted_at_source IN ({sources})
  AND cik != ''
  AND accession_number != ''
{limit_clause}
FORMAT JSONEachRow
"""
    handles: OrderedDict[str, Any] = OrderedDict()
    ciks: set[str] = set()
    rows = 0
    source_counts: Counter[str] = Counter()
    started = time.perf_counter()
    try:
        for line in stream_clickhouse_lines(client, sql):
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            row = json.loads(text)
            cik = normalize_cik(row.get("cik"))
            if not cik:
                continue
            if args.limit_ciks and cik not in ciks and len(ciks) >= args.limit_ciks:
                continue
            ciks.add(cik)
            handle = handles.get(cik)
            if handle is None:
                path = candidate_path(paths.candidate_root, cik)
                path.parent.mkdir(parents=True, exist_ok=True)
                handle = path.open("a", encoding="utf-8")
                handles[cik] = handle
                if len(handles) > 256:
                    _, old_handle = handles.popitem(last=False)
                    old_handle.close()
            else:
                handles.move_to_end(cik)
            row["cik"] = cik
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n")
            rows += 1
            source_counts[str(row.get("accepted_at_source") or "")] += 1
            if rows % max(1, int(args.progress_interval)) == 0:
                print(f"candidate_rows={rows:,} ciks={len(ciks):,} elapsed={time.perf_counter() - started:.1f}s", flush=True)
    finally:
        for handle in handles.values():
            handle.close()
    print(f"candidate_done rows={rows:,} ciks={len(ciks):,} elapsed={time.perf_counter() - started:.1f}s", flush=True)
    return {"candidate_rows": rows, "candidate_ciks": len(ciks), "candidate_source_counts": dict(source_counts)}


def stream_clickhouse_lines(client: ClickHouseHttpClient, sql: str) -> Any:
    req = request.Request(client.base_url + "/", data=sql.encode("utf-8"), method="POST")
    if client.user:
        req.add_header("X-ClickHouse-User", client.user)
    if client.password:
        req.add_header("X-ClickHouse-Key", client.password)
    try:
        with request.urlopen(req, timeout=None) as response:
            yield from response
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"ClickHouse HTTP {exc.code} {exc.reason}: {body}") from exc


def scan_submissions_zip(
    args: argparse.Namespace,
    paths: RunPaths,
    submissions_zip: Path,
    run_id: str,
) -> dict[str, Any]:
    matched_rows = 0
    unresolved_rows = 0
    zip_entries_scanned = 0
    zip_entries_with_candidates = 0
    candidate_files_used: set[str] = set()
    new_source_counts: Counter[str] = Counter()
    part_state = {
        "part_index": 0,
        "rows_in_part": 0,
        "current_path": None,
        "handle": None,
        "total_rows": 0,
    }
    started = time.perf_counter()
    last_status_at = started
    with (
        zipfile.ZipFile(submissions_zip) as archive,
        paths.accepted_jsonl.open("w", encoding="utf-8", buffering=1) as accepted_out,
        paths.unresolved_jsonl.open("w", encoding="utf-8", buffering=1) as unresolved_out,
        paths.source_results_jsonl.open("w", encoding="utf-8", buffering=1) as source_out,
    ):
        try:
            names = sorted((name for name in archive.namelist() if name.lower().endswith(".json")), key=zip_sort_key)
            current_cik = ""
            candidates: dict[str, list[dict[str, Any]]] = {}
            for name in names:
                if args.limit_zip_entries and zip_entries_scanned >= args.limit_zip_entries:
                    break
                zip_entries_scanned += 1
                cik = cik_from_zip_name(name)
                if not cik:
                    continue
                if cik != current_cik:
                    if current_cik and candidates:
                        unresolved_rows += write_unresolved(unresolved_out, current_cik, candidates, "not_found_in_submissions_zip")
                    current_cik = cik
                    candidates = load_candidates(paths.candidate_root, cik)
                    if candidates:
                        candidate_files_used.add(cik)
                        print(
                            "submissions_cik "
                            f"cik={cik} candidate_accessions={len(candidates):,} "
                            f"entries={zip_entries_scanned:,}/{len(names):,} matched={matched_rows:,}",
                            flush=True,
                        )
                if not candidates:
                    continue
                zip_entries_with_candidates += 1
                payload = json.loads(archive.read(name).decode("utf-8", errors="replace"))
                source_kind = "fragment" if "-submissions-" in Path(name).name else "recent"
                entry_matched = 0
                for row in iter_submission_replacements(payload, cik, candidates, source_kind, run_id):
                    accepted_out.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n")
                    write_repaired_row(paths, part_state, row, args.rows_per_part)
                    matched_rows += 1
                    entry_matched += 1
                    new_source_counts[str(row.get("accepted_at_source") or "")] += 1
                    now = time.perf_counter()
                    if matched_rows % int(args.row_progress_interval) == 0 or now - last_status_at >= float(args.status_interval_seconds):
                        write_scan_status(
                            paths,
                            zip_entries_scanned=zip_entries_scanned,
                            total_entries=len(names),
                            current_cik=cik,
                            current_entry=name,
                            matched_rows=matched_rows,
                            unresolved_rows=unresolved_rows,
                            candidate_ciks_seen=len(candidate_files_used),
                            part_state=part_state,
                            started=started,
                            new_source_counts=new_source_counts,
                        )
                        print(
                            "submissions_rows "
                            f"entries={zip_entries_scanned:,}/{len(names):,} cik={cik} "
                            f"entry_matched={entry_matched:,} matched={matched_rows:,} "
                            f"part={int(part_state['part_index']):,} elapsed={now - started:.1f}s",
                            flush=True,
                        )
                        last_status_at = now
                result = {
                    "zip_entry": name,
                    "cik": cik,
                    "source_kind": source_kind,
                    "matched_rows": entry_matched,
                    "remaining_candidates": sum(len(items) for items in candidates.values()),
                }
                source_out.write(json.dumps(result, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n")
                now = time.perf_counter()
                if zip_entries_scanned % max(1, int(args.progress_interval)) == 0 or now - last_status_at >= float(args.status_interval_seconds):
                    write_scan_status(
                        paths,
                        zip_entries_scanned=zip_entries_scanned,
                        total_entries=len(names),
                        current_cik=cik,
                        current_entry=name,
                        matched_rows=matched_rows,
                        unresolved_rows=unresolved_rows,
                        candidate_ciks_seen=len(candidate_files_used),
                        part_state=part_state,
                        started=started,
                        new_source_counts=new_source_counts,
                    )
                    print(
                        "submissions_scan "
                        f"entries={zip_entries_scanned:,}/{len(names):,} matched={matched_rows:,} "
                        f"ciks_used={len(candidate_files_used):,} elapsed={now - started:.1f}s",
                        flush=True,
                    )
                    last_status_at = now
            if current_cik and candidates:
                unresolved_rows += write_unresolved(unresolved_out, current_cik, candidates, "not_found_in_submissions_zip")
            for path in sorted(paths.candidate_root.glob("*.jsonl")):
                cik = path.stem
                if cik in candidate_files_used:
                    continue
                unresolved_rows += write_unresolved(unresolved_out, cik, load_candidates(paths.candidate_root, cik), "cik_not_found_in_submissions_zip")
        finally:
            close_part_writer(part_state)
    print(
        f"submissions_done entries={zip_entries_scanned:,} matched={matched_rows:,} "
        f"unresolved={unresolved_rows:,} elapsed={time.perf_counter() - started:.1f}s",
        flush=True,
    )
    return {
        "zip_entries_scanned": zip_entries_scanned,
        "zip_entries_with_candidates": zip_entries_with_candidates,
        "candidate_ciks_seen_in_zip": len(candidate_files_used),
        "matched_rows": matched_rows,
        "unresolved_rows": unresolved_rows,
        "new_source_counts": dict(new_source_counts),
    }


def iter_submission_replacements(
    payload: dict[str, Any],
    cik: str,
    candidates: dict[str, list[dict[str, Any]]],
    source_kind: str,
    run_id: str,
) -> Any:
    recent = payload.get("filings", {}).get("recent", {}) if isinstance(payload.get("filings"), dict) else payload
    if not isinstance(recent, dict):
        return
    lengths = [len(value) for value in recent.values() if isinstance(value, list)]
    count = max(lengths) if lengths else 0
    for index in range(count):
        accession = clean_string(recent_value(recent, "accessionNumber", index))
        if not accession:
            continue
        matched_candidates = candidates.pop(accession, [])
        if not matched_candidates:
            continue
        accepted_raw = clean_string(recent_value(recent, "acceptanceDateTime", index))
        accepted = accepted_at_utc(accepted_raw)
        if not accepted:
            for candidate in matched_candidates:
                candidates.setdefault(accession, []).append(candidate)
            continue
        for candidate in matched_candidates:
            replacement = dict(candidate)
            replacement["accepted_at_utc"] = accepted
            replacement["acceptance_datetime_raw"] = accepted_raw
            replacement["accepted_at_source"] = f"submissions_bulk_{source_kind}_fallback_repair"
            replacement["source_run_id"] = run_id
            replacement["inserted_at"] = clickhouse_now64_ms()
            yield clean_filing_row(replacement)


def load_candidates(root: Path, cik: str) -> dict[str, list[dict[str, Any]]]:
    path = candidate_path(root, cik)
    if not path.exists():
        return {}
    output: dict[str, list[dict[str, Any]]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            accession = clean_string(row.get("accession_number"))
            if accession:
                output.setdefault(accession, []).append(row)
    return output


def write_repaired_row(paths: RunPaths, part_state: dict[str, Any], row: dict[str, Any], rows_per_part: int) -> None:
    if part_state["current_path"] is None or int(part_state["rows_in_part"]) >= rows_per_part:
        close_part_writer(part_state)
        part_state["part_index"] = int(part_state["part_index"]) + 1
        part_state["rows_in_part"] = 0
        part_state["current_path"] = paths.parts_root / f"part_{int(part_state['part_index']):06d}.jsonl"
    path = Path(part_state["current_path"])
    handle = part_state.get("handle")
    if handle is None:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a", encoding="utf-8", buffering=1)
        part_state["handle"] = handle
    handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n")
    part_state["rows_in_part"] = int(part_state["rows_in_part"]) + 1
    part_state["total_rows"] = int(part_state.get("total_rows") or 0) + 1


def close_part_writer(part_state: dict[str, Any]) -> None:
    handle = part_state.get("handle")
    if handle is not None:
        handle.flush()
        handle.close()
        part_state["handle"] = None


def write_scan_status(
    paths: RunPaths,
    *,
    zip_entries_scanned: int,
    total_entries: int,
    current_cik: str,
    current_entry: str,
    matched_rows: int,
    unresolved_rows: int,
    candidate_ciks_seen: int,
    part_state: dict[str, Any],
    started: float,
    new_source_counts: Counter[str],
) -> None:
    current_path = Path(part_state["current_path"]) if part_state.get("current_path") else None
    status = {
        "updated_at_utc": datetime.now(UTC).isoformat(),
        "zip_entries_scanned": zip_entries_scanned,
        "total_entries": total_entries,
        "progress_pct": 100.0 * zip_entries_scanned / max(1, total_entries),
        "current_cik": current_cik,
        "current_entry": current_entry,
        "matched_rows": matched_rows,
        "unresolved_rows": unresolved_rows,
        "candidate_ciks_seen": candidate_ciks_seen,
        "part_index": int(part_state.get("part_index") or 0),
        "part_rows": int(part_state.get("rows_in_part") or 0),
        "part_total_rows": int(part_state.get("total_rows") or 0),
        "part_path": "" if current_path is None else str(current_path),
        "part_bytes": 0 if current_path is None or not current_path.exists() else current_path.stat().st_size,
        "new_source_counts": dict(new_source_counts),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
    }
    tmp_path = paths.scan_status_json.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(status, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(paths.scan_status_json)


def write_unresolved(handle: Any, cik: str, candidates: dict[str, list[dict[str, Any]]], reason: str) -> int:
    written = 0
    for accession, rows in sorted(candidates.items()):
        for row in rows:
            handle.write(
                json.dumps(
                    {
                        "cik": cik,
                        "accession_number": accession,
                        "form_type": row.get("form_type"),
                        "filing_date": row.get("filing_date"),
                        "accepted_at_source": row.get("accepted_at_source"),
                        "accepted_at_utc": row.get("accepted_at_utc"),
                        "reason": reason,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                + "\n"
            )
            written += 1
    return written


def insert_part_files(client: ClickHouseHttpClient, args: argparse.Namespace, part_files: list[Path]) -> int:
    target = f"{quote_ident(args.database)}.{quote_ident(args.target_table)}"
    columns = ", ".join(quote_ident(column) for column in FILING_COLUMNS)
    inserted = 0
    batch: list[str] = []
    started = time.perf_counter()
    for path in part_files:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                batch.append(line.rstrip("\n"))
                if len(batch) >= args.insert_batch_size:
                    inserted += insert_batch(client, target, columns, batch)
                    batch.clear()
                    print(f"inserted_rows={inserted:,} elapsed={time.perf_counter() - started:.1f}s", flush=True)
    if batch:
        inserted += insert_batch(client, target, columns, batch)
    print(f"insert_done rows={inserted:,} elapsed={time.perf_counter() - started:.1f}s", flush=True)
    return inserted


def insert_batch(client: ClickHouseHttpClient, target: str, columns: str, rows: list[str]) -> int:
    if not rows:
        return 0
    body = "\n".join(rows)
    client.execute(f"INSERT INTO {target} ({columns}) FORMAT JSONEachRow\n{body}")
    return len(rows)


def build_summary(
    args: argparse.Namespace,
    fallback_sources: list[str],
    candidate_stats: dict[str, Any],
    scan_stats: dict[str, Any],
    part_files: list[Path],
    inserted_rows: int,
    elapsed_seconds: float,
) -> dict[str, Any]:
    return {
        "fallback_sources": fallback_sources,
        "candidate_rows": int(candidate_stats.get("candidate_rows") or 0),
        "candidate_ciks": int(candidate_stats.get("candidate_ciks") or 0),
        "candidate_source_counts": candidate_stats.get("candidate_source_counts") or {},
        "zip_entries_scanned": int(scan_stats.get("zip_entries_scanned") or 0),
        "zip_entries_with_candidates": int(scan_stats.get("zip_entries_with_candidates") or 0),
        "candidate_ciks_seen_in_zip": int(scan_stats.get("candidate_ciks_seen_in_zip") or 0),
        "matched_rows": int(scan_stats.get("matched_rows") or 0),
        "unresolved_rows": int(scan_stats.get("unresolved_rows") or 0),
        "new_source_counts": scan_stats.get("new_source_counts") or {},
        "part_files": len(part_files),
        "part_rows": sum(count_lines(path) for path in part_files),
        "inserted_rows": inserted_rows,
        "execute": bool(args.execute),
        "skip_insert": bool(args.skip_insert),
        "elapsed_seconds": round(elapsed_seconds, 3),
    }


def write_manifest(
    paths: RunPaths,
    args: argparse.Namespace,
    loaded_env_files: list[Path],
    run_id: str,
    submissions_zip: Path,
    source_sha256: str,
    fallback_sources: list[str],
    part_files: list[Path],
    summary: dict[str, Any],
) -> None:
    payload = {
        "run_id": run_id,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "script": str(Path(__file__).resolve()),
        "repo_root": str(REPO_ROOT),
        "target": f"{args.database}.{args.target_table}",
        "submissions_zip": str(submissions_zip),
        "submissions_zip_sha256": source_sha256,
        "fallback_sources": fallback_sources,
        "loaded_env_files": [str(path) for path in loaded_env_files],
        "secret_status": secret_status(secret_keys()),
        "args": vars(args),
        "part_files": [{"path": str(path), "rows": count_lines(path), "bytes": path.stat().st_size, "columns": FILING_COLUMNS, "format": "JSONEachRow"} for path in part_files],
        "summary": summary,
    }
    paths.manifest_json.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def write_summary(path: Path, args: argparse.Namespace, run_id: str, submissions_zip: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# SEC Fallback Acceptance Submissions Repair",
        "",
        f"- run_id: `{run_id}`",
        f"- target: `{args.database}.{args.target_table}`",
        f"- submissions_zip: `{submissions_zip}`",
        f"- execute: `{args.execute}`",
        f"- candidate_rows: `{summary['candidate_rows']:,}`",
        f"- matched_rows: `{summary['matched_rows']:,}`",
        f"- unresolved_rows: `{summary['unresolved_rows']:,}`",
        f"- part_rows: `{summary['part_rows']:,}`",
        f"- inserted_rows: `{summary['inserted_rows']:,}`",
        f"- elapsed_seconds: `{summary['elapsed_seconds']}`",
        "",
        "## New Source Counts",
        "",
    ]
    for source, count in sorted((summary.get("new_source_counts") or {}).items()):
        lines.append(f"- `{source}`: `{count:,}`")
    if not summary.get("new_source_counts"):
        lines.append("- none")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def clean_filing_row(row: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for column in FILING_COLUMNS:
        value = row.get(column)
        if value == "\\N":
            value = None
        output[column] = value
    return output


def candidate_path(root: Path, cik: str) -> Path:
    return root / f"{cik}.jsonl"


def cik_from_zip_name(name: str) -> str:
    match = re.search(r"CIK(\d{10})", Path(name).name)
    return match.group(1) if match else ""


def zip_sort_key(name: str) -> tuple[str, str]:
    return cik_from_zip_name(name), Path(name).name


def normalize_cik(value: Any) -> str:
    digits = re.sub(r"\D+", "", str(value or ""))
    return digits.zfill(10) if digits else ""


def parse_sources(text: str) -> list[str]:
    values = [item.strip() for item in text.split(",") if item.strip()]
    if not values:
        raise SystemExit("--fallback-sources produced no values")
    return values


def clickhouse_now64_ms() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def count_lines(path: Path) -> int:
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for _ in handle)


def validate_identifier(value: str, label: str) -> None:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value or ""):
        raise SystemExit(f"{label} must be a simple ClickHouse identifier: {value!r}")


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
        "CLICKHOUSE_USER",
        "CLICKHOUSE_PASSWORD",
        "CLICKHOUSE_WORKSTATION_USER",
        "CLICKHOUSE_WORKSTATION_PASSWORD",
    ]


if __name__ == "__main__":
    main()

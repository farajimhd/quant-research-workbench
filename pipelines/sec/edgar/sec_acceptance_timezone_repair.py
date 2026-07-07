from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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
from pipelines.sec.edgar.sec_filing_text_extract_parts import FILING_COLUMNS  # noqa: E402
from pipelines.sec.edgar.sec_pipeline.submissions import parse_acceptance_datetime  # noqa: E402


DEFAULT_DATABASE = "q_live"
DEFAULT_TARGET_TABLE = "sec_filing_v2"
DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_acceptance_timezone_repair")
DEFAULT_REPAIR_SOURCES = (
    "submissions_recent",
)
HISTORICAL_BULK_REPAIR_SOURCES = (
    "submissions_bulk",
    "submissions_bulk_recent",
    "submissions_bulk_fragment",
    "submissions_bulk_recent_fallback_repair",
    "submissions_bulk_fragment_fallback_repair",
)


@dataclass(frozen=True, slots=True)
class RepairCandidate:
    row: dict[str, Any]
    current_accepted_at_utc: datetime
    corrected_accepted_at_utc: datetime
    shift_seconds: float


@dataclass(frozen=True, slots=True)
class RunPaths:
    run_root: Path
    candidates_jsonl: Path
    repaired_rows_jsonl: Path
    skipped_jsonl: Path
    manifest_json: Path
    summary_md: Path

    @classmethod
    def create(cls, output_root: Path, run_id: str) -> "RunPaths":
        run_root = output_root / run_id
        run_root.mkdir(parents=True, exist_ok=True)
        return cls(
            run_root=run_root,
            candidates_jsonl=run_root / "timezone_repair_candidates.jsonl",
            repaired_rows_jsonl=run_root / "sec_filing_v2_timezone_repair_rows.jsonl",
            skipped_jsonl=run_root / "timezone_repair_skipped.jsonl",
            manifest_json=run_root / "sec_acceptance_timezone_repair_manifest.json",
            summary_md=run_root / "sec_acceptance_timezone_repair_summary.md",
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Repair SEC filing parent rows whose accepted_at_utc disagrees with the explicit "
            "timezone in acceptance_datetime_raw by a timezone-sized offset. The script "
            "recomputes raw SEC timestamps with normal ISO/RFC3339 semantics and inserts "
            "replacement rows into sec_filing_v2. Dry-run is the default."
        )
    )
    parser.add_argument("--clickhouse-url", default=default_migration_clickhouse_url())
    parser.add_argument("--user", default=default_migration_clickhouse_user())
    parser.add_argument("--password", default=default_migration_clickhouse_password())
    parser.add_argument("--database", default=os.environ.get("SEC_CLICKHOUSE_DATABASE", DEFAULT_DATABASE))
    parser.add_argument("--target-table", default=os.environ.get("SEC_FILING_TABLE", DEFAULT_TARGET_TABLE))
    parser.add_argument("--output-root-win", default=os.environ.get("SEC_ACCEPTANCE_TIMEZONE_REPAIR_OUTPUT_ROOT_WIN", str(DEFAULT_OUTPUT_ROOT_WIN)))
    parser.add_argument("--start-inserted-at", default="", help="Inclusive inserted_at lower bound, UTC. Defaults to now minus --lookback-hours.")
    parser.add_argument("--end-inserted-at", default="", help="Exclusive inserted_at upper bound, UTC. Defaults to now plus 5 minutes.")
    parser.add_argument("--lookback-hours", type=float, default=float(os.environ.get("SEC_ACCEPTANCE_TIMEZONE_REPAIR_LOOKBACK_HOURS", "72")))
    parser.add_argument("--repair-sources", default=",".join(DEFAULT_REPAIR_SOURCES))
    parser.add_argument("--min-abs-shift-hours", type=float, default=3.0)
    parser.add_argument("--max-abs-shift-hours", type=float, default=6.0)
    parser.add_argument("--limit-rows", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=int(os.environ.get("SEC_ACCEPTANCE_TIMEZONE_REPAIR_BATCH_SIZE", "5000")))
    parser.add_argument("--allow-month-partition-move", action="store_true", help="Allow replacements whose corrected accepted_at_utc lands in another month.")
    parser.add_argument("--optimize-final", action="store_true", help="Run OPTIMIZE TABLE ... FINAL after insert. Usually not required.")
    parser.add_argument("--execute", action="store_true", help="Insert replacement rows. Without this, only reports and part files are written.")
    return parser.parse_args()


def main() -> None:
    loaded_env_files = load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args()
    validate_args(args)
    run_id = f"sec_acceptance_timezone_repair_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    paths = RunPaths.create(Path(args.output_root_win), run_id)
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    start_inserted, end_inserted = inserted_bounds(args)
    repair_sources = parse_sources(args.repair_sources)
    print_header(args, paths, loaded_env_files, run_id, start_inserted, end_inserted, repair_sources)

    started = time.perf_counter()
    rows = load_candidate_rows(client, args, start_inserted, end_inserted, repair_sources)
    candidates, skipped = classify_rows(args, rows)
    replacements = build_replacement_rows(candidates, run_id)
    write_jsonl(paths.candidates_jsonl, candidate_records(candidates))
    write_jsonl(paths.skipped_jsonl, skipped)
    write_jsonl(paths.repaired_rows_jsonl, replacements)

    inserted_rows = 0
    if args.execute and replacements:
        inserted_rows = insert_replacements(client, args, replacements)
        if args.optimize_final:
            target = f"{quote_ident(args.database)}.{quote_ident(args.target_table)}"
            client.execute(f"OPTIMIZE TABLE {target} FINAL")

    summary = {
        "run_id": run_id,
        "execute": bool(args.execute),
        "database": args.database,
        "target_table": args.target_table,
        "start_inserted_at": format_dt(start_inserted),
        "end_inserted_at": format_dt(end_inserted),
        "repair_sources": repair_sources,
        "rows_loaded": len(rows),
        "repair_candidates": len(candidates),
        "replacement_rows_written": len(replacements),
        "inserted_rows": inserted_rows,
        "skipped_rows": len(skipped),
        "shift_seconds_min": min((item.shift_seconds for item in candidates), default=0.0),
        "shift_seconds_max": max((item.shift_seconds for item in candidates), default=0.0),
        "wall_seconds": round(time.perf_counter() - started, 3),
        "paths": {
            "run_root": str(paths.run_root),
            "candidates_jsonl": str(paths.candidates_jsonl),
            "repaired_rows_jsonl": str(paths.repaired_rows_jsonl),
            "skipped_jsonl": str(paths.skipped_jsonl),
            "manifest_json": str(paths.manifest_json),
            "summary_md": str(paths.summary_md),
        },
    }
    write_manifest(paths.manifest_json, args, loaded_env_files, summary)
    write_summary(paths.summary_md, summary)
    print("summary=" + json.dumps(summary, sort_keys=True), flush=True)
    print(f"manifest={paths.manifest_json}", flush=True)
    print(f"summary_md={paths.summary_md}", flush=True)


def default_migration_clickhouse_url() -> str:
    return (
        os.environ.get("QLIVE_MIGRATION_CLICKHOUSE_URL")
        or os.environ.get("QMD_CLICKHOUSE_URL")
        or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_URL")
        or os.environ.get("REAL_LIVE_CLICKHOUSE_READ_URL")
        or default_clickhouse_url()
    )


def default_migration_clickhouse_user() -> str:
    return (
        os.environ.get("QLIVE_MIGRATION_CLICKHOUSE_USER")
        or os.environ.get("QMD_CLICKHOUSE_USER")
        or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_USER")
        or os.environ.get("CLICKHOUSE_USER")
        or os.environ.get("CLICKHOUSE_WORKSTATION_USER")
        or default_clickhouse_user()
    )


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
    if args.lookback_hours <= 0:
        raise SystemExit("--lookback-hours must be positive")
    if args.min_abs_shift_hours < 0 or args.max_abs_shift_hours <= args.min_abs_shift_hours:
        raise SystemExit("--max-abs-shift-hours must be greater than --min-abs-shift-hours")
    if args.batch_size <= 0:
        raise SystemExit("--batch-size must be positive")


def validate_identifier(value: str, label: str) -> None:
    if not value or not all(part.replace("_", "").isalnum() for part in value.split(".")):
        raise SystemExit(f"{label} must be a simple ClickHouse identifier, got {value!r}")


def inserted_bounds(args: argparse.Namespace) -> tuple[datetime, datetime]:
    now = datetime.now(UTC)
    start = parse_cli_datetime(args.start_inserted_at) if args.start_inserted_at else now - timedelta(hours=args.lookback_hours)
    end = parse_cli_datetime(args.end_inserted_at) if args.end_inserted_at else now + timedelta(minutes=5)
    if start >= end:
        raise SystemExit("--start-inserted-at must be earlier than --end-inserted-at")
    return start, end


def parse_cli_datetime(value: str) -> datetime:
    text = value.strip()
    if not text:
        raise ValueError("empty datetime")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_sources(value: str) -> list[str]:
    sources = [item.strip() for item in value.split(",") if item.strip()]
    if not sources:
        raise SystemExit("--repair-sources must include at least one source")
    return sources


def load_candidate_rows(
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    start_inserted: datetime,
    end_inserted: datetime,
    repair_sources: list[str],
) -> list[dict[str, Any]]:
    target = f"{quote_ident(args.database)}.{quote_ident(args.target_table)}"
    sources = ", ".join(sql_string(source) for source in repair_sources)
    limit_clause = f"\nLIMIT {int(args.limit_rows)}" if args.limit_rows > 0 else ""
    sql = f"""
SELECT {", ".join(FILING_COLUMNS)}
FROM {target} FINAL
WHERE accepted_at_utc IS NOT NULL
  AND acceptance_datetime_raw IS NOT NULL
  AND acceptance_datetime_raw != ''
  AND accepted_at_source IN ({sources})
  AND inserted_at >= toDateTime64({sql_string(format_dt(start_inserted))}, 3, 'UTC')
  AND inserted_at < toDateTime64({sql_string(format_dt(end_inserted))}, 3, 'UTC')
ORDER BY inserted_at, cik, accession_number
{limit_clause}
FORMAT JSONEachRow
"""
    text = client.execute(sql)
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def classify_rows(args: argparse.Namespace, rows: list[dict[str, Any]]) -> tuple[list[RepairCandidate], list[dict[str, Any]]]:
    candidates: list[RepairCandidate] = []
    skipped: list[dict[str, Any]] = []
    min_shift = args.min_abs_shift_hours * 3600.0
    max_shift = args.max_abs_shift_hours * 3600.0
    for row in rows:
        corrected_text = parse_acceptance_datetime(row.get("acceptance_datetime_raw"))
        current_dt = parse_db_datetime(row.get("accepted_at_utc"))
        corrected_dt = parse_db_datetime(corrected_text)
        if current_dt is None or corrected_dt is None:
            skipped.append(skip_record(row, "unparseable_timestamp", corrected_text))
            continue
        shift_seconds = (corrected_dt - current_dt).total_seconds()
        if abs(shift_seconds) < min_shift or abs(shift_seconds) > max_shift:
            skipped.append(skip_record(row, "shift_outside_repair_window", corrected_text, shift_seconds))
            continue
        if month_key(current_dt) != month_key(corrected_dt) and not args.allow_month_partition_move:
            skipped.append(skip_record(row, "corrected_timestamp_moves_month_partition", corrected_text, shift_seconds))
            continue
        candidates.append(
            RepairCandidate(
                row=row,
                current_accepted_at_utc=current_dt,
                corrected_accepted_at_utc=corrected_dt,
                shift_seconds=shift_seconds,
            )
        )
    return candidates, skipped


def parse_db_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("T", " ").replace("Z", "")
    if "." in text:
        head, tail = text.split(".", 1)
        text = f"{head}.{tail[:6].ljust(6, '0')}"
        fmt = "%Y-%m-%d %H:%M:%S.%f"
    else:
        fmt = "%Y-%m-%d %H:%M:%S"
    try:
        return datetime.strptime(text, fmt).replace(tzinfo=UTC)
    except ValueError:
        return None


def build_replacement_rows(candidates: list[RepairCandidate], run_id: str) -> list[dict[str, Any]]:
    inserted_at = format_dt3_json(datetime.now(UTC))
    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        row = dict(candidate.row)
        old_source = str(row.get("accepted_at_source") or "unknown")
        row["accepted_at_utc"] = format_dt9(candidate.corrected_accepted_at_utc)
        if not old_source.endswith("_timezone_repair"):
            row["accepted_at_source"] = f"{old_source}_timezone_repair"
        row["source_run_id"] = run_id
        row["inserted_at"] = inserted_at
        rows.append({column: row.get(column) for column in FILING_COLUMNS})
    return rows


def insert_replacements(client: ClickHouseHttpClient, args: argparse.Namespace, rows: list[dict[str, Any]]) -> int:
    target = f"{quote_ident(args.database)}.{quote_ident(args.target_table)}"
    columns = ", ".join(quote_ident(column) for column in FILING_COLUMNS)
    inserted = 0
    for start in range(0, len(rows), args.batch_size):
        batch = rows[start : start + args.batch_size]
        body = "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in batch)
        client.execute(f"INSERT INTO {target} ({columns}) SETTINGS date_time_input_format = 'best_effort' FORMAT JSONEachRow\n{body}")
        inserted += len(batch)
        print(f"inserted_rows={inserted:,}/{len(rows):,}", flush=True)
    return inserted


def candidate_records(candidates: list[RepairCandidate]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for candidate in candidates:
        row = candidate.row
        records.append(
            {
                "cik": row.get("cik"),
                "accession_number": row.get("accession_number"),
                "accepted_at_source": row.get("accepted_at_source"),
                "acceptance_datetime_raw": row.get("acceptance_datetime_raw"),
                "current_accepted_at_utc": format_dt9(candidate.current_accepted_at_utc),
                "corrected_accepted_at_utc": format_dt9(candidate.corrected_accepted_at_utc),
                "shift_seconds": candidate.shift_seconds,
                "inserted_at": row.get("inserted_at"),
            }
        )
    return records


def skip_record(row: dict[str, Any], reason: str, corrected_text: str | None = None, shift_seconds: float | None = None) -> dict[str, Any]:
    return {
        "reason": reason,
        "cik": row.get("cik"),
        "accession_number": row.get("accession_number"),
        "accepted_at_source": row.get("accepted_at_source"),
        "acceptance_datetime_raw": row.get("acceptance_datetime_raw"),
        "accepted_at_utc": row.get("accepted_at_utc"),
        "corrected_accepted_at_utc": corrected_text,
        "shift_seconds": shift_seconds,
        "inserted_at": row.get("inserted_at"),
    }


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def write_manifest(path: Path, args: argparse.Namespace, loaded_env_files: list[Path], summary: dict[str, Any]) -> None:
    payload = {
        "script": Path(__file__).name,
        "args": sanitize_args(vars(args)),
        "loaded_env_files": [str(path) for path in loaded_env_files],
        "secret_status": secret_status(
            [
                "REAL_LIVE_CLICKHOUSE_WRITE_URL",
                "REAL_LIVE_CLICKHOUSE_WRITE_USER",
                "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD",
                "CLICKHOUSE_USER",
                "CLICKHOUSE_PASSWORD",
                "CLICKHOUSE_WORKSTATION_USER",
                "CLICKHOUSE_WORKSTATION_PASSWORD",
            ]
        ),
        "summary": summary,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def write_summary(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# SEC Acceptance Timezone Repair",
        "",
        f"- Run id: `{summary['run_id']}`",
        f"- Execute: `{summary['execute']}`",
        f"- Target: `{summary['database']}.{summary['target_table']}`",
        f"- Inserted-at window: `{summary['start_inserted_at']}` to `{summary['end_inserted_at']}`",
        f"- Rows loaded: `{summary['rows_loaded']:,}`",
        f"- Repair candidates: `{summary['repair_candidates']:,}`",
        f"- Replacement rows written: `{summary['replacement_rows_written']:,}`",
        f"- Inserted rows: `{summary['inserted_rows']:,}`",
        f"- Skipped rows: `{summary['skipped_rows']:,}`",
        f"- Shift seconds: `{summary['shift_seconds_min']}` to `{summary['shift_seconds_max']}`",
        f"- Wall seconds: `{summary['wall_seconds']}`",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def sanitize_args(args: dict[str, Any]) -> dict[str, Any]:
    redacted = dict(args)
    for key in list(redacted):
        upper = key.upper()
        if "PASSWORD" in upper or "TOKEN" in upper or "SECRET" in upper or upper.endswith("KEY"):
            redacted[key] = "<redacted>" if redacted[key] else ""
    return redacted


def print_header(
    args: argparse.Namespace,
    paths: RunPaths,
    loaded_env_files: list[Path],
    run_id: str,
    start_inserted: datetime,
    end_inserted: datetime,
    repair_sources: list[str],
) -> None:
    print("=" * 96, flush=True)
    print("SEC acceptance timezone repair", flush=True)
    print(f"run_id={run_id}", flush=True)
    print(f"execute={args.execute}", flush=True)
    print(f"target={args.database}.{args.target_table}", flush=True)
    print(f"inserted_at_window={format_dt(start_inserted)} -> {format_dt(end_inserted)}", flush=True)
    print(f"repair_sources={','.join(repair_sources)}", flush=True)
    print(f"abs_shift_window_hours={args.min_abs_shift_hours:g}..{args.max_abs_shift_hours:g}", flush=True)
    print(f"allow_month_partition_move={args.allow_month_partition_move}", flush=True)
    print(f"run_root={paths.run_root}", flush=True)
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    print(
        "secret_status="
        + json.dumps(
            secret_status(
                [
                    "REAL_LIVE_CLICKHOUSE_WRITE_URL",
                    "REAL_LIVE_CLICKHOUSE_WRITE_USER",
                    "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD",
                    "CLICKHOUSE_USER",
                    "CLICKHOUSE_PASSWORD",
                    "CLICKHOUSE_WORKSTATION_USER",
                    "CLICKHOUSE_WORKSTATION_PASSWORD",
                ]
            ),
            sort_keys=True,
        ),
        flush=True,
    )
    print("=" * 96, flush=True)


def month_key(value: datetime) -> str:
    return f"{value.year:04d}{value.month:02d}"


def format_dt(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def format_dt9(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f000Z")


def format_dt3_json(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


if __name__ == "__main__":
    main()

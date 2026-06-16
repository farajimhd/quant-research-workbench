from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
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
DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_integrity_audit")
DEFAULT_ARCHIVE_ROOT_WIN = Path("D:/market-data/sec_core/daily_archives")
SEC_TABLES = (
    "sec_filing_v2",
    "sec_filing_document_v1",
    "sec_filing_text_v1",
    "sec_filing_document_v2",
    "sec_filing_text_v2",
    "sec_filing_document_skip_v1",
    "sec_xbrl_company_fact_v1",
    "sec_xbrl_frame_observation_v1",
    "sec_xbrl_frame_v1",
    "sec_xbrl_concept_v1",
)


@dataclass(frozen=True, slots=True)
class AuditPaths:
    run_root: Path
    manifest_json: Path
    checks_jsonl: Path
    summary_md: Path

    @classmethod
    def create(cls, output_root: Path, run_id: str) -> "AuditPaths":
        run_root = output_root / run_id
        run_root.mkdir(parents=True, exist_ok=True)
        return cls(
            run_root=run_root,
            manifest_json=run_root / "sec_integrity_audit_manifest.json",
            checks_jsonl=run_root / "sec_integrity_audit_checks.jsonl",
            summary_md=run_root / "sec_integrity_audit_summary.md",
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only SEC q_live integrity audit before archive-derived filing text extraction."
    )
    parser.add_argument("--clickhouse-url", default=default_sec_clickhouse_url())
    parser.add_argument("--user", default=default_sec_clickhouse_user())
    parser.add_argument("--password", default=default_sec_clickhouse_password())
    parser.add_argument("--database", default=os.environ.get("SEC_INTEGRITY_DATABASE", DEFAULT_DATABASE))
    parser.add_argument("--output-root-win", default=os.environ.get("SEC_INTEGRITY_AUDIT_OUTPUT_ROOT_WIN", str(DEFAULT_OUTPUT_ROOT_WIN)))
    parser.add_argument("--archive-root-win", default=os.environ.get("SEC_DAILY_ARCHIVE_ROOT_WIN", str(DEFAULT_ARCHIVE_ROOT_WIN)))
    parser.add_argument("--archive-start-date", default=os.environ.get("SEC_ARCHIVE_START_DATE", "2019-01-01"))
    parser.add_argument("--archive-end-date", default=os.environ.get("SEC_ARCHIVE_END_DATE", ""))
    parser.add_argument("--xbrl-sample-limit", type=int, default=int(os.environ.get("SEC_INTEGRITY_XBRL_SAMPLE_LIMIT", "200000")))
    parser.add_argument("--skip-xbrl-sample", action="store_true", help="Skip the sampled XBRL-to-filing orphan check.")
    parser.add_argument("--require-v2-tables", action="store_true", help="Fail if SEC document/text v2 target tables are absent.")
    parser.add_argument("--fail-on-warn", action="store_true", help="Exit non-zero when any warning is present.")
    return parser.parse_args()


def main() -> None:
    loaded_env = load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args()
    validate_identifier(args.database, "--database")
    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    paths = AuditPaths.create(Path(args.output_root_win), run_id)
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)

    print_header(args, paths, loaded_env)
    started = time.perf_counter()
    checks: list[dict[str, Any]] = []
    table_meta = query_table_metadata(client, args.database)
    column_map = query_column_map(client, args.database)

    checks.extend(check_required_tables(table_meta, args.require_v2_tables))
    if "sec_filing_v2" in table_meta:
        checks.extend(check_filing_parent(client, args.database))
    if "sec_filing_document_v1" in table_meta and "sec_filing_v2" in table_meta:
        checks.extend(check_document_v1(client, args.database))
    if "sec_filing_text_v1" in table_meta:
        checks.extend(check_text_table(client, args.database, text_table="sec_filing_text_v1", document_table="sec_filing_document_v1"))
    if "sec_filing_document_v2" in table_meta:
        checks.extend(check_document_v2_shape(column_map))
    if "sec_filing_text_v2" in table_meta:
        checks.extend(check_text_v2_shape(column_map))
        if "sec_filing_document_v2" in table_meta:
            checks.extend(check_text_table(client, args.database, text_table="sec_filing_text_v2", document_table="sec_filing_document_v2"))
    checks.extend(check_xbrl_presence(table_meta))
    if not args.skip_xbrl_sample and args.xbrl_sample_limit > 0 and {"sec_xbrl_company_fact_v1", "sec_filing_v2"}.issubset(table_meta):
        checks.extend(check_xbrl_sample(client, args.database, args.xbrl_sample_limit))
    checks.extend(check_archive_inventory(args))

    wall_seconds = round(time.perf_counter() - started, 3)
    write_jsonl(paths.checks_jsonl, checks)
    write_manifest(paths.manifest_json, args, paths, loaded_env, checks, wall_seconds)
    write_summary(paths.summary_md, args, checks, wall_seconds)

    counts = status_counts(checks)
    print(f"checks={len(checks)} pass={counts['pass']} warn={counts['warn']} fail={counts['fail']} elapsed_seconds={wall_seconds}", flush=True)
    print(f"summary_md={paths.summary_md}", flush=True)
    if counts["fail"] or (args.fail_on_warn and counts["warn"]):
        raise SystemExit(1)


def default_sec_clickhouse_url() -> str:
    return os.environ.get("SEC_CLICKHOUSE_URL") or os.environ.get("QMD_CLICKHOUSE_URL") or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_URL") or default_clickhouse_url()


def default_sec_clickhouse_user() -> str:
    return os.environ.get("SEC_CLICKHOUSE_USER") or os.environ.get("QMD_CLICKHOUSE_USER") or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_USER") or default_clickhouse_user()


def default_sec_clickhouse_password() -> str:
    return (
        os.environ.get("SEC_CLICKHOUSE_PASSWORD")
        or os.environ.get("QMD_CLICKHOUSE_PASSWORD")
        or os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD")
        or default_clickhouse_password()
    )


def check_required_tables(table_meta: dict[str, dict[str, Any]], require_v2_tables: bool) -> list[dict[str, Any]]:
    rows = []
    required = {"sec_filing_v2", "sec_filing_document_v1", "sec_filing_text_v1"}
    if require_v2_tables:
        required |= {"sec_filing_document_v2", "sec_filing_text_v2", "sec_filing_document_skip_v1"}
    for table in SEC_TABLES:
        exists = table in table_meta
        status = "pass" if exists or table not in required else "fail"
        if table in {"sec_filing_document_v2", "sec_filing_text_v2", "sec_filing_document_skip_v1"} and not exists and not require_v2_tables:
            status = "warn"
        rows.append(
            check(
                name=f"table_exists_{table}",
                status=status,
                message="table exists" if exists else "table missing",
                table=table,
                details=table_meta.get(table, {}),
            )
        )
    return rows


def check_filing_parent(client: ClickHouseHttpClient, db: str) -> list[dict[str, Any]]:
    rows = []
    summary = query_one(
        client,
        f"""
        SELECT
            count() AS rows,
            countIf(accepted_at_utc IS NULL) AS missing_accepted_at_utc,
            toString(min(accepted_at_utc)) AS min_accepted_at_utc,
            toString(max(accepted_at_utc)) AS max_accepted_at_utc,
            countIf(primary_document IS NOT NULL AND primary_document != '') AS rows_with_primary_document,
            countIf(primary_document IS NULL OR primary_document = '') AS rows_missing_primary_document,
            countIf(accepted_at_utc >= toDateTime64('2019-01-01 00:00:00', 9, 'UTC')) AS rows_2019_plus,
            countIf(accepted_at_utc >= toDateTime64('2019-01-01 00:00:00', 9, 'UTC') AND primary_document IS NOT NULL AND primary_document != '') AS rows_2019_plus_with_primary_document
        FROM {qi(db)}.sec_filing_v2 FINAL
        FORMAT TSVWithNames
        """,
    )
    rows.append(
        check(
            "sec_filing_v2_parent_summary",
            "pass" if int(summary["rows"]) > 0 and int(summary["missing_accepted_at_utc"]) == 0 else "fail",
            "filing parent has rows and accepted timestamps are populated",
            table="sec_filing_v2",
            details=summary,
        )
    )
    duplicate_count = scalar_int(
        client,
        f"""
        SELECT count()
        FROM (
            SELECT cik, accession_number, count() AS c
            FROM {qi(db)}.sec_filing_v2 FINAL
            GROUP BY cik, accession_number
            HAVING c > 1
        )
        """,
    )
    rows.append(check("sec_filing_v2_duplicate_keys", "pass" if duplicate_count == 0 else "fail", "duplicate filing keys", table="sec_filing_v2", details={"duplicates": duplicate_count}))
    return rows


def check_document_v1(client: ClickHouseHttpClient, db: str) -> list[dict[str, Any]]:
    rows = []
    relations = query_rows(
        client,
        f"""
        SELECT 'doc_without_filing' AS metric, count() AS value
        FROM (SELECT cik, accession_number FROM {qi(db)}.sec_filing_document_v1 FINAL) AS d
        LEFT ANTI JOIN (SELECT cik, accession_number FROM {qi(db)}.sec_filing_v2 FINAL) AS f
        ON d.cik = f.cik AND d.accession_number = f.accession_number
        UNION ALL
        SELECT 'filing_without_doc', count()
        FROM (SELECT cik, accession_number FROM {qi(db)}.sec_filing_v2 FINAL) AS f
        LEFT ANTI JOIN (SELECT cik, accession_number FROM {qi(db)}.sec_filing_document_v1 FINAL) AS d
        ON d.cik = f.cik AND d.accession_number = f.accession_number
        UNION ALL
        SELECT 'duplicate_documents', count()
        FROM (
            SELECT document_id, count() AS c
            FROM {qi(db)}.sec_filing_document_v1 FINAL
            GROUP BY document_id
            HAVING c > 1
        )
        FORMAT TSVWithNames
        """,
    )
    relation_details = {row["metric"]: int(row["value"]) for row in relations}
    relation_status = "pass" if relation_details.get("doc_without_filing", 0) == 0 and relation_details.get("duplicate_documents", 0) == 0 else "fail"
    rows.append(check("sec_filing_document_v1_relations", relation_status, "current document v1 relation checks", table="sec_filing_document_v1", details=relation_details))

    fingerprint = query_one(
        client,
        f"""
        SELECT
            count() AS joined_docs,
            countIf(d.document_name = ifNull(f.primary_document, '')) AS doc_name_matches_primary,
            countIf(d.document_url = ifNull(f.primary_document_url, '')) AS doc_url_matches_primary,
            countIf(d.sequence_number = 1) AS sequence_one,
            countIf(d.document_type = f.form_type) AS doc_type_matches_form,
            countIf(d.description = 'primary_document_from_sec_filing_metadata') AS bridge_description
        FROM (SELECT * FROM {qi(db)}.sec_filing_document_v1 FINAL) AS d
        INNER JOIN (SELECT * FROM {qi(db)}.sec_filing_v2 FINAL) AS f
        ON d.cik = f.cik AND d.accession_number = f.accession_number
        FORMAT TSVWithNames
        """,
    )
    joined_docs = int(fingerprint["joined_docs"])
    is_synthetic_bridge = joined_docs > 0 and all(int(fingerprint[key]) == joined_docs for key in ("doc_name_matches_primary", "doc_url_matches_primary", "sequence_one", "doc_type_matches_form", "bridge_description"))
    rows.append(
        check(
            "sec_filing_document_v1_synthetic_bridge",
            "warn" if is_synthetic_bridge else "pass",
            "document v1 is synthetic primary-document bridge" if is_synthetic_bridge else "document v1 is not a pure synthetic bridge",
            table="sec_filing_document_v1",
            details=fingerprint,
        )
    )
    distribution = query_rows(
        client,
        f"""
        SELECT docs_per_accession, count() AS accessions
        FROM (
            SELECT cik, accession_number, count() AS docs_per_accession
            FROM {qi(db)}.sec_filing_document_v1 FINAL
            GROUP BY cik, accession_number
        )
        GROUP BY docs_per_accession
        ORDER BY docs_per_accession
        LIMIT 20
        FORMAT TSVWithNames
        """,
    )
    rows.append(check("sec_filing_document_v1_docs_per_accession", "warn", "document v1 distribution shows provisional one-row-per-accession shape", table="sec_filing_document_v1", details={"distribution": distribution}))
    return rows


def check_document_v2_shape(column_map: dict[str, set[str]]) -> list[dict[str, Any]]:
    required = {
        "document_id",
        "filing_id",
        "accession_number",
        "accession_number_compact",
        "cik",
        "sequence_number",
        "document_role",
        "source_archive_date",
        "source_archive_member",
        "content_format",
        "content_sha256",
        "text_sha256",
        "has_normalized_text",
        "normalizer_version",
    }
    columns = column_map.get("sec_filing_document_v2", set())
    missing = sorted(required - columns)
    return [check("sec_filing_document_v2_required_columns", "pass" if not missing else "fail", "document v2 required schema columns", table="sec_filing_document_v2", details={"missing_columns": missing})]


def check_text_v2_shape(column_map: dict[str, set[str]]) -> list[dict[str, Any]]:
    required = {
        "document_id",
        "filing_id",
        "accession_number",
        "accession_number_compact",
        "cik",
        "text_kind",
        "text",
        "text_char_count",
        "text_byte_count",
        "text_sha256",
        "normalizer_version",
        "quality_flags",
        "source_archive_date",
        "source_archive_member",
    }
    columns = column_map.get("sec_filing_text_v2", set())
    missing = sorted(required - columns)
    return [check("sec_filing_text_v2_required_columns", "pass" if not missing else "fail", "text v2 required schema columns", table="sec_filing_text_v2", details={"missing_columns": missing})]


def check_text_table(client: ClickHouseHttpClient, db: str, *, text_table: str, document_table: str) -> list[dict[str, Any]]:
    if scalar_int(client, f"SELECT count() FROM {qi(db)}.{qi(text_table)}") == 0:
        return [check(f"{text_table}_empty", "warn", f"{text_table} has zero rows", table=text_table, details={"rows": 0})]
    duplicate_text = scalar_int(
        client,
        f"""
        SELECT count()
        FROM (
            SELECT document_id, text_kind, count() AS c
            FROM {qi(db)}.{qi(text_table)} FINAL
            GROUP BY document_id, text_kind
            HAVING c > 1
        )
        """,
    )
    without_document = scalar_int(
        client,
        f"""
        SELECT count()
        FROM (SELECT document_id FROM {qi(db)}.{qi(text_table)} FINAL GROUP BY document_id) AS t
        LEFT ANTI JOIN (SELECT document_id FROM {qi(db)}.{qi(document_table)} FINAL GROUP BY document_id) AS d
        ON t.document_id = d.document_id
        """,
    )
    return [
        check(f"{text_table}_duplicate_keys", "pass" if duplicate_text == 0 else "fail", "duplicate text keys", table=text_table, details={"duplicates": duplicate_text}),
        check(f"{text_table}_without_document_parent", "pass" if without_document == 0 else "fail", "text rows without document parent", table=text_table, details={"without_document_parent": without_document}),
    ]


def check_xbrl_presence(table_meta: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for table in ("sec_xbrl_company_fact_v1", "sec_xbrl_frame_observation_v1", "sec_xbrl_frame_v1", "sec_xbrl_concept_v1"):
        meta = table_meta.get(table, {})
        total_rows = int(meta.get("total_rows") or 0)
        rows.append(check(f"{table}_presence", "pass" if total_rows > 0 else "warn", "structured SEC/XBRL table presence", table=table, details={"system_total_rows": total_rows}))
    return rows


def check_xbrl_sample(client: ClickHouseHttpClient, db: str, sample_limit: int) -> list[dict[str, Any]]:
    sample_orphans = scalar_int(
        client,
        f"""
        SELECT count()
        FROM (
            SELECT cik, accession_number
            FROM {qi(db)}.sec_xbrl_company_fact_v1 FINAL
            WHERE accession_number IS NOT NULL AND accession_number != ''
            GROUP BY cik, accession_number
            LIMIT {int(sample_limit)}
        ) AS x
        LEFT ANTI JOIN (SELECT cik, accession_number FROM {qi(db)}.sec_filing_v2 FINAL) AS f
        ON x.cik = f.cik AND x.accession_number = f.accession_number
        """,
    )
    status = "pass" if sample_orphans == 0 else "warn"
    return [check("sec_xbrl_company_fact_sample_orphans", status, "sampled XBRL accession references that do not join to sec_filing_v2", table="sec_xbrl_company_fact_v1", details={"sample_limit": sample_limit, "sample_orphans": sample_orphans})]


def check_archive_inventory(args: argparse.Namespace) -> list[dict[str, Any]]:
    root = Path(args.archive_root_win)
    if not root.exists():
        return [check("sec_daily_archive_root_exists", "warn", "archive root missing or unavailable", details={"archive_root_win": str(root)})]
    files = sorted(root.glob("*/*/*.nc.tar.gz"))
    dates = [archive_date_from_name(path.name) for path in files]
    dates = [item for item in dates if item is not None]
    start = parse_date_or_none(args.archive_start_date)
    end = parse_date_or_none(args.archive_end_date)
    in_range = [item for item in dates if (start is None or item >= start) and (end is None or item < end)]
    details = {
        "archive_root_win": str(root),
        "archive_files": len(files),
        "archive_dates_in_range": len(in_range),
        "min_archive_date": min(dates).isoformat() if dates else "",
        "max_archive_date": max(dates).isoformat() if dates else "",
        "archive_start_date": args.archive_start_date,
        "archive_end_date": args.archive_end_date,
    }
    return [check("sec_daily_archive_inventory", "pass" if files else "warn", "daily archive inventory summary", details=details)]


def query_table_metadata(client: ClickHouseHttpClient, db: str) -> dict[str, dict[str, Any]]:
    rows = query_rows(
        client,
        f"""
        SELECT name, engine, total_rows, partition_key, sorting_key
        FROM system.tables
        WHERE database = {sql_string(db)} AND name LIKE 'sec_%'
        ORDER BY name
        FORMAT TSVWithNames
        """,
    )
    return {row["name"]: row for row in rows}


def query_column_map(client: ClickHouseHttpClient, db: str) -> dict[str, set[str]]:
    rows = query_rows(
        client,
        f"""
        SELECT table, name
        FROM system.columns
        WHERE database = {sql_string(db)} AND table LIKE 'sec_%'
        ORDER BY table, position
        FORMAT TSVWithNames
        """,
    )
    result: dict[str, set[str]] = {}
    for row in rows:
        result.setdefault(row["table"], set()).add(row["name"])
    return result


def query_one(client: ClickHouseHttpClient, sql: str) -> dict[str, str]:
    rows = query_rows(client, sql)
    if not rows:
        return {}
    return rows[0]


def query_rows(client: ClickHouseHttpClient, sql: str) -> list[dict[str, str]]:
    text = client.execute(sql.strip())
    lines = [line for line in text.splitlines() if line]
    if not lines:
        return []
    header = lines[0].split("\t")
    return [dict(zip(header, line.split("\t"))) for line in lines[1:]]


def scalar_int(client: ClickHouseHttpClient, sql: str) -> int:
    text = client.execute(sql.strip() + " FORMAT TSV").strip()
    if not text:
        return 0
    return int(text.splitlines()[0].split("\t")[0])


def check(name: str, status: str, message: str, *, table: str = "", details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "table": table,
        "message": message,
        "details": details or {},
        "checked_at_utc": datetime.now(UTC).isoformat(),
    }


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def write_manifest(path: Path, args: argparse.Namespace, paths: AuditPaths, loaded_env: list[Path], checks: list[dict[str, Any]], wall_seconds: float) -> None:
    payload = {
        "run_root": str(paths.run_root),
        "checks_jsonl": str(paths.checks_jsonl),
        "summary_md": str(paths.summary_md),
        "database": args.database,
        "archive_root_win": args.archive_root_win,
        "archive_start_date": args.archive_start_date,
        "archive_end_date": args.archive_end_date,
        "status_counts": status_counts(checks),
        "wall_seconds": wall_seconds,
        "git_commit": git_commit(),
        "loaded_env_files": [str(item) for item in loaded_env],
        "secret_status": secret_status(
            [
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
        ),
        "created_at_utc": datetime.now(UTC).isoformat(),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_summary(path: Path, args: argparse.Namespace, checks: list[dict[str, Any]], wall_seconds: float) -> None:
    counts = status_counts(checks)
    lines = [
        "# SEC Integrity Audit",
        "",
        f"- Database: `{args.database}`",
        f"- Archive root: `{args.archive_root_win}`",
        f"- Wall seconds: `{wall_seconds}`",
        f"- Checks: `{len(checks)}`",
        f"- Pass: `{counts['pass']}`",
        f"- Warn: `{counts['warn']}`",
        f"- Fail: `{counts['fail']}`",
        "",
        "## Checks",
        "",
        "| Status | Check | Table | Message | Details |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in checks:
        lines.append(
            "| {status} | `{name}` | `{table}` | {message} | `{details}` |".format(
                status=row["status"],
                name=row["name"],
                table=row.get("table") or "",
                message=str(row.get("message") or "").replace("|", "\\|"),
                details=json.dumps(row.get("details") or {}, sort_keys=True)[:1200].replace("|", "\\|"),
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def status_counts(checks: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "pass": sum(1 for row in checks if row["status"] == "pass"),
        "warn": sum(1 for row in checks if row["status"] == "warn"),
        "fail": sum(1 for row in checks if row["status"] == "fail"),
    }


def print_header(args: argparse.Namespace, paths: AuditPaths, loaded_env: list[Path]) -> None:
    print("=" * 96, flush=True)
    print("SEC integrity audit", flush=True)
    print(f"database={args.database}", flush=True)
    print(f"run_root={paths.run_root}", flush=True)
    print(f"archive_root_win={args.archive_root_win}", flush=True)
    print(f"xbrl_sample_limit={args.xbrl_sample_limit if not args.skip_xbrl_sample else 0}", flush=True)
    print("loaded_env_files=" + json.dumps([str(item) for item in loaded_env]), flush=True)
    print("=" * 96, flush=True)


def archive_date_from_name(name: str) -> date | None:
    match = re.match(r"(\d{8})\.nc\.tar\.gz$", name)
    if not match:
        return None
    return datetime.strptime(match.group(1), "%Y%m%d").date()


def parse_date_or_none(value: str) -> date | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def validate_identifier(value: str, label: str) -> None:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value or ""):
        raise SystemExit(f"{label} must be a simple ClickHouse identifier: {value!r}")


def qi(value: str) -> str:
    return quote_ident(value)


def git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, stderr=subprocess.DEVNULL, text=True).strip()
    except Exception:  # noqa: BLE001
        return ""


if __name__ == "__main__":
    main()

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
from pipelines.sec.edgar.sec_pipeline.submissions import parse_acceptance_datetime  # noqa: E402


DEFAULT_DATABASE = "q_live"
DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_integrity_audit")
DEFAULT_ARCHIVE_ROOT_WIN = Path("D:/market-data/sec_core/daily_archives")
SEC_TABLES = (
    "sec_filing_v3",
    "sec_filing_entity_v3",
    "sec_filing_entity_current_v3",
    "sec_filing_archive_accession_v3",
    "sec_filing_archive_accession_current_v3",
    "sec_filing_document_v3",
    "sec_filing_text_v3",
    "sec_filing_text_rendered_v3",
    "sec_filing_document_skip_v3",
    "sec_xbrl_company_fact_v3",
    "sec_xbrl_frame_observation_v3",
    "sec_xbrl_frame_v3",
    "sec_xbrl_concept_v3",
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
    parser.add_argument("--submissions-database", default=os.environ.get("SEC_BULK_MIRROR_DATABASE", "sec_core"))
    parser.add_argument("--submissions-table", default="sec_bulk_mirror_filing_v3")
    parser.add_argument("--submissions-overlay-table", default="sec_submissions_filing_overlay_v3")
    parser.add_argument("--output-root-win", default=os.environ.get("SEC_INTEGRITY_AUDIT_OUTPUT_ROOT_WIN", str(DEFAULT_OUTPUT_ROOT_WIN)))
    parser.add_argument("--archive-root-win", default=os.environ.get("SEC_DAILY_ARCHIVE_ROOT_WIN", str(DEFAULT_ARCHIVE_ROOT_WIN)))
    parser.add_argument("--archive-start-date", default=os.environ.get("SEC_ARCHIVE_START_DATE", "2019-01-01"))
    parser.add_argument("--archive-end-date", default=os.environ.get("SEC_ARCHIVE_END_DATE", ""))
    parser.add_argument(
        "--scope-start-date",
        default=os.environ.get("SEC_INTEGRITY_SCOPE_START_DATE", "2019-01-01"),
        help="Earliest date treated as actionable for SEC integrity. Older rows are summarized as legacy.",
    )
    parser.add_argument("--xbrl-sample-limit", type=int, default=int(os.environ.get("SEC_INTEGRITY_XBRL_SAMPLE_LIMIT", "200000")))
    parser.add_argument("--skip-xbrl-sample", action="store_true", help="Skip the sampled XBRL-to-filing orphan check.")
    parser.add_argument("--require-v3-tables", action="store_true", help="Fail if SEC document/text v3 target tables are absent.")
    parser.add_argument("--fail-on-warn", action="store_true", help="Exit non-zero when any warning is present.")
    return parser.parse_args()


def main() -> None:
    loaded_env = load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args()
    validate_identifier(args.database, "--database")
    validate_identifier(args.submissions_database, "--submissions-database")
    validate_identifier(args.submissions_table, "--submissions-table")
    validate_identifier(args.submissions_overlay_table, "--submissions-overlay-table")
    scope_start = parse_date_or_none(args.scope_start_date)
    if scope_start is None:
        raise SystemExit("--scope-start-date must be YYYY-MM-DD")
    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    paths = AuditPaths.create(Path(args.output_root_win), run_id)
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)

    print_header(args, paths, loaded_env)
    started = time.perf_counter()
    checks: list[dict[str, Any]] = []
    table_meta = query_table_metadata(client, args.database)
    column_map = query_column_map(client, args.database)

    checks.extend(check_required_tables(table_meta, args.require_v3_tables))
    if "sec_filing_v3" in table_meta:
        checks.extend(check_filing_parent(client, args.database, scope_start))
        checks.extend(
            check_submissions_relationships(
                client,
                args.database,
                args.submissions_database,
                args.submissions_table,
                args.submissions_overlay_table,
            )
        )
    if {"sec_filing_v3", "sec_filing_entity_current_v3"}.issubset(table_meta):
        checks.extend(check_filing_entities(client, args.database))
    if {"sec_filing_v3", "sec_filing_document_v3", "sec_filing_archive_accession_current_v3"}.issubset(table_meta):
        checks.extend(check_archive_backed_repairs(client, args.database))
    if "sec_filing_document_v3" in table_meta:
        checks.extend(check_document_v2_shape(column_map))
    if "sec_filing_text_v3" in table_meta:
        checks.extend(check_text_source_shape(column_map))
        if "sec_filing_document_v3" in table_meta:
            checks.extend(check_text_source_table(client, args.database, text_source_table="sec_filing_text_v3", document_table="sec_filing_document_v3"))
    if "sec_filing_text_rendered_v3" in table_meta:
        checks.extend(check_text_v2_shape(column_map))
        if "sec_filing_document_v3" in table_meta:
            checks.extend(check_text_table(client, args.database, text_table="sec_filing_text_rendered_v3", document_table="sec_filing_document_v3"))
    checks.extend(check_xbrl_presence(table_meta))
    if not args.skip_xbrl_sample and args.xbrl_sample_limit > 0 and {"sec_xbrl_company_fact_v3", "sec_filing_v3"}.issubset(table_meta):
        checks.extend(check_xbrl_sample(client, args.database, args.xbrl_sample_limit, scope_start))
    if {"sec_filing_v3", "sec_filing_document_v3", "sec_xbrl_company_fact_v3", "sec_xbrl_frame_v3", "sec_xbrl_frame_observation_v3", "sec_xbrl_concept_v3"}.issubset(table_meta):
        checks.extend(check_xbrl_scoped_integrity(client, args.database, scope_start))
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


def check_required_tables(table_meta: dict[str, dict[str, Any]], require_v3_tables: bool) -> list[dict[str, Any]]:
    rows = []
    required = {"sec_filing_v3"}
    if require_v3_tables:
        required |= {
            "sec_filing_entity_v3", "sec_filing_entity_current_v3", "sec_filing_document_v3",
            "sec_filing_archive_accession_v3", "sec_filing_archive_accession_current_v3",
            "sec_filing_text_v3", "sec_filing_text_rendered_v3", "sec_filing_document_skip_v3",
        }
    for table in SEC_TABLES:
        exists = table in table_meta
        status = "pass" if exists or table not in required else "fail"
        if table in {"sec_filing_document_v3", "sec_filing_text_v3", "sec_filing_text_rendered_v3", "sec_filing_document_skip_v3"} and not exists and not require_v3_tables:
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


def check_filing_entities(client: ClickHouseHttpClient, db: str) -> list[dict[str, Any]]:
    summary = query_one(
        client,
        f"""
        SELECT count() AS rows,
               uniqExact(accession_number) AS accessions,
               uniqExact(entity_cik) AS entity_ciks,
               countIf(entity_role = '') AS missing_roles,
               countIf(entity_cik = '') AS missing_ciks,
               uniqExactIf(accession_number, substring(accession_number_compact, 1, 10) != primary_cik) AS accession_prefix_primary_cik_mismatches
        FROM {qi(db)}.sec_filing_entity_current_v3
        FORMAT TSVWithNames
        """,
    )
    orphans = scalar_int(
        client,
        f"""
        SELECT count()
        FROM (SELECT DISTINCT accession_number FROM {qi(db)}.sec_filing_entity_current_v3) AS e
        LEFT ANTI JOIN (SELECT DISTINCT accession_number FROM {qi(db)}.sec_filing_v3 FINAL) AS f USING (accession_number)
        """,
    )
    missing_entities = scalar_int(
        client,
        f"""
        SELECT count()
        FROM (SELECT DISTINCT accession_number FROM {qi(db)}.sec_filing_v3 FINAL) AS f
        LEFT ANTI JOIN (SELECT DISTINCT accession_number FROM {qi(db)}.sec_filing_entity_current_v3) AS e USING (accession_number)
        """,
    )
    status = "pass" if int(summary["rows"]) > 0 and int(summary["missing_roles"]) == 0 and int(summary["missing_ciks"]) == 0 and orphans == 0 and missing_entities == 0 else "fail"
    summary["accessions_without_filing"] = orphans
    summary["filings_without_entities"] = missing_entities
    return [
        check(
            "sec_filing_entity_relationships",
            status,
            "current SGML entity relationships are populated and join to filings by accession",
            table="sec_filing_entity_current_v3",
            details=summary,
        )
    ]


def check_archive_backed_repairs(client: ClickHouseHttpClient, db: str) -> list[dict[str, Any]]:
    classification = query_one(
        client,
        f"""
        WITH parents AS (
          SELECT f.cik, f.accession_number
          FROM {qi(db)}.sec_filing_v3 AS f FINAL
          LEFT ANTI JOIN (SELECT DISTINCT cik, accession_number FROM {qi(db)}.sec_filing_document_v3 FINAL) AS d USING (cik, accession_number)
        ), docs_any AS (SELECT DISTINCT accession_number FROM {qi(db)}.sec_filing_document_v3 FINAL),
        inventory AS (SELECT * FROM {qi(db)}.sec_filing_archive_accession_current_v3 WHERE source_kind='daily_archive')
        SELECT count() AS parents_without_exact_documents,
               countIf(d.accession_number != '') AS documents_under_other_cik,
               countIf(d.accession_number = '' AND i.accession_number != '' AND i.document_count > 0 AND p.cik=i.primary_cik) AS archive_backed_repairable,
               countIf(d.accession_number = '' AND i.accession_number != '' AND i.document_count = 0) AS archive_member_without_documents,
               countIf(d.accession_number = '' AND i.accession_number = '') AS metadata_only_not_disseminated,
               countIf(d.accession_number = '' AND i.accession_number != '' AND i.document_count > 0 AND p.cik!=i.primary_cik) AS archive_identity_mismatch
        FROM parents AS p LEFT JOIN docs_any AS d USING (accession_number) LEFT JOIN inventory AS i USING (accession_number)
        FORMAT TSVWithNames
        """,
    )
    fallback_rows = query_rows(
        client,
        f"""
        SELECT f.cik, f.accession_number, ifNull(i.acceptance_datetime_raw, '') AS archive_acceptance_raw
        FROM {qi(db)}.sec_filing_v3 AS f FINAL
        LEFT JOIN {qi(db)}.sec_filing_archive_accession_current_v3 AS i USING (accession_number)
        WHERE f.accepted_at_source IN ('archive_filing_date_midnight','archive_date_midnight','filing_date_midnight_fallback')
        FORMAT TSVWithNames
        """,
    )
    repairable_times = sum(1 for row in fallback_rows if parse_acceptance_datetime(row["archive_acceptance_raw"]))
    unresolved_times = len(fallback_rows) - repairable_times
    return [
        check(
            "sec_archive_backed_missing_documents",
            "pass" if int(classification["archive_backed_repairable"]) == 0 else "fail",
            "archive-backed filing parents with public documents are fully extracted",
            table="sec_filing_document_v3",
            details=classification,
        ),
        check(
            "sec_source_repairable_acceptance_fallbacks",
            "pass" if repairable_times == 0 else "fail",
            "date-only acceptance fallbacks have no deterministic archive timestamp remaining",
            table="sec_filing_v3",
            details={
                "fallback_rows": len(fallback_rows),
                "source_repairable_rows": repairable_times,
                "source_unresolved_rows": unresolved_times,
            },
        ),
    ]


def check_filing_parent(client: ClickHouseHttpClient, db: str, scope_start: date) -> list[dict[str, Any]]:
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
            countIf(accepted_at_utc >= {dt64_sql(scope_start, precision=9)}) AS rows_in_scope,
            countIf(accepted_at_utc >= {dt64_sql(scope_start, precision=9)} AND primary_document IS NOT NULL AND primary_document != '') AS rows_in_scope_with_primary_document
        FROM {qi(db)}.sec_filing_v3 FINAL
        FORMAT TSVWithNames
        """,
    )
    rows.append(
        check(
            "sec_filing_v3_parent_summary",
            "pass" if int(summary["rows"]) > 0 and int(summary["missing_accepted_at_utc"]) == 0 else "fail",
            "filing parent has rows and accepted timestamps are populated",
            table="sec_filing_v3",
            details=summary,
        )
    )
    duplicate_count = scalar_int(
        client,
        f"""
        SELECT count()
        FROM (
            SELECT cik, accession_number, count() AS c
            FROM {qi(db)}.sec_filing_v3 FINAL
            GROUP BY cik, accession_number
            HAVING c > 1
        )
        """,
    )
    rows.append(check("sec_filing_v3_duplicate_keys", "pass" if duplicate_count == 0 else "fail", "duplicate filing keys", table="sec_filing_v3", details={"duplicates": duplicate_count}))
    scoped_duplicate_count = scalar_int(
        client,
        f"""
        SELECT count()
        FROM (
            SELECT cik, accession_number, count() AS c
            FROM {qi(db)}.sec_filing_v3 FINAL
            WHERE accepted_at_utc >= {dt64_sql(scope_start, precision=9)}
            GROUP BY cik, accession_number
            HAVING c > 1
        )
        """,
    )
    rows.append(
        check(
            "sec_filing_v3_duplicate_keys_in_scope",
            "pass" if scoped_duplicate_count == 0 else "fail",
            f"duplicate filing keys from {scope_start.isoformat()} onward",
            table="sec_filing_v3",
            details={"scope_start_date": scope_start.isoformat(), "duplicates": scoped_duplicate_count},
        )
    )
    return rows


def check_submissions_relationships(
    client: ClickHouseHttpClient,
    target_database: str,
    submissions_database: str,
    submissions_table: str,
    overlay_table: str,
) -> list[dict[str, Any]]:
    if not table_exists(client, submissions_database, submissions_table):
        return [
            check(
                "sec_filing_submissions_relationship_coverage",
                "fail",
                "authoritative SEC submissions mirror is missing",
                table="sec_filing_v3",
                details={"required_table": f"{submissions_database}.{submissions_table}"},
            )
        ]
    overlay_union = ""
    overlay_present = table_exists(client, submissions_database, overlay_table)
    if overlay_present:
        overlay_union = f"""
        UNION ALL
        SELECT cik, accession_number
        FROM {qi(submissions_database)}.{qi(overlay_table)} FINAL
        WHERE cik != '' AND accession_number != ''
        """
    details = query_one(
        client,
        f"""
        WITH
        authoritative AS
        (
            SELECT cik, accession_number
            FROM
            (
                SELECT cik, accession_number
                FROM {qi(submissions_database)}.{qi(submissions_table)} FINAL
                WHERE cik != '' AND accession_number != ''
                {overlay_union}
            )
            GROUP BY cik, accession_number
        ),
        authoritative_accessions AS
        (
            SELECT accession_number FROM authoritative GROUP BY accession_number
        )
        SELECT
            count() AS filing_rows,
            countIf(substring(q.accession_number, 1, 10) != q.cik) AS accession_prefix_differs_from_cik,
            countIf(a.cik != '') AS confirmed_exact_relationships,
            countIf(a.cik = '' AND aa.accession_number != '') AS accession_known_under_other_cik_only,
            countIf(aa.accession_number = '') AS accession_absent_from_submissions,
            countIf(
                a.cik = ''
                AND q.text_status IN ('archive_text_extracted', 'xbrl_parent_only')
            ) AS separately_source_backed_relationships,
            countIf(
                a.cik = ''
                AND q.text_status NOT IN ('archive_text_extracted', 'xbrl_parent_only')
            ) AS unsupported_relationships
        FROM {qi(target_database)}.sec_filing_v3 AS q FINAL
        LEFT JOIN authoritative AS a
            ON q.cik = a.cik AND q.accession_number = a.accession_number
        LEFT JOIN authoritative_accessions AS aa
            ON q.accession_number = aa.accession_number
        FORMAT TSVWithNames
        """,
    )
    unsupported = int(details["unsupported_relationships"])
    separately_backed = int(details["separately_source_backed_relationships"])
    details["submissions_table"] = f"{submissions_database}.{submissions_table}"
    details["overlay_table"] = f"{submissions_database}.{overlay_table}"
    details["overlay_present"] = overlay_present
    return [
        check(
            "sec_filing_submissions_relationship_coverage",
            "fail" if unsupported else ("warn" if separately_backed else "pass"),
            "q_live filing CIK/accession relationships have an explicit submissions, archive-SGML, or XBRL authority",
            table="sec_filing_v3",
            details=details,
        )
    ]


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
    columns = column_map.get("sec_filing_document_v3", set())
    missing = sorted(required - columns)
    return [check("sec_filing_document_v3_required_columns", "pass" if not missing else "fail", "document v3 required schema columns", table="sec_filing_document_v3", details={"missing_columns": missing})]


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
    columns = column_map.get("sec_filing_text_rendered_v3", set())
    missing = sorted(required - columns)
    return [check("sec_filing_text_rendered_v3_required_columns", "pass" if not missing else "fail", "rendered text v3 required schema columns", table="sec_filing_text_rendered_v3", details={"missing_columns": missing})]


def check_text_source_shape(column_map: dict[str, set[str]]) -> list[dict[str, Any]]:
    required = {
        "document_id",
        "filing_id",
        "accession_number",
        "accession_number_compact",
        "cik",
        "sequence_number",
        "document_name",
        "document_type",
        "document_role",
        "text_kind",
        "content_format",
        "mime_type",
        "source_text",
        "source_text_char_count",
        "source_text_byte_count",
        "content_sha256",
        "source_archive_date",
        "source_archive_member",
        "source_archive_path",
        "source_run_id",
        "inserted_at",
    }
    columns = column_map.get("sec_filing_text_v3", set())
    missing = sorted(required - columns)
    return [
        check(
            "sec_filing_text_v3_required_columns",
            "pass" if not missing else "fail",
            "source text required schema columns",
            table="sec_filing_text_v3",
            details={"missing_columns": missing},
        )
    ]


def check_text_source_table(client: ClickHouseHttpClient, db: str, *, text_source_table: str, document_table: str) -> list[dict[str, Any]]:
    duplicate_text_source = scalar_int(
        client,
        f"""
        SELECT count()
        FROM (
            SELECT document_id, count() AS c
            FROM {qi(db)}.{qi(text_source_table)} FINAL
            GROUP BY document_id
            HAVING c > 1
        )
        """,
    )
    without_document = scalar_int(
        client,
        f"""
        SELECT count()
        FROM (SELECT document_id FROM {qi(db)}.{qi(text_source_table)} FINAL GROUP BY document_id) AS s
        LEFT ANTI JOIN (SELECT document_id FROM {qi(db)}.{qi(document_table)} FINAL GROUP BY document_id) AS d
        ON s.document_id = d.document_id
        """,
    )
    xbrl_text_source_rows = scalar_int(
        client,
        f"""
        SELECT count()
        FROM {qi(db)}.{qi(text_source_table)} FINAL
        WHERE content_format = 'xbrl' OR document_role = 'xbrl_sidecar'
        """,
    )
    return [
        check(f"{text_source_table}_duplicate_keys", "pass" if duplicate_text_source == 0 else "fail", "duplicate source text keys", table=text_source_table, details={"duplicates": duplicate_text_source}),
        check(f"{text_source_table}_without_document_parent", "pass" if without_document == 0 else "fail", "source text rows without document parent", table=text_source_table, details={"without_document_parent": without_document}),
        check(f"{text_source_table}_xbrl_sidecar_exclusion", "pass" if xbrl_text_source_rows == 0 else "fail", "source text table excludes XBRL sidecars", table=text_source_table, details={"xbrl_text_source_rows": xbrl_text_source_rows}),
    ]


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
    for table in ("sec_xbrl_company_fact_v3", "sec_xbrl_frame_observation_v3", "sec_xbrl_frame_v3", "sec_xbrl_concept_v3"):
        meta = table_meta.get(table, {})
        total_rows = int(meta.get("total_rows") or 0)
        rows.append(check(f"{table}_presence", "pass" if total_rows > 0 else "warn", "structured SEC/XBRL table presence", table=table, details={"system_total_rows": total_rows}))
    return rows


def check_xbrl_sample(client: ClickHouseHttpClient, db: str, sample_limit: int, scope_start: date) -> list[dict[str, Any]]:
    sample_orphans = scalar_int(
        client,
        f"""
        SELECT count()
        FROM (
            SELECT cik, accession_number
            FROM {qi(db)}.sec_xbrl_company_fact_v3 FINAL
            WHERE accession_number IS NOT NULL AND accession_number != ''
            GROUP BY cik, accession_number
            LIMIT {int(sample_limit)}
        ) AS x
        LEFT ANTI JOIN (SELECT cik, accession_number FROM {qi(db)}.sec_filing_v3 FINAL) AS f
        ON x.cik = f.cik AND x.accession_number = f.accession_number
        """,
    )
    scoped_sample_orphans = scalar_int(
        client,
        f"""
        SELECT count()
        FROM (
            SELECT cik, accession_number
            FROM {qi(db)}.sec_xbrl_company_fact_v3 FINAL
            WHERE accession_number IS NOT NULL
              AND accession_number != ''
              AND filed_at_utc >= {dt64_sql(scope_start, precision=3)}
            GROUP BY cik, accession_number
            LIMIT {int(sample_limit)}
        ) AS x
        LEFT ANTI JOIN (SELECT cik, accession_number FROM {qi(db)}.sec_filing_v3 FINAL) AS f
        ON x.cik = f.cik AND x.accession_number = f.accession_number
        """,
    )
    return [
        check(
            "sec_xbrl_company_fact_sample_orphans_legacy_including_pre_scope",
            "pass" if sample_orphans == 0 else "warn",
            "legacy-inclusive sampled XBRL accession references that do not join to sec_filing_v3",
            table="sec_xbrl_company_fact_v3",
            details={"sample_limit": sample_limit, "sample_orphans": sample_orphans, "scope_note": f"Rows before {scope_start.isoformat()} are legacy-only."},
        ),
        check(
            "sec_xbrl_company_fact_sample_orphans_in_scope",
            "pass" if scoped_sample_orphans == 0 else "fail",
            f"sampled XBRL accession references from {scope_start.isoformat()} onward join to sec_filing_v3",
            table="sec_xbrl_company_fact_v3",
            details={"sample_limit": sample_limit, "sample_orphans": scoped_sample_orphans, "scope_start_date": scope_start.isoformat()},
        ),
    ]


def check_xbrl_scoped_integrity(client: ClickHouseHttpClient, db: str, scope_start: date) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    recency = query_rows(
        client,
        f"""
        SELECT 'filings' AS dataset, count() AS rows, uniqExact(accession_number) AS accessions,
               toString(min(accepted_at_utc)) AS min_event_utc, toString(max(accepted_at_utc)) AS max_event_utc,
               toString(max(inserted_at)) AS max_inserted_at
        FROM {qi(db)}.sec_filing_v3 FINAL
        WHERE accepted_at_utc >= {dt64_sql(scope_start, precision=9)}
        UNION ALL
        SELECT 'documents_v2', count(), uniqExact(accession_number),
               toString(min(toDateTime64(source_archive_date, 3, 'UTC'))), toString(max(toDateTime64(source_archive_date, 3, 'UTC'))), toString(max(inserted_at))
        FROM {qi(db)}.sec_filing_document_v3 FINAL
        WHERE source_archive_date >= toDate({sql_string(scope_start.isoformat())})
        UNION ALL
        SELECT 'texts_v2', count(), uniqExact(accession_number),
               toString(min(toDateTime64(source_archive_date, 3, 'UTC'))), toString(max(toDateTime64(source_archive_date, 3, 'UTC'))), toString(max(inserted_at))
        FROM {qi(db)}.sec_filing_text_rendered_v3 FINAL
        WHERE source_archive_date >= toDate({sql_string(scope_start.isoformat())})
        UNION ALL
        SELECT 'xbrl_company_facts', count(), uniqExact(accession_number),
               toString(min(filed_at_utc)), toString(max(filed_at_utc)), toString(max(inserted_at))
        FROM {qi(db)}.sec_xbrl_company_fact_v3 FINAL
        WHERE filed_at_utc >= {dt64_sql(scope_start, precision=3)}
        FORMAT TSVWithNames
        """,
    )
    rows.append(
        check(
            "sec_in_scope_recency_summary",
            "pass",
            f"SEC table recency summary from {scope_start.isoformat()} onward",
            details={"scope_start_date": scope_start.isoformat(), "datasets": recency},
        )
    )
    orphan_summary = query_rows(
        client,
        f"""
        SELECT 'xbrl_company_facts_without_filing_in_scope' AS metric, count() AS rows
        FROM (
            SELECT cik, accession_number
            FROM {qi(db)}.sec_xbrl_company_fact_v3 FINAL
            WHERE accession_number IS NOT NULL
              AND accession_number != ''
              AND filed_at_utc >= {dt64_sql(scope_start, precision=3)}
        ) AS x
        LEFT ANTI JOIN (SELECT cik, accession_number FROM {qi(db)}.sec_filing_v3 FINAL) AS f
        ON x.cik = f.cik AND x.accession_number = f.accession_number
        UNION ALL
        SELECT 'xbrl_frame_observations_without_frame_in_scope', count()
        FROM (
            SELECT o.taxonomy, o.tag, o.unit_code, o.calendar_period_code
            FROM (
                SELECT cik, accession_number, taxonomy, tag, unit_code, calendar_period_code, period_end_date
                FROM {qi(db)}.sec_xbrl_frame_observation_v3 FINAL
            ) AS o
            INNER JOIN (
                SELECT cik, accession_number, taxonomy, tag, unit_code, period_end_date
                FROM {qi(db)}.sec_xbrl_company_fact_v3 FINAL
                WHERE accession_number IS NOT NULL
                  AND accession_number != ''
                  AND filed_at_utc >= {dt64_sql(scope_start, precision=3)}
            ) AS x
            ON o.cik = x.cik
               AND o.accession_number = x.accession_number
               AND o.taxonomy = x.taxonomy
               AND o.tag = x.tag
               AND o.unit_code = x.unit_code
               AND o.period_end_date = x.period_end_date
        ) AS scoped_obs
        LEFT ANTI JOIN (SELECT taxonomy, tag, unit_code, calendar_period_code FROM {qi(db)}.sec_xbrl_frame_v3 FINAL) AS fr
        ON scoped_obs.taxonomy = fr.taxonomy
           AND scoped_obs.tag = fr.tag
           AND scoped_obs.unit_code = fr.unit_code
           AND scoped_obs.calendar_period_code = fr.calendar_period_code
        UNION ALL
        SELECT 'xbrl_facts_without_concept_in_scope', count()
        FROM (
            SELECT taxonomy, tag
            FROM {qi(db)}.sec_xbrl_company_fact_v3 FINAL
            WHERE filed_at_utc >= {dt64_sql(scope_start, precision=3)}
        ) AS x
        LEFT ANTI JOIN (SELECT taxonomy, tag FROM {qi(db)}.sec_xbrl_concept_v3 FINAL) AS c
        ON x.taxonomy = c.taxonomy AND x.tag = c.tag
        FORMAT TSVWithNames
        """,
    )
    orphan_details = {row["metric"]: int(row["rows"]) for row in orphan_summary}
    rows.append(
        check(
            "sec_xbrl_relations_in_scope",
            "pass" if all(value == 0 for value in orphan_details.values()) else "fail",
            f"XBRL relationships are coherent from {scope_start.isoformat()} onward",
            table="sec_xbrl_company_fact_v3",
            details={"scope_start_date": scope_start.isoformat(), **orphan_details},
        )
    )
    legacy_details = query_one(
        client,
        f"""
        SELECT
            countIf(filed_at_utc < {dt64_sql(scope_start, precision=3)}) AS legacy_xbrl_company_fact_rows,
            uniqExactIf(accession_number, filed_at_utc < {dt64_sql(scope_start, precision=3)}) AS legacy_xbrl_accessions
        FROM {qi(db)}.sec_xbrl_company_fact_v3 FINAL
        FORMAT TSVWithNames
        """,
    )
    rows.append(
        check(
            "sec_xbrl_legacy_pre_scope_summary",
            "pass",
            f"pre-{scope_start.isoformat()} XBRL rows are legacy and not actionable for this audit",
            table="sec_xbrl_company_fact_v3",
            details={"scope_start_date": scope_start.isoformat(), **legacy_details},
        )
    )
    missing_by_form = query_rows(
        client,
        f"""
        WITH missing AS (
            SELECT d.cik AS cik, d.accession_number AS accession_number
            FROM (
                SELECT DISTINCT cik, accession_number
                FROM {qi(db)}.sec_filing_document_v3 FINAL
                WHERE source_archive_date >= toDate({sql_string(scope_start.isoformat())})
                  AND accession_number != ''
                  AND (content_format = 'xbrl' OR document_role = 'xbrl_sidecar' OR positionCaseInsensitive(document_type, 'xbrl') > 0)
            ) AS d
            LEFT ANTI JOIN (
                SELECT DISTINCT cik, accession_number
                FROM {qi(db)}.sec_xbrl_company_fact_v3 FINAL
                WHERE accession_number IS NOT NULL AND accession_number != ''
            ) AS x ON d.cik = x.cik AND d.accession_number = x.accession_number
        )
        SELECT f.form_type, count() AS accessions, uniqExact(m.cik) AS ciks
        FROM missing AS m
        INNER JOIN (
            SELECT cik, accession_number, form_type
            FROM {qi(db)}.sec_filing_v3 FINAL
            WHERE accepted_at_utc >= {dt64_sql(scope_start, precision=9)}
        ) AS f
        ON m.cik = f.cik AND m.accession_number = f.accession_number
        GROUP BY f.form_type
        ORDER BY accessions DESC
        LIMIT 30
        FORMAT TSVWithNames
        """,
    )
    missing_total = sum(int(row["accessions"]) for row in missing_by_form)
    rows.append(
        check(
            "sec_xbrl_like_documents_without_companyfacts_in_scope",
            "warn" if missing_total else "pass",
            "XBRL-looking archive documents without SEC companyfacts rows; not all XML/XBRL document types map to companyfacts",
            table="sec_filing_document_v3",
            details={"scope_start_date": scope_start.isoformat(), "top_forms": missing_by_form, "top_form_accessions": missing_total},
        )
    )
    return rows


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


def table_exists(client: ClickHouseHttpClient, database: str, name: str) -> bool:
    return bool(
        scalar_int(
            client,
            f"SELECT count() FROM system.tables WHERE database={sql_string(database)} AND name={sql_string(name)}",
        )
    )


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
        "scope_start_date": args.scope_start_date,
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
        f"- Actionable scope start: `{args.scope_start_date}`",
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
    print(f"scope_start_date={args.scope_start_date}", flush=True)
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


def dt64_sql(value: date, *, precision: int) -> str:
    return f"toDateTime64({sql_string(value.isoformat() + ' 00:00:00')}, {int(precision)}, 'UTC')"


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

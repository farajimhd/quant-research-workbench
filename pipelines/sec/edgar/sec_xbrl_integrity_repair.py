from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
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
DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_xbrl_integrity_repair")
LEGACY_V1_TABLES = ("sec_filing_document_v1",)


@dataclass(frozen=True, slots=True)
class RepairPaths:
    run_root: Path
    manifest_json: Path
    events_jsonl: Path
    summary_json: Path
    summary_md: Path

    @classmethod
    def create(cls, output_root: Path, run_id: str) -> "RepairPaths":
        run_root = output_root / run_id
        run_root.mkdir(parents=True, exist_ok=True)
        return cls(
            run_root=run_root,
            manifest_json=run_root / "sec_xbrl_integrity_repair_manifest.json",
            events_jsonl=run_root / "sec_xbrl_integrity_repair_events.jsonl",
            summary_json=run_root / "sec_xbrl_integrity_repair_summary.json",
            summary_md=run_root / "sec_xbrl_integrity_repair_summary.md",
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Repair SEC XBRL integrity issues after historical SEC loads. "
            "The script can drop legacy v1 document/text tables, insert missing "
            "sec_filing_v2 parents for XBRL facts, and insert missing frame parents "
            "for frame observations."
        )
    )
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--database", default=os.environ.get("SEC_XBRL_REPAIR_DATABASE", DEFAULT_DATABASE))
    parser.add_argument("--output-root-win", default=os.environ.get("SEC_XBRL_REPAIR_OUTPUT_ROOT_WIN", str(DEFAULT_OUTPUT_ROOT_WIN)))
    parser.add_argument("--scope-start-date", default=os.environ.get("SEC_XBRL_REPAIR_SCOPE_START_DATE", "2019-01-01"))
    parser.add_argument(
        "--stages",
        default=os.environ.get("SEC_XBRL_REPAIR_STAGES", "drop-legacy,filing-parents,frame-parents"),
        help="Comma-separated stages: drop-legacy, filing-parents, frame-parents.",
    )
    parser.add_argument("--execute", action="store_true", help="Apply changes. Without this, only counts and SQL plans are written.")
    return parser.parse_args()


def main() -> None:
    loaded_env = load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args()
    validate_identifier(args.database, "--database")
    validate_date(args.scope_start_date, "--scope-start-date")
    stages = parse_stages(args.stages)
    run_id = "sec_xbrl_integrity_repair_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    paths = RepairPaths.create(Path(args.output_root_win), run_id)
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    events: list[dict[str, Any]] = []

    manifest = {
        "run_id": run_id,
        "created_at_utc": utc_now(),
        "database": args.database,
        "scope_start_date": args.scope_start_date,
        "stages": stages,
        "execute": bool(args.execute),
        "run_root": str(paths.run_root),
        "loaded_env_files": [str(path) for path in loaded_env],
        "secret_status": secret_status(
            [
                "REAL_LIVE_CLICKHOUSE_WRITE_URL",
                "REAL_LIVE_CLICKHOUSE_WRITE_USER",
                "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD",
                "CLICKHOUSE_WORKSTATION_USER",
                "CLICKHOUSE_WORKSTATION_PASSWORD",
            ]
        ),
    }
    write_json(paths.manifest_json, manifest)

    print("=" * 96, flush=True)
    print("SEC XBRL integrity repair", flush=True)
    print(f"execute={args.execute} database={args.database} scope_start={args.scope_start_date}", flush=True)
    print(f"stages={','.join(stages)}", flush=True)
    print(f"run_root={paths.run_root}", flush=True)
    print("=" * 96, flush=True)

    started = time.perf_counter()
    require_tables(client, args.database)
    events.append(snapshot_counts(client, args.database, args.scope_start_date, label="before"))
    print_count_snapshot(events[-1])

    if "drop-legacy" in stages:
        events.append(run_drop_legacy(client, args, paths))
    if "filing-parents" in stages:
        events.append(run_filing_parent_repair(client, args, paths, run_id))
    if "frame-parents" in stages:
        events.append(run_frame_parent_repair(client, args, paths, run_id))

    events.append(snapshot_counts(client, args.database, args.scope_start_date, label="after"))
    print_count_snapshot(events[-1])
    for event in events:
        append_jsonl(paths.events_jsonl, event)

    summary = {
        **manifest,
        "status": "completed" if args.execute else "planned",
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "events": events,
    }
    write_json(paths.summary_json, summary)
    write_markdown(paths.summary_md, summary)
    print("summary_json=" + str(paths.summary_json), flush=True)
    print("summary_md=" + str(paths.summary_md), flush=True)
    print("remaining_xbrl_fact_orphan_rows=" + str(events[-1]["details"]["xbrl_company_fact_orphan_rows"]), flush=True)
    print("remaining_frame_observation_orphan_rows=" + str(events[-1]["details"]["frame_observation_orphan_rows"]), flush=True)


def run_drop_legacy(client: ClickHouseHttpClient, args: argparse.Namespace, paths: RepairPaths) -> dict[str, Any]:
    print("[stage drop-legacy] checking legacy v1 tables", flush=True)
    started = time.perf_counter()
    present = [table for table in LEGACY_V1_TABLES if table_exists(client, args.database, table)]
    sql_path = paths.run_root / "drop_legacy_v1_tables.sql"
    sql_text = "\n".join(f"DROP TABLE IF EXISTS {qi(args.database)}.{qi(table)} SYNC;" for table in LEGACY_V1_TABLES) + "\n"
    sql_path.write_text(sql_text, encoding="utf-8")
    if args.execute:
        for table in LEGACY_V1_TABLES:
            print(f"[stage drop-legacy] dropping {args.database}.{table}", flush=True)
            client.execute(f"DROP TABLE IF EXISTS {qi(args.database)}.{qi(table)} SYNC")
    else:
        print(f"[stage drop-legacy] dry-run; SQL written to {sql_path}", flush=True)
    remaining = [table for table in LEGACY_V1_TABLES if table_exists(client, args.database, table)]
    status = "pass" if not remaining else ("planned" if not args.execute else "fail")
    event = {
        "stage": "drop-legacy",
        "status": status,
        "started_at_utc": utc_now(),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "details": {"present_before": present, "remaining_after": remaining, "sql_path": str(sql_path)},
    }
    print(f"[stage drop-legacy] status={status} present_before={present} remaining_after={remaining}", flush=True)
    return event


def run_filing_parent_repair(client: ClickHouseHttpClient, args: argparse.Namespace, paths: RepairPaths, run_id: str) -> dict[str, Any]:
    print("[stage filing-parents] counting missing XBRL filing parents", flush=True)
    started = time.perf_counter()
    before = xbrl_orphan_counts(client, args.database, args.scope_start_date)
    sql_text = filing_parent_insert_sql(args.database, args.scope_start_date, run_id)
    sql_path = paths.run_root / "repair_xbrl_missing_filing_parents.sql"
    sql_path.write_text(sql_text, encoding="utf-8")
    print(
        "[stage filing-parents] "
        f"orphan_rows={before['rows']:,} orphan_accessions={before['accessions']:,} "
        f"orphan_ciks={before['ciks']:,}",
        flush=True,
    )
    if args.execute and before["accessions"] > 0:
        print("[stage filing-parents] inserting missing sec_filing_v2 parents", flush=True)
        timed_execute(client, sql_text, query_id=run_id + "_filing_parents")
    elif not args.execute:
        print(f"[stage filing-parents] dry-run; SQL written to {sql_path}", flush=True)
    after = xbrl_orphan_counts(client, args.database, args.scope_start_date)
    inserted_accessions = max(0, int(before["accessions"]) - int(after["accessions"]))
    status = "pass" if after["rows"] == 0 else ("planned" if not args.execute else "warn")
    print(
        "[stage filing-parents] "
        f"status={status} inserted_accessions_estimate={inserted_accessions:,} "
        f"remaining_rows={after['rows']:,} remaining_accessions={after['accessions']:,}",
        flush=True,
    )
    return {
        "stage": "filing-parents",
        "status": status,
        "started_at_utc": utc_now(),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "details": {"before": before, "after": after, "inserted_accessions_estimate": inserted_accessions, "sql_path": str(sql_path)},
    }


def run_frame_parent_repair(client: ClickHouseHttpClient, args: argparse.Namespace, paths: RepairPaths, run_id: str) -> dict[str, Any]:
    print("[stage frame-parents] counting missing XBRL frame parents", flush=True)
    started = time.perf_counter()
    before = frame_orphan_counts(client, args.database, args.scope_start_date)
    sql_text = frame_parent_insert_sql(args.database, args.scope_start_date, run_id)
    sql_path = paths.run_root / "repair_xbrl_missing_frame_parents.sql"
    sql_path.write_text(sql_text, encoding="utf-8")
    print(
        "[stage frame-parents] "
        f"orphan_rows={before['rows']:,} orphan_frame_keys={before['frame_keys']:,}",
        flush=True,
    )
    if args.execute and before["frame_keys"] > 0:
        print("[stage frame-parents] inserting missing sec_xbrl_frame_v1 parents", flush=True)
        timed_execute(client, sql_text, query_id=run_id + "_frame_parents")
    elif not args.execute:
        print(f"[stage frame-parents] dry-run; SQL written to {sql_path}", flush=True)
    after = frame_orphan_counts(client, args.database, args.scope_start_date)
    inserted_frames = max(0, int(before["frame_keys"]) - int(after["frame_keys"]))
    status = "pass" if after["rows"] == 0 else ("planned" if not args.execute else "warn")
    print(
        "[stage frame-parents] "
        f"status={status} inserted_frames_estimate={inserted_frames:,} "
        f"remaining_rows={after['rows']:,} remaining_frame_keys={after['frame_keys']:,}",
        flush=True,
    )
    return {
        "stage": "frame-parents",
        "status": status,
        "started_at_utc": utc_now(),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "details": {"before": before, "after": after, "inserted_frames_estimate": inserted_frames, "sql_path": str(sql_path)},
    }


def snapshot_counts(client: ClickHouseHttpClient, database: str, scope_start_date: str, *, label: str) -> dict[str, Any]:
    xbrl_counts = xbrl_orphan_counts(client, database, scope_start_date)
    frame_counts = frame_orphan_counts(client, database, scope_start_date)
    details = {
        "legacy_v1_tables_present": [table for table in LEGACY_V1_TABLES if table_exists(client, database, table)],
        "xbrl_company_fact_orphan_rows": xbrl_counts["rows"],
        "xbrl_company_fact_orphan_accessions": xbrl_counts["accessions"],
        "frame_observation_orphan_rows": frame_counts["rows"],
        "frame_observation_orphan_frame_keys": frame_counts["frame_keys"],
    }
    return {"stage": f"snapshot-{label}", "status": "pass", "created_at_utc": utc_now(), "details": details}


def print_count_snapshot(event: dict[str, Any]) -> None:
    details = event["details"]
    print(
        f"[{event['stage']}] "
        f"legacy_v1={details['legacy_v1_tables_present']} "
        f"xbrl_orphan_rows={details['xbrl_company_fact_orphan_rows']:,} "
        f"xbrl_orphan_accessions={details['xbrl_company_fact_orphan_accessions']:,} "
        f"frame_orphan_rows={details['frame_observation_orphan_rows']:,} "
        f"frame_orphan_frame_keys={details['frame_observation_orphan_frame_keys']:,}",
        flush=True,
    )


def xbrl_orphan_counts(client: ClickHouseHttpClient, database: str, scope_start_date: str) -> dict[str, int]:
    row = query_one_json(
        client,
        f"""
        SELECT
            count() AS rows,
            uniqExact(tuple(cik, accession_number)) AS accessions,
            uniqExact(cik) AS ciks
        FROM (
            SELECT cik, accession_number
            FROM {qi(database)}.sec_xbrl_company_fact_v1 FINAL
            WHERE accession_number IS NOT NULL
              AND accession_number != ''
              AND filed_at_utc >= toDateTime64({sql_string(scope_start_date + " 00:00:00")}, 3, 'UTC')
        ) AS x
        LEFT ANTI JOIN (SELECT cik, accession_number FROM {qi(database)}.sec_filing_v2 FINAL) AS f
        ON x.cik = f.cik AND x.accession_number = f.accession_number
        FORMAT JSONEachRow
        """,
    )
    return {"rows": int(row["rows"]), "accessions": int(row["accessions"]), "ciks": int(row["ciks"])}


def frame_orphan_counts(client: ClickHouseHttpClient, database: str, scope_start_date: str) -> dict[str, int]:
    row = query_one_json(
        client,
        f"""
        SELECT
            count() AS rows,
            uniqExact(tuple(scoped_obs.taxonomy, scoped_obs.tag, scoped_obs.unit_code, scoped_obs.calendar_period_code)) AS frame_keys
        FROM (
            SELECT o.taxonomy, o.tag, o.unit_code, o.calendar_period_code
            FROM (
                SELECT cik, accession_number, taxonomy, tag, unit_code, calendar_period_code, period_end_date
                FROM {qi(database)}.sec_xbrl_frame_observation_v1 FINAL
            ) AS o
            INNER JOIN (
                SELECT cik, accession_number, taxonomy, tag, unit_code, period_end_date
                FROM {qi(database)}.sec_xbrl_company_fact_v1 FINAL
                WHERE accession_number IS NOT NULL
                  AND accession_number != ''
                  AND filed_at_utc >= toDateTime64({sql_string(scope_start_date + " 00:00:00")}, 3, 'UTC')
            ) AS x
            ON o.cik = x.cik
               AND o.accession_number = x.accession_number
               AND o.taxonomy = x.taxonomy
               AND o.tag = x.tag
               AND o.unit_code = x.unit_code
               AND o.period_end_date = x.period_end_date
        ) AS scoped_obs
        LEFT ANTI JOIN (SELECT taxonomy, tag, unit_code, calendar_period_code FROM {qi(database)}.sec_xbrl_frame_v1 FINAL) AS fr
        ON scoped_obs.taxonomy = fr.taxonomy
           AND scoped_obs.tag = fr.tag
           AND scoped_obs.unit_code = fr.unit_code
           AND scoped_obs.calendar_period_code = fr.calendar_period_code
        FORMAT JSONEachRow
        """,
    )
    return {"rows": int(row["rows"]), "frame_keys": int(row["frame_keys"])}


def filing_parent_insert_sql(database: str, scope_start_date: str, run_id: str) -> str:
    return f"""
INSERT INTO {qi(database)}.sec_filing_v2
(
    filing_id,
    accession_number,
    accession_number_compact,
    cik,
    issuer_id,
    company_name,
    form_type,
    filing_date,
    report_date,
    accepted_at_utc,
    acceptance_datetime_raw,
    accepted_at_source,
    primary_document,
    primary_document_url,
    filing_detail_url,
    source_file_name,
    filing_size,
    items,
    text_status,
    source_run_id,
    source_content_sha256,
    inserted_at
)
WITH
    {sql_string(run_id)} AS repair_run_id,
    now64(3, 'UTC') AS repair_inserted_at,
    missing AS (
        SELECT DISTINCT x.cik, x.accession_number
        FROM (
            SELECT cik, accession_number
            FROM {qi(database)}.sec_xbrl_company_fact_v1 FINAL
            WHERE accession_number IS NOT NULL
              AND accession_number != ''
              AND filed_at_utc >= toDateTime64({sql_string(scope_start_date + " 00:00:00")}, 3, 'UTC')
        ) AS x
        LEFT ANTI JOIN (SELECT cik, accession_number FROM {qi(database)}.sec_filing_v2 FINAL) AS f
        ON x.cik = f.cik AND x.accession_number = f.accession_number
    ),
    fact_agg AS (
        SELECT
            cik,
            accession_number,
            min(filed_at_utc) AS filed_at_utc,
            max(period_end_date) AS report_date,
            any(ifNull(form_type, '')) AS form_type,
            count() AS fact_rows
        FROM {qi(database)}.sec_xbrl_company_fact_v1 FINAL
        WHERE (cik, accession_number) IN (SELECT cik, accession_number FROM missing)
        GROUP BY cik, accession_number
    ),
    doc_agg AS (
        SELECT
            cik,
            accession_number,
            any(accession_number_compact) AS accession_number_compact,
            any(filing_id) AS document_filing_id,
            anyIf(document_name, sequence_number = 1) AS primary_document,
            anyIf(document_url, sequence_number = 1) AS primary_document_url,
            anyIf(document_type, sequence_number = 1) AS primary_document_type,
            any(source_archive_member) AS source_archive_member,
            min(source_archive_date) AS source_archive_date,
            sum(byte_size) AS filing_size,
            count() AS document_rows
        FROM {qi(database)}.sec_filing_document_v2 FINAL
        WHERE (cik, accession_number) IN (SELECT cik, accession_number FROM missing)
        GROUP BY cik, accession_number
    )
SELECT
    if(
        ifNull(d.document_filing_id, '') != '',
        d.document_filing_id,
        concat('sec-filing-v2-xbrl-parent:', fa.cik, ':', replaceAll(fa.accession_number, '-', ''))
    ) AS filing_id,
    fa.accession_number,
    if(
        ifNull(d.accession_number_compact, '') != '',
        d.accession_number_compact,
        replaceAll(fa.accession_number, '-', '')
    ) AS accession_number_compact,
    fa.cik,
    CAST(NULL, 'Nullable(String)') AS issuer_id,
    CAST(NULL, 'Nullable(String)') AS company_name,
    if(fa.form_type != '', fa.form_type, ifNull(d.primary_document_type, '')) AS form_type,
    toDate(fa.filed_at_utc) AS filing_date,
    fa.report_date AS report_date,
    toDateTime64(fa.filed_at_utc, 9, 'UTC') AS accepted_at_utc,
    CAST(NULL, 'Nullable(String)') AS acceptance_datetime_raw,
    'xbrl_companyfacts_filed_at' AS accepted_at_source,
    nullIf(d.primary_document, '') AS primary_document,
    nullIf(d.primary_document_url, '') AS primary_document_url,
    concat('https://www.sec.gov/Archives/edgar/data/', toString(toUInt64OrZero(fa.cik)), '/', replaceAll(fa.accession_number, '-', ''), '/') AS filing_detail_url,
    if(ifNull(d.source_archive_member, '') != '', d.source_archive_member, 'sec_xbrl_company_fact_v1') AS source_file_name,
    CAST(ifNull(d.filing_size, 0), 'Nullable(UInt64)') AS filing_size,
    CAST(NULL, 'Nullable(String)') AS items,
    if(ifNull(d.document_rows, 0) > 0, 'archive_text_extracted', 'xbrl_parent_only') AS text_status,
    repair_run_id AS source_run_id,
    lower(hex(SHA256(concat('sec-xbrl-parent-repair|', fa.cik, '|', fa.accession_number, '|', toString(fa.filed_at_utc), '|', toString(fa.fact_rows))))) AS source_content_sha256,
    repair_inserted_at AS inserted_at
FROM fact_agg AS fa
LEFT JOIN doc_agg AS d
ON fa.cik = d.cik AND fa.accession_number = d.accession_number
"""


def frame_parent_insert_sql(database: str, scope_start_date: str, run_id: str) -> str:
    return f"""
INSERT INTO {qi(database)}.sec_xbrl_frame_v1
(
    frame_id,
    taxonomy,
    tag,
    unit_code,
    calendar_period_code,
    recorded_at_utc,
    source_run_id,
    source_content_sha256,
    inserted_at
)
WITH
    {sql_string(run_id)} AS repair_run_id,
    now64(3, 'UTC') AS repair_inserted_at
SELECT
    any(scoped_obs.frame_id) AS frame_id,
    scoped_obs.taxonomy,
    scoped_obs.tag,
    scoped_obs.unit_code,
    scoped_obs.calendar_period_code,
    max(scoped_obs.recorded_at_utc) AS recorded_at_utc,
    repair_run_id AS source_run_id,
    lower(hex(SHA256(concat('sec-xbrl-frame-parent-repair|', scoped_obs.taxonomy, '|', scoped_obs.tag, '|', scoped_obs.unit_code, '|', scoped_obs.calendar_period_code)))) AS source_content_sha256,
    repair_inserted_at AS inserted_at
FROM (
    SELECT o.frame_id, o.taxonomy, o.tag, o.unit_code, o.calendar_period_code, o.cik, o.accession_number, o.period_end_date, o.recorded_at_utc
    FROM (
        SELECT frame_id, taxonomy, tag, unit_code, calendar_period_code, cik, accession_number, period_end_date, recorded_at_utc
        FROM {qi(database)}.sec_xbrl_frame_observation_v1 FINAL
    ) AS o
    INNER JOIN (
        SELECT cik, accession_number, taxonomy, tag, unit_code, period_end_date
        FROM {qi(database)}.sec_xbrl_company_fact_v1 FINAL
        WHERE accession_number IS NOT NULL
          AND accession_number != ''
          AND filed_at_utc >= toDateTime64({sql_string(scope_start_date + " 00:00:00")}, 3, 'UTC')
    ) AS x
    ON o.cik = x.cik
       AND o.accession_number = x.accession_number
       AND o.taxonomy = x.taxonomy
       AND o.tag = x.tag
       AND o.unit_code = x.unit_code
       AND o.period_end_date = x.period_end_date
) AS scoped_obs
LEFT ANTI JOIN (SELECT taxonomy, tag, unit_code, calendar_period_code FROM {qi(database)}.sec_xbrl_frame_v1 FINAL) AS fr
ON scoped_obs.taxonomy = fr.taxonomy
   AND scoped_obs.tag = fr.tag
   AND scoped_obs.unit_code = fr.unit_code
   AND scoped_obs.calendar_period_code = fr.calendar_period_code
GROUP BY scoped_obs.taxonomy, scoped_obs.tag, scoped_obs.unit_code, scoped_obs.calendar_period_code
"""


def require_tables(client: ClickHouseHttpClient, database: str) -> None:
    required = {
        "sec_filing_v2",
        "sec_filing_document_v2",
        "sec_xbrl_company_fact_v1",
        "sec_xbrl_frame_v1",
        "sec_xbrl_frame_observation_v1",
    }
    rows = client.execute(
        f"""
        SELECT name
        FROM system.tables
        WHERE database = {sql_string(database)}
          AND name IN ({','.join(sql_string(table) for table in sorted(required))})
        FORMAT TSV
        """
    )
    present = {line.strip() for line in rows.splitlines() if line.strip()}
    missing = sorted(required - present)
    if missing:
        raise SystemExit(f"missing required SEC tables in {database}: {missing}")


def table_exists(client: ClickHouseHttpClient, database: str, table: str) -> bool:
    out = client.execute(
        f"""
        SELECT count()
        FROM system.tables
        WHERE database = {sql_string(database)}
          AND name = {sql_string(table)}
        FORMAT TSV
        """
    ).strip()
    return int(out or "0") > 0


def timed_execute(client: ClickHouseHttpClient, sql: str, *, query_id: str) -> None:
    started = time.perf_counter()
    client.execute(sql, query_id=query_id)
    print(f"[query] query_id={query_id} elapsed={time.perf_counter() - started:.1f}s", flush=True)


def query_one_json(client: ClickHouseHttpClient, sql: str) -> dict[str, Any]:
    out = client.execute(sql).strip()
    if not out:
        return {}
    return json.loads(out.splitlines()[0])


def parse_stages(raw: str) -> list[str]:
    valid = {"drop-legacy", "filing-parents", "frame-parents"}
    stages = [item.strip() for item in raw.split(",") if item.strip()]
    invalid = sorted(set(stages) - valid)
    if invalid:
        raise SystemExit(f"invalid --stages values: {invalid}; expected subset of {sorted(valid)}")
    return stages


def validate_identifier(value: str, label: str) -> None:
    if not value or not all(char.isalnum() or char == "_" for char in value):
        raise SystemExit(f"{label} must contain only letters, numbers, and underscores: {value!r}")


def validate_date(value: str, label: str) -> None:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise SystemExit(f"{label} must be YYYY-MM-DD: {value!r}") from exc


def qi(value: str) -> str:
    return quote_ident(value)


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str) + "\n")


def write_markdown(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# SEC XBRL Integrity Repair",
        "",
        f"- Database: `{summary['database']}`",
        f"- Scope start: `{summary['scope_start_date']}`",
        f"- Execute: `{summary['execute']}`",
        f"- Status: `{summary['status']}`",
        f"- Elapsed seconds: `{summary['elapsed_seconds']}`",
        "",
        "| Stage | Status | Details |",
        "| --- | --- | --- |",
    ]
    for event in summary["events"]:
        details = json.dumps(event.get("details", {}), sort_keys=True, default=str)
        if len(details) > 700:
            details = details[:700] + "...<truncated>"
        lines.append(f"| `{event['stage']}` | `{event['status']}` | `{details}` |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

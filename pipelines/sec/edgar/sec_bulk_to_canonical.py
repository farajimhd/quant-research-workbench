from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time as dt_time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines.sec.edgar.sec_pipeline.clickhouse_writer import (  # noqa: E402
    FILING_TABLE,
    XBRL_COMPANY_FACT_TABLE,
    XBRL_CONCEPT_TABLE,
    XBRL_FRAME_OBSERVATION_TABLE,
    XBRL_FRAME_TABLE,
    WRITE_TABLES,
    ensure_sec_write_database,
)
from research.mlops.clickhouse import ClickHouseHttpClient, default_clickhouse_password, default_clickhouse_url, default_clickhouse_user, quote_ident, sql_string  # noqa: E402
from research.mlops.env import discover_env_files, load_env_files, secret_status  # noqa: E402


DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_bulk_to_canonical")
SEC_CORE_DATABASE = "sec_core"
DEFAULT_TARGET_DATABASE = "q_live"


@dataclass(frozen=True, slots=True)
class StageProfile:
    stage: str
    status: str
    before_rows: int
    after_rows: int
    inserted_delta: int
    elapsed_seconds: float
    error: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build canonical SEC parent and XBRL rows from the SEC bulk mirror. "
            "This is the bulk-first bridge from sec_core to the configured SEC write database."
        )
    )
    parser.add_argument("--source-database", default=os.environ.get("SEC_BULK_MIRROR_DATABASE", SEC_CORE_DATABASE))
    parser.add_argument("--schema-source-database", default=os.environ.get("SEC_CLICKHOUSE_READ_DATABASE", "q_live"))
    parser.add_argument("--target-database", default=os.environ.get("SEC_CLICKHOUSE_WRITE_DATABASE", os.environ.get("SEC_GATEWAY_WRITE_DATABASE", DEFAULT_TARGET_DATABASE)))
    parser.add_argument("--start-date", required=True, help="Inclusive accepted/filed date, YYYY-MM-DD.")
    parser.add_argument("--end-date", required=True, help="Exclusive accepted/filed date, YYYY-MM-DD.")
    parser.add_argument("--stages", default="parents,xbrl", help="Comma-separated subset of parents,xbrl.")
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--output-root-win", default=os.environ.get("SEC_BULK_CANONICAL_OUTPUT_ROOT_WIN", str(DEFAULT_OUTPUT_ROOT_WIN)))
    parser.add_argument(
        "--max-partitions-per-insert-block",
        type=int,
        default=int(os.environ.get("SEC_BULK_CANONICAL_MAX_PARTITIONS_PER_INSERT_BLOCK", "10000")),
        help="ClickHouse insert setting for wide historical XBRL rebuilds. 0 leaves the server default unchanged.",
    )
    parser.add_argument("--execute", action="store_true")
    return parser.parse_args()


def main() -> None:
    loaded_env = load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args()
    start_date = parse_date(args.start_date)
    end_date = parse_date(args.end_date)
    if start_date < date(2019, 1, 1):
        raise SystemExit("--start-date must be >= 2019-01-01")
    if end_date <= start_date:
        raise SystemExit("--end-date must be later than --start-date")
    stages = parse_stages(args.stages)
    run_id = "sec_bulk_to_canonical_" + datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    run_root = Path(args.output_root_win) / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    manifest_path = run_root / "sec_bulk_to_canonical_manifest.json"
    events_path = run_root / "sec_bulk_to_canonical_events.jsonl"

    manifest = {
        "run_id": run_id,
        "created_at_utc": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "execute": bool(args.execute),
        "source_database": args.source_database,
        "schema_source_database": args.schema_source_database,
        "target_database": args.target_database,
        "start_date": args.start_date,
        "end_date": args.end_date,
        "stages": stages,
        "max_partitions_per_insert_block": int(args.max_partitions_per_insert_block),
        "loaded_env_files": [str(path) for path in loaded_env],
        "secret_status": secret_status(["REAL_LIVE_CLICKHOUSE_WRITE_URL", "REAL_LIVE_CLICKHOUSE_WRITE_USER", "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD"]),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    print("=" * 96, flush=True)
    print("SEC bulk to canonical", flush=True)
    print(f"execute={args.execute} source={args.source_database} target={args.target_database}", flush=True)
    print(f"range=[{args.start_date},{args.end_date}) stages={','.join(stages)}", flush=True)
    print(f"run_root={run_root}", flush=True)
    print("=" * 96, flush=True)
    if not args.execute:
        for stage in stages:
            print(f"dry_run_stage={stage}", flush=True)
        return

    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    ensure_sec_write_database(client, read_database=args.schema_source_database, write_database=args.target_database)
    validate_tables(client, args.source_database, args.target_database)
    profiles: list[StageProfile] = []
    insert_settings = insert_settings_sql(args)
    if "parents" in stages:
        profiles.append(run_insert_stage(client, "parents", args.target_database, FILING_TABLE, parent_insert_sql(args, run_id), insert_settings))
        append_jsonl(events_path, asdict(profiles[-1]))
    if "xbrl" in stages:
        for stage, table, sql in [
            ("xbrl_concepts", XBRL_CONCEPT_TABLE, xbrl_concept_insert_sql(args, run_id)),
            ("xbrl_company_facts", XBRL_COMPANY_FACT_TABLE, xbrl_company_fact_insert_sql(args, run_id)),
            ("xbrl_frames", XBRL_FRAME_TABLE, xbrl_frame_insert_sql(args, run_id)),
            ("xbrl_frame_observations", XBRL_FRAME_OBSERVATION_TABLE, xbrl_frame_observation_insert_sql(args, run_id)),
        ]:
            profiles.append(run_insert_stage(client, stage, args.target_database, table, sql, insert_settings))
            append_jsonl(events_path, asdict(profiles[-1]))
    summary = {"run_id": run_id, "profiles": [asdict(item) for item in profiles], "status": "ok"}
    (run_root / "sec_bulk_to_canonical_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print("summary=" + json.dumps(summary, sort_keys=True), flush=True)


def validate_tables(client: ClickHouseHttpClient, source_database: str, target_database: str) -> None:
    required_source = {
        "sec_bulk_mirror_filing_v3",
        "sec_bulk_mirror_xbrl_fact_v3",
    }
    required_target = set(WRITE_TABLES)
    source_missing = missing_tables(client, source_database, required_source)
    target_missing = missing_tables(client, target_database, required_target)
    if source_missing or target_missing:
        raise RuntimeError(f"missing source tables={source_missing} target tables={target_missing}")


def missing_tables(client: ClickHouseHttpClient, database: str, required: set[str]) -> list[str]:
    out = client.execute(
        f"""
        SELECT name
        FROM system.tables
        WHERE database = {sql_string(database)}
          AND name IN ({','.join(sql_string(item) for item in sorted(required))})
        FORMAT TSV
        """
    )
    present = {line.strip() for line in out.splitlines() if line.strip()}
    return sorted(required - present)


def run_insert_stage(client: ClickHouseHttpClient, stage: str, database: str, table: str, sql: str, insert_settings: str = "") -> StageProfile:
    started = time.perf_counter()
    before = table_rows(client, database, table)
    try:
        client.execute(apply_insert_settings(sql, insert_settings))
        after = table_rows(client, database, table)
        profile = StageProfile(stage=stage, status="ok", before_rows=before, after_rows=after, inserted_delta=max(0, after - before), elapsed_seconds=round(time.perf_counter() - started, 3))
    except Exception as exc:  # noqa: BLE001
        profile = StageProfile(stage=stage, status="failed", before_rows=before, after_rows=before, inserted_delta=0, elapsed_seconds=round(time.perf_counter() - started, 3), error=repr(exc))
    print(json.dumps(asdict(profile), sort_keys=True), flush=True)
    if profile.status != "ok":
        raise RuntimeError(profile.error)
    return profile


def insert_settings_sql(args: argparse.Namespace) -> str:
    settings: list[str] = []
    if int(args.max_partitions_per_insert_block) > 0:
        settings.append(f"max_partitions_per_insert_block = {int(args.max_partitions_per_insert_block)}")
    return "SETTINGS " + ", ".join(settings) if settings else ""


def apply_insert_settings(sql: str, insert_settings: str) -> str:
    if not insert_settings:
        return sql
    lines = sql.splitlines()
    for index, line in enumerate(lines):
        if line.strip().upper().startswith("INSERT INTO "):
            lines.insert(index + 1, "    " + insert_settings)
            return "\n".join(lines)
    return sql


def table_rows(client: ClickHouseHttpClient, database: str, table: str) -> int:
    out = client.execute(f"SELECT count() FROM {quote_ident(database)}.{quote_ident(table)} FINAL FORMAT TSV").strip()
    return int(out or "0")


def parent_insert_sql(args: argparse.Namespace, run_id: str) -> str:
    source = quote_ident(args.source_database)
    target = quote_ident(args.target_database)
    start = start_dt(args.start_date)
    end = start_dt(args.end_date)
    run = sql_string(run_id)
    return f"""
    INSERT INTO {target}.{quote_ident(FILING_TABLE)}
    SELECT
        lower(hex(SHA256(concat('sec-filing-v2-submissions-bulk|', cik, '|', accession_number)))) AS filing_id,
        accession_number,
        accession_number_compact,
        cik,
        CAST(NULL, 'Nullable(String)') AS issuer_id,
        nullIf(company_name, '') AS company_name,
        form_type,
        filing_date,
        report_date,
        accepted_at_utc,
        acceptance_datetime_raw,
        ifNull(nullIf(accepted_at_source, ''), 'submissions_bulk') AS accepted_at_source,
        primary_document,
        primary_document_url,
        filing_detail_url,
        primary_document AS source_file_name,
        filing_size,
        items,
        'submissions_bulk_parent' AS text_status,
        {run} AS source_run_id,
        lower(hex(SHA256(concat('sec-bulk-submission|', cik, '|', accession_number, '|', raw_submission_json)))) AS source_content_sha256,
        now64(3, 'UTC') AS inserted_at
    FROM {source}.sec_bulk_mirror_filing_v3 FINAL
    WHERE accepted_at_utc >= toDateTime64({sql_string(start)}, 9, 'UTC')
      AND accepted_at_utc < toDateTime64({sql_string(end)}, 9, 'UTC')
      AND accession_number != ''
      AND cik != ''
    """


def xbrl_concept_insert_sql(args: argparse.Namespace, run_id: str) -> str:
    source = quote_ident(args.source_database)
    target = quote_ident(args.target_database)
    run = sql_string(run_id)
    return f"""
    INSERT INTO {target}.{quote_ident(XBRL_CONCEPT_TABLE)}
    SELECT
        lower(hex(SHA256(concat('sec-xbrl-concept|', taxonomy, '|', tag)))) AS concept_id,
        taxonomy,
        tag,
        anyLast(label) AS concept_label,
        anyLast(description) AS concept_description,
        min(toDateTime64(ifNull(filed_date, toDate('1970-01-01')), 3, 'UTC')) AS first_observed_at_utc,
        max(toDateTime64(ifNull(filed_date, toDate('1970-01-01')), 3, 'UTC')) AS last_observed_at_utc,
        {run} AS source_run_id,
        lower(hex(SHA256(concat('sec-core-xbrl-concept|', taxonomy, '|', tag)))) AS source_content_sha256,
        now64(3, 'UTC') AS inserted_at
    FROM {source}.sec_bulk_mirror_xbrl_fact_v3 FINAL
    WHERE filed_date >= toDate({sql_string(args.start_date)})
      AND filed_date < toDate({sql_string(args.end_date)})
      AND taxonomy != ''
      AND tag != ''
    GROUP BY taxonomy, tag
    """


def xbrl_company_fact_insert_sql(args: argparse.Namespace, run_id: str) -> str:
    source = quote_ident(args.source_database)
    target = quote_ident(args.target_database)
    run = sql_string(run_id)
    return f"""
    INSERT INTO {target}.{quote_ident(XBRL_COMPANY_FACT_TABLE)}
    SELECT
        lower(hex(SHA256(concat('sec-xbrl-company-fact|', cik, '|', taxonomy, '|', tag, '|', unit, '|', ifNull(toString(start_date), ''), '|', ifNull(toString(end_date), ''), '|', ifNull(accession_number, ''), '|', ifNull(frame, ''))))) AS company_fact_id,
        CAST(NULL, 'Nullable(String)') AS issuer_id,
        cik,
        taxonomy,
        tag,
        unit AS unit_code,
        fy AS fiscal_year,
        fp AS fiscal_period,
        toDateTime64(ifNull(filed_date, toDate('1970-01-01')), 3, 'UTC') AS filed_at_utc,
        end_date AS period_end_date,
        value,
        form_type,
        accession_number,
        now64(3, 'UTC') AS recorded_at_utc,
        {run} AS source_run_id,
        lower(hex(SHA256(concat('sec-core-xbrl-fact|', fact_id)))) AS source_content_sha256,
        now64(3, 'UTC') AS inserted_at
    FROM {source}.sec_bulk_mirror_xbrl_fact_v3 FINAL
    WHERE filed_date >= toDate({sql_string(args.start_date)})
      AND filed_date < toDate({sql_string(args.end_date)})
      AND value IS NOT NULL
      AND end_date IS NOT NULL
      AND accession_number IS NOT NULL
      AND accession_number != ''
    """


def xbrl_frame_insert_sql(args: argparse.Namespace, run_id: str) -> str:
    source = quote_ident(args.source_database)
    target = quote_ident(args.target_database)
    run = sql_string(run_id)
    return f"""
    INSERT INTO {target}.{quote_ident(XBRL_FRAME_TABLE)}
    SELECT
        lower(hex(SHA256(concat('sec-xbrl-frame|', taxonomy, '|', tag, '|', unit, '|', frame)))) AS frame_id,
        taxonomy,
        tag,
        unit AS unit_code,
        frame AS calendar_period_code,
        now64(3, 'UTC') AS recorded_at_utc,
        {run} AS source_run_id,
        lower(hex(SHA256(concat('sec-core-xbrl-frame|', taxonomy, '|', tag, '|', unit, '|', frame)))) AS source_content_sha256,
        now64(3, 'UTC') AS inserted_at
    FROM {source}.sec_bulk_mirror_xbrl_fact_v3 FINAL
    WHERE filed_date >= toDate({sql_string(args.start_date)})
      AND filed_date < toDate({sql_string(args.end_date)})
      AND frame IS NOT NULL
      AND frame != ''
    GROUP BY taxonomy, tag, unit, frame
    """


def xbrl_frame_observation_insert_sql(args: argparse.Namespace, run_id: str) -> str:
    source = quote_ident(args.source_database)
    target = quote_ident(args.target_database)
    run = sql_string(run_id)
    return f"""
    INSERT INTO {target}.{quote_ident(XBRL_FRAME_OBSERVATION_TABLE)}
    SELECT
        lower(hex(SHA256(concat('sec-xbrl-frame-observation|', taxonomy, '|', tag, '|', unit, '|', frame, '|', cik, '|', ifNull(accession_number, ''), '|', ifNull(toString(end_date), ''))))) AS frame_observation_id,
        lower(hex(SHA256(concat('sec-xbrl-frame|', taxonomy, '|', tag, '|', unit, '|', frame)))) AS frame_id,
        taxonomy,
        tag,
        unit AS unit_code,
        frame AS calendar_period_code,
        CAST(NULL, 'Nullable(String)') AS issuer_id,
        cik,
        entity_name,
        CAST(NULL, 'Nullable(String)') AS location_code,
        end_date AS period_end_date,
        value,
        accession_number,
        now64(3, 'UTC') AS recorded_at_utc,
        {run} AS source_run_id,
        lower(hex(SHA256(concat('sec-core-xbrl-frame-observation|', fact_id)))) AS source_content_sha256,
        now64(3, 'UTC') AS inserted_at
    FROM {source}.sec_bulk_mirror_xbrl_fact_v3 FINAL
    WHERE filed_date >= toDate({sql_string(args.start_date)})
      AND filed_date < toDate({sql_string(args.end_date)})
      AND frame IS NOT NULL
      AND frame != ''
      AND value IS NOT NULL
      AND end_date IS NOT NULL
      AND accession_number IS NOT NULL
      AND accession_number != ''
    """


def parse_stages(value: str) -> list[str]:
    stages = [item.strip().lower() for item in value.split(",") if item.strip()]
    valid = {"parents", "xbrl"}
    invalid = sorted(set(stages) - valid)
    if invalid:
        raise SystemExit(f"invalid --stages values: {invalid}")
    return stages or ["parents", "xbrl"]


def start_dt(value: str) -> str:
    parsed = datetime.combine(parse_date(value), dt_time.min, tzinfo=UTC)
    return parsed.strftime("%Y-%m-%d %H:%M:%S")


def parse_date(value: str) -> date:
    return date.fromisoformat(value[:10])


def append_jsonl(path: Path, row: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, default=str, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()

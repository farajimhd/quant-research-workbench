from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists() and (parent / "pipelines").exists())
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines.market_sip.events.clickhouse_build_sec_context import (  # noqa: E402
    create_filing_context_table_sql,
    create_text_context_table_sql,
    text_context_columns_sql,
    text_context_schema_migration_sqls,
)
from pipelines.sec.edgar.sec_pipeline.text_renderer import (  # noqa: E402
    SEC_PACKED_TEXT_RENDERER_VERSION,
    build_sec_text_context_row,
)
from research.mlops.clickhouse import (  # noqa: E402
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_user,
    default_storage_policy,
    discover_clickhouse_env_files,
    parse_size_bytes,
    quote_ident,
    sql_string,
)
from research.mlops.env import load_env_files, secret_status  # noqa: E402
from pipelines.market_sip.validation.clickhouse_delete_compact_audit_rows import default_clickhouse_url_with_network_fallback  # noqa: E402


DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_acceptance_downstream_repair")


@dataclass(frozen=True, slots=True)
class RepairSummary:
    run_id: str
    execute: bool
    source_database: str
    context_database: str
    target_database: str
    candidate_rows: int
    bad_parent_rows: int
    stale_context_rows: int
    stale_text_context_rows: int
    stale_token_rows: int
    stale_embedding_rows: int
    stale_coverage_rows: int
    rebuilt_filing_context_rows: int
    rebuilt_text_context_rows: int
    min_bad_accepted_at_utc: str
    max_bad_accepted_at_utc: str
    min_expected_accepted_at_utc: str
    max_expected_accepted_at_utc: str
    wall_seconds: float
    run_root: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Invalidate and rebuild SEC downstream rows whose accepted_at_utc/timestamp_us no longer "
            "matches q_live.sec_filing_v3 FINAL after an acceptance timestamp repair. Dry-run is default."
        )
    )
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url_with_network_fallback())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--source-database", default="q_live")
    parser.add_argument("--context-database", default="market_sip_compact")
    parser.add_argument("--target-database", default="market_sip_compact")
    parser.add_argument("--sec-filing-table", default="sec_filing_v3")
    parser.add_argument("--sec-bridge-table", default="id_sec_market_bridge_v3")
    parser.add_argument("--sec-text-source-table", default="sec_filing_text_v3")
    parser.add_argument("--filing-context-table", default="sec_filing_context_v3")
    parser.add_argument("--text-context-table", default="sec_filing_text_context_v3")
    parser.add_argument("--sec-token-table", default="sec_filing_text_tokens_v3")
    parser.add_argument("--sec-embedding-table", default="sec_filing_text_embeddings_v3")
    parser.add_argument("--coverage-table", default="text_embedding_coverage_v1")
    parser.add_argument("--accepted-at-sources", default="submissions_recent,submissions_recent_timezone_repair")
    parser.add_argument("--lookback-hours", type=float, default=168.0)
    parser.add_argument("--start-inserted-at", default="", help="Inclusive sec_filing_v3 inserted_at lower bound, UTC.")
    parser.add_argument("--end-inserted-at", default="", help="Exclusive sec_filing_v3 inserted_at upper bound, UTC.")
    parser.add_argument("--min-abs-shift-hours", type=float, default=3.0)
    parser.add_argument("--max-abs-shift-hours", type=float, default=6.0)
    parser.add_argument("--output-root-win", default=str(DEFAULT_OUTPUT_ROOT_WIN))
    parser.add_argument("--storage-policy", default=default_storage_policy())
    parser.add_argument("--text-prefix-chars", type=int, default=0, help="Deprecated no-op. SEC text context now stores full text.")
    parser.add_argument("--max-text-rows-per-filing", type=int, default=0, help="Deprecated no-op. SEC text context now stores every text row.")
    parser.add_argument("--sec-text-buckets", type=int, default=64)
    parser.add_argument("--render-batch-rows", type=int, default=256)
    parser.add_argument("--max-threads", type=int, default=8)
    parser.add_argument("--max-memory-usage", default="16G")
    parser.add_argument("--wait-mutations", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--mutation-timeout-seconds", type=int, default=900)
    parser.add_argument("--execute", action="store_true")
    return parser.parse_args()


def main() -> int:
    started = time.perf_counter()
    loaded_env_files = load_env_files(discover_clickhouse_env_files(), verbose=True)
    args = parse_args()
    validate_args(args)
    run_id = f"sec_acceptance_downstream_repair_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    run_root = Path(args.output_root_win) / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    report_jsonl = run_root / "sec_acceptance_downstream_repair_steps.jsonl"
    summary_json = run_root / "sec_acceptance_downstream_repair_summary.json"
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)
    inserted_start, inserted_end = inserted_window(args)
    stage_database = args.target_database
    stage_table = f"_tmp_sec_acceptance_downstream_candidate_{run_id}"

    print_header(args, run_id, run_root, inserted_start, inserted_end, loaded_env_files)
    if args.execute:
        ensure_context_tables(client, args, report_jsonl)
    candidate_summary = query_one(client, candidate_summary_sql(args, inserted_start, inserted_end))
    candidate_rows = int(candidate_summary.get("candidate_rows", 0) or 0)
    write_jsonl(report_jsonl, {"step": "candidate_summary", "status": "ok", **candidate_summary})
    print(
        "candidates="
        f"{candidate_rows:,} bad_parent_rows={int(candidate_summary.get('bad_parent_rows', 0) or 0):,} "
        f"stale_context={int(candidate_summary.get('stale_context_rows', 0) or 0):,} "
        f"stale_text_context={int(candidate_summary.get('stale_text_context_rows', 0) or 0):,} "
        f"stale_tokens={int(candidate_summary.get('stale_token_rows', 0) or 0):,} "
        f"stale_embeddings={int(candidate_summary.get('stale_embedding_rows', 0) or 0):,} "
        f"stale_coverage={int(candidate_summary.get('stale_coverage_rows', 0) or 0):,}",
        flush=True,
    )

    if candidate_rows <= 0:
        summary = build_summary(args, run_id, run_root, candidate_summary, {}, started)
        summary_json.write_text(json.dumps(asdict(summary), indent=2, sort_keys=True), encoding="utf-8")
        print(f"summary={summary_json}", flush=True)
        return 0
    if args.execute and int(candidate_summary.get("bad_parent_rows", 0) or 0) > 0:
        raise SystemExit(
            "Refusing downstream repair while sec_filing_v3 FINAL still has bad parent timestamps. "
            "Run sec_acceptance_timezone_repair.py --execute first, then rerun this script."
        )

    action_counts: dict[str, int] = {}
    if not args.execute:
        for label, sql in delete_sqls(args, inserted_start, inserted_end):
            run_sql(client, label, sql, report_jsonl, execute=False)
        preview = " ".join(
            part.strip()
            for part in corrected_text_context_source_sql(args, candidate_cte_body(args, inserted_start, inserted_end), limit=max(1, int(args.render_batch_rows))).strip().splitlines()
            if part.strip()
        )
        write_jsonl(report_jsonl, {"step": "insert_corrected_sec_text_context", "status": "dry_run", "sql_preview": preview[:4000], "renderer": SEC_PACKED_TEXT_RENDERER_VERSION})
        print(f"DRY RUN insert_corrected_sec_text_context: {preview[:500]}", flush=True)
        print("dry_run=True; downstream rows were not deleted and context was not rebuilt.", flush=True)
    else:
        try:
            run_sql(client, "create_candidate_stage", create_candidate_stage_sql(stage_database, stage_table), report_jsonl, execute=True)
            run_sql(
                client,
                "load_candidate_stage",
                load_candidate_stage_sql(args, inserted_start, inserted_end, stage_database, stage_table),
                report_jsonl,
                execute=True,
            )
            for label, sql in delete_sqls_for_stage(args, stage_database, stage_table):
                run_sql(client, label, sql, report_jsonl, execute=True)
                if args.wait_mutations:
                    wait_for_mutations(client, target_database_for_label(args, label), table_for_label(args, label), int(args.mutation_timeout_seconds), report_jsonl)
            for label, sql in rebuild_filing_context_sqls_for_stage(args, stage_database, stage_table):
                run_sql(client, label, sql, report_jsonl, execute=True)
            action_counts["rebuilt_text_context_rows"] = rebuild_text_context_for_stage(client, args, stage_database, stage_table, report_jsonl)
            rebuilt = query_one(client, rebuilt_summary_sql_for_stage(args, stage_database, stage_table))
            action_counts.update({f"rebuilt_{key}": int(value or 0) for key, value in rebuilt.items() if str(value).isdigit()})
            run_sql(client, "drop_candidate_stage", drop_candidate_stage_sql(stage_database, stage_table), report_jsonl, execute=True)
        except Exception:
            write_jsonl(
                report_jsonl,
                {
                    "step": "candidate_stage_retained",
                    "status": "retained_for_debug",
                    "database": stage_database,
                    "table": stage_table,
                },
            )
            print(f"candidate_stage_retained={stage_database}.{stage_table}", flush=True)
            raise

    summary = build_summary(args, run_id, run_root, candidate_summary, action_counts, started)
    summary_json.write_text(json.dumps(asdict(summary), indent=2, sort_keys=True), encoding="utf-8")
    print("=" * 96, flush=True)
    print(f"summary={summary_json}", flush=True)
    print(json.dumps(asdict(summary), sort_keys=True), flush=True)
    print("=" * 96, flush=True)
    return 0


def validate_args(args: argparse.Namespace) -> None:
    for name in (
        "source_database",
        "context_database",
        "target_database",
        "sec_filing_table",
        "sec_bridge_table",
        "sec_text_source_table",
        "filing_context_table",
        "text_context_table",
        "sec_token_table",
        "sec_embedding_table",
        "coverage_table",
    ):
        validate_identifier(getattr(args, name), f"--{name.replace('_', '-')}")
    if args.lookback_hours <= 0:
        raise SystemExit("--lookback-hours must be positive")
    if args.max_abs_shift_hours <= args.min_abs_shift_hours:
        raise SystemExit("--max-abs-shift-hours must be greater than --min-abs-shift-hours")


def validate_identifier(value: str, label: str) -> None:
    if not value or not value.replace("_", "").isalnum() or value[0].isdigit():
        raise SystemExit(f"{label} must be a simple ClickHouse identifier")


def inserted_window(args: argparse.Namespace) -> tuple[datetime, datetime]:
    end = parse_dt(args.end_inserted_at) if args.end_inserted_at.strip() else datetime.now(UTC) + timedelta(minutes=5)
    start = parse_dt(args.start_inserted_at) if args.start_inserted_at.strip() else end - timedelta(hours=float(args.lookback_hours))
    return start, end


def parse_dt(text: str) -> datetime:
    parsed = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def print_header(args: argparse.Namespace, run_id: str, run_root: Path, inserted_start: datetime, inserted_end: datetime, loaded_env_files: list[Path]) -> None:
    print("=" * 96, flush=True)
    print("SEC acceptance downstream repair", flush=True)
    print(f"run_id={run_id} execute={args.execute}", flush=True)
    print(f"source={args.source_database} context={args.context_database} target={args.target_database}", flush=True)
    print(f"inserted_at_window={dt_text(inserted_start)} -> {dt_text(inserted_end)}", flush=True)
    print(f"accepted_at_sources={args.accepted_at_sources}", flush=True)
    print(f"run_root={run_root}", flush=True)
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    print(
        "secret_status="
        f"{secret_status(['REAL_LIVE_CLICKHOUSE_WRITE_URL', 'REAL_LIVE_CLICKHOUSE_WRITE_USER', 'REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD', 'CLICKHOUSE_WORKSTATION_USER', 'CLICKHOUSE_WORKSTATION_PASSWORD'])}",
        flush=True,
    )
    print("=" * 96, flush=True)


def ensure_context_tables(client: ClickHouseHttpClient, args: argparse.Namespace, report_jsonl: Path) -> None:
    statements = [
        create_filing_context_table_sql(args.context_database, args.filing_context_table, args.storage_policy),
        create_text_context_table_sql(args.context_database, args.text_context_table, args.storage_policy),
        *text_context_schema_migration_sqls(args.context_database, args.text_context_table),
    ]
    for index, statement in enumerate(statements, 1):
        run_sql(client, f"ensure_context_schema_{index}", statement, report_jsonl, execute=True)


def candidate_cte(args: argparse.Namespace, inserted_start: datetime, inserted_end: datetime) -> str:
    return f"WITH\n{candidate_cte_body(args, inserted_start, inserted_end)}"


def candidate_cte_body(args: argparse.Namespace, inserted_start: datetime, inserted_end: datetime) -> str:
    source_db = quote_ident(args.source_database)
    context_db = quote_ident(args.context_database)
    target_db = quote_ident(args.target_database)
    sources = ", ".join(sql_string(value) for value in csv_values(args.accepted_at_sources))
    min_shift = int(float(args.min_abs_shift_hours) * 3600)
    max_shift = int(float(args.max_abs_shift_hours) * 3600)
    return f"""
parent AS
(
    SELECT
        cik,
        accession_number,
        accepted_at_utc AS current_accepted_at_utc,
        toUInt64(toUnixTimestamp64Micro(accepted_at_utc)) AS current_timestamp_us,
        accepted_at_source,
        acceptance_datetime_raw,
        inserted_at
    FROM {source_db}.{quote_ident(args.sec_filing_table)} FINAL
    WHERE accepted_at_source IN ({sources})
      AND inserted_at >= {dt64_sql(inserted_start, 3)}
      AND inserted_at < {dt64_sql(inserted_end, 3)}
),
raw_bad_parent AS
(
    SELECT
        cik,
        accession_number,
        current_accepted_at_utc AS bad_accepted_at_utc,
        current_timestamp_us AS bad_timestamp_us,
        toDateTime64(replaceRegexpOne(acceptance_datetime_raw, 'Z$', ''), 9, 'UTC') AS expected_accepted_at_utc,
        toUInt64(toUnixTimestamp64Micro(toDateTime64(replaceRegexpOne(acceptance_datetime_raw, 'Z$', ''), 9, 'UTC'))) AS expected_timestamp_us,
        'bad_parent_explicit_offset' AS reason
    FROM parent
    WHERE endsWith(acceptance_datetime_raw, 'Z')
      AND abs(toUnixTimestamp64Second(toDateTime64(replaceRegexpOne(acceptance_datetime_raw, 'Z$', ''), 3, 'UTC')) - toUnixTimestamp64Second(current_accepted_at_utc)) BETWEEN {min_shift} AND {max_shift}
),
context_stale AS
(
    SELECT
        c.cik,
        c.accession_number,
        c.accepted_at_utc AS bad_accepted_at_utc,
        c.timestamp_us AS bad_timestamp_us,
        p.current_accepted_at_utc AS expected_accepted_at_utc,
        p.current_timestamp_us AS expected_timestamp_us,
        'stale_filing_context' AS reason
    FROM {context_db}.{quote_ident(args.filing_context_table)} AS c
    INNER JOIN parent AS p
        ON p.cik = c.cik
       AND p.accession_number = c.accession_number
    WHERE c.timestamp_us != p.current_timestamp_us
       OR c.accepted_at_utc != p.current_accepted_at_utc
),
text_context_stale AS
(
    SELECT
        c.cik,
        c.accession_number,
        c.accepted_at_utc AS bad_accepted_at_utc,
        c.timestamp_us AS bad_timestamp_us,
        p.current_accepted_at_utc AS expected_accepted_at_utc,
        p.current_timestamp_us AS expected_timestamp_us,
        'stale_text_context' AS reason
    FROM {context_db}.{quote_ident(args.text_context_table)} AS c
    INNER JOIN parent AS p
        ON p.cik = c.cik
       AND p.accession_number = c.accession_number
    WHERE c.timestamp_us != p.current_timestamp_us
       OR c.accepted_at_utc != p.current_accepted_at_utc
),
token_stale AS
(
    SELECT
        t.cik,
        t.accession_number,
        t.accepted_at_utc AS bad_accepted_at_utc,
        t.timestamp_us AS bad_timestamp_us,
        p.current_accepted_at_utc AS expected_accepted_at_utc,
        p.current_timestamp_us AS expected_timestamp_us,
        'stale_token' AS reason
    FROM {target_db}.{quote_ident(args.sec_token_table)} AS t
    INNER JOIN parent AS p
        ON p.cik = t.cik
       AND p.accession_number = t.accession_number
    WHERE t.timestamp_us != p.current_timestamp_us
       OR t.accepted_at_utc != p.current_accepted_at_utc
),
embedding_stale AS
(
    SELECT
        e.cik,
        e.accession_number,
        e.accepted_at_utc AS bad_accepted_at_utc,
        e.timestamp_us AS bad_timestamp_us,
        p.current_accepted_at_utc AS expected_accepted_at_utc,
        p.current_timestamp_us AS expected_timestamp_us,
        'stale_embedding' AS reason
    FROM {target_db}.{quote_ident(args.sec_embedding_table)} AS e
    INNER JOIN parent AS p
        ON p.cik = e.cik
       AND p.accession_number = e.accession_number
    WHERE e.timestamp_us != p.current_timestamp_us
       OR e.accepted_at_utc != p.current_accepted_at_utc
),
candidate AS
(
    SELECT * FROM raw_bad_parent
    UNION DISTINCT SELECT * FROM context_stale
    UNION DISTINCT SELECT * FROM text_context_stale
    UNION DISTINCT SELECT * FROM token_stale
    UNION DISTINCT SELECT * FROM embedding_stale
)
"""


def candidate_summary_sql(args: argparse.Namespace, inserted_start: datetime, inserted_end: datetime) -> str:
    return f"""
{candidate_cte(args, inserted_start, inserted_end)}
SELECT
    uniqExact(tuple(cik, accession_number, bad_timestamp_us)) AS candidate_rows,
    countIf(reason = 'bad_parent_explicit_offset') AS bad_parent_rows,
    (SELECT count() FROM {quote_ident(args.context_database)}.{quote_ident(args.filing_context_table)} AS c INNER JOIN candidate AS x ON c.cik=x.cik AND c.accession_number=x.accession_number AND c.timestamp_us=x.bad_timestamp_us) AS stale_context_rows,
    (SELECT count() FROM {quote_ident(args.context_database)}.{quote_ident(args.text_context_table)} AS c INNER JOIN candidate AS x ON c.cik=x.cik AND c.accession_number=x.accession_number AND c.timestamp_us=x.bad_timestamp_us) AS stale_text_context_rows,
    (SELECT count() FROM {quote_ident(args.target_database)}.{quote_ident(args.sec_token_table)} AS t INNER JOIN candidate AS x ON t.cik=x.cik AND t.accession_number=x.accession_number AND t.timestamp_us=x.bad_timestamp_us) AS stale_token_rows,
    (SELECT count() FROM {quote_ident(args.target_database)}.{quote_ident(args.sec_embedding_table)} AS e INNER JOIN candidate AS x ON e.cik=x.cik AND e.accession_number=x.accession_number AND e.timestamp_us=x.bad_timestamp_us) AS stale_embedding_rows,
    (SELECT count() FROM {quote_ident(args.target_database)}.{quote_ident(args.coverage_table)} AS cov INNER JOIN candidate AS x ON cov.source='sec' AND cov.timestamp_us=x.bad_timestamp_us AND startsWith(cov.source_id, concat(x.accession_number, ':'))) AS stale_coverage_rows,
    min(bad_accepted_at_utc) AS min_bad_accepted_at_utc,
    max(bad_accepted_at_utc) AS max_bad_accepted_at_utc,
    min(expected_accepted_at_utc) AS min_expected_accepted_at_utc,
    max(expected_accepted_at_utc) AS max_expected_accepted_at_utc
FROM candidate
FORMAT JSONEachRow
"""


def create_candidate_stage_sql(database: str, table: str) -> str:
    return f"""
DROP TABLE IF EXISTS {quote_ident(database)}.{quote_ident(table)}
"""


def load_candidate_stage_sql(args: argparse.Namespace, inserted_start: datetime, inserted_end: datetime, database: str, table: str) -> str:
    return f"""
CREATE TABLE {quote_ident(database)}.{quote_ident(table)}
ENGINE = Memory
AS
{candidate_cte(args, inserted_start, inserted_end)}
SELECT DISTINCT
    cik,
    accession_number,
    bad_accepted_at_utc,
    bad_timestamp_us,
    expected_accepted_at_utc,
    expected_timestamp_us,
    reason
FROM candidate
{query_settings(args)}
"""


def drop_candidate_stage_sql(database: str, table: str) -> str:
    return f"DROP TABLE IF EXISTS {quote_ident(database)}.{quote_ident(table)}"


def delete_sqls(args: argparse.Namespace, inserted_start: datetime, inserted_end: datetime) -> list[tuple[str, str]]:
    cte = candidate_cte(args, inserted_start, inserted_end)
    return [
        (
            "delete_sec_filing_context_stale",
            f"""
ALTER TABLE {quote_ident(args.context_database)}.{quote_ident(args.filing_context_table)}
DELETE WHERE (cik, accession_number, timestamp_us) IN
(
    {cte}
    SELECT cik, accession_number, bad_timestamp_us FROM candidate
)
""",
        ),
        (
            "delete_sec_filing_text_context_stale",
            f"""
ALTER TABLE {quote_ident(args.context_database)}.{quote_ident(args.text_context_table)}
DELETE WHERE (cik, accession_number, timestamp_us) IN
(
    {cte}
    SELECT cik, accession_number, bad_timestamp_us FROM candidate
)
""",
        ),
        (
            "delete_sec_tokens_stale",
            f"""
ALTER TABLE {quote_ident(args.target_database)}.{quote_ident(args.sec_token_table)}
DELETE WHERE (cik, accession_number, timestamp_us) IN
(
    {cte}
    SELECT cik, accession_number, bad_timestamp_us FROM candidate
)
""",
        ),
        (
            "delete_sec_embeddings_stale",
            f"""
ALTER TABLE {quote_ident(args.target_database)}.{quote_ident(args.sec_embedding_table)}
DELETE WHERE (cik, accession_number, timestamp_us) IN
(
    {cte}
    SELECT cik, accession_number, bad_timestamp_us FROM candidate
)
""",
        ),
        (
            "delete_sec_coverage_stale",
            f"""
ALTER TABLE {quote_ident(args.target_database)}.{quote_ident(args.coverage_table)}
DELETE WHERE source = 'sec'
  AND (timestamp_us, splitByChar(':', source_id)[1]) IN
(
    {cte}
    SELECT bad_timestamp_us, accession_number
    FROM candidate
)
""",
        ),
    ]


def delete_sqls_for_stage(args: argparse.Namespace, stage_database: str, stage_table: str) -> list[tuple[str, str]]:
    stage = f"{quote_ident(stage_database)}.{quote_ident(stage_table)}"
    return [
        (
            "delete_sec_filing_context_stale",
            f"""
ALTER TABLE {quote_ident(args.context_database)}.{quote_ident(args.filing_context_table)}
DELETE WHERE (cik, accession_number, timestamp_us) IN
(
    SELECT cik, accession_number, bad_timestamp_us FROM {stage}
)
""",
        ),
        (
            "delete_sec_filing_text_context_stale",
            f"""
ALTER TABLE {quote_ident(args.context_database)}.{quote_ident(args.text_context_table)}
DELETE WHERE (cik, accession_number, timestamp_us) IN
(
    SELECT cik, accession_number, bad_timestamp_us FROM {stage}
)
""",
        ),
        (
            "delete_sec_tokens_stale",
            f"""
ALTER TABLE {quote_ident(args.target_database)}.{quote_ident(args.sec_token_table)}
DELETE WHERE (cik, accession_number, timestamp_us) IN
(
    SELECT cik, accession_number, bad_timestamp_us FROM {stage}
)
""",
        ),
        (
            "delete_sec_embeddings_stale",
            f"""
ALTER TABLE {quote_ident(args.target_database)}.{quote_ident(args.sec_embedding_table)}
DELETE WHERE (cik, accession_number, timestamp_us) IN
(
    SELECT cik, accession_number, bad_timestamp_us FROM {stage}
)
""",
        ),
        (
            "delete_sec_coverage_stale",
            f"""
ALTER TABLE {quote_ident(args.target_database)}.{quote_ident(args.coverage_table)}
DELETE WHERE source = 'sec'
  AND (timestamp_us, splitByChar(':', source_id)[1]) IN
(
    SELECT bad_timestamp_us, accession_number
    FROM {stage}
)
""",
        ),
    ]


def rebuild_filing_context_sqls(args: argparse.Namespace, inserted_start: datetime, inserted_end: datetime) -> list[tuple[str, str]]:
    cte_body = candidate_cte_body(args, inserted_start, inserted_end)
    return [("insert_corrected_sec_filing_context_v3", insert_corrected_filing_context_sql(args, cte_body))]


def rebuild_filing_context_sqls_for_stage(args: argparse.Namespace, stage_database: str, stage_table: str) -> list[tuple[str, str]]:
    return [("insert_corrected_sec_filing_context_v3", insert_corrected_filing_context_sql_for_stage(args, stage_database, stage_table))]


def insert_corrected_filing_context_sql(args: argparse.Namespace, cte_body: str) -> str:
    source_db = quote_ident(args.source_database)
    target = f"{quote_ident(args.context_database)}.{quote_ident(args.filing_context_table)}"
    return f"""
INSERT INTO {target}
WITH
{bridge_cte_sql(args)},
{cte_body}
SELECT DISTINCT
    b.ticker AS ticker,
    toUInt64(toUnixTimestamp64Micro(f.accepted_at_utc)) AS timestamp_us,
    f.accepted_at_utc AS accepted_at_utc,
    f.cik AS cik,
    f.accession_number AS accession_number,
    ifNull(f.form_type, '') AS form_type,
    ifNull(f.accepted_at_source, '') AS accepted_at_source,
    toFloat32(b.confidence_score) AS mapping_confidence,
    b.bridge_id AS bridge_id,
    b.security_id AS security_id,
    b.listing_id AS listing_id,
    b.symbol_id AS symbol_id,
    toString(f.filing_id) AS filing_id,
    ifNull(f.company_name, '') AS company_name,
    ifNull(f.primary_document, '') AS primary_document,
    ifNull(f.primary_document_url, '') AS primary_document_url,
    ifNull(f.filing_detail_url, '') AS filing_detail_url,
    ifNull(f.items, '') AS items,
    now64(3, 'UTC') AS updated_at
FROM {source_db}.{quote_ident(args.sec_filing_table)} AS f FINAL
INNER JOIN candidate AS x
    ON x.cik = f.cik
   AND x.accession_number = f.accession_number
INNER JOIN bridge AS b
    ON b.cik = f.cik
WHERE f.accepted_at_utc IS NOT NULL
  AND (b.accession_number = '' OR b.accession_number = f.accession_number)
  AND (b.valid_from_date IS NULL OR b.valid_from_date <= toDate(f.accepted_at_utc))
  AND (b.valid_to_date_exclusive IS NULL OR b.valid_to_date_exclusive > toDate(f.accepted_at_utc))
{query_settings(args)}
"""


def insert_corrected_filing_context_sql_for_stage(args: argparse.Namespace, stage_database: str, stage_table: str) -> str:
    source_db = quote_ident(args.source_database)
    stage = f"{quote_ident(stage_database)}.{quote_ident(stage_table)}"
    target = f"{quote_ident(args.context_database)}.{quote_ident(args.filing_context_table)}"
    return f"""
INSERT INTO {target}
WITH
{bridge_cte_sql(args)}
SELECT DISTINCT
    b.ticker AS ticker,
    toUInt64(toUnixTimestamp64Micro(f.accepted_at_utc)) AS timestamp_us,
    f.accepted_at_utc AS accepted_at_utc,
    f.cik AS cik,
    f.accession_number AS accession_number,
    ifNull(f.form_type, '') AS form_type,
    ifNull(f.accepted_at_source, '') AS accepted_at_source,
    toFloat32(b.confidence_score) AS mapping_confidence,
    b.bridge_id AS bridge_id,
    b.security_id AS security_id,
    b.listing_id AS listing_id,
    b.symbol_id AS symbol_id,
    toString(f.filing_id) AS filing_id,
    ifNull(f.company_name, '') AS company_name,
    ifNull(f.primary_document, '') AS primary_document,
    ifNull(f.primary_document_url, '') AS primary_document_url,
    ifNull(f.filing_detail_url, '') AS filing_detail_url,
    ifNull(f.items, '') AS items,
    now64(3, 'UTC') AS updated_at
FROM {source_db}.{quote_ident(args.sec_filing_table)} AS f FINAL
INNER JOIN {stage} AS x
    ON x.cik = f.cik
   AND x.accession_number = f.accession_number
INNER JOIN bridge AS b
    ON b.cik = f.cik
WHERE f.accepted_at_utc IS NOT NULL
  AND (b.accession_number = '' OR b.accession_number = f.accession_number)
  AND (b.valid_from_date IS NULL OR b.valid_from_date <= toDate(f.accepted_at_utc))
  AND (b.valid_to_date_exclusive IS NULL OR b.valid_to_date_exclusive > toDate(f.accepted_at_utc))
{query_settings(args)}
"""


def rebuild_text_context_for_stage(client: ClickHouseHttpClient, args: argparse.Namespace, stage_database: str, stage_table: str, report_jsonl: Path) -> int:
    target = f"{quote_ident(args.context_database)}.{quote_ident(args.text_context_table)}"
    total_inserted = 0
    batch = 0
    limit = max(1, int(args.render_batch_rows))
    while True:
        rows = query_json_rows(client, corrected_text_context_source_sql_for_stage(args, stage_database, stage_table, limit=limit))
        if not rows:
            break
        batch += 1
        updated_at = utc_now_clickhouse_text()
        context_rows = [build_sec_text_context_row(row, updated_at=updated_at) for row in rows]
        insert_json_each_row(client, target, context_rows)
        total_inserted += len(context_rows)
        write_jsonl(
            report_jsonl,
            {
                "step": "insert_corrected_sec_text_context",
                "status": "ok",
                "batch": batch,
                "source_rows": len(rows),
                "inserted_rows": len(context_rows),
                "renderer": SEC_PACKED_TEXT_RENDERER_VERSION,
            },
        )
        print(f"DONE insert_corrected_sec_text_context batch={batch} rows={len(context_rows):,}", flush=True)
        if len(rows) < limit:
            break
    write_jsonl(report_jsonl, {"step": "insert_corrected_sec_text_context", "status": "done", "inserted_rows": total_inserted, "renderer": SEC_PACKED_TEXT_RENDERER_VERSION})
    return total_inserted


def corrected_text_context_source_sql(args: argparse.Namespace, cte_body: str, *, limit: int) -> str:
    source_db = quote_ident(args.source_database)
    filing_context = f"{quote_ident(args.context_database)}.{quote_ident(args.filing_context_table)}"
    return f"""
WITH
{cte_body}
SELECT
    f.ticker AS ticker,
    f.timestamp_us AS timestamp_us,
    f.accepted_at_utc AS accepted_at_utc,
    f.cik AS cik,
    f.accession_number AS accession_number,
    f.form_type AS form_type,
    toUInt8(least(toUInt32(ifNull(t.sequence_number, 0)), 255)) AS text_rank,
    ifNull(t.document_id, '') AS document_id,
    ifNull(t.text_kind, '') AS text_kind,
    toUInt32(ifNull(t.sequence_number, 0)) AS sequence_number,
    ifNull(t.document_name, '') AS document_name,
    ifNull(t.document_type, '') AS document_type,
    ifNull(t.document_role, '') AS document_role,
    ifNull(t.content_format, '') AS content_format,
    ifNull(t.source_text, '') AS source_text,
    toUInt32(least(toUInt64(ifNull(t.source_text_char_count, lengthUTF8(ifNull(t.source_text, '')))), toUInt64(4294967295))) AS source_text_char_count,
    cityHash64(ifNull(t.source_text, '')) AS source_text_hash,
    '' AS quality_flags
FROM {filing_context} AS f
INNER JOIN candidate AS x
    ON x.cik = f.cik
   AND x.accession_number = f.accession_number
   AND x.expected_timestamp_us = f.timestamp_us
INNER JOIN {source_db}.{quote_ident(args.sec_text_source_table)} AS t FINAL
    ON t.cik = f.cik
   AND t.accession_number = f.accession_number
ORDER BY ticker, accepted_at_utc, accession_number, text_rank, document_id
LIMIT {max(1, int(limit))}
{query_settings(args)}
FORMAT JSONEachRow
"""


def corrected_text_context_source_sql_for_stage(args: argparse.Namespace, stage_database: str, stage_table: str, *, limit: int) -> str:
    source_db = quote_ident(args.source_database)
    stage = f"{quote_ident(stage_database)}.{quote_ident(stage_table)}"
    filing_context = f"{quote_ident(args.context_database)}.{quote_ident(args.filing_context_table)}"
    return f"""
SELECT
    f.ticker AS ticker,
    f.timestamp_us AS timestamp_us,
    f.accepted_at_utc AS accepted_at_utc,
    f.cik AS cik,
    f.accession_number AS accession_number,
    f.form_type AS form_type,
    toUInt8(least(toUInt32(ifNull(t.sequence_number, 0)), 255)) AS text_rank,
    ifNull(t.document_id, '') AS document_id,
    ifNull(t.text_kind, '') AS text_kind,
    toUInt32(ifNull(t.sequence_number, 0)) AS sequence_number,
    ifNull(t.document_name, '') AS document_name,
    ifNull(t.document_type, '') AS document_type,
    ifNull(t.document_role, '') AS document_role,
    ifNull(t.content_format, '') AS content_format,
    ifNull(t.source_text, '') AS source_text,
    toUInt32(least(toUInt64(ifNull(t.source_text_char_count, lengthUTF8(ifNull(t.source_text, '')))), toUInt64(4294967295))) AS source_text_char_count,
    cityHash64(ifNull(t.source_text, '')) AS source_text_hash,
    '' AS quality_flags
FROM {filing_context} AS f
INNER JOIN {stage} AS x
    ON x.cik = f.cik
   AND x.accession_number = f.accession_number
   AND x.expected_timestamp_us = f.timestamp_us
INNER JOIN {source_db}.{quote_ident(args.sec_text_source_table)} AS t FINAL
    ON t.cik = f.cik
   AND t.accession_number = f.accession_number
LEFT JOIN {quote_ident(args.context_database)}.{quote_ident(args.text_context_table)} AS existing FINAL
    ON existing.ticker = f.ticker
   AND existing.timestamp_us = f.timestamp_us
   AND existing.accession_number = f.accession_number
   AND existing.text_rank = toUInt8(least(toUInt32(ifNull(t.sequence_number, 0)), 255))
   AND existing.document_id = ifNull(t.document_id, '')
WHERE existing.document_id = ''
ORDER BY ticker, accepted_at_utc, accession_number, text_rank, document_id
LIMIT {max(1, int(limit))}
{query_settings(args)}
FORMAT JSONEachRow
"""


def bridge_cte_sql(args: argparse.Namespace) -> str:
    source_db = quote_ident(args.source_database)
    return f"""
bridge AS
(
    SELECT
        ifNull(ticker, '') AS ticker,
        cik,
        ifNull(accession_number, '') AS accession_number,
        valid_from_date,
        valid_to_date_exclusive,
        any(bridge_id) AS bridge_id,
        any(ifNull(security_id, '')) AS security_id,
        any(ifNull(listing_id, '')) AS listing_id,
        any(ifNull(symbol_id, '')) AS symbol_id,
        max(confidence_score) AS confidence_score
    FROM {source_db}.{quote_ident(args.sec_bridge_table)}
    WHERE ifNull(ticker, '') != ''
      AND mapping_status IN ('active', 'mapped', 'accepted', '')
    GROUP BY ticker, cik, accession_number, valid_from_date, valid_to_date_exclusive
)
"""


def rebuilt_summary_sql(args: argparse.Namespace, inserted_start: datetime, inserted_end: datetime) -> str:
    return f"""
{candidate_cte(args, inserted_start, inserted_end)}
SELECT
    (SELECT count() FROM {quote_ident(args.context_database)}.{quote_ident(args.filing_context_table)} AS c INNER JOIN candidate AS x ON c.cik=x.cik AND c.accession_number=x.accession_number AND c.timestamp_us=x.expected_timestamp_us) AS filing_context_rows,
    (SELECT count() FROM {quote_ident(args.context_database)}.{quote_ident(args.text_context_table)} AS c INNER JOIN candidate AS x ON c.cik=x.cik AND c.accession_number=x.accession_number AND c.timestamp_us=x.expected_timestamp_us) AS text_context_rows
FORMAT JSONEachRow
"""


def rebuilt_summary_sql_for_stage(args: argparse.Namespace, stage_database: str, stage_table: str) -> str:
    stage = f"{quote_ident(stage_database)}.{quote_ident(stage_table)}"
    return f"""
SELECT
    (SELECT count() FROM {quote_ident(args.context_database)}.{quote_ident(args.filing_context_table)} AS c INNER JOIN {stage} AS x ON c.cik=x.cik AND c.accession_number=x.accession_number AND c.timestamp_us=x.expected_timestamp_us) AS filing_context_rows,
    (SELECT count() FROM {quote_ident(args.context_database)}.{quote_ident(args.text_context_table)} AS c INNER JOIN {stage} AS x ON c.cik=x.cik AND c.accession_number=x.accession_number AND c.timestamp_us=x.expected_timestamp_us) AS text_context_rows
FORMAT JSONEachRow
"""


def target_database_for_label(args: argparse.Namespace, label: str) -> str:
    if "context" in label and "tokens" not in label and "embeddings" not in label and "coverage" not in label:
        return args.context_database
    return args.target_database


def table_for_label(args: argparse.Namespace, label: str) -> str:
    if label == "delete_sec_filing_context_stale":
        return args.filing_context_table
    if label == "delete_sec_filing_text_context_stale":
        return args.text_context_table
    if label == "delete_sec_tokens_stale":
        return args.sec_token_table
    if label == "delete_sec_embeddings_stale":
        return args.sec_embedding_table
    if label == "delete_sec_coverage_stale":
        return args.coverage_table
    raise ValueError(label)


def run_sql(client: ClickHouseHttpClient, label: str, sql: str, report_jsonl: Path, *, execute: bool) -> None:
    preview = " ".join(part.strip() for part in sql.strip().splitlines() if part.strip())
    started = time.perf_counter()
    if not execute:
        print(f"DRY RUN {label}: {preview[:500]}", flush=True)
        write_jsonl(report_jsonl, {"step": label, "status": "dry_run", "sql_preview": preview[:4000]})
        return
    print(f"START {label}", flush=True)
    try:
        client.execute(sql)
    except Exception as exc:
        seconds = time.perf_counter() - started
        write_jsonl(report_jsonl, {"step": label, "status": "failed", "seconds": round(seconds, 3), "error": repr(exc)})
        print(f"FAILED {label}: {exc!r}", flush=True)
        raise
    seconds = time.perf_counter() - started
    write_jsonl(report_jsonl, {"step": label, "status": "ok", "seconds": round(seconds, 3)})
    print(f"DONE {label} seconds={seconds:.2f}", flush=True)


def wait_for_mutations(client: ClickHouseHttpClient, database: str, table: str, timeout_seconds: int, report_jsonl: Path) -> None:
    deadline = time.perf_counter() + float(timeout_seconds)
    while True:
        sql = f"""
SELECT count()
FROM system.mutations
WHERE database = {sql_string(database)}
  AND table = {sql_string(table)}
  AND is_done = 0
FORMAT TSV
"""
        pending = int((client.execute(sql).strip() or "0").splitlines()[0])
        if pending == 0:
            write_jsonl(report_jsonl, {"step": "wait_mutations", "database": database, "table": table, "status": "ok", "pending": 0})
            print(f"MUTATIONS DONE {database}.{table}", flush=True)
            return
        if time.perf_counter() >= deadline:
            raise TimeoutError(f"Timed out waiting for mutations on {database}.{table}; pending={pending}")
        print(f"MUTATIONS WAIT {database}.{table} pending={pending}", flush=True)
        time.sleep(2.0)


def query_one(client: ClickHouseHttpClient, sql: str) -> dict[str, object]:
    text = client.execute(sql).strip()
    if not text:
        return {}
    return json.loads(text.splitlines()[0])


def query_json_rows(client: ClickHouseHttpClient, sql: str) -> list[dict[str, Any]]:
    text = client.execute(sql).strip()
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def insert_json_each_row(client: ClickHouseHttpClient, target: str, rows: list[dict[str, Any]]) -> None:
    payload = "\n".join(json.dumps(row, separators=(",", ":"), ensure_ascii=False) for row in rows)
    if payload:
        client.execute(f"INSERT INTO {target} ({text_context_columns_sql()}) SETTINGS date_time_input_format = 'best_effort' FORMAT JSONEachRow\n{payload}")


def build_summary(args: argparse.Namespace, run_id: str, run_root: Path, candidate_summary: dict[str, object], action_counts: dict[str, int], started: float) -> RepairSummary:
    return RepairSummary(
        run_id=run_id,
        execute=bool(args.execute),
        source_database=args.source_database,
        context_database=args.context_database,
        target_database=args.target_database,
        candidate_rows=int(candidate_summary.get("candidate_rows", 0) or 0),
        bad_parent_rows=int(candidate_summary.get("bad_parent_rows", 0) or 0),
        stale_context_rows=int(candidate_summary.get("stale_context_rows", 0) or 0),
        stale_text_context_rows=int(candidate_summary.get("stale_text_context_rows", 0) or 0),
        stale_token_rows=int(candidate_summary.get("stale_token_rows", 0) or 0),
        stale_embedding_rows=int(candidate_summary.get("stale_embedding_rows", 0) or 0),
        stale_coverage_rows=int(candidate_summary.get("stale_coverage_rows", 0) or 0),
        rebuilt_filing_context_rows=int(action_counts.get("rebuilt_filing_context_rows", 0) or 0),
        rebuilt_text_context_rows=int(action_counts.get("rebuilt_text_context_rows", 0) or 0),
        min_bad_accepted_at_utc=str(candidate_summary.get("min_bad_accepted_at_utc", "") or ""),
        max_bad_accepted_at_utc=str(candidate_summary.get("max_bad_accepted_at_utc", "") or ""),
        min_expected_accepted_at_utc=str(candidate_summary.get("min_expected_accepted_at_utc", "") or ""),
        max_expected_accepted_at_utc=str(candidate_summary.get("max_expected_accepted_at_utc", "") or ""),
        wall_seconds=round(time.perf_counter() - started, 3),
        run_root=str(run_root),
    )


def csv_values(text: str) -> list[str]:
    return [item.strip() for item in text.split(",") if item.strip()]


def dt64_sql(value: datetime, precision: int = 9) -> str:
    return f"toDateTime64({sql_string(dt_text(value, precision=3))}, {precision}, 'UTC')"


def dt_text(value: datetime, *, precision: int = 3) -> str:
    value = value.astimezone(UTC)
    if precision <= 3:
        return value.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    return value.strftime("%Y-%m-%d %H:%M:%S.%f")


def utc_now_clickhouse_text() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def query_settings(args: argparse.Namespace) -> str:
    settings: list[str] = []
    if int(args.max_threads) > 0:
        settings.append(f"max_threads = {int(args.max_threads)}")
    if str(args.max_memory_usage).strip():
        settings.append(f"max_memory_usage = {parse_size_bytes(str(args.max_memory_usage))}")
    return "\nSETTINGS " + ", ".join(settings) if settings else ""


def write_jsonl(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())

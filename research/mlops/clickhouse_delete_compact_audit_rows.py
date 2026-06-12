from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.env import load_env_files, secret_status  # noqa: E402
from research.mlops.clickhouse_ingest_sip_compact_codec import (  # noqa: E402
    DEFAULT_DATABASE,
    DEFAULT_MANIFEST_TABLE,
    STATUS_PRIORITY_SQL,
    ensure_manifest_columns,
)
from research.mlops.clickhouse_ingest_sip_flatfiles import (  # noqa: E402
    CLICKHOUSE_ENDPOINT_ENV,
    CLICKHOUSE_PASSWORD_ENV,
    CLICKHOUSE_PASSWORD_SIMPLE_ENV,
    CLICKHOUSE_URL_ENV,
    CLICKHOUSE_USER_ENV,
    CLICKHOUSE_USER_SIMPLE_ENV,
    CLICKHOUSE_WORKSTATION_PASSWORD_ENV,
    CLICKHOUSE_WORKSTATION_USER_ENV,
    DEFAULT_OUTPUT_ROOT_WIN,
    ClickHouseHttpClient,
    QueryProfile,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    discover_clickhouse_env_files,
    parse_kinds,
    quote_ident,
    run_profiled,
    sql_string,
)


DELETE_AUDIT_STATUSES = ("should_delete", "orphan_should_delete")


@dataclass(frozen=True, slots=True)
class DeleteCandidate:
    kind: str
    source_date: str
    source_file: str
    source_path_ch: str
    file_bytes: int
    target_table: str
    expected_rows: int
    expected_min_sip_timestamp: int
    expected_max_sip_timestamp: int
    actual_rows: int
    status: str
    audit_status: str
    audit_run_id: str
    audit_actual_rows: int
    audit_note: str

    @property
    def key(self) -> str:
        return f"{self.kind}|{self.source_date}|{self.source_file}|{self.target_table}"

    @property
    def min_us(self) -> int:
        return self.expected_min_sip_timestamp // 1000

    @property
    def max_us(self) -> int:
        return self.expected_max_sip_timestamp // 1000


@dataclass(frozen=True, slots=True)
class DeleteDecision:
    key: str
    kind: str
    source_date: str
    source_file: str
    target_table: str
    audit_status: str
    expected_rows: int
    audit_actual_rows: int
    rows_to_delete: int
    min_us: int
    max_us: int
    min_event_date: str
    max_event_date: str
    action: str
    note: str


def default_clickhouse_url_with_network_fallback() -> str:
    return (
        os.environ.get("REAL_LIVE_CLICKHOUSE_WRITE_URL")
        or os.environ.get(CLICKHOUSE_URL_ENV)
        or os.environ.get(CLICKHOUSE_ENDPOINT_ENV)
        or os.environ.get("REAL_LIVE_CLICKHOUSE_READ_URL")
        or default_clickhouse_url()
    )


def env_status_keys() -> list[str]:
    return [
        CLICKHOUSE_URL_ENV,
        "REAL_LIVE_CLICKHOUSE_WRITE_URL",
        "REAL_LIVE_CLICKHOUSE_WRITE_USER",
        "REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD",
        CLICKHOUSE_WORKSTATION_USER_ENV,
        CLICKHOUSE_WORKSTATION_PASSWORD_ENV,
        CLICKHOUSE_USER_SIMPLE_ENV,
        CLICKHOUSE_PASSWORD_SIMPLE_ENV,
        CLICKHOUSE_ENDPOINT_ENV,
        CLICKHOUSE_USER_ENV,
        CLICKHOUSE_PASSWORD_ENV,
        "REAL_LIVE_CLICKHOUSE_READ_URL",
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Delete compact SIP quote/trade rows that were marked by manifest audit labels."
    )
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url_with_network_fallback())
    parser.add_argument("--user", default=default_clickhouse_user())
    parser.add_argument("--password", default=default_clickhouse_password())
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--manifest-table", default=DEFAULT_MANIFEST_TABLE)
    parser.add_argument("--quote-table", default="quotes")
    parser.add_argument("--trade-table", default="trades")
    parser.add_argument("--kinds", default="quotes,trades")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--audit-statuses", default=",".join(DELETE_AUDIT_STATUSES))
    parser.add_argument("--apply", action="store_true", help="Run ALTER TABLE DELETE and append deleted audit rows.")
    parser.add_argument("--mutations-sync", type=int, default=1, choices=[0, 1, 2])
    parser.add_argument("--max-candidates", type=int, default=0, help="Debug limit. 0 means all candidates.")
    parser.add_argument("--output-root-win", default=str(DEFAULT_OUTPUT_ROOT_WIN / "compact_audit_deletes"))
    return parser.parse_args()


def date_from_us(timestamp_us: int) -> date:
    return datetime.fromtimestamp(timestamp_us / 1_000_000, tz=UTC).date()


def load_delete_candidates(
    client: ClickHouseHttpClient,
    database: str,
    manifest_table: str,
    kinds: list[str],
    start_date: str,
    end_date: str,
    audit_statuses: list[str],
) -> list[DeleteCandidate]:
    query = f"""
SELECT
    kind,
    toString(source_date),
    source_file,
    source_path_ch,
    file_bytes,
    target_table,
    expected_rows,
    expected_min_sip_timestamp,
    expected_max_sip_timestamp,
    actual_rows,
    status,
    audit_status,
    audit_run_id,
    audit_actual_rows,
    audit_note
FROM
(
    SELECT *
    FROM {quote_ident(database)}.{quote_ident(manifest_table)}
    WHERE source_date BETWEEN toDate({sql_string(start_date)}) AND toDate({sql_string(end_date)})
      AND kind IN ({", ".join(sql_string(kind) for kind in kinds)})
    ORDER BY kind, source_date, source_file, target_table, updated_at DESC, {STATUS_PRIORITY_SQL} DESC
    LIMIT 1 BY kind, source_date, source_file, target_table
)
WHERE audit_status IN ({", ".join(sql_string(status) for status in audit_statuses)})
ORDER BY kind, source_date, source_file, target_table
"""
    candidates: list[DeleteCandidate] = []
    for line in client.query_tsv(query).splitlines():
        values = line.split("\t")
        if len(values) < 15:
            values.extend([""] * (15 - len(values)))
        candidates.append(
            DeleteCandidate(
                kind=values[0],
                source_date=values[1],
                source_file=values[2],
                source_path_ch=values[3],
                file_bytes=int(values[4] or 0),
                target_table=values[5],
                expected_rows=int(values[6] or 0),
                expected_min_sip_timestamp=int(values[7] or 0),
                expected_max_sip_timestamp=int(values[8] or 0),
                actual_rows=int(values[9] or 0),
                status=values[10],
                audit_status=values[11],
                audit_run_id=values[12],
                audit_actual_rows=int(values[13] or 0),
                audit_note=values[14],
            )
        )
    return candidates


def count_rows_in_range(client: ClickHouseHttpClient, database: str, candidate: DeleteCandidate) -> tuple[int, str, str]:
    min_event_date = date_from_us(candidate.min_us).isoformat()
    max_event_date = date_from_us(candidate.max_us).isoformat()
    rows = client.query_tsv(
        f"""
SELECT count()
FROM {quote_ident(database)}.{quote_ident(candidate.target_table)}
WHERE event_date BETWEEN toDate({sql_string(min_event_date)}) AND toDate({sql_string(max_event_date)})
  AND sip_timestamp_us BETWEEN {int(candidate.min_us)} AND {int(candidate.max_us)}
"""
    ).strip()
    return int(rows or 0), min_event_date, max_event_date


def delete_rows_in_range(
    client: ClickHouseHttpClient,
    database: str,
    candidate: DeleteCandidate,
    *,
    min_event_date: str,
    max_event_date: str,
    mutations_sync: int,
) -> QueryProfile:
    sql = f"""
ALTER TABLE {quote_ident(database)}.{quote_ident(candidate.target_table)} DELETE
WHERE event_date BETWEEN toDate({sql_string(min_event_date)}) AND toDate({sql_string(max_event_date)})
  AND sip_timestamp_us BETWEEN {int(candidate.min_us)} AND {int(candidate.max_us)}
SETTINGS mutations_sync = {int(mutations_sync)}
"""
    return run_profiled(client, f"compact_audit_delete_{candidate.kind}_{candidate.source_date}", sql)


def insert_deleted_audit_row(
    client: ClickHouseHttpClient,
    database: str,
    manifest_table: str,
    candidate: DeleteCandidate,
    *,
    audit_run_id: str,
    rows_deleted: int,
    profile: QueryProfile | None,
    note: str,
) -> None:
    profile = profile or QueryProfile(label="", query_id="", wall_seconds=0.0)
    query = f"""
INSERT INTO {quote_ident(database)}.{quote_ident(manifest_table)}
(
    kind, source_date, source_file, source_path_ch, file_bytes, target_table,
    expected_rows, expected_min_sip_timestamp, expected_max_sip_timestamp, actual_rows,
    status, run_id, query_id, wall_seconds, query_duration_ms, memory_usage_bytes,
    read_rows, read_bytes, written_rows, written_bytes, exception,
    audit_status, audit_run_id, audit_actual_rows, audit_note, audit_checked_at
)
VALUES
(
    {sql_string(candidate.kind)},
    toDate({sql_string(candidate.source_date)}),
    {sql_string(candidate.source_file)},
    {sql_string(candidate.source_path_ch)},
    {int(candidate.file_bytes)},
    {sql_string(candidate.target_table)},
    {int(candidate.expected_rows)},
    {int(candidate.expected_min_sip_timestamp)},
    {int(candidate.expected_max_sip_timestamp)},
    {int(rows_deleted)},
    'discovered',
    {sql_string(audit_run_id)},
    {sql_string(profile.query_id or '')},
    {float(profile.wall_seconds or 0.0)},
    {int(profile.query_duration_ms or 0)},
    {int(profile.memory_usage_bytes or 0)},
    {int(profile.read_rows or 0)},
    {int(profile.read_bytes or 0)},
    {int(profile.written_rows or 0)},
    {int(profile.written_bytes or 0)},
    {sql_string(profile.exception or '')},
    'deleted',
    {sql_string(audit_run_id)},
    {int(rows_deleted)},
    {sql_string(note)},
    now()
)
"""
    client.execute(query)


def append_jsonl(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def main() -> None:
    loaded_env_files = load_env_files(discover_clickhouse_env_files(), verbose=True)
    args = parse_args()
    kinds = parse_kinds(args.kinds)
    audit_statuses = [item.strip() for item in args.audit_statuses.split(",") if item.strip()]
    if not audit_statuses:
        raise SystemExit("--audit-statuses resolved to an empty list")
    run_id = "compact_audit_delete_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = Path(args.output_root_win) / f"{run_id}.jsonl"
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)

    print("=" * 96, flush=True)
    print("Compact SIP audit delete", flush=True)
    print(f"database={args.database} manifest_table={args.manifest_table}", flush=True)
    print(f"kinds={kinds} start_date={args.start_date} end_date={args.end_date}", flush=True)
    print(
        f"audit_statuses={audit_statuses} apply={args.apply} "
        f"mutations_sync={args.mutations_sync} max_candidates={args.max_candidates}",
        flush=True,
    )
    print(f"report={report_path}", flush=True)
    print(f"secret_status={secret_status(env_status_keys())}", flush=True)
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    print("=" * 96, flush=True)

    ensure_manifest_columns(client, args.database, args.manifest_table)
    candidates = load_delete_candidates(
        client,
        args.database,
        args.manifest_table,
        kinds,
        args.start_date,
        args.end_date,
        audit_statuses,
    )
    if args.max_candidates > 0:
        candidates = candidates[: args.max_candidates]
    print(f"Loaded delete candidates={len(candidates):,}", flush=True)

    decisions: list[DeleteDecision] = []
    started_at = time.perf_counter()
    for index, candidate in enumerate(candidates, start=1):
        if candidate.min_us <= 0 or candidate.max_us <= 0 or candidate.max_us < candidate.min_us:
            decision = DeleteDecision(
                key=candidate.key,
                kind=candidate.kind,
                source_date=candidate.source_date,
                source_file=candidate.source_file,
                target_table=candidate.target_table,
                audit_status=candidate.audit_status,
                expected_rows=candidate.expected_rows,
                audit_actual_rows=candidate.audit_actual_rows,
                rows_to_delete=0,
                min_us=candidate.min_us,
                max_us=candidate.max_us,
                min_event_date="",
                max_event_date="",
                action="skipped_invalid_range",
                note="Invalid audited timestamp range; no delete attempted.",
            )
        else:
            rows_to_delete, min_event_date, max_event_date = count_rows_in_range(client, args.database, candidate)
            action = "delete" if rows_to_delete > 0 else "already_empty"
            note = (
                "Rows will be deleted by audited SIP timestamp range."
                if rows_to_delete > 0
                else "No rows currently match audited SIP timestamp range."
            )
            decision = DeleteDecision(
                key=candidate.key,
                kind=candidate.kind,
                source_date=candidate.source_date,
                source_file=candidate.source_file,
                target_table=candidate.target_table,
                audit_status=candidate.audit_status,
                expected_rows=candidate.expected_rows,
                audit_actual_rows=candidate.audit_actual_rows,
                rows_to_delete=rows_to_delete,
                min_us=candidate.min_us,
                max_us=candidate.max_us,
                min_event_date=min_event_date,
                max_event_date=max_event_date,
                action=action,
                note=note,
            )
        decisions.append(decision)
        append_jsonl(report_path, {"type": "decision", **asdict(decision)})
        elapsed = time.perf_counter() - started_at
        print(
            f"CHECK [{index:,}/{len(candidates):,}] {decision.action} {decision.key} "
            f"rows_to_delete={decision.rows_to_delete:,} elapsed_min={elapsed / 60:.1f}",
            flush=True,
        )

    total_rows = sum(decision.rows_to_delete for decision in decisions)
    by_action: dict[str, int] = {}
    for decision in decisions:
        by_action[decision.action] = by_action.get(decision.action, 0) + 1

    print("=" * 96, flush=True)
    print(f"SUMMARY candidates={len(decisions):,} rows_to_delete={total_rows:,} by_action={by_action}", flush=True)
    print(f"report={report_path}", flush=True)
    if not args.apply:
        print("DRY RUN: no rows were deleted and no manifest audit rows were inserted. Rerun with --apply to delete.", flush=True)
        print("=" * 96, flush=True)
        return

    for index, (candidate, decision) in enumerate(zip(candidates, decisions, strict=True), start=1):
        if decision.action == "skipped_invalid_range":
            print(f"APPLY SKIP [{index:,}/{len(decisions):,}] invalid range {decision.key}", flush=True)
            continue
        profile: QueryProfile | None = None
        if decision.rows_to_delete > 0:
            profile = delete_rows_in_range(
                client,
                args.database,
                candidate,
                min_event_date=decision.min_event_date,
                max_event_date=decision.max_event_date,
                mutations_sync=args.mutations_sync,
            )
        note = f"{decision.note} rows_deleted={decision.rows_to_delete} previous_audit_status={decision.audit_status}."
        insert_deleted_audit_row(
            client,
            args.database,
            args.manifest_table,
            candidate,
            audit_run_id=run_id,
            rows_deleted=decision.rows_to_delete,
            profile=profile,
            note=note,
        )
        append_jsonl(report_path, {"type": "applied", **asdict(decision), "profile": None if profile is None else asdict(profile)})
        print(f"APPLY [{index:,}/{len(decisions):,}] deleted {decision.rows_to_delete:,} rows for {decision.key}", flush=True)
    print("=" * 96, flush=True)


if __name__ == "__main__":
    main()

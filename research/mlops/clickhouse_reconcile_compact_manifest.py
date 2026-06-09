from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
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
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    discover_clickhouse_env_files,
    parse_kinds,
    quote_ident,
    sql_string,
)


ORPHAN_KEY = "__ORPHAN__"


@dataclass(frozen=True, slots=True)
class ManifestItem:
    kind: str
    source_date: str
    source_file: str
    source_path_ch: str
    file_bytes: int
    target_table: str
    expected_rows: int
    expected_min_sip_timestamp: int
    expected_max_sip_timestamp: int
    manifest_actual_rows: int
    status: str
    run_id: str
    query_id: str
    exception: str
    audit_status: str

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
class AuditDecision:
    key: str
    kind: str
    source_date: str
    source_file: str
    target_table: str
    previous_status: str
    audit_status: str
    expected_rows: int
    actual_rows: int
    min_us: int
    max_us: int
    note: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reconcile compact SIP ingest_manifest rows against rows physically present in quotes/trades."
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
    parser.add_argument("--apply", action="store_true", help="Insert reconciliation audit rows into ingest_manifest.")
    parser.add_argument(
        "--range-padding-days",
        type=int,
        default=7,
        help="Extra source-date days loaded before/after the requested range so orphan checks handle UTC boundary rows.",
    )
    parser.add_argument(
        "--scan-table-dates",
        action="store_true",
        help="Also scan table event_date values in range to find dates with table rows but no overlapping manifest range.",
    )
    parser.add_argument("--limit-dates", type=int, default=0, help="Debug limit for event dates per kind. 0 means all.")
    parser.add_argument("--output-root-win", default=str(DEFAULT_OUTPUT_ROOT_WIN / "compact_manifest_reconcile"))
    return parser.parse_args()


def default_clickhouse_url_with_network_fallback() -> str:
    return (
        os.environ.get(CLICKHOUSE_URL_ENV)
        or os.environ.get(CLICKHOUSE_ENDPOINT_ENV)
        or os.environ.get("REAL_LIVE_CLICKHOUSE_READ_URL")
        or default_clickhouse_url()
    )


def env_status_keys() -> list[str]:
    return [
        CLICKHOUSE_URL_ENV,
        CLICKHOUSE_WORKSTATION_USER_ENV,
        CLICKHOUSE_WORKSTATION_PASSWORD_ENV,
        CLICKHOUSE_USER_SIMPLE_ENV,
        CLICKHOUSE_PASSWORD_SIMPLE_ENV,
        CLICKHOUSE_ENDPOINT_ENV,
        CLICKHOUSE_USER_ENV,
        CLICKHOUSE_PASSWORD_ENV,
        "REAL_LIVE_CLICKHOUSE_READ_URL",
    ]


def date_from_us(timestamp_us: int) -> date:
    return datetime.fromtimestamp(timestamp_us / 1_000_000, tz=UTC).date()


def iter_dates(start: date, end: date) -> list[date]:
    days: list[date] = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return days


def load_latest_manifest(
    client: ClickHouseHttpClient,
    database: str,
    manifest_table: str,
    kinds: list[str],
    start_date: str,
    end_date: str,
) -> list[ManifestItem]:
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
    run_id,
    query_id,
    exception,
    audit_status
FROM
(
    SELECT *
    FROM {quote_ident(database)}.{quote_ident(manifest_table)}
    WHERE source_date BETWEEN toDate({sql_string(start_date)}) AND toDate({sql_string(end_date)})
      AND kind IN ({", ".join(sql_string(kind) for kind in kinds)})
    ORDER BY kind, source_date, source_file, target_table, updated_at DESC, {STATUS_PRIORITY_SQL} DESC
    LIMIT 1 BY kind, source_date, source_file, target_table
)
ORDER BY kind, source_date, source_file, target_table
"""
    items: list[ManifestItem] = []
    for line in client.query_tsv(query).splitlines():
        values = line.split("\t")
        if len(values) < 15:
            values.extend([""] * (15 - len(values)))
        items.append(
            ManifestItem(
                kind=values[0],
                source_date=values[1],
                source_file=values[2],
                source_path_ch=values[3],
                file_bytes=int(values[4] or 0),
                target_table=values[5],
                expected_rows=int(values[6] or 0),
                expected_min_sip_timestamp=int(values[7] or 0),
                expected_max_sip_timestamp=int(values[8] or 0),
                manifest_actual_rows=int(values[9] or 0),
                status=values[10],
                run_id=values[11],
                query_id=values[12],
                exception=values[13],
                audit_status=values[14],
            )
        )
    return items


def table_dates(
    client: ClickHouseHttpClient,
    database: str,
    table: str,
    start_date: str,
    end_date: str,
) -> set[date]:
    rows = client.query_tsv(
        f"""
SELECT toString(event_date)
FROM {quote_ident(database)}.{quote_ident(table)}
WHERE event_date BETWEEN toDate({sql_string(start_date)}) AND toDate({sql_string(end_date)})
GROUP BY event_date
ORDER BY event_date
"""
    ).strip().splitlines()
    return {date.fromisoformat(row) for row in rows if row}


def key_sql(key: str) -> str:
    return sql_string(key)


def scan_date_counts(
    client: ClickHouseHttpClient,
    database: str,
    table: str,
    event_date: date,
    ranges: list[ManifestItem],
) -> dict[str, tuple[int, int, int]]:
    parts: list[str] = []
    for item in ranges:
        parts.append(f"sip_timestamp_us BETWEEN {item.min_us} AND {item.max_us}")
        parts.append(key_sql(item.key))
    parts.append(key_sql(ORPHAN_KEY))
    classifier = parts[0] if not ranges else "multiIf(" + ", ".join(parts) + ")"
    query = f"""
SELECT range_key, count(), min(sip_timestamp_us), max(sip_timestamp_us)
FROM
(
    SELECT {classifier} AS range_key, sip_timestamp_us
    FROM {quote_ident(database)}.{quote_ident(table)}
    WHERE event_date = toDate({sql_string(event_date.isoformat())})
)
GROUP BY range_key
ORDER BY range_key
"""
    counts: dict[str, tuple[int, int, int]] = {}
    for line in client.query_tsv(query).strip().splitlines():
        key, rows, min_us, max_us = line.split("\t")[:4]
        counts[key] = (int(rows or 0), int(min_us or 0), int(max_us or 0))
    return counts


def insert_manifest_audit_row(
    client: ClickHouseHttpClient,
    database: str,
    manifest_table: str,
    item: ManifestItem,
    *,
    status: str,
    audit_status: str,
    audit_run_id: str,
    actual_rows: int,
    note: str,
) -> None:
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
    {sql_string(item.kind)},
    toDate({sql_string(item.source_date)}),
    {sql_string(item.source_file)},
    {sql_string(item.source_path_ch)},
    {int(item.file_bytes)},
    {sql_string(item.target_table)},
    {int(item.expected_rows)},
    {int(item.expected_min_sip_timestamp)},
    {int(item.expected_max_sip_timestamp)},
    {int(actual_rows)},
    {sql_string(status)},
    {sql_string(audit_run_id)},
    '',
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    {sql_string(note)},
    {sql_string(audit_status)},
    {sql_string(audit_run_id)},
    {int(actual_rows)},
    {sql_string(note)},
    now()
)
"""
    client.execute(query)


def insert_orphan_audit_row(
    client: ClickHouseHttpClient,
    database: str,
    manifest_table: str,
    *,
    kind: str,
    target_table: str,
    event_date: date,
    actual_rows: int,
    min_us: int,
    max_us: int,
    audit_run_id: str,
) -> None:
    source_file = f"orphan_{kind}_{event_date.isoformat()}_{min_us}_{max_us}"
    note = "Rows exist in compact table but are not covered by any manifest timestamp range."
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
    {sql_string(kind)},
    toDate({sql_string(event_date.isoformat())}),
    {sql_string(source_file)},
    '',
    0,
    {sql_string(target_table)},
    0,
    {int(min_us * 1000)},
    {int(max_us * 1000)},
    {int(actual_rows)},
    'discovered',
    {sql_string(audit_run_id)},
    '',
    0,
    0,
    0,
    0,
    0,
    0,
    0,
    {sql_string(note)},
    'orphan_should_delete',
    {sql_string(audit_run_id)},
    {int(actual_rows)},
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
    run_id = "manifest_reconcile_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = Path(args.output_root_win) / f"{run_id}.jsonl"
    client = ClickHouseHttpClient(args.clickhouse_url, args.user, args.password)

    print("=" * 96, flush=True)
    print("Compact SIP manifest reconciliation", flush=True)
    print(f"database={args.database} manifest_table={args.manifest_table}", flush=True)
    print(f"kinds={kinds} start_date={args.start_date} end_date={args.end_date}", flush=True)
    print(
        f"apply={args.apply} scan_table_dates={args.scan_table_dates} "
        f"limit_dates={args.limit_dates} range_padding_days={args.range_padding_days}",
        flush=True,
    )
    print(f"report={report_path}", flush=True)
    print(f"secret_status={secret_status(env_status_keys())}", flush=True)
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    print("=" * 96, flush=True)

    ensure_manifest_columns(client, args.database, args.manifest_table)
    primary_start = date.fromisoformat(args.start_date)
    primary_end = date.fromisoformat(args.end_date)
    coverage_start = (primary_start - timedelta(days=max(0, args.range_padding_days))).isoformat()
    coverage_end = (primary_end + timedelta(days=max(0, args.range_padding_days))).isoformat()
    coverage_items = load_latest_manifest(client, args.database, args.manifest_table, kinds, coverage_start, coverage_end)
    items = [item for item in coverage_items if args.start_date <= item.source_date <= args.end_date]
    print(
        f"Loaded latest manifest items={len(items):,} coverage_items={len(coverage_items):,} "
        f"coverage={coverage_start}->{coverage_end}",
        flush=True,
    )

    by_kind: dict[str, list[ManifestItem]] = {kind: [] for kind in kinds}
    for item in coverage_items:
        by_kind.setdefault(item.kind, []).append(item)

    decisions: list[AuditDecision] = []
    orphan_decisions: list[AuditDecision] = []
    started_at = time.perf_counter()

    for kind in kinds:
        table = args.quote_table if kind == "quotes" else args.trade_table
        coverage_kind_items = by_kind.get(kind, [])
        primary_kind_items = [item for item in coverage_kind_items if args.start_date <= item.source_date <= args.end_date]
        orphan_candidate_dates: set[date] = set()
        for item in primary_kind_items:
            if item.expected_rows <= 0 or item.expected_min_sip_timestamp <= 0 or item.expected_max_sip_timestamp <= 0:
                continue
            orphan_candidate_dates.update(iter_dates(date_from_us(item.min_us), date_from_us(item.max_us)))
        if args.scan_table_dates:
            orphan_candidate_dates.update(iter_dates(primary_start, primary_end))
        range_dates: dict[date, list[ManifestItem]] = {}
        for item in coverage_kind_items:
            if item.expected_rows <= 0 or item.expected_min_sip_timestamp <= 0 or item.expected_max_sip_timestamp <= 0:
                continue
            for event_date in iter_dates(date_from_us(item.min_us), date_from_us(item.max_us)):
                range_dates.setdefault(event_date, []).append(item)

        dates = set(range_dates)
        if args.scan_table_dates:
            dates.update(table_dates(client, args.database, table, args.start_date, args.end_date))
        ordered_dates = sorted(dates)
        if args.limit_dates > 0:
            ordered_dates = ordered_dates[: args.limit_dates]

        actual_by_key: dict[str, int] = {}
        print(f"START kind={kind} table={table} event_dates={len(ordered_dates):,}", flush=True)
        for index, event_date in enumerate(ordered_dates, start=1):
            ranges = range_dates.get(event_date, [])
            counts = scan_date_counts(client, args.database, table, event_date, ranges)
            for key, (rows, min_us, max_us) in counts.items():
                if key == ORPHAN_KEY:
                    if rows > 0 and event_date in orphan_candidate_dates:
                        orphan_decisions.append(
                            AuditDecision(
                                key=f"{kind}|{event_date.isoformat()}|orphan",
                                kind=kind,
                                source_date=event_date.isoformat(),
                                source_file=f"orphan_{kind}_{event_date.isoformat()}_{min_us}_{max_us}",
                                target_table=table,
                                previous_status="",
                                audit_status="orphan_should_delete",
                                expected_rows=0,
                                actual_rows=rows,
                                min_us=min_us,
                                max_us=max_us,
                                note="Rows exist in compact table but are not covered by any manifest timestamp range.",
                            )
                        )
                    continue
                actual_by_key[key] = actual_by_key.get(key, 0) + rows
            elapsed = time.perf_counter() - started_at
            print(
                f"SCAN {kind} [{index:,}/{len(ordered_dates):,}] {event_date} "
                f"ranges={len(ranges):,} elapsed_min={elapsed / 60:.1f}",
                flush=True,
            )

        for item in primary_kind_items:
            actual_rows = actual_by_key.get(item.key, 0)
            if actual_rows == item.expected_rows:
                if item.status != "ok" or item.manifest_actual_rows != actual_rows or item.audit_status in {"should_delete", "orphan_should_delete"}:
                    decisions.append(
                        AuditDecision(
                            key=item.key,
                            kind=item.kind,
                            source_date=item.source_date,
                            source_file=item.source_file,
                            target_table=item.target_table,
                            previous_status=item.status,
                            audit_status="verified_ok",
                            expected_rows=item.expected_rows,
                            actual_rows=actual_rows,
                            min_us=item.min_us,
                            max_us=item.max_us,
                            note="Reconciled against compact table timestamp range; row count matches expected source rows.",
                        )
                    )
            else:
                decisions.append(
                    AuditDecision(
                        key=item.key,
                        kind=item.kind,
                        source_date=item.source_date,
                        source_file=item.source_file,
                        target_table=item.target_table,
                        previous_status=item.status,
                        audit_status="should_delete",
                        expected_rows=item.expected_rows,
                        actual_rows=actual_rows,
                        min_us=item.min_us,
                        max_us=item.max_us,
                        note="Compact table timestamp-range row count does not match expected source rows; delete range before retry.",
                    )
                )

    summary: dict[str, int] = {}
    for decision in decisions + orphan_decisions:
        summary[decision.audit_status] = summary.get(decision.audit_status, 0) + 1
        append_jsonl(report_path, {"type": "decision", **asdict(decision)})

    print("=" * 96, flush=True)
    print(f"SUMMARY {summary}", flush=True)
    print(f"normal_decisions={len(decisions):,} orphan_decisions={len(orphan_decisions):,}", flush=True)
    print(f"report={report_path}", flush=True)

    if args.apply:
        indexed_items = {item.key: item for item in items}
        for index, decision in enumerate(decisions, start=1):
            item = indexed_items[decision.key]
            status = "ok" if decision.audit_status == "verified_ok" else item.status
            insert_manifest_audit_row(
                client,
                args.database,
                args.manifest_table,
                item,
                status=status,
                audit_status=decision.audit_status,
                audit_run_id=run_id,
                actual_rows=decision.actual_rows,
                note=decision.note,
            )
            print(f"APPLY [{index:,}/{len(decisions):,}] {decision.audit_status} {decision.key}", flush=True)
        for index, decision in enumerate(orphan_decisions, start=1):
            insert_orphan_audit_row(
                client,
                args.database,
                args.manifest_table,
                kind=decision.kind,
                target_table=decision.target_table,
                event_date=date.fromisoformat(decision.source_date),
                actual_rows=decision.actual_rows,
                min_us=decision.min_us,
                max_us=decision.max_us,
                audit_run_id=run_id,
            )
            print(f"APPLY ORPHAN [{index:,}/{len(orphan_decisions):,}] {decision.key}", flush=True)
    else:
        print("DRY RUN: no manifest rows were inserted. Rerun with --apply to write audit rows.", flush=True)
    print("=" * 96, flush=True)


if __name__ == "__main__":
    main()

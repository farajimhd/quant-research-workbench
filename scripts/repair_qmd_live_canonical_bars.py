#!/usr/bin/env python3
"""Plan or execute a bounded canonical QMD intraday-bar repair.

The repair discovers event ranges that contain physical replay versions, then
recomputes only their affected 100 ms buckets and configured parent rollups from
``q_live.events FINAL``. Plan-only is the safe default.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.request
import urllib.parse
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


BASE_RESOLUTION_US = 100_000
DEFAULT_RUNTIME_ROOT = Path(r"D:\TradingML\runtimes\qmd_gateway\canonical_bar_repair")


@dataclass(frozen=True)
class RepairRange:
    event_date: str
    ticker: str
    duplicate_rows: int
    first_sip_us: int
    last_sip_us: int

    @property
    def start_us(self) -> int:
        return self.first_sip_us // BASE_RESOLUTION_US * BASE_RESOLUTION_US

    @property
    def end_us(self) -> int:
        return (self.last_sip_us // BASE_RESOLUTION_US + 1) * BASE_RESOLUTION_US


class ClickHouseHttp:
    def __init__(self, url: str, user: str, password: str, database: str) -> None:
        self.url = url.rstrip("/") + f"/?database={urllib.parse.quote(database)}"
        self.authorization = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()

    def query(self, sql: str, timeout: int = 300) -> str:
        request = urllib.request.Request(
            self.url,
            data=sql.encode(),
            headers={"Authorization": self.authorization, "Content-Type": "text/plain"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode()

    def json_rows(self, sql: str, timeout: int = 300) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.query(sql, timeout).splitlines() if line.strip()]


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def connection_from_env(database: str) -> ClickHouseHttp:
    url = os.getenv("QMD_CLICKHOUSE_URL") or os.getenv("REAL_LIVE_CLICKHOUSE_WRITE_URL")
    user = (
        os.getenv("QMD_CLICKHOUSE_USER")
        or os.getenv("REAL_LIVE_CLICKHOUSE_WRITE_USER")
        or os.getenv("CLICKHOUSE_WORKSTATION_USER")
        or os.getenv("CLICKHOUSE_USER")
        or "default"
    )
    password = (
        os.getenv("QMD_CLICKHOUSE_PASSWORD")
        or os.getenv("REAL_LIVE_CLICKHOUSE_WRITE_PASSWORD")
        or os.getenv("CLICKHOUSE_WORKSTATION_PASSWORD")
        or os.getenv("CLICKHOUSE_PASSWORD")
        or ""
    )
    if not url:
        raise RuntimeError("REAL_LIVE_CLICKHOUSE_WRITE_URL or QMD_CLICKHOUSE_URL is required")
    return ClickHouseHttp(url, user, password, database)


def sql_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def duplicate_ranges_sql(events_table: str, start_date: str, end_date: str) -> str:
    return f"""
    SELECT event_date, ticker, sum(copies - 1) AS duplicate_rows,
           min(sip_timestamp_us) AS first_sip_us, max(sip_timestamp_us) AS last_sip_us
    FROM
    (
        SELECT event_date, ticker, sip_timestamp_us, source_sequence,
               bitAnd(event_meta, 1) AS event_type, count() AS copies
        FROM {events_table}
        WHERE event_date BETWEEN toDate('{start_date}') AND toDate('{end_date}')
          AND ticker != ''
        GROUP BY event_date, ticker, sip_timestamp_us, source_sequence, event_type,
                 event_meta, price_primary_int, price_secondary_int,
                 size_primary, size_secondary, exchange_primary, exchange_secondary,
                 condition_token_1, condition_token_2, condition_token_3,
                 condition_token_4, condition_token_5
        HAVING copies > 1
    )
    GROUP BY event_date, ticker
    ORDER BY event_date, ticker
    FORMAT JSONEachRow
    """


def base_rebuild_sql(events_table: str, bars_table: str, item: RepairRange) -> str:
    ticker = sql_string(item.ticker)
    return f"""
    INSERT INTO {bars_table}
    (schema_version, ticker, local_date, label_resolution_us, bucket_index, bar_family,
     open, close, high, low, size_sum, size_open, size_close, size_high, size_low,
     event_count, first_event_timestamp_us, last_event_timestamp_us,
     bar_start_session_us, bar_end_session_us)
    WITH
      fromUnixTimestamp64Micro(toInt64(sip_timestamp_us)) AS event_ts_utc,
      toTimeZone(event_ts_utc, 'America/New_York') AS event_ts_local,
      toDate(event_ts_local) AS local_date_value,
      toInt64(sip_timestamp_us)
        - toUnixTimestamp64Micro(toDateTime64(toStartOfDay(event_ts_local), 6, 'America/New_York')) AS session_us,
      intDiv(session_us, {BASE_RESOLUTION_US}) AS bucket,
      tuple(sip_timestamp_us, source_sequence, bitAnd(event_meta, 1), arrival_sequence) AS event_order
    SELECT 2, ticker, local_date_value, {BASE_RESOLUTION_US}, bucket, bar_family,
      toFloat32(argMin(price, event_order)), toFloat32(argMax(price, event_order)),
      toFloat32(max(price)), toFloat32(min(price)), toFloat64(sum(size)),
      toFloat64(argMin(size, event_order)), toFloat64(argMax(size, event_order)),
      toFloat64(max(size)), toFloat64(min(size)), toUInt64(count()),
      toUInt64(min(sip_timestamp_us)), toUInt64(max(sip_timestamp_us)),
      bucket * {BASE_RESOLUTION_US}, (bucket + 1) * {BASE_RESOLUTION_US}
    FROM
    (
      SELECT *, 'trade' AS bar_family,
        toFloat64(price_primary_int) / if(bitAnd(event_meta, 2) != 0, 10000., 100.) AS price,
        toFloat64(size_primary) AS size
      FROM {events_table} FINAL WHERE bitAnd(event_meta, 1) = 1
      UNION ALL
      SELECT *, 'quote_bid' AS bar_family,
        toFloat64(price_secondary_int) / if(bitAnd(event_meta, 4) != 0, 10000., 100.) AS price,
        toFloat64(size_secondary) AS size
      FROM {events_table} FINAL WHERE bitAnd(event_meta, 1) = 0
      UNION ALL
      SELECT *, 'quote_ask' AS bar_family,
        toFloat64(price_primary_int) / if(bitAnd(event_meta, 2) != 0, 10000., 100.) AS price,
        toFloat64(size_primary) AS size
      FROM {events_table} FINAL WHERE bitAnd(event_meta, 1) = 0
    )
    WHERE ticker = '{ticker}' AND sip_timestamp_us >= {item.start_us}
      AND sip_timestamp_us < {item.end_us} AND price > 0
      AND session_us >= 14400000000 AND session_us < 72000000000
    GROUP BY ticker, local_date_value, bucket, bar_family
    """


def rollup_rebuild_sql(
    events_table: str, bars_table: str, item: RepairRange, resolution_us: int
) -> str:
    ticker = sql_string(item.ticker)
    return f"""
    INSERT INTO {bars_table}
    (schema_version, ticker, local_date, label_resolution_us, bucket_index, bar_family,
     open, close, high, low, size_sum, size_open, size_close, size_high, size_low,
     event_count, first_event_timestamp_us, last_event_timestamp_us,
     bar_start_session_us, bar_end_session_us)
    SELECT 2, ticker, local_date, {resolution_us},
      intDiv(bar_start_session_us, {resolution_us}) AS bucket, bar_family,
      argMin(open, bucket_index), argMax(close, bucket_index), max(high), min(low), sum(size_sum),
      argMin(size_open, bucket_index), argMax(size_close, bucket_index), max(size_high), min(size_low),
      toUInt64(sum(event_count)), min(first_event_timestamp_us), max(last_event_timestamp_us),
      bucket * {resolution_us}, (bucket + 1) * {resolution_us}
    FROM {bars_table} FINAL
    WHERE ticker = '{ticker}' AND label_resolution_us = {BASE_RESOLUTION_US}
      AND tuple(local_date, intDiv(bar_start_session_us, {resolution_us})) IN
      (
        SELECT DISTINCT
          toDate(toTimeZone(fromUnixTimestamp64Micro(toInt64(sip_timestamp_us)), 'America/New_York')),
          intDiv(
            toInt64(sip_timestamp_us) - toUnixTimestamp64Micro(toDateTime64(toStartOfDay(toTimeZone(fromUnixTimestamp64Micro(toInt64(sip_timestamp_us)), 'America/New_York')), 6, 'America/New_York')),
            {resolution_us}
          )
        FROM {events_table} FINAL
        WHERE ticker = '{ticker}' AND sip_timestamp_us >= {item.start_us}
          AND sip_timestamp_us < {item.end_us}
      )
    GROUP BY ticker, local_date, bucket, bar_family
    """


def base_validation_sql(events_table: str, bars_table: str, item: RepairRange) -> str:
    ticker = sql_string(item.ticker)
    return f"""
    WITH expected AS
    (
      WITH
        fromUnixTimestamp64Micro(toInt64(sip_timestamp_us)) AS event_ts_utc,
        toTimeZone(event_ts_utc, 'America/New_York') AS event_ts_local,
        toDate(event_ts_local) AS local_date,
        toInt64(sip_timestamp_us) - toUnixTimestamp64Micro(toDateTime64(toStartOfDay(event_ts_local), 6, 'America/New_York')) AS session_us,
        intDiv(session_us, {BASE_RESOLUTION_US}) AS bucket
      SELECT local_date, bucket, bar_family, count() AS expected_count
      FROM
      (
        SELECT *, 'trade' AS bar_family, price_primary_int AS valid_price FROM {events_table} FINAL WHERE bitAnd(event_meta, 1) = 1
        UNION ALL
        SELECT *, 'quote_bid' AS bar_family, price_secondary_int AS valid_price FROM {events_table} FINAL WHERE bitAnd(event_meta, 1) = 0
        UNION ALL
        SELECT *, 'quote_ask' AS bar_family, price_primary_int AS valid_price FROM {events_table} FINAL WHERE bitAnd(event_meta, 1) = 0
      )
      WHERE ticker = '{ticker}' AND sip_timestamp_us >= {item.start_us}
        AND sip_timestamp_us < {item.end_us} AND valid_price > 0
        AND session_us >= 14400000000 AND session_us < 72000000000
      GROUP BY local_date, bucket, bar_family
    ), actual AS
    (
      SELECT local_date, bucket_index AS bucket, bar_family, event_count
      FROM {bars_table} FINAL
      WHERE ticker = '{ticker}' AND label_resolution_us = {BASE_RESOLUTION_US}
    )
    SELECT count() AS expected_buckets,
           countIf(ifNull(actual.event_count, toUInt64(0)) != expected.expected_count)
             AS mismatched_buckets
    FROM expected LEFT JOIN actual USING (local_date, bucket, bar_family)
    FORMAT JSONEachRow
    """


def qmd_live_is_running() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8795/health", timeout=2) as response:
            return response.status == 200
    except Exception:
        return False


def parse_args() -> argparse.Namespace:
    today = date.today().isoformat()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", default=today)
    parser.add_argument("--database", default="q_live")
    parser.add_argument("--events-table", default="events")
    parser.add_argument("--bars-table", default="intraday_family_bars_v2")
    parser.add_argument("--ticker", action="append", default=[])
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT)
    parser.add_argument("--execute", action="store_true", help="Apply the bounded repair; default is plan-only.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]
    load_dotenv(repo_root / ".env")
    runtime_root = args.runtime_root.resolve()
    if runtime_root == repo_root or repo_root in runtime_root.parents:
        raise RuntimeError("repair artifacts must be outside the repository")
    runtime_root.mkdir(parents=True, exist_ok=True)
    client = connection_from_env(args.database)
    raw = client.json_rows(
        duplicate_ranges_sql(args.events_table, args.start_date, args.end_date), timeout=600
    )
    tickers = {value.strip().upper() for value in args.ticker if value.strip()}
    repairs = [
        RepairRange(
            event_date=str(row["event_date"]),
            ticker=str(row["ticker"]),
            duplicate_rows=int(row["duplicate_rows"]),
            first_sip_us=int(row["first_sip_us"]),
            last_sip_us=int(row["last_sip_us"]),
        )
        for row in raw
        if not tickers or str(row["ticker"]).upper() in tickers
    ]
    resolutions = [
        int(row["label_resolution_us"])
        for row in client.json_rows(
            f"SELECT DISTINCT label_resolution_us FROM {args.bars_table} WHERE label_resolution_us > {BASE_RESOLUTION_US} ORDER BY label_resolution_us FORMAT JSONEachRow"
        )
    ]
    syntax_checks = 0
    if repairs:
        representative = repairs[0]
        statements = [
            base_rebuild_sql(args.events_table, args.bars_table, representative),
            base_validation_sql(args.events_table, args.bars_table, representative),
            *(
                rollup_rebuild_sql(
                    args.events_table, args.bars_table, representative, resolution
                )
                for resolution in resolutions
            ),
        ]
        for statement in statements:
            client.query(f"EXPLAIN SYNTAX {statement}", timeout=60)
            syntax_checks += 1
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    manifest_path = runtime_root / f"canonical_bar_repair_{run_id}.json"
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "mode": "execute" if args.execute else "plan",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "database": args.database,
        "events_table": args.events_table,
        "bars_table": args.bars_table,
        "start_date": args.start_date,
        "end_date": args.end_date,
        "resolutions_us": resolutions,
        "sql_syntax_checks": syntax_checks,
        "repairs": [asdict(item) | {"start_us": item.start_us, "end_us": item.end_us} for item in repairs],
        "status": "planned",
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"QMD canonical bar repair: {len(repairs)} bounded ticker/date range(s)")
    print(f"Manifest: {manifest_path}")
    if not args.execute:
        print("Plan only. Re-run with --execute after reviewing the manifest and stopping QMD Live.")
        return 0
    if qmd_live_is_running():
        raise RuntimeError("QMD Live is running on port 8795; stop it before executing bar repair")
    validations: list[dict[str, Any]] = []
    for index, item in enumerate(repairs, start=1):
        print(f"[{index}/{len(repairs)}] {item.event_date} {item.ticker}: {item.duplicate_rows} replay row(s)")
        client.query(base_rebuild_sql(args.events_table, args.bars_table, item), timeout=600)
        for resolution in resolutions:
            client.query(
                rollup_rebuild_sql(args.events_table, args.bars_table, item, resolution),
                timeout=600,
            )
        validation = client.json_rows(
            base_validation_sql(args.events_table, args.bars_table, item), timeout=300
        )[0]
        validation.update({"event_date": item.event_date, "ticker": item.ticker})
        validations.append(validation)
        if int(validation["mismatched_buckets"]) != 0:
            raise RuntimeError(
                f"canonical bar validation failed for {item.event_date} {item.ticker}: {validation}"
            )
    manifest["status"] = "completed"
    manifest["validations"] = validations
    manifest["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("Completed bounded canonical bar repair.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted; completed inserts remain restart-safe.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as error:
        print(f"QMD canonical bar repair failed: {error}", file=sys.stderr)
        raise SystemExit(1)

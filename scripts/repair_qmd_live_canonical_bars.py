#!/usr/bin/env python3
"""Audit physical replay duplicates relevant to canonical QMD v3 bars.

Version 3 repairs late or duplicate buckets inside the Rust QMD authority from
``q_live.events FINAL``. This script produces a review manifest only; the old
parallel SQL bar writer is intentionally retired.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


BASE_RESOLUTION_US = 100_000
DEFAULT_RUNTIME_ROOT = Path(r"D:\TradingML\runtimes\qmd_gateway\canonical_bar_repair")


@dataclass(frozen=True)
class DuplicateRange:
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
        encoded = base64.b64encode(f"{user}:{password}".encode()).decode()
        self.authorization = "Basic " + encoded

    def query(self, sql: str, timeout: int = 300) -> str:
        request = urllib.request.Request(
            self.url,
            data=sql.encode(),
            headers={"Authorization": self.authorization, "Content-Type": "text/plain"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read().decode()
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace").strip()
            raise RuntimeError(
                f"ClickHouse HTTP {error.code}: {detail or error.reason}"
            ) from error

    def json_rows(self, sql: str, timeout: int = 300) -> list[dict[str, Any]]:
        return [
            json.loads(line)
            for line in self.query(sql, timeout).splitlines()
            if line.strip()
        ]


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


def identifier(value: str) -> str:
    if not value or not all(character.isalnum() or character == "_" for character in value):
        raise ValueError(f"invalid ClickHouse identifier: {value!r}")
    return value


def duplicate_ranges_sql(events_table: str, start_date: str, end_date: str) -> str:
    return f"""
    SELECT event_date, ticker, sum(copies - 1) AS duplicate_rows,
           min(sip_timestamp_us) AS first_sip_us, max(sip_timestamp_us) AS last_sip_us
    FROM
    (
        SELECT event_date, ticker, sip_timestamp_us, source_sequence,
               bitAnd(event_meta, 1) AS event_type, count() AS copies
        FROM {identifier(events_table)}
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", default=date.today().isoformat())
    parser.add_argument("--database", default="q_live")
    parser.add_argument("--events-table", default="events")
    parser.add_argument("--bars-table", default="intraday_family_bars_v3")
    parser.add_argument("--ticker", action="append", default=[])
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Retired: fails closed because v3 repair belongs to the Rust QMD authority.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.execute:
        raise RuntimeError(
            "direct SQL bar repair is retired for v3; use QMD Live late-event repair "
            "or the managed -BootstrapBars migration while QMD Live is stopped"
        )
    start_date = date.fromisoformat(args.start_date).isoformat()
    end_date = date.fromisoformat(args.end_date).isoformat()
    if start_date > end_date:
        raise ValueError("start-date must not follow end-date")
    database = identifier(args.database)
    events_table = identifier(args.events_table)
    bars_table = identifier(args.bars_table)
    repo_root = Path(__file__).resolve().parents[1]
    load_dotenv(repo_root / ".env")
    runtime_root = args.runtime_root.resolve()
    if runtime_root == repo_root or repo_root in runtime_root.parents:
        raise RuntimeError("repair artifacts must be outside the repository")
    runtime_root.mkdir(parents=True, exist_ok=True)
    client = connection_from_env(database)
    rows = client.json_rows(
        duplicate_ranges_sql(events_table, start_date, end_date), timeout=600
    )
    selected = {value.strip().upper() for value in args.ticker if value.strip()}
    duplicates = [
        DuplicateRange(
            event_date=str(row["event_date"]),
            ticker=str(row["ticker"]),
            duplicate_rows=int(row["duplicate_rows"]),
            first_sip_us=int(row["first_sip_us"]),
            last_sip_us=int(row["last_sip_us"]),
        )
        for row in rows
        if not selected or str(row["ticker"]).upper() in selected
    ]
    resolutions = [
        int(row["label_resolution_us"])
        for row in client.json_rows(
            f"SELECT DISTINCT label_resolution_us FROM {bars_table} "
            f"WHERE schema_version = 4 AND calculation_revision = 'qmd-family-bars-v4' "
            f"AND complete = 1 AND label_resolution_us > {BASE_RESOLUTION_US} "
            "ORDER BY label_resolution_us FORMAT JSONEachRow"
        )
    ]
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    manifest_path = runtime_root / f"canonical_bar_audit_{run_id}.json"
    manifest = {
        "schema_version": 2,
        "mode": "audit",
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "database": database,
        "events_table": events_table,
        "bars_table": bars_table,
        "start_date": start_date,
        "end_date": end_date,
        "resolutions_us": resolutions,
        "repair_authority": "qmd_gateway_intraday_bars_v3",
        "direct_sql_execution": "retired",
        "duplicate_ranges": [
            asdict(item) | {"start_us": item.start_us, "end_us": item.end_us}
            for item in duplicates
        ],
        "status": "audited",
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"QMD canonical bar audit: {len(duplicates)} duplicate ticker/date range(s)")
    print(f"Manifest: {manifest_path}")
    print(
        "QMD v3 repairs late/duplicate buckets from events FINAL; use managed "
        "-BootstrapBars only for a reviewed full-window rebuild."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as error:
        print(f"QMD canonical bar audit failed: {error}", file=sys.stderr)
        raise SystemExit(1)

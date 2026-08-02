from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
import math
import os
import random
import shutil
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib import error, parse, request
from zoneinfo import ZoneInfo

from research.bar_gpt.v1.cohort import (
    BAR_GPT_COHORT_2TB,
    BAR_GPT_COHORT_2TB_SHA256,
    BAR_GPT_DAILY_BOOTSTRAP_MANIFEST_TABLE,
    BAR_GPT_DAILY_BOOTSTRAP_TABLE,
)
from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    discover_clickhouse_env_files,
    insert_json_each_row,
    quote_ident,
    sql_string,
)
from research.mlops.env import load_env_files, secret_status


SCHEMA_VERSION = 1
BUILD_VERSION = "bar_gpt_daily_context_massive_v1"
SOURCE_SYSTEM = "massive"
SOURCE_CONTRACT = "custom_bars_v2_1hour_0400_2000_unadjusted"
SOURCE_ENDPOINT = "/v2/aggs/ticker/{ticker}/range/1/hour/{start}/{end_inclusive}"
CONTRACT_SHA256 = hashlib.sha256(
    f"{BUILD_VERSION}|{SOURCE_CONTRACT}|adjusted=false|sort=asc|limit=50000".encode("utf-8")
).hexdigest()
DEFAULT_DATABASE = "market_sip_compact"
DEFAULT_START_DATE = "2016-01-01"
DEFAULT_END_DATE = "2019-01-02"
DEFAULT_RUNTIME_ROOT = Path(r"D:\TradingML\runtimes\bar_gpt\v1\build_daily_context")
DEFAULT_API_BASE = "https://api.massive.com"
SESSION_TIMEZONE = "America/New_York"
SESSION_START = dt.time(4, 0)
SESSION_END = dt.time(20, 0)

TARGET_COLUMNS = [
    "schema_version",
    "build_version",
    "source_system",
    "source_contract",
    "adjusted",
    "session_date",
    "ticker",
    "bar_start_us",
    "bar_end_us",
    "available_at_us",
    "provider_first_window_start_ms",
    "provider_last_window_start_ms",
    "provider_hour_count",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "vwap",
    "transaction_count",
    "pulled_at",
]

TARGET_TYPES = {
    "schema_version": "UInt16",
    "build_version": "LowCardinality(String)",
    "source_system": "LowCardinality(String)",
    "source_contract": "LowCardinality(String)",
    "adjusted": "UInt8",
    "session_date": "Date",
    "ticker": "LowCardinality(String)",
    "bar_start_us": "UInt64",
    "bar_end_us": "UInt64",
    "available_at_us": "UInt64",
    "provider_first_window_start_ms": "UInt64",
    "provider_last_window_start_ms": "UInt64",
    "provider_hour_count": "UInt16",
    "open": "Float64",
    "high": "Float64",
    "low": "Float64",
    "close": "Float64",
    "volume": "Float64",
    "vwap": "Nullable(Float64)",
    "transaction_count": "UInt64",
    "pulled_at": "DateTime64(3, 'UTC')",
}

MANIFEST_TYPES = {
    "artifact_name": "LowCardinality(String)",
    "unit_id": "String",
    "ticker": "LowCardinality(String)",
    "start_date": "Date",
    "end_date": "Date",
    "status": "LowCardinality(String)",
    "output_row_count": "UInt64",
    "provider_request_count": "UInt32",
    "provider_retry_count": "UInt32",
    "cohort_sha256": "FixedString(64)",
    "contract_sha256": "FixedString(64)",
    "message": "String",
    "completed_at": "DateTime64(3, 'UTC')",
}


@dataclass(frozen=True, slots=True)
class TickerResult:
    ticker: str
    rows: tuple[dict[str, Any], ...]
    requests: int
    retries: int
    elapsed_seconds: float


class DailyBootstrapReporter:
    """Stable bounded-job display; the JSONL report remains the evidence authority."""

    def __init__(self, report_path: Path, total_tickers: int, *, layout: str) -> None:
        self.report_path = report_path
        self.total_tickers = total_tickers
        self.layout = layout
        self.started = time.perf_counter()
        self.state = "starting"
        self.current = "-"
        self.completed = 0
        self.skipped = 0
        self.failed = 0
        self.rows = 0
        self.requests = 0
        self.retries = 0
        self.last_seconds = 0.0
        self.last_rows = 0
        self.message = "Starting"
        self._lock = threading.Lock()
        self._console: Any | None = None
        self._live: Any | None = None

    def __enter__(self) -> "DailyBootstrapReporter":
        rich = self.layout == "rich" or (
            self.layout == "auto" and sys.stdout.isatty() and not os.environ.get("NO_COLOR")
        )
        if rich:
            from rich.console import Console
            from rich.live import Live

            self._console = Console()
            self._live = Live(self._render(), console=self._console, refresh_per_second=2, transient=False)
            self._live.start()
        self.state = "running"
        self.event("start", message="Massive daily context bootstrap started")
        return self

    def __exit__(self, exc_type: object, exc: object, _tb: object) -> bool:
        if exc_type is KeyboardInterrupt:
            self.state = "interrupted"
            self.message = "Interrupted; certified ticker units remain resumable"
            self.event("interrupted", message=self.message)
        elif exc is not None:
            self.state = "failed"
            self.message = str(exc)
            self.event("failed", message=self.message)
        elif self.failed:
            self.state = "failed"
            self.message = f"Completed with {self.failed} failed ticker units"
        else:
            self.state = "complete"
            self.message = "All requested ticker units are certified"
        self.refresh()
        if self._live is not None:
            self._live.stop()
        return False

    def event(self, kind: str, **payload: object) -> None:
        record = {
            "event": kind,
            "utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "elapsed_seconds": round(time.perf_counter() - self.started, 6),
            **payload,
        }
        with self._lock:
            with self.report_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True, default=str) + "\n")
        if self._live is None and self.layout != "none":
            detail = payload.get("message") or payload.get("ticker") or ""
            print(f"[{kind}] {detail}", flush=True)

    def refresh(self) -> None:
        if self._live is not None:
            self._live.update(self._render(), refresh=True)

    def _render(self) -> Any:
        from rich.console import Group
        from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn
        from rich.table import Table

        width = self._console.width if self._console is not None else shutil.get_terminal_size((100, 24)).columns
        table = Table(title="BarGPT pre-2019 daily context", expand=True)
        table.add_column("Status", no_wrap=True, width=11)
        table.add_column("Value", overflow="fold", ratio=1)
        style = {"complete": "bold green", "failed": "bold red", "interrupted": "bold yellow"}.get(
            self.state, "bold cyan"
        )
        table.add_row("state", self.state, style=style)
        table.add_row("current", self.current)
        table.add_row(
            "durable",
            f"tickers {self.completed}/{self.total_tickers}  skipped {self.skipped}  failed {self.failed}  rows {self.rows:,}",
        )
        table.add_row("provider", f"requests {self.requests:,}  retries {self.retries:,}")
        if self.last_seconds:
            table.add_row("last unit", f"{self.last_rows:,} rows in {self.last_seconds:,.2f}s")
        table.add_row("elapsed", _duration(time.perf_counter() - self.started))
        table.add_row("latest", self.message)
        if width >= 80:
            table.add_row("evidence", str(self.report_path))
        progress = Progress(TextColumn("tickers"), BarColumn(), TaskProgressColumn(), expand=True)
        progress.add_task(
            "tickers",
            total=max(1, self.total_tickers),
            completed=min(self.completed, max(1, self.total_tickers)),
        )
        return Group(table, progress)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download certified unadjusted Massive daily trade context for BarGPT."
    )
    parser.add_argument("--execute", action="store_true", help="Create tables, download, and insert. Omit for a plan preview.")
    parser.add_argument("--start-date", default=DEFAULT_START_DATE, help="Inclusive session date.")
    parser.add_argument("--end-date", default=DEFAULT_END_DATE, help="Exclusive session date.")
    parser.add_argument("--tickers", default=",".join(BAR_GPT_COHORT_2TB))
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--target-table", default=BAR_GPT_DAILY_BOOTSTRAP_TABLE)
    parser.add_argument("--manifest-table", default=BAR_GPT_DAILY_BOOTSTRAP_MANIFEST_TABLE)
    parser.add_argument("--storage-policy", default=os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY", ""))
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url())
    parser.add_argument("--clickhouse-user", default=default_clickhouse_user())
    parser.add_argument("--clickhouse-password", default=default_clickhouse_password())
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--api-key-env", default="MASSIVE_API_KEY")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--max-retries", type=int, default=6)
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT)
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text", "none"), default="auto")
    return parser.parse_args(argv)


def create_target_table_sql(args: argparse.Namespace) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(args.database)}.{quote_ident(args.target_table)}
(
    schema_version UInt16,
    build_version LowCardinality(String),
    source_system LowCardinality(String),
    source_contract LowCardinality(String),
    adjusted UInt8,
    session_date Date,
    ticker LowCardinality(String),
    bar_start_us UInt64,
    bar_end_us UInt64,
    available_at_us UInt64,
    provider_first_window_start_ms UInt64,
    provider_last_window_start_ms UInt64,
    provider_hour_count UInt16,
    open Float64,
    high Float64,
    low Float64,
    close Float64,
    volume Float64,
    vwap Nullable(Float64),
    transaction_count UInt64,
    pulled_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(pulled_at)
PARTITION BY toYear(session_date)
ORDER BY (ticker, session_date)
SETTINGS storage_policy = {sql_string(args.storage_policy)}
"""


def create_manifest_table_sql(args: argparse.Namespace) -> str:
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(args.database)}.{quote_ident(args.manifest_table)}
(
    artifact_name LowCardinality(String),
    unit_id String,
    ticker LowCardinality(String),
    start_date Date,
    end_date Date,
    status LowCardinality(String),
    output_row_count UInt64,
    provider_request_count UInt32,
    provider_retry_count UInt32,
    cohort_sha256 FixedString(64),
    contract_sha256 FixedString(64),
    message String,
    completed_at DateTime64(3, 'UTC')
)
ENGINE = ReplacingMergeTree(completed_at)
PARTITION BY toYear(start_date)
ORDER BY (artifact_name, unit_id)
SETTINGS storage_policy = {sql_string(args.storage_policy)}
"""


def validate_table_contracts(client: ClickHouseHttpClient, args: argparse.Namespace) -> None:
    for table, expected, physical in (
        (args.target_table, TARGET_TYPES, ("ReplacingMergeTree(pulled_at)", "PARTITION BY toYear(session_date)", "ORDER BY (ticker, session_date)")),
        (args.manifest_table, MANIFEST_TYPES, ("ReplacingMergeTree(completed_at)", "PARTITION BY toYear(start_date)", "ORDER BY (artifact_name, unit_id)")),
    ):
        column_query = f"""
SELECT name, type
FROM system.columns
WHERE database = {sql_string(args.database)} AND table = {sql_string(table)}
ORDER BY position
FORMAT TSVRaw
"""
        actual: dict[str, str] = {}
        for line in client.execute(column_query).splitlines():
            if not line:
                continue
            name, column_type = line.split("\t", 1)
            actual[name] = column_type
        mismatches = {name: column_type for name, column_type in expected.items() if actual.get(name) != column_type}
        extras = sorted(set(actual) - set(expected))
        if mismatches or extras:
            raise RuntimeError(f"{args.database}.{table} schema mismatch: expected={mismatches} unexpected={extras}")
        ddl = client.execute(
            f"SHOW CREATE TABLE {quote_ident(args.database)}.{quote_ident(table)} FORMAT TSVRaw"
        )
        missing = [fragment for fragment in physical if fragment not in ddl]
        policy_fragment = f"storage_policy = {sql_string(args.storage_policy)}"
        if policy_fragment not in ddl:
            missing.append(policy_fragment)
        if missing:
            raise RuntimeError(f"{args.database}.{table} physical contract mismatch: missing {missing}")


def unit_id(ticker: str, start: dt.date, end: dt.date) -> str:
    return f"{ticker}:{start.isoformat()}:{end.isoformat()}:{CONTRACT_SHA256[:16]}"


def completed_units(client: ClickHouseHttpClient, args: argparse.Namespace, start: dt.date, end: dt.date) -> set[str]:
    query = f"""
SELECT unit_id
FROM {quote_ident(args.database)}.{quote_ident(args.manifest_table)} FINAL
WHERE artifact_name = {sql_string(args.target_table)}
  AND start_date = toDate({sql_string(start.isoformat())})
  AND end_date = toDate({sql_string(end.isoformat())})
  AND contract_sha256 = {sql_string(CONTRACT_SHA256)}
  AND status IN ('certified', 'certified_empty')
FORMAT JSONEachRow
"""
    return {str(row["unit_id"]) for row in client.iter_json_each_row(query)}


def massive_url(api_base: str, ticker: str, start: dt.date, end: dt.date) -> str:
    inclusive_end = end - dt.timedelta(days=1)
    endpoint = SOURCE_ENDPOINT.format(
        ticker=parse.quote(ticker, safe=""), start=start.isoformat(), end_inclusive=inclusive_end.isoformat()
    )
    query = parse.urlencode({"adjusted": "false", "sort": "asc", "limit": "50000"})
    return f"{api_base.rstrip('/')}{endpoint}?{query}"


def append_api_key(url: str, api_key: str) -> str:
    parsed = parse.urlsplit(url)
    values = parse.parse_qsl(parsed.query, keep_blank_values=True)
    if not any(key.lower() == "apikey" for key, _ in values):
        values.append(("apiKey", api_key))
    return parse.urlunsplit(parsed._replace(query=parse.urlencode(values)))


def redact_url(url: str) -> str:
    parsed = parse.urlsplit(url)
    values = [(key, "***" if key.lower() == "apikey" else value) for key, value in parse.parse_qsl(parsed.query)]
    return parse.urlunsplit(parsed._replace(query=parse.urlencode(values)))


def request_json(
    url: str,
    *,
    api_key: str,
    timeout: float,
    max_retries: int,
    opener: Callable[..., Any] = request.urlopen,
) -> tuple[dict[str, Any], int]:
    retries = 0
    while True:
        secured = append_api_key(url, api_key)
        req = request.Request(secured, headers={"Accept": "application/json", "User-Agent": "BarGPT-v1-daily-context"})
        try:
            with opener(req, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise RuntimeError(f"Expected JSON object from {redact_url(secured)}")
            return payload, retries
        except error.HTTPError as exc:
            retryable = exc.code == 429 or 500 <= exc.code < 600
            if not retryable or retries >= max_retries:
                body = exc.read().decode("utf-8", errors="replace")[:500]
                raise RuntimeError(f"Massive HTTP {exc.code} for {redact_url(secured)}: {body}") from exc
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            delay = float(retry_after) if retry_after and retry_after.replace(".", "", 1).isdigit() else min(30.0, 0.5 * 2**retries)
        except (error.URLError, TimeoutError, OSError) as exc:
            if retries >= max_retries:
                raise RuntimeError(f"Massive request failed for {redact_url(secured)}: {exc}") from exc
            delay = min(30.0, 0.5 * 2**retries)
        retries += 1
        time.sleep(delay + random.uniform(0.0, min(0.25, delay * 0.1)))


def parse_hour_row(ticker: str, value: dict[str, Any], start: dt.date, end: dt.date) -> tuple[dt.date, dict[str, Any]] | None:
    required = ("t", "o", "h", "l", "c", "v")
    missing = [name for name in required if name not in value]
    if missing:
        raise RuntimeError(f"{ticker} Massive aggregate missing fields {missing}")
    timestamp_ms = int(value["t"])
    local_day = dt.datetime.fromtimestamp(timestamp_ms / 1000.0, dt.timezone.utc).astimezone(
        ZoneInfo(SESSION_TIMEZONE)
    ).date()
    if not start <= local_day < end:
        raise RuntimeError(f"{ticker} returned session {local_day} outside [{start}, {end})")
    local_timestamp = dt.datetime.fromtimestamp(timestamp_ms / 1000.0, dt.timezone.utc).astimezone(ZoneInfo(SESSION_TIMEZONE))
    if not SESSION_START.hour <= local_timestamp.hour < SESSION_END.hour:
        return None
    prices = {name: float(value[key]) for name, key in (("open", "o"), ("high", "h"), ("low", "l"), ("close", "c"))}
    if not all(math.isfinite(number) and number > 0 for number in prices.values()):
        raise RuntimeError(f"{ticker} {local_day} contains a non-positive or non-finite OHLC value")
    if prices["low"] > min(prices["open"], prices["close"]) or prices["high"] < max(prices["open"], prices["close"]):
        raise RuntimeError(f"{ticker} {local_day} violates OHLC containment")
    volume = float(value["v"])
    if not math.isfinite(volume) or volume < 0:
        raise RuntimeError(f"{ticker} {local_day} contains invalid volume")
    transaction_count = int(value.get("n") or 0)
    if transaction_count < 0:
        raise RuntimeError(f"{ticker} {local_day} contains invalid transaction count")
    raw_vwap = value.get("vw")
    vwap = None if raw_vwap is None else float(raw_vwap)
    if vwap is not None and (not math.isfinite(vwap) or vwap <= 0):
        raise RuntimeError(f"{ticker} {local_day} contains invalid VWAP")
    return local_day, {
        "timestamp_ms": timestamp_ms,
        **prices,
        "volume": volume,
        "vwap": vwap,
        "transaction_count": transaction_count,
    }


def aggregate_session_rows(ticker: str, hours: list[dict[str, Any]], pulled_at: str) -> dict[str, Any]:
    if not hours:
        raise ValueError("at least one provider hour is required")
    hours.sort(key=lambda row: int(row["timestamp_ms"]))
    timestamps = [int(row["timestamp_ms"]) for row in hours]
    if len(timestamps) != len(set(timestamps)):
        raise RuntimeError(f"{ticker} provider returned duplicate hourly windows")
    local_timestamps = [
        dt.datetime.fromtimestamp(value / 1000.0, dt.timezone.utc).astimezone(ZoneInfo(SESSION_TIMEZONE))
        for value in timestamps
    ]
    if len({value.date() for value in local_timestamps}) != 1:
        raise RuntimeError(f"{ticker} session rollup crossed a New York date boundary")
    first_local = local_timestamps[0]
    local_day = first_local.date()
    volume = sum(float(row["volume"]) for row in hours)
    vwap_complete = all(row["vwap"] is not None or float(row["volume"]) == 0.0 for row in hours)
    weighted_vwap = sum(float(row["vwap"] or 0.0) * float(row["volume"]) for row in hours)
    timezone = ZoneInfo(SESSION_TIMEZONE)
    bar_start = dt.datetime.combine(local_day, SESSION_START, tzinfo=timezone)
    bar_end = dt.datetime.combine(local_day, SESSION_END, tzinfo=timezone)
    return {
        "schema_version": SCHEMA_VERSION,
        "build_version": BUILD_VERSION,
        "source_system": SOURCE_SYSTEM,
        "source_contract": SOURCE_CONTRACT,
        "adjusted": 0,
        "session_date": local_day.isoformat(),
        "ticker": ticker,
        "bar_start_us": int(bar_start.timestamp() * 1_000_000),
        "bar_end_us": int(bar_end.timestamp() * 1_000_000),
        "available_at_us": int(bar_end.timestamp() * 1_000_000),
        "provider_first_window_start_ms": timestamps[0],
        "provider_last_window_start_ms": timestamps[-1],
        "provider_hour_count": len(hours),
        "open": float(hours[0]["open"]),
        "high": max(float(row["high"]) for row in hours),
        "low": min(float(row["low"]) for row in hours),
        "close": float(hours[-1]["close"]),
        "volume": volume,
        "vwap": weighted_vwap / volume if volume > 0 and vwap_complete else None,
        "transaction_count": sum(int(row["transaction_count"]) for row in hours),
        "pulled_at": pulled_at,
    }


def fetch_ticker(
    ticker: str,
    *,
    start: dt.date,
    end: dt.date,
    api_base: str,
    api_key: str,
    timeout: float,
    max_retries: int,
) -> TickerResult:
    started = time.perf_counter()
    url = massive_url(api_base, ticker, start, end)
    requests = 0
    retries = 0
    raw_rows: list[dict[str, Any]] = []
    while url:
        payload, page_retries = request_json(
            url, api_key=api_key, timeout=timeout, max_retries=max_retries
        )
        requests += 1
        retries += page_retries
        if payload.get("adjusted") is not False:
            raise RuntimeError(f"{ticker} provider did not confirm adjusted=false")
        results = payload.get("results") or []
        if not isinstance(results, list):
            raise RuntimeError(f"{ticker} Massive response results is not a list")
        raw_rows.extend(row for row in results if isinstance(row, dict))
        next_url = payload.get("next_url")
        url = str(next_url) if next_url else ""
    pulled_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")
    grouped: dict[dt.date, list[dict[str, Any]]] = {}
    for raw_row in raw_rows:
        parsed = parse_hour_row(ticker, raw_row, start, end)
        if parsed is not None:
            local_day, hour = parsed
            grouped.setdefault(local_day, []).append(hour)
    rows = [aggregate_session_rows(ticker, grouped[day], pulled_at) for day in sorted(grouped)]
    dates = [str(row["session_date"]) for row in rows]
    if dates != sorted(dates) or len(dates) != len(set(dates)):
        raise RuntimeError(f"{ticker} Massive rows are not unique and ascending by session date")
    return TickerResult(ticker, tuple(rows), requests, retries, time.perf_counter() - started)


def certify_ticker(
    client: ClickHouseHttpClient,
    args: argparse.Namespace,
    result: TickerResult,
    start: dt.date,
    end: dt.date,
) -> None:
    ticker = result.ticker
    count_query = f"""
SELECT count()
FROM {quote_ident(args.database)}.{quote_ident(args.target_table)} FINAL
WHERE ticker = {sql_string(ticker)}
  AND session_date >= toDate({sql_string(start.isoformat())})
  AND session_date < toDate({sql_string(end.isoformat())})
FORMAT TSVRaw
"""
    actual = int(client.execute(count_query).strip() or "0")
    expected = len(result.rows)
    if actual != expected:
        raise RuntimeError(f"{ticker} certification mismatch: provider={expected} ClickHouse={actual}")
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")
    insert_json_each_row(
        client,
        args.database,
        args.manifest_table,
        [
            "artifact_name", "unit_id", "ticker", "start_date", "end_date", "status",
            "output_row_count", "provider_request_count", "provider_retry_count", "cohort_sha256",
            "contract_sha256", "message", "completed_at",
        ],
        [{
            "artifact_name": args.target_table,
            "unit_id": unit_id(ticker, start, end),
            "ticker": ticker,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "status": "certified" if expected else "certified_empty",
            "output_row_count": expected,
            "provider_request_count": result.requests,
            "provider_retry_count": result.retries,
            "cohort_sha256": BAR_GPT_COHORT_2TB_SHA256,
            "contract_sha256": CONTRACT_SHA256,
            "message": "Unadjusted 04:00-20:00 provider hourly trade rollup; quote families and trade-size OHLC are unavailable",
            "completed_at": now,
        }],
    )


def validate_dates(start_text: str, end_text: str) -> tuple[dt.date, dt.date]:
    start = dt.date.fromisoformat(start_text)
    end = dt.date.fromisoformat(end_text)
    if end <= start:
        raise ValueError("--end-date must be after --start-date")
    return start, end


def requested_tickers(text: str) -> tuple[str, ...]:
    tickers = tuple(sorted({item.strip().upper() for item in text.split(",") if item.strip()}))
    if not tickers:
        raise ValueError("at least one ticker is required")
    return tickers


def _duration(value: float) -> str:
    seconds = max(0, int(value))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}h {minutes:02d}m" if hours else (f"{minutes}m {secs:02d}s" if minutes else f"{secs}s")


def main(argv: list[str] | None = None) -> int:
    load_env_files(discover_clickhouse_env_files())
    args = parse_args(argv)
    if not args.storage_policy:
        args.storage_policy = os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY", "")
    if args.clickhouse_url == default_clickhouse_url():
        args.clickhouse_url = default_clickhouse_url()
    if args.clickhouse_user == default_clickhouse_user():
        args.clickhouse_user = default_clickhouse_user()
    if args.clickhouse_password == default_clickhouse_password():
        args.clickhouse_password = default_clickhouse_password()
    start, end = validate_dates(args.start_date, args.end_date)
    tickers = requested_tickers(args.tickers)
    plan = {
        "range": f"[{start}, {end})",
        "tickers": len(tickers),
        "source": SOURCE_CONTRACT,
        "adjusted": False,
        "target": f"{args.database}.{args.target_table}",
        "manifest": f"{args.database}.{args.manifest_table}",
        "storage_policy": args.storage_policy or "MISSING",
        "contract_sha256": CONTRACT_SHA256,
    }
    if not args.execute:
        print(json.dumps(plan, indent=2), flush=True)
        print(create_target_table_sql(args).strip(), flush=True)
        print(create_manifest_table_sql(args).strip(), flush=True)
        return 0
    if not args.storage_policy:
        raise RuntimeError("CLICKHOUSE_LIVE_STORAGE_POLICY/--storage-policy is required")
    api_key = os.environ.get(args.api_key_env, "").strip()
    if not api_key:
        raise RuntimeError(f"{args.api_key_env} is required")
    if args.workers <= 0:
        raise ValueError("--workers must be positive")
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = args.runtime_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    report_path = run_dir / "build.jsonl"
    client = ClickHouseHttpClient(
        args.clickhouse_url, args.clickhouse_user, args.clickhouse_password, timeout_seconds=max(60.0, args.timeout), persistent=True
    )
    try:
        client.execute(create_target_table_sql(args))
        client.execute(create_manifest_table_sql(args))
        validate_table_contracts(client, args)
        done = completed_units(client, args, start, end)
        with DailyBootstrapReporter(report_path, len(tickers), layout=args.progress_layout) as reporter:
            reporter.event("preflight", message=json.dumps(plan, sort_keys=True), secrets=secret_status([args.api_key_env]))
            pending: list[str] = []
            for ticker in tickers:
                if unit_id(ticker, start, end) in done:
                    reporter.completed += 1
                    reporter.skipped += 1
                    reporter.event("skipped", ticker=ticker, message=f"{ticker} already certified")
                else:
                    pending.append(ticker)
            reporter.refresh()
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(args.workers, len(pending) or 1)) as pool:
                futures = {
                    pool.submit(
                        fetch_ticker,
                        ticker,
                        start=start,
                        end=end,
                        api_base=args.api_base,
                        api_key=api_key,
                        timeout=args.timeout,
                        max_retries=args.max_retries,
                    ): ticker
                    for ticker in pending
                }
                try:
                    for future in concurrent.futures.as_completed(futures):
                        ticker = futures[future]
                        reporter.current = ticker
                        try:
                            result = future.result()
                            if result.rows:
                                insert_json_each_row(client, args.database, args.target_table, TARGET_COLUMNS, list(result.rows))
                            certify_ticker(client, args, result, start, end)
                        except Exception as exc:
                            reporter.failed += 1
                            reporter.message = f"{ticker}: {exc}"
                            reporter.event("ticker_failed", ticker=ticker, message=str(exc))
                            reporter.refresh()
                            continue
                        reporter.completed += 1
                        reporter.rows += len(result.rows)
                        reporter.requests += result.requests
                        reporter.retries += result.retries
                        reporter.last_rows = len(result.rows)
                        reporter.last_seconds = result.elapsed_seconds
                        reporter.message = f"Certified {ticker}: {len(result.rows):,} sessions"
                        reporter.event(
                            "ticker_certified", ticker=ticker, rows=len(result.rows), requests=result.requests,
                            retries=result.retries, unit_seconds=result.elapsed_seconds, message=reporter.message,
                        )
                        reporter.refresh()
                except KeyboardInterrupt:
                    for future in futures:
                        future.cancel()
                    pool.shutdown(wait=True, cancel_futures=True)
                    raise
            if reporter.failed:
                raise RuntimeError(f"{reporter.failed} ticker units failed; rerun to resume uncertified units")
            reporter.event("complete", message=f"Certified {reporter.completed} tickers and {reporter.rows} rows")
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

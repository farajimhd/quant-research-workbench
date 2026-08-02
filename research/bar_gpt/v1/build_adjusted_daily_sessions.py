from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import parse
from zoneinfo import ZoneInfo

from research.bar_gpt.v1.build_daily_context import (
    DailyBootstrapReporter,
    request_json,
    requested_tickers,
    validate_dates,
)
from research.bar_gpt.v1.build_adjusted_1s import adjustment_basis_hash, load_split_actions
from research.bar_gpt.v1.cohort import (
    BAR_GPT_ADJUSTED_DAILY_MANIFEST_TABLE,
    BAR_GPT_ADJUSTED_DAILY_TABLE,
    BAR_GPT_COHORT_2TB,
    BAR_GPT_COHORT_2TB_SHA256,
    BAR_GPT_REVIEWED_TICKER_CHAINS,
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


SCHEMA_VERSION = 2
BUILD_VERSION = "bar_gpt_daily_sessions_massive_adjusted_v2"
SOURCE_SYSTEM = "massive"
SOURCE_CONTRACT = "custom_bars_v2_30minute_three_sessions_adjusted"
SOURCE_ENDPOINT = "/v2/aggs/ticker/{ticker}/range/30/minute/{start}/{end_inclusive}"
TICKER_EVENTS_ENDPOINT = "/vX/reference/tickers/{ticker}/events"
CONTRACT_SHA256 = hashlib.sha256(
    f"{BUILD_VERSION}|{SOURCE_CONTRACT}|adjusted=true|sort=asc|limit=50000|ticker_events_vX".encode()
).hexdigest()
DEFAULT_DATABASE = "market_sip_compact"
DEFAULT_START_DATE = "2017-01-01"
DEFAULT_RUNTIME_ROOT = Path(r"D:\TradingML\runtimes\bar_gpt\v1\build_adjusted_daily_sessions")
DEFAULT_API_BASE = "https://api.massive.com"
NY = ZoneInfo("America/New_York")

SESSION_BOUNDS: tuple[tuple[str, dt.time, dt.time], ...] = (
    ("premarket", dt.time(4, 0), dt.time(9, 30)),
    ("regular", dt.time(9, 30), dt.time(16, 0)),
    ("after_hours", dt.time(16, 0), dt.time(20, 0)),
)

TARGET_COLUMNS = (
    "schema_version", "build_version", "source_system", "source_contract", "adjusted",
    "adjustment_asof_date", "split_schedule_sha256", "session_date", "ticker", "provider_ticker", "session_kind", "present",
    "bar_start_us", "bar_end_us", "available_at_us", "provider_first_window_start_ms",
    "provider_last_window_start_ms", "provider_window_count", "open", "high", "low",
    "close", "volume", "vwap", "transaction_count", "pulled_at",
)

TARGET_TYPES = {
    "schema_version": "UInt16", "build_version": "LowCardinality(String)",
    "source_system": "LowCardinality(String)", "source_contract": "LowCardinality(String)",
    "adjusted": "UInt8", "adjustment_asof_date": "Date", "split_schedule_sha256": "FixedString(64)",
    "session_date": "Date",
    "ticker": "LowCardinality(String)", "provider_ticker": "LowCardinality(String)",
    "session_kind": "LowCardinality(String)",
    "present": "UInt8", "bar_start_us": "UInt64", "bar_end_us": "UInt64",
    "available_at_us": "UInt64", "provider_first_window_start_ms": "Nullable(UInt64)",
    "provider_last_window_start_ms": "Nullable(UInt64)", "provider_window_count": "UInt16",
    "open": "Nullable(Float64)", "high": "Nullable(Float64)", "low": "Nullable(Float64)",
    "close": "Nullable(Float64)", "volume": "Float64", "vwap": "Nullable(Float64)",
    "transaction_count": "UInt64", "pulled_at": "DateTime64(3, 'UTC')",
}

MANIFEST_TYPES = {
    "artifact_name": "LowCardinality(String)", "unit_id": "String",
    "ticker": "LowCardinality(String)", "start_date": "Date", "end_date": "Date",
    "adjustment_asof_date": "Date", "split_schedule_sha256": "FixedString(64)",
    "status": "LowCardinality(String)",
    "output_row_count": "UInt64", "present_row_count": "UInt64",
    "provider_request_count": "UInt32", "provider_retry_count": "UInt32",
    "cohort_sha256": "FixedString(64)", "contract_sha256": "FixedString(64)",
    "message": "String", "completed_at": "DateTime64(3, 'UTC')",
}


@dataclass(frozen=True, slots=True)
class TickerResult:
    ticker: str
    rows: tuple[dict[str, Any], ...]
    requests: int
    retries: int
    elapsed_seconds: float


def clickhouse_utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def completed_end_date() -> dt.date:
    now = dt.datetime.now(NY)
    return now.date() + dt.timedelta(days=1 if now.time() >= dt.time(20, 0) else 0)


def provider_adjustment_asof_date() -> dt.date:
    """Massive adjusted aggregates are a request-time snapshot, not a historical as-of API."""
    return dt.datetime.now(NY).date()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build split-adjusted Massive daily bars for three market sessions.")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--start-date", default=DEFAULT_START_DATE)
    parser.add_argument("--end-date", default="auto", help="Exclusive date; auto includes only completed NY sessions.")
    parser.add_argument("--adjustment-asof-date", default="auto")
    parser.add_argument("--tickers", default=",".join(BAR_GPT_COHORT_2TB))
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--target-table", default=BAR_GPT_ADJUSTED_DAILY_TABLE)
    parser.add_argument("--manifest-table", default=BAR_GPT_ADJUSTED_DAILY_MANIFEST_TABLE)
    parser.add_argument("--storage-policy", default=os.environ.get("CLICKHOUSE_LIVE_STORAGE_POLICY", ""))
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url())
    parser.add_argument("--clickhouse-user", default=default_clickhouse_user())
    parser.add_argument("--clickhouse-password", default=default_clickhouse_password())
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--api-key-env", default="MASSIVE_API_KEY")
    parser.add_argument("--split-database", default="q_live")
    parser.add_argument("--split-table", default="market_stock_split_v1")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--max-retries", type=int, default=6)
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_RUNTIME_ROOT)
    parser.add_argument("--progress-layout", choices=("auto", "rich", "text", "none"), default="auto")
    return parser.parse_args(argv)


def create_target_table_sql(args: argparse.Namespace) -> str:
    columns = ",\n    ".join(f"{quote_ident(name)} {kind}" for name, kind in TARGET_TYPES.items())
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(args.database)}.{quote_ident(args.target_table)}
(
    {columns}
)
ENGINE = ReplacingMergeTree(pulled_at)
PARTITION BY toYear(session_date)
ORDER BY (ticker, session_date, session_kind)
SETTINGS storage_policy = {sql_string(args.storage_policy)}
"""


def create_manifest_table_sql(args: argparse.Namespace) -> str:
    columns = ",\n    ".join(f"{quote_ident(name)} {kind}" for name, kind in MANIFEST_TYPES.items())
    return f"""
CREATE TABLE IF NOT EXISTS {quote_ident(args.database)}.{quote_ident(args.manifest_table)}
(
    {columns}
)
ENGINE = ReplacingMergeTree(completed_at)
PARTITION BY toYear(start_date)
ORDER BY (artifact_name, unit_id)
SETTINGS storage_policy = {sql_string(args.storage_policy)}
"""


def validate_table_contracts(client: ClickHouseHttpClient, args: argparse.Namespace) -> None:
    for table, expected in ((args.target_table, TARGET_TYPES), (args.manifest_table, MANIFEST_TYPES)):
        query = f"SELECT name, type FROM system.columns WHERE database={sql_string(args.database)} AND table={sql_string(table)} ORDER BY position FORMAT TSVRaw"
        actual = dict(line.split("\t", 1) for line in client.execute(query).splitlines() if line)
        mismatch = {name: kind for name, kind in expected.items() if actual.get(name) != kind}
        extras = sorted(set(actual) - set(expected))
        if mismatch or extras:
            raise RuntimeError(f"{args.database}.{table} schema mismatch: expected={mismatch} unexpected={extras}")


def massive_url(api_base: str, ticker: str, start: dt.date, end: dt.date) -> str:
    endpoint = SOURCE_ENDPOINT.format(
        ticker=parse.quote(ticker, safe=""), start=start.isoformat(),
        end_inclusive=(end - dt.timedelta(days=1)).isoformat(),
    )
    return f"{api_base.rstrip('/')}{endpoint}?{parse.urlencode({'adjusted': 'true', 'sort': 'asc', 'limit': '50000'})}"


def ticker_events_url(api_base: str, ticker: str) -> str:
    endpoint = TICKER_EVENTS_ENDPOINT.format(ticker=parse.quote(ticker, safe=""))
    return f"{api_base.rstrip('/')}{endpoint}?{parse.urlencode({'types': 'ticker_change'})}"


def parse_ticker_segments(
    canonical_ticker: str, start: dt.date, end: dt.date, payload: dict[str, Any]
) -> tuple[tuple[str, dt.date, dt.date], ...]:
    results = payload.get("results") or {}
    events = results.get("events") if isinstance(results, dict) else None
    parsed: list[tuple[dt.date, str]] = []
    if isinstance(events, list):
        for event in events:
            if not isinstance(event, dict) or event.get("type") != "ticker_change":
                continue
            change = event.get("ticker_change") or {}
            ticker = str(change.get("ticker") or "").strip().upper() if isinstance(change, dict) else ""
            try:
                date = dt.date.fromisoformat(str(event.get("date")))
            except ValueError:
                continue
            if ticker:
                parsed.append((date, ticker))
    parsed = sorted(set(parsed))
    # The endpoint is experimental and has returned a different share-class
    # ticker for some identifiers.  Such a chain is not an authority for this
    # canonical symbol; retain the literal ticker instead of cross-contaminating.
    if not parsed or parsed[-1][1] != canonical_ticker:
        return ((canonical_ticker, start, end),)
    boundaries = [(start, canonical_ticker)]
    for date, ticker in parsed:
        if date <= start:
            boundaries[0] = (start, ticker)
        elif date < end:
            boundaries.append((date, ticker))
    segments: list[tuple[str, dt.date, dt.date]] = []
    for index, (left, ticker) in enumerate(boundaries):
        right = boundaries[index + 1][0] if index + 1 < len(boundaries) else end
        if left < right:
            segments.append((ticker, left, right))
    return tuple(segments)


def reviewed_ticker_segments(
    canonical_ticker: str, start: dt.date, end: dt.date
) -> tuple[tuple[str, dt.date, dt.date], ...] | None:
    chain = BAR_GPT_REVIEWED_TICKER_CHAINS.get(canonical_ticker)
    if chain is None:
        return None
    segments = []
    for provider_ticker, left_text, right_text in chain:
        left = max(start, dt.date.fromisoformat(left_text))
        right = min(end, dt.date.fromisoformat(right_text))
        if left < right:
            segments.append((provider_ticker, left, right))
    return tuple(segments)


def fetch_ticker_segments(
    canonical_ticker: str, *, start: dt.date, end: dt.date, api_base: str, api_key: str,
    timeout: float, max_retries: int,
) -> tuple[tuple[tuple[str, dt.date, dt.date], ...], int, int]:
    reviewed = reviewed_ticker_segments(canonical_ticker, start, end)
    if reviewed is not None:
        return reviewed, 0, 0
    try:
        payload, retries = request_json(
            ticker_events_url(api_base, canonical_ticker), api_key=api_key,
            timeout=timeout, max_retries=max_retries,
        )
    except RuntimeError as exc:
        # Massive returns 404 when an otherwise valid ticker has no event
        # timeline.  Absence means no provider alias is asserted.
        if "Massive HTTP 404" not in str(exc):
            raise
        return ((canonical_ticker, start, end),), 1, 0
    return parse_ticker_segments(canonical_ticker, start, end, payload), 1, retries


def _parse_window(ticker: str, raw: dict[str, Any], start: dt.date, end: dt.date) -> dict[str, Any] | None:
    missing = [key for key in ("t", "o", "h", "l", "c", "v") if key not in raw]
    if missing:
        raise RuntimeError(f"{ticker} Massive aggregate missing fields {missing}")
    timestamp_ms = int(raw["t"])
    local = dt.datetime.fromtimestamp(timestamp_ms / 1000, dt.timezone.utc).astimezone(NY)
    if not start <= local.date() < end:
        raise RuntimeError(f"{ticker} returned {local.date()} outside [{start}, {end})")
    session_kind = next((name for name, left, right in SESSION_BOUNDS if left <= local.time() < right), None)
    if session_kind is None:
        return None
    prices = {name: float(raw[key]) for name, key in (("open", "o"), ("high", "h"), ("low", "l"), ("close", "c"))}
    if not all(math.isfinite(value) and value > 0 for value in prices.values()):
        raise RuntimeError(f"{ticker} {local.isoformat()} has invalid adjusted OHLC")
    if prices["low"] > min(prices["open"], prices["close"]) or prices["high"] < max(prices["open"], prices["close"]):
        raise RuntimeError(f"{ticker} {local.isoformat()} violates OHLC containment")
    volume = float(raw["v"])
    vwap = None if raw.get("vw") is None else float(raw["vw"])
    transactions = int(raw.get("n") or 0)
    if not math.isfinite(volume) or volume < 0 or transactions < 0 or (vwap is not None and (not math.isfinite(vwap) or vwap <= 0)):
        raise RuntimeError(f"{ticker} {local.isoformat()} has invalid volume/VWAP/count")
    return {"session_date": local.date(), "session_kind": session_kind, "timestamp_ms": timestamp_ms,
            **prices, "volume": volume, "vwap": vwap, "transaction_count": transactions}


def _empty_or_rollup(
    ticker: str, provider_ticker: str, day: dt.date, kind: str, windows: list[dict[str, Any]],
    pulled_at: str, adjustment_asof: dt.date, schedule_sha256: str,
) -> dict[str, Any]:
    left, right = next((left, right) for name, left, right in SESSION_BOUNDS if name == kind)
    start_us = int(dt.datetime.combine(day, left, NY).timestamp() * 1_000_000)
    end_us = int(dt.datetime.combine(day, right, NY).timestamp() * 1_000_000)
    windows.sort(key=lambda row: int(row["timestamp_ms"]))
    timestamps = [int(row["timestamp_ms"]) for row in windows]
    if len(timestamps) != len(set(timestamps)):
        raise RuntimeError(f"{ticker} {day} {kind} has duplicate provider windows")
    volume = sum(float(row["volume"]) for row in windows)
    complete_vwap = all(row["vwap"] is not None or float(row["volume"]) == 0 for row in windows)
    weighted = sum(float(row["vwap"] or 0) * float(row["volume"]) for row in windows)
    return {
        "schema_version": SCHEMA_VERSION, "build_version": BUILD_VERSION, "source_system": SOURCE_SYSTEM,
        "source_contract": SOURCE_CONTRACT, "adjusted": 1, "adjustment_asof_date": adjustment_asof.isoformat(),
        "split_schedule_sha256": schedule_sha256,
        "session_date": day.isoformat(), "ticker": ticker, "provider_ticker": provider_ticker,
        "session_kind": kind, "present": int(bool(windows)),
        "bar_start_us": start_us, "bar_end_us": end_us, "available_at_us": end_us,
        "provider_first_window_start_ms": timestamps[0] if timestamps else None,
        "provider_last_window_start_ms": timestamps[-1] if timestamps else None,
        "provider_window_count": len(windows), "open": float(windows[0]["open"]) if windows else None,
        "high": max(float(row["high"]) for row in windows) if windows else None,
        "low": min(float(row["low"]) for row in windows) if windows else None,
        "close": float(windows[-1]["close"]) if windows else None, "volume": volume,
        "vwap": weighted / volume if volume > 0 and complete_vwap else None,
        "transaction_count": sum(int(row["transaction_count"]) for row in windows), "pulled_at": pulled_at,
    }


def fetch_ticker(
    ticker: str, *, start: dt.date, end: dt.date, adjustment_asof: dt.date,
    api_base: str, api_key: str, timeout: float, max_retries: int, schedule_sha256: str,
) -> TickerResult:
    began = time.perf_counter()
    segments, requests, retries = fetch_ticker_segments(
        ticker, start=start, end=end, api_base=api_base, api_key=api_key,
        timeout=timeout, max_retries=max_retries,
    )
    parsed_rows: list[dict[str, Any]] = []
    for provider_ticker, segment_start, segment_end in segments:
        url = massive_url(api_base, provider_ticker, segment_start, segment_end)
        while url:
            payload, page_retries = request_json(url, api_key=api_key, timeout=timeout, max_retries=max_retries)
            requests += 1
            retries += page_retries
            if payload.get("adjusted") is not True:
                raise RuntimeError(f"{ticker}/{provider_ticker} provider did not confirm adjusted=true")
            results = payload.get("results") or []
            if not isinstance(results, list):
                raise RuntimeError(f"{ticker}/{provider_ticker} Massive results is not a list")
            for raw in results:
                if isinstance(raw, dict):
                    row = _parse_window(ticker, raw, segment_start, segment_end)
                    if row is not None:
                        row["provider_ticker"] = provider_ticker
                        parsed_rows.append(row)
            url = str(payload.get("next_url") or "")
    grouped: dict[tuple[dt.date, str], list[dict[str, Any]]] = {}
    for row in parsed_rows:
        grouped.setdefault((row["session_date"], str(row["session_kind"])), []).append(row)
    # A date with any provider activity is emitted as an explicit three-row
    # session contract; absent extended-hours sessions carry present=0.
    days = sorted({day for day, _kind in grouped})
    provider_by_day = {
        day: next(str(row["provider_ticker"]) for (row_day, _kind), values in grouped.items() if row_day == day for row in values)
        for day in days
    }
    pulled_at = clickhouse_utc_now()
    rows = tuple(
        _empty_or_rollup(ticker, provider_by_day[day], day, kind, grouped.get((day, kind), []),
                         pulled_at, adjustment_asof, schedule_sha256)
        for day in days for kind, _left, _right in SESSION_BOUNDS
    )
    return TickerResult(ticker, rows, requests, retries, time.perf_counter() - began)


def unit_id(ticker: str, start: dt.date, end: dt.date, asof: dt.date, schedule_sha256: str) -> str:
    return f"{ticker}:{start}:{end}:{asof}:{CONTRACT_SHA256[:12]}:{schedule_sha256[:12]}"


def completed_units(client: ClickHouseHttpClient, args: argparse.Namespace, start: dt.date, end: dt.date,
                    asof: dt.date, schedule_sha256: str) -> set[str]:
    sql = f"""SELECT unit_id FROM {quote_ident(args.database)}.{quote_ident(args.manifest_table)} FINAL
WHERE artifact_name={sql_string(args.target_table)} AND start_date=toDate({sql_string(str(start))})
AND end_date=toDate({sql_string(str(end))}) AND adjustment_asof_date=toDate({sql_string(str(asof))})
AND contract_sha256={sql_string(CONTRACT_SHA256)} AND split_schedule_sha256={sql_string(schedule_sha256)}
AND status IN ('certified','certified_empty') FORMAT TSVRaw"""
    return {line for line in client.execute(sql).splitlines() if line}


def certify(client: ClickHouseHttpClient, args: argparse.Namespace, result: TickerResult, start: dt.date, end: dt.date,
            asof: dt.date, schedule_sha256: str) -> None:
    sql = f"""SELECT count(), countIf(present=1) FROM {quote_ident(args.database)}.{quote_ident(args.target_table)} FINAL
WHERE ticker={sql_string(result.ticker)} AND session_date>=toDate({sql_string(str(start))})
AND session_date<toDate({sql_string(str(end))}) AND adjustment_asof_date=toDate({sql_string(str(asof))})
AND split_schedule_sha256={sql_string(schedule_sha256)} FORMAT TSVRaw"""
    actual, present = (int(value) for value in client.execute(sql).strip().split("\t"))
    if actual != len(result.rows):
        raise RuntimeError(f"{result.ticker} certification mismatch: provider={len(result.rows)} ClickHouse={actual}")
    manifest = {
        "artifact_name": args.target_table, "unit_id": unit_id(result.ticker, start, end, asof, schedule_sha256),
        "ticker": result.ticker, "start_date": str(start), "end_date": str(end),
        "adjustment_asof_date": str(asof), "split_schedule_sha256": schedule_sha256,
        "status": "certified" if actual else "certified_empty",
        "output_row_count": actual, "present_row_count": present,
        "provider_request_count": result.requests, "provider_retry_count": result.retries,
        "cohort_sha256": BAR_GPT_COHORT_2TB_SHA256, "contract_sha256": CONTRACT_SHA256,
        "message": "Massive adjusted=true 30-minute bars; point-in-time provider ticker chain; explicit three-session rows",
        "completed_at": clickhouse_utc_now(),
    }
    insert_json_each_row(client, args.database, args.manifest_table, list(MANIFEST_TYPES), [manifest])


def main(argv: list[str] | None = None) -> int:
    load_env_files(discover_clickhouse_env_files())
    args = parse_args(argv)
    end_text = str(completed_end_date()) if args.end_date == "auto" else args.end_date
    start, end = validate_dates(args.start_date, end_text)
    provider_asof = provider_adjustment_asof_date()
    asof = provider_asof if args.adjustment_asof_date == "auto" else dt.date.fromisoformat(args.adjustment_asof_date)
    if asof > provider_asof:
        raise ValueError(f"--adjustment-asof-date cannot be later than provider request date {provider_asof}")
    tickers = requested_tickers(args.tickers)
    plan = {"range": f"[{start}, {end})", "adjustment_asof": str(asof), "tickers": len(tickers),
            "sessions": [item[0] for item in SESSION_BOUNDS], "adjusted": True,
            "target": f"{args.database}.{args.target_table}", "contract_sha256": CONTRACT_SHA256}
    if not args.execute:
        print(json.dumps(plan, indent=2))
        print(create_target_table_sql(args).strip())
        print(create_manifest_table_sql(args).strip())
        return 0
    if not args.storage_policy:
        raise RuntimeError("CLICKHOUSE_LIVE_STORAGE_POLICY/--storage-policy is required")
    api_key = os.environ.get(args.api_key_env, "").strip()
    if not api_key:
        raise RuntimeError(f"{args.api_key_env} is required")
    if args.workers <= 0:
        raise ValueError("--workers must be positive")
    run_dir = args.runtime_root / dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=False)
    client = ClickHouseHttpClient(args.clickhouse_url, args.clickhouse_user, args.clickhouse_password,
                                  timeout_seconds=max(args.timeout, 60), persistent=True)
    try:
        _split_actions, split_schedule_sha256 = load_split_actions(client, args, tickers, asof)
        schedule_sha256 = adjustment_basis_hash(split_schedule_sha256, tickers)
        if asof != provider_asof:
            _current_actions, current_split_schedule_sha256 = load_split_actions(client, args, tickers, provider_asof)
            current_schedule_sha256 = adjustment_basis_hash(current_split_schedule_sha256, tickers)
            if current_schedule_sha256 != schedule_sha256:
                raise RuntimeError(
                    f"Massive adjusted=true now uses a newer split basis ({provider_asof}); "
                    f"requested cutoff {asof} is no longer reproducible"
                )
        plan["split_schedule_sha256"] = schedule_sha256
        client.execute(create_target_table_sql(args)); client.execute(create_manifest_table_sql(args))
        validate_table_contracts(client, args)
        done = completed_units(client, args, start, end, asof, schedule_sha256)
        with DailyBootstrapReporter(run_dir / "build.jsonl", len(tickers), layout=args.progress_layout,
                                    title="BarGPT adjusted daily sessions", job_label="Adjusted daily session build") as reporter:
            reporter.event("preflight", message=json.dumps(plan, sort_keys=True), secrets=secret_status([args.api_key_env]))
            pending = [ticker for ticker in tickers if unit_id(ticker, start, end, asof, schedule_sha256) not in done]
            reporter.completed = reporter.skipped = len(tickers) - len(pending); reporter.refresh()
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(args.workers, len(pending) or 1)) as pool:
                futures = {pool.submit(fetch_ticker, ticker, start=start, end=end, adjustment_asof=asof,
                                       api_base=args.api_base, api_key=api_key, timeout=args.timeout,
                                       max_retries=args.max_retries, schedule_sha256=schedule_sha256): ticker for ticker in pending}
                for future in concurrent.futures.as_completed(futures):
                    ticker = futures[future]; reporter.current = ticker
                    try:
                        result = future.result()
                        if result.rows:
                            insert_json_each_row(client, args.database, args.target_table, list(TARGET_COLUMNS), list(result.rows))
                        certify(client, args, result, start, end, asof, schedule_sha256)
                    except Exception as exc:
                        reporter.failed += 1; reporter.message = f"{ticker}: {exc}"
                        reporter.event("ticker_failed", ticker=ticker, message=str(exc)); reporter.refresh(); continue
                    reporter.completed += 1; reporter.rows += len(result.rows); reporter.requests += result.requests
                    reporter.retries += result.retries; reporter.last_rows = len(result.rows)
                    reporter.last_seconds = result.elapsed_seconds
                    reporter.message = f"Certified {ticker}: {len(result.rows):,} three-session rows"
                    reporter.event("ticker_certified", ticker=ticker, rows=len(result.rows), requests=result.requests,
                                   retries=result.retries, unit_seconds=result.elapsed_seconds, message=reporter.message)
                    reporter.refresh()
            if reporter.failed:
                raise RuntimeError(f"{reporter.failed} ticker units failed; rerun to resume")
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Capture fail-closed QMD boundary and lineage acceptance evidence.

Generated reports belong in the configured runtime root, never in the source
checkout.  The validator is read-only: it calls QMD Live and QMD History and
does not trigger maintenance or mutate either service.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import re
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DEFAULT_RUNTIME_ROOT = Path(r"D:\TradingML\runtimes\qmd_validation")
APPROVED_EVENT_SOURCE = re.compile(
    r"^(?:market_sip_compact\.events_(?:YYYY|[0-9]{4})|q_live\.events)$"
)


class ValidationFailure(RuntimeError):
    pass


def _get_json(base_url: str, path: str, params: dict[str, Any] | None = None) -> Any:
    query = urllib.parse.urlencode(
        {key: value for key, value in (params or {}).items() if value is not None}
    )
    url = f"{base_url.rstrip('/')}{path}"
    if query:
        url = f"{url}?{query}"
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "X-Correlation-ID": "qmd-authority-validation"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise ValidationFailure(f"GET {url} failed: {error}") from error


def _timestamp(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise ValidationFailure(f"invalid RFC3339 timestamp: {value!r}") from error


def validate_source_plan(plan: dict[str, Any], *, start: str, end: str) -> list[str]:
    failures: list[str] = []
    segments = plan.get("segments")
    if not isinstance(segments, list) or not segments:
        return ["source plan has no segments"]
    expected_start = _timestamp(start)
    requested_end = _timestamp(end)
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            failures.append(f"segment {index} is not an object")
            continue
        segment_start = _timestamp(str(segment.get("start", "")))
        segment_end = _timestamp(str(segment.get("end", "")))
        if segment_start != expected_start:
            failures.append(
                f"segment {index} starts at {segment_start.isoformat()}, expected {expected_start.isoformat()}"
            )
        if segment_end <= segment_start:
            failures.append(f"segment {index} is empty or reversed")
        tier = str(segment.get("tier", ""))
        if tier not in {"archive", "recent", "current_live", "gap"}:
            failures.append(f"segment {index} has unknown tier {tier!r}")
        expected_start = segment_end
    if expected_start != requested_end:
        failures.append(
            f"source plan ends at {expected_start.isoformat()}, expected {requested_end.isoformat()}"
        )
    if not str(plan.get("plan_hash", "")):
        failures.append("source plan has no plan_hash")
    if plan.get("event_schema_version") is None:
        failures.append("source plan has no event_schema_version")
    return failures


def _event_key(event: dict[str, Any]) -> tuple[datetime, str]:
    return (_timestamp(str(event.get("ts", ""))), str(event.get("ticker", "")))


def validate_event_page(events: list[Any], previous_key: tuple[datetime, str] | None) -> tuple[list[str], tuple[datetime, str] | None, int]:
    failures: list[str] = []
    lineage_count = 0
    last_key = previous_key
    for index, value in enumerate(events):
        if not isinstance(value, dict):
            failures.append(f"event {index} is not an object")
            continue
        key = _event_key(value)
        if last_key is not None and key < last_key:
            failures.append(f"event order regressed at {key!r} after {last_key!r}")
        last_key = key
        raw = value.get("raw")
        if isinstance(raw, dict) and raw.get("correlation_id") and raw.get("causation_id"):
            lineage_count += 1
        else:
            failures.append(f"event {index} lacks correlation_id or causation_id")
    return failures, last_key, lineage_count


def _empty_scanner_row() -> dict[str, Any]:
    return {
        "first": None,
        "first_5m": None,
        "last": None,
        "quote_count": 0,
        "trade_count": 0,
        "volume": 0.0,
    }


def aggregate_qmd_scanner(
    events: list[Any],
    rows: dict[str, dict[str, Any]],
    *,
    five_minute_start: datetime,
) -> None:
    for value in events:
        if not isinstance(value, dict):
            continue
        ticker = str(value.get("ticker") or "").upper()
        if not ticker:
            continue
        row = rows.setdefault(ticker, _empty_scanner_row())
        if value.get("kind") == "quote":
            row["quote_count"] += 1
            continue
        if value.get("kind") != "trade":
            continue
        price = float(value.get("price") or 0)
        size = float(value.get("size") or 0)
        if price <= 0 or size <= 0:
            continue
        event_at = _timestamp(str(value.get("ts") or ""))
        row["trade_count"] += 1
        row["volume"] += size
        row["first"] = price if row["first"] is None else row["first"]
        row["last"] = price
        if event_at >= five_minute_start and row["first_5m"] is None:
            row["first_5m"] = price


def _clickhouse_json_rows(
    *,
    clickhouse_url: str,
    user: str,
    password: str,
    sql: str,
) -> list[dict[str, Any]]:
    body = f"{sql}\nFORMAT JSONEachRow".encode("utf-8")
    headers = {"Content-Type": "text/plain; charset=utf-8"}
    if user:
        token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
    request = urllib.request.Request(clickhouse_url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return [json.loads(line) for line in response if line.strip()]
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise ValidationFailure(f"approved direct ClickHouse parity query failed: {error}") from error


def direct_scanner_rows(
    *,
    plan: dict[str, Any],
    tickers: str,
    clickhouse_url: str,
    clickhouse_user: str,
    clickhouse_password: str,
) -> list[dict[str, Any]]:
    symbols = sorted({value.strip().upper() for value in tickers.split(",") if value.strip()})
    if not symbols or any(not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", value) for value in symbols):
        raise ValidationFailure("direct parity requires valid explicit tickers")
    ticker_sql = ",".join("'" + value.replace("'", "''") + "'" for value in symbols)
    selects: list[str] = []
    for index, segment in enumerate(plan.get("segments") or []):
        if not isinstance(segment, dict):
            raise ValidationFailure(f"direct parity source segment {index} is invalid")
        source = str(segment.get("source") or "")
        tier = str(segment.get("tier") or "")
        if tier == "gap" or not bool(segment.get("queryable_by_history")):
            raise ValidationFailure(
                f"direct parity requires a fully durable history window; segment {index} is {tier!r}"
            )
        if not APPROVED_EVENT_SOURCE.fullmatch(source):
            raise ValidationFailure(f"direct parity refused unapproved source {source!r}")
        segment_start = _timestamp(str(segment.get("start") or ""))
        segment_end = _timestamp(str(segment.get("end") or ""))
        start_us = int(segment_start.timestamp() * 1_000_000)
        end_us = int(segment_end.timestamp() * 1_000_000)
        sources = (
            [source.replace("YYYY", str(year)) for year in range(segment_start.year, (segment_end - timedelta(microseconds=1)).year + 1)]
            if source.endswith("YYYY")
            else [source]
        )
        for source_table in sources:
            ordinal = "arrival_sequence" if source_table == "q_live.events" else "ordinal"
            selects.append(
                "SELECT ticker, "
                f"{ordinal} AS source_ordinal, event_meta, sip_timestamp_us, "
                f"price_primary_int, size_primary FROM {source_table} FINAL "
                f"PREWHERE sip_timestamp_us >= {start_us} AND sip_timestamp_us < {end_us} "
                f"WHERE ticker IN ({ticker_sql})"
            )
    if not selects:
        raise ValidationFailure("direct parity source plan has no queryable segments")
    source_sql = " UNION ALL ".join(selects)
    end_at = _timestamp(str(plan.get("end") or ""))
    five_minute_us = int((end_at.timestamp() - 300) * 1_000_000)
    sql = f"""
        SELECT
            ticker,
            argMinIf(price, tuple(sip_timestamp_us, source_ordinal), is_trade) AS first,
            argMaxIf(price, tuple(sip_timestamp_us, source_ordinal), is_trade) AS last,
            argMinIf(price, tuple(sip_timestamp_us, source_ordinal), is_trade AND sip_timestamp_us >= {five_minute_us}) AS first_5m,
            sumIf(toFloat64(size_primary), is_trade) AS volume,
            countIf(is_trade) AS trade_count,
            countIf(is_quote) AS quote_count
        FROM
        (
            SELECT
                ticker, source_ordinal, sip_timestamp_us, size_primary,
                bitAnd(event_meta, 1) = 1 AND price_primary_int > 0 AND size_primary > 0 AS is_trade,
                bitAnd(event_meta, 1) = 0 AS is_quote,
                toFloat64(price_primary_int) / if(bitAnd(event_meta, 2) != 0, 10000., 100.) AS price
            FROM ({source_sql})
        )
        GROUP BY ticker
        HAVING trade_count > 0
        ORDER BY ticker
    """
    return _clickhouse_json_rows(
        clickhouse_url=clickhouse_url,
        user=clickhouse_user,
        password=clickhouse_password,
        sql=sql,
    )


def compare_scanner_parity(
    qmd_rows: dict[str, dict[str, Any]], direct_rows: list[dict[str, Any]]
) -> list[str]:
    failures: list[str] = []
    direct = {str(row.get("ticker") or "").upper(): row for row in direct_rows}
    if set(qmd_rows) != set(direct):
        failures.append(
            f"scanner ticker population differs: qmd={sorted(qmd_rows)} direct={sorted(direct)}"
        )
    for ticker in sorted(set(qmd_rows) & set(direct)):
        qmd = qmd_rows[ticker]
        row = direct[ticker]
        for field in ("quote_count", "trade_count"):
            if int(qmd[field]) != int(row.get(field) or 0):
                failures.append(f"{ticker} {field} differs: qmd={qmd[field]} direct={row.get(field)}")
        for field in ("first", "first_5m", "last", "volume"):
            qmd_value = qmd.get(field)
            direct_value = row.get(field)
            if qmd_value is None and direct_value in (None, 0, 0.0):
                continue
            if qmd_value is None or direct_value is None or not math.isclose(
                float(qmd_value), float(direct_value), rel_tol=1e-9, abs_tol=1e-6
            ):
                failures.append(
                    f"{ticker} {field} differs: qmd={qmd_value} direct={direct_value}"
                )
    return failures


def collect_evidence(
    *,
    live_url: str,
    history_url: str,
    start: str,
    end: str,
    tickers: str,
    page_size: int,
    max_events: int,
    direct_clickhouse_parity: bool = False,
    clickhouse_url: str = "http://127.0.0.1:8123/",
    clickhouse_user: str = "default",
    clickhouse_password: str = "",
) -> dict[str, Any]:
    live_health = _get_json(live_url, "/health")
    history_health = _get_json(history_url, "/health")
    live_status = _get_json(live_url, "/snapshot/status")
    history_status = _get_json(history_url, "/snapshot/status")
    params = {"start": start, "end": end, "tickers": tickers}
    plan = _get_json(history_url, "/source-plan", params)
    coverage = _get_json(history_url, "/coverage", params)
    if not isinstance(plan, dict):
        raise ValidationFailure("source plan response is not an object")
    failures = validate_source_plan(plan, start=start, end=end)
    if not isinstance(live_health, dict):
        failures.append("live health response is not an object")
    else:
        if live_health.get("running") is not True:
            failures.append("live health does not report running=true")
        if live_health.get("status") in {"degraded", "action_required"}:
            failures.append(f"live health is not operational: {live_health.get('status')!r}")
    if not isinstance(history_health, dict):
        failures.append("history health response is not an object")
    else:
        if history_health.get("service") != "qmd_history_gateway":
            failures.append(
                f"history health identifies {history_health.get('service')!r}, expected 'qmd_history_gateway'"
            )
        if history_health.get("status") != "ready":
            failures.append(f"history health is not ready: {history_health.get('status')!r}")
    for label, status, expected_service in (
        ("live", live_status, "qmd_gateway"),
        ("history", history_status, "qmd_history_gateway"),
    ):
        header = status.get("header") if isinstance(status, dict) else None
        actual_service = header.get("service") if isinstance(header, dict) else None
        if actual_service != expected_service:
            failures.append(
                f"{label} status identifies {actual_service!r}, expected {expected_service!r}"
            )

    revision: dict[str, Any] | None = None
    cursor: dict[str, Any] | None = None
    event_count = 0
    lineage_count = 0
    pages = 0
    last_key: tuple[datetime, str] | None = None
    complete = False
    qmd_scanner_rows: dict[str, dict[str, Any]] = {}
    five_minute_start = _timestamp(end) - timedelta(minutes=5)
    while event_count < max_events:
        request_params: dict[str, Any] = {**params, "limit": min(page_size, max_events - event_count)}
        if cursor:
            request_params.update(
                {
                    "cursor_sip_timestamp_us": cursor.get("sip_timestamp_us"),
                    "cursor_ticker": cursor.get("ticker"),
                    "cursor_ordinal": cursor.get("ordinal"),
                }
            )
        if revision:
            request_params.update(
                {
                    "expected_source_plan_hash": revision.get("source_plan_hash"),
                    "expected_revision_token": revision.get("token"),
                }
            )
        page = _get_json(history_url, "/snapshot/events", request_params)
        if not isinstance(page, dict):
            raise ValidationFailure("event page response is not an object")
        actual_revision = page.get("source_revision")
        if not isinstance(actual_revision, dict):
            failures.append("event page has no source_revision")
            break
        if revision is None:
            revision = actual_revision
        elif actual_revision != revision:
            failures.append("pinned source revision changed between pages")
        events = page.get("events")
        if not isinstance(events, list):
            failures.append("event page events is not a list")
            break
        page_failures, last_key, page_lineage = validate_event_page(events, last_key)
        failures.extend(page_failures)
        aggregate_qmd_scanner(
            events, qmd_scanner_rows, five_minute_start=five_minute_start
        )
        lineage_count += page_lineage
        event_count += len(events)
        pages += 1
        cursor = page.get("next_cursor") if isinstance(page.get("next_cursor"), dict) else None
        complete = bool(page.get("complete"))
        if complete or not cursor or not events:
            break

    if revision and revision.get("source_plan_hash") != plan.get("plan_hash"):
        failures.append("event revision source_plan_hash differs from planned hash")
    parity: dict[str, Any] = {"enabled": direct_clickhouse_parity}
    if direct_clickhouse_parity:
        if not complete:
            failures.append("direct parity requires a complete, non-truncated QMD event read")
            parity["verdict"] = "fail"
        else:
            direct_rows = direct_scanner_rows(
                plan=plan,
                tickers=tickers,
                clickhouse_url=clickhouse_url,
                clickhouse_user=clickhouse_user,
                clickhouse_password=clickhouse_password,
            )
            parity_failures = compare_scanner_parity(qmd_scanner_rows, direct_rows)
            failures.extend(parity_failures)
            parity = {
                "enabled": True,
                "direct_row_count": len(direct_rows),
                "qmd_row_count": len(qmd_scanner_rows),
                "verdict": "pass" if not parity_failures else "fail",
            }
    return {
        "schema_version": 1,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "request": {"start": start, "end": end, "tickers": tickers},
        "services": {"live_health": live_health, "history_health": history_health},
        "operations": {"live": live_status, "history": history_status},
        "source_plan": plan,
        "coverage": coverage,
        "event_page_proof": {
            "complete": complete,
            "event_count": event_count,
            "lineage_count": lineage_count,
            "max_events": max_events,
            "pages": pages,
            "source_revision": revision,
        },
        "direct_clickhouse_scanner_parity": parity,
        "verdict": "pass" if not failures else "fail",
        "failures": failures,
    }


def _write_report(report: dict[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = output_dir / f"qmd_authority_validation_{stamp}.json"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output_dir, delete=False) as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, target)
    return target


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True, help="Inclusive RFC3339 window start")
    parser.add_argument("--end", required=True, help="Exclusive RFC3339 window end")
    parser.add_argument("--tickers", required=True, help="Comma-separated ticker population")
    parser.add_argument("--live-url", default="http://127.0.0.1:8800")
    parser.add_argument("--history-url", default="http://127.0.0.1:8801")
    parser.add_argument("--page-size", type=int, default=25_000)
    parser.add_argument("--max-events", type=int, default=250_000)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_RUNTIME_ROOT)
    parser.add_argument(
        "--direct-clickhouse-parity",
        action="store_true",
        help="Compare QMD decoded Scanner primitives with approved plan-declared ClickHouse sources",
    )
    parser.add_argument("--clickhouse-url", default=os.environ.get("CLICKHOUSE_URL", "http://127.0.0.1:8123/"))
    parser.add_argument("--clickhouse-user", default=os.environ.get("CLICKHOUSE_USER", "default"))
    parser.add_argument(
        "--clickhouse-password-env",
        default="CLICKHOUSE_PASSWORD",
        help="Environment variable containing the ClickHouse password",
    )
    args = parser.parse_args(argv)
    if args.page_size < 1 or args.page_size > 100_000:
        parser.error("--page-size must be between 1 and 100000")
    if args.max_events < 1:
        parser.error("--max-events must be positive")
    if _timestamp(args.end) <= _timestamp(args.start):
        parser.error("--end must be after --start")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = collect_evidence(
            live_url=args.live_url,
            history_url=args.history_url,
            start=args.start,
            end=args.end,
            tickers=args.tickers,
            page_size=args.page_size,
            max_events=args.max_events,
            direct_clickhouse_parity=args.direct_clickhouse_parity,
            clickhouse_url=args.clickhouse_url,
            clickhouse_user=args.clickhouse_user,
            clickhouse_password=os.environ.get(args.clickhouse_password_env, ""),
        )
        target = _write_report(report, args.output_dir)
    except ValidationFailure as error:
        print(f"QMD authority validation failed before report creation: {error}", file=sys.stderr)
        return 2
    print(f"QMD authority validation: {report['verdict']} ({target})")
    for failure in report["failures"]:
        print(f"- {failure}", file=sys.stderr)
    return 0 if report["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

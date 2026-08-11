#!/usr/bin/env python3
"""Capture fail-closed QMD boundary and lineage acceptance evidence.

Generated reports belong in the configured runtime root, never in the source
checkout.  The validator is read-only: it calls QMD Live and QMD History and
does not trigger maintenance or mutate either service.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_RUNTIME_ROOT = Path(r"D:\TradingML\runtimes\qmd_validation")


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


def collect_evidence(
    *,
    live_url: str,
    history_url: str,
    start: str,
    end: str,
    tickers: str,
    page_size: int,
    max_events: int,
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
        lineage_count += page_lineage
        event_count += len(events)
        pages += 1
        cursor = page.get("next_cursor") if isinstance(page.get("next_cursor"), dict) else None
        complete = bool(page.get("complete"))
        if complete or not cursor or not events:
            break

    if revision and revision.get("source_plan_hash") != plan.get("plan_hash"):
        failures.append("event revision source_plan_hash differs from planned hash")
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

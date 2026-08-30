#!/usr/bin/env python3
"""Build restart-safe end-of-day Structural Level Book checkpoints through QMD Live."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


NEW_YORK = ZoneInfo("America/New_York")


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid date {value!r}; expected YYYY-MM-DD") from exc


def dates_between(start: date, end: date):
    cursor = start
    while cursor <= end:
        yield cursor
        cursor += timedelta(days=1)


def request_checkpoint(
    *,
    base_url: str,
    operator_token: str,
    ticker: str,
    session_date: date,
    rebuild_start: datetime,
    event_limit: int | None,
    timeout_seconds: float,
) -> dict:
    payload: dict[str, object] = {
        "session_date": session_date.isoformat(),
        "rebuild_start": rebuild_start.astimezone(timezone.utc).isoformat(),
    }
    if event_limit is not None:
        payload["event_limit"] = event_limit
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/admin/structure-checkpoints/{ticker}/daily",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-qmd-operator-token": operator_token,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(body)
        except json.JSONDecodeError:
            detail = {"error": body or str(exc)}
        raise RuntimeError(
            f"QMD Live returned HTTP {exc.code}: {detail.get('error', detail)}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"QMD Live request failed: {exc.reason}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build versioned end-of-day Structural Level Book checkpoints. "
            "The command is idempotent and resumes each ticker from its latest prior compatible day."
        )
    )
    parser.add_argument("--ticker", action="append", required=True, help="Ticker; repeat for more than one")
    parser.add_argument("--start-date", required=True, type=parse_date, help="First session date")
    parser.add_argument("--end-date", required=True, type=parse_date, help="Last session date")
    parser.add_argument(
        "--rebuild-start",
        type=parse_date,
        help="Cold-start history date; defaults to 30 calendar days before --start-date",
    )
    parser.add_argument(
        "--qmd-url",
        default=os.environ.get("QMD_LIVE_URL", "http://127.0.0.1:8795"),
        help="QMD Live base URL",
    )
    parser.add_argument(
        "--operator-token",
        default=os.environ.get("QMD_OPERATOR_TOKEN", ""),
        help="QMD operator token; defaults to QMD_OPERATOR_TOKEN",
    )
    parser.add_argument("--event-limit", type=int, help="Optional bounded cold-rebuild event limit")
    parser.add_argument("--timeout-seconds", type=float, default=1800.0)
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue independent ticker-days after a failed unit",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.start_date > args.end_date:
        raise SystemExit("--start-date must not be after --end-date")
    if not args.operator_token.strip():
        raise SystemExit("QMD operator token is required via --operator-token or QMD_OPERATOR_TOKEN")
    if args.event_limit is not None and args.event_limit < 1:
        raise SystemExit("--event-limit must be positive")

    tickers = sorted({ticker.strip().upper() for ticker in args.ticker if ticker.strip()})
    if not tickers:
        raise SystemExit("at least one non-empty --ticker is required")
    cold_start_date = args.rebuild_start or (args.start_date - timedelta(days=30))
    rebuild_start = datetime.combine(cold_start_date, time(4, 0), tzinfo=NEW_YORK)
    units = [(ticker, session) for ticker in tickers for session in dates_between(args.start_date, args.end_date)]
    counts = {"completed": 0, "skipped": 0, "failed": 0}

    print("Structural Level Book checkpoint builder")
    print(
        f"Units: {len(units)} | Tickers: {len(tickers)} | Sessions: "
        f"{args.start_date} to {args.end_date} | Cold start: {cold_start_date}"
    )
    for index, (ticker, session) in enumerate(units, start=1):
        label = f"[{index}/{len(units)}] {ticker} {session}"
        print(f"ACTIVE    {label}", flush=True)
        try:
            result = request_checkpoint(
                base_url=args.qmd_url,
                operator_token=args.operator_token,
                ticker=ticker,
                session_date=session,
                rebuild_start=rebuild_start,
                event_limit=args.event_limit,
                timeout_seconds=args.timeout_seconds,
            )
        except RuntimeError as exc:
            counts["failed"] += 1
            print(f"FAILED    {label} | {exc}", file=sys.stderr, flush=True)
            if not args.continue_on_error:
                break
            continue
        status = str(result.get("status", "")).strip()
        if status == "skipped_non_session":
            counts["skipped"] += 1
            print(f"SKIPPED   {label} | non-session")
            continue
        if status != "completed":
            counts["failed"] += 1
            print(f"FAILED    {label} | unexpected status {status!r}", file=sys.stderr)
            if not args.continue_on_error:
                break
            continue
        counts["completed"] += 1
        print(
            f"COMPLETED {label} | events={result.get('event_count', 0)} "
            f"advanced={result.get('advanced_event_count', 0)} "
            f"cursor={result.get('checkpoint_arrival_sequence', 0)} "
            f"seed={result.get('seeded_from_session_date') or 'cold'}"
        )

    print(
        "Summary: "
        f"completed={counts['completed']} skipped={counts['skipped']} failed={counts['failed']}"
    )
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

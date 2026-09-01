#!/usr/bin/env python3
"""Build restart-safe end-of-day Structural Level Book checkpoints through QMD Live."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
import threading
import time as wall_time
import urllib.error
import urllib.request
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
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


def previous_weekday(value: date) -> date:
    """Move weekend checkpoint boundaries to the prior possible market day."""

    while value.weekday() >= 5:
        value -= timedelta(days=1)
    return value


def checkpoint_schedule(
    *,
    rebuild_start: date,
    target_start: date,
    target_end: date,
    bootstrap_days: int,
) -> tuple[date, ...]:
    """Return bounded bootstrap checkpoints followed by requested target days.

    A cold multi-month history can exceed the history service's intentionally
    bounded per-request event limit.  Periodic end-of-session checkpoints keep
    every request bounded while preserving one causal book and restart-safe
    lineage for the ticker.
    """

    scheduled: set[date] = set(dates_between(target_start, target_end))
    if bootstrap_days > 0 and rebuild_start < target_start:
        cursor = rebuild_start + timedelta(days=bootstrap_days)
        while cursor < target_start:
            scheduled.add(previous_weekday(cursor))
            cursor += timedelta(days=bootstrap_days)
        # Bound the final bootstrap-to-target gap even when the cadence does
        # not divide the requested historical window evenly.
        scheduled.add(previous_weekday(target_start - timedelta(days=1)))
    return tuple(sorted(scheduled))


def is_retryable_error(error: BaseException | str) -> bool:
    """Classify only transient capacity/transport failures for bounded retry."""

    message = str(error).lower()
    return any(
        marker in message
        for marker in (
            "http 429",
            "http 502",
            "http 503",
            "http 504",
            '"retryable": true',
            "timed out",
            "temporarily unavailable",
            "error sending request",
            "connection aborted",
            "connection reset",
            "unexpected eof",
        )
    )


def is_no_history_error(error: BaseException | str) -> bool:
    message = str(error).lower()
    return (
        "structure_checkpoint_source_unavailable" in message
        and "found no canonical events" in message
    )


def load_tickers(
    *, inline: list[str] | None, ticker_files: list[str] | None
) -> tuple[str, ...]:
    """Load a bounded ticker universe from CLI values or a persisted API response.

    The file may be newline-delimited symbols, a JSON list, or a scanner-market
    response containing ``rows`` with ``symbol``/``ticker`` fields.  This avoids
    Windows command-line limits for session-sized historical populations.
    """

    values = list(inline or [])
    for ticker_file in ticker_files or []:
        path = Path(ticker_file).expanduser().resolve()
        if not path.is_file():
            raise ValueError(f"ticker file is unavailable: {path}")
        raw = path.read_text(encoding="utf-8-sig")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            values.extend(line for line in raw.splitlines() if line.strip())
        else:
            if isinstance(payload, dict):
                payload = payload.get("rows", payload.get("tickers", []))
            if not isinstance(payload, list):
                raise ValueError("ticker file JSON must be a list or contain rows/tickers")
            for item in payload:
                if isinstance(item, str):
                    values.append(item)
                elif isinstance(item, dict):
                    symbol = item.get("symbol", item.get("ticker", ""))
                    if symbol:
                        values.append(str(symbol))
    return tuple(sorted({value.strip().upper() for value in values if value.strip()}))


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
    parser.add_argument("--ticker", action="append", help="Ticker; repeat for more than one")
    parser.add_argument(
        "--ticker-file",
        action="append",
        help=(
            "UTF-8 newline/JSON ticker universe. JSON may be a list or a scanner-market "
            "response with rows containing symbol/ticker."
        ),
    )
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
    parser.add_argument(
        "--bootstrap-days",
        type=int,
        default=14,
        help=(
            "Maximum calendar-day gap between historical bootstrap checkpoints "
            "before --start-date; use 0 to cold-rebuild directly into the first "
            "requested checkpoint (default: 14)"
        ),
    )
    parser.add_argument("--timeout-seconds", type=float, default=1800.0)
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help=(
            "Independent ticker workers; each ticker's session checkpoints remain "
            "strictly sequential (default: 4)"
        ),
    )
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--retry-delay-seconds", type=float, default=2.0)
    parser.add_argument(
        "--report-path",
        help="Optional restart-safe JSON status report written atomically after progress changes",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue independent ticker-days after a failed unit",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress per-unit stdout; the atomic JSON report remains authoritative",
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
    if not 0 <= args.bootstrap_days <= 31:
        raise SystemExit("--bootstrap-days must be between 0 and 31")
    if not 1 <= args.workers <= 32:
        raise SystemExit("--workers must be between 1 and 32")
    if not 0 <= args.max_retries <= 10:
        raise SystemExit("--max-retries must be between 0 and 10")
    if not 0.1 <= args.retry_delay_seconds <= 60:
        raise SystemExit("--retry-delay-seconds must be between 0.1 and 60")

    try:
        tickers = load_tickers(inline=args.ticker, ticker_files=args.ticker_file)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if not tickers:
        raise SystemExit("at least one non-empty --ticker or --ticker-file is required")
    cold_start_date = args.rebuild_start or (args.start_date - timedelta(days=30))
    rebuild_start = datetime.combine(cold_start_date, time(4, 0), tzinfo=NEW_YORK)
    sessions = checkpoint_schedule(
        rebuild_start=cold_start_date,
        target_start=args.start_date,
        target_end=args.end_date,
        bootstrap_days=args.bootstrap_days,
    )
    target_sessions = frozenset(dates_between(args.start_date, args.end_date))
    units = [(ticker, session) for ticker in tickers for session in sessions]
    counts = {
        "completed": 0,
        "skipped": 0,
        "unavailable": 0,
        "retried": 0,
        "failed": 0,
        "blocked": 0,
    }
    issues: list[dict[str, object]] = []
    active_units: dict[str, str] = {}
    counter_lock = threading.Lock()
    output_lock = threading.Lock()
    stop_requested = threading.Event()
    report_path = Path(args.report_path).expanduser().resolve() if args.report_path else None

    def write_report(snapshot: dict[str, int], *, status: str) -> None:
        if report_path is None:
            return
        report_path.parent.mkdir(parents=True, exist_ok=True)
        finished = int(snapshot.get("finished") or 0)
        active = len(active_units)
        payload = {
            "schema_version": 1,
            "status": status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "ticker_count": len(tickers),
            "session_count": len(sessions),
            "total_units": len(units),
            "target_start": args.start_date.isoformat(),
            "target_end": args.end_date.isoformat(),
            "rebuild_start": cold_start_date.isoformat(),
            "counts": {
                **snapshot,
                "active": active,
                "queued": max(0, len(units) - finished - active),
            },
            "active_units": sorted(active_units.values()),
            # Bounded, actionable identities are required for a restart-safe
            # long-running campaign. Counts alone previously made a failed
            # ticker impossible to recover after terminal output scrolled.
            "issues": list(issues[-100:]),
        }
        temporary = report_path.with_suffix(report_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(temporary, report_path)

    def emit(message: str, *, error: bool = False) -> None:
        if args.quiet and not error:
            return
        with output_lock:
            print(message, file=sys.stderr if error else sys.stdout, flush=True)

    def record(
        status: str,
        amount: int = 1,
        *,
        issue: dict[str, object] | None = None,
    ) -> dict[str, int]:
        with counter_lock:
            if issue is not None:
                issues.append(issue)
            counts[status] += amount
            snapshot = dict(counts)
            snapshot["finished"] = sum(
                snapshot[key]
                for key in ("completed", "skipped", "unavailable", "failed", "blocked")
            )
            write_report(snapshot, status="running")
            return snapshot

    def set_active(ticker: str, label: str | None) -> None:
        with counter_lock:
            if label is None:
                active_units.pop(ticker, None)
            else:
                active_units[ticker] = label
            snapshot = dict(counts)
            snapshot["finished"] = sum(
                snapshot[key]
                for key in ("completed", "skipped", "unavailable", "failed", "blocked")
            )
            write_report(snapshot, status="running")

    def progress(snapshot: dict[str, int]) -> str:
        return (
            f"progress={snapshot['finished']}/{len(units)} "
            f"completed={snapshot['completed']} skipped={snapshot['skipped']} "
            f"unavailable={snapshot['unavailable']} "
            f"retried={snapshot['retried']} failed={snapshot['failed']} "
            f"blocked={snapshot['blocked']}"
        )

    ticker_ordinals = {ticker: index for index, ticker in enumerate(tickers)}
    session_ordinals = {session: index for index, session in enumerate(sessions)}

    def build_ticker(ticker: str) -> None:
        for session_index, session in enumerate(sessions):
            if stop_requested.is_set() and not args.continue_on_error:
                return
            unit_number = (
                ticker_ordinals[ticker] * len(sessions) + session_ordinals[session] + 1
            )
            phase = "target" if session in target_sessions else "bootstrap"
            label = f"[{unit_number}/{len(units)}] {ticker} {session} ({phase})"
            set_active(ticker, label)
            emit(f"ACTIVE    {label}")
            result: dict | None = None
            failure: RuntimeError | None = None
            for attempt in range(args.max_retries + 1):
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
                    failure = None
                    break
                except RuntimeError as exc:
                    failure = exc
                    if is_no_history_error(exc):
                        break
                    if attempt >= args.max_retries or not is_retryable_error(exc):
                        break
                    snapshot = record("retried")
                    delay = args.retry_delay_seconds * (2**attempt)
                    emit(
                        f"RETRYING  {label} | attempt={attempt + 1}/{args.max_retries} "
                        f"delay={delay:.1f}s reason={exc} | {progress(snapshot)}",
                        error=True,
                    )
                    wall_time.sleep(delay)
            set_active(ticker, None)
            if failure is not None or result is None:
                if failure is not None and is_no_history_error(failure):
                    snapshot = record("unavailable")
                    emit(
                        f"UNAVAILABLE {label} | no canonical events yet; later days remain eligible | "
                        f"{progress(snapshot)}"
                    )
                    continue
                remaining = len(sessions) - session_index - 1
                snapshot = record(
                    "failed",
                    issue={
                        "ticker": ticker,
                        "session_date": session.isoformat(),
                        "error": str(failure),
                        "blocked_later_sessions": remaining,
                    },
                )
                if remaining:
                    snapshot = record("blocked", remaining)
                emit(f"FAILED    {label} | {failure} | {progress(snapshot)}", error=True)
                if remaining:
                    emit(
                        f"BLOCKED   {ticker} | {remaining} later session(s) depend on the failed base day | "
                        f"{progress(snapshot)}",
                        error=True,
                    )
                if not args.continue_on_error:
                    stop_requested.set()
                return
            status = str(result.get("status", "")).strip()
            if status == "already_current":
                snapshot = record("skipped")
                emit(
                    f"SKIPPED   {label} | already current at cursor="
                    f"{result.get('checkpoint_arrival_sequence', 0)} | "
                    f"{progress(snapshot)}"
                )
                continue
            if status == "skipped_non_session":
                snapshot = record("skipped")
                emit(
                    f"SKIPPED   {label} | non-session | {progress(snapshot)}"
                )
                continue
            if status != "completed":
                remaining = len(sessions) - session_index - 1
                snapshot = record(
                    "failed",
                    issue={
                        "ticker": ticker,
                        "session_date": session.isoformat(),
                        "error": f"unexpected status {status!r}",
                        "blocked_later_sessions": remaining,
                    },
                )
                if remaining:
                    snapshot = record("blocked", remaining)
                emit(
                    f"FAILED    {label} | unexpected status {status!r} | "
                    f"{progress(snapshot)}",
                    error=True,
                )
                if not args.continue_on_error:
                    stop_requested.set()
                return
            snapshot = record("completed")
            emit(
                f"COMPLETED {label} | events={result.get('event_count', 0)} "
                f"advanced={result.get('advanced_event_count', 0)} "
                f"cursor={result.get('checkpoint_arrival_sequence', 0)} "
                f"seed={result.get('seeded_from_session_date') or 'cold'} | "
                f"{progress(snapshot)}"
            )

    emit("Structural Level Book checkpoint builder")
    emit(
        f"QUEUED    units={len(units)} tickers={len(tickers)} "
        f"workers={min(args.workers, len(tickers))} sessions={args.start_date}..{args.end_date} "
        f"cold_start={cold_start_date} bootstrap_days={args.bootstrap_days}"
    )
    write_report({**counts, "finished": 0}, status="running")
    try:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(args.workers, len(tickers)),
            thread_name_prefix="structure-checkpoint",
        ) as executor:
            futures = [executor.submit(build_ticker, ticker) for ticker in tickers]
            for future in concurrent.futures.as_completed(futures):
                future.result()
    except KeyboardInterrupt:
        stop_requested.set()
        emit(
            "INTERRUPTED after the active ticker-days finish; completed daily checkpoints "
            "remain durable and the command can be rerun safely.",
            error=True,
        )
        write_report(
            {
                **counts,
                "finished": sum(
                    counts[key]
                    for key in ("completed", "skipped", "unavailable", "failed", "blocked")
                ),
            },
            status="interrupted",
        )
        return 130

    emit(
        "Summary: "
        f"completed={counts['completed']} skipped={counts['skipped']} "
        f"unavailable={counts['unavailable']} "
        f"retried={counts['retried']} failed={counts['failed']} blocked={counts['blocked']}"
    )
    write_report(
        {
            **counts,
            "finished": sum(
                counts[key]
                for key in ("completed", "skipped", "unavailable", "failed", "blocked")
            ),
        },
        status="failed" if counts["failed"] else "completed",
    )
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

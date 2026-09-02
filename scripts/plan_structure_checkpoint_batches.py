from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError


BOOTSTRAP_BUCKETS = (90, 56, 28, 14, 7, 3, 1)


def load_tickers(paths: list[Path]) -> list[str]:
    tickers: set[str] = set()
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload if isinstance(payload, list) else payload.get("tickers") or payload.get("rows") or []
        for row in rows:
            value = (
                row.get("ticker") or row.get("symbol") or row.get("sym")
                if isinstance(row, dict)
                else row
            )
            if str(value or "").strip():
                tickers.add(str(value).strip().upper())
    return sorted(tickers)


def bootstrap_days(*, total: int, maximum_session: int, event_budget: int) -> int:
    if total <= event_budget or maximum_session <= 0:
        return 0
    safe_sessions = max(1, event_budget // maximum_session)
    # Convert trading sessions to conservative calendar days. A bucket must be
    # no larger than the estimate; the runtime event cap remains authoritative.
    safe_calendar_days = max(1, safe_sessions * 7 // 5)
    return next((days for days in BOOTSTRAP_BUCKETS if days <= safe_calendar_days), 1)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Plan event-budgeted Generic Structure checkpoint ticker batches from the "
            "lightweight continuity index; raw compact-event tables are never scanned."
        )
    )
    parser.add_argument("--ticker-file", action="append", required=True, type=Path)
    parser.add_argument("--start-date", required=True, type=date.fromisoformat)
    parser.add_argument("--end-date", required=True, type=date.fromisoformat)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument(
        "--estimate-url",
        default="http://127.0.0.1:8801/estimate/generic-structure-event-counts",
    )
    parser.add_argument("--event-budget", type=int, default=3_500_000)
    parser.add_argument("--estimate-batch-size", type=int, default=2_000)
    args = parser.parse_args()
    if args.start_date > args.end_date:
        parser.error("--start-date must be on or before --end-date")
    if args.event_budget < 1:
        parser.error("--event-budget must be positive")
    if not 1 <= args.estimate_batch_size <= 25_000:
        parser.error("--estimate-batch-size must be between 1 and 25000")

    tickers = load_tickers(args.ticker_file)
    request_as_of = datetime.now(timezone.utc).isoformat()
    estimate_rows: list[dict[str, object]] = []
    estimates_payload: dict[str, object] = {}
    for offset in range(0, len(tickers), args.estimate_batch_size):
        batch = tickers[offset : offset + args.estimate_batch_size]
        request_payload = json.dumps(
            {
                "as_of": request_as_of,
                "start_date": args.start_date.isoformat(),
                "end_date": (args.end_date + timedelta(days=1)).isoformat(),
                "tickers": batch,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        request = Request(
            args.estimate_url,
            data=request_payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=180) as response:
                batch_payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"estimate endpoint returned HTTP {error.code} for ticker batch "
                f"{offset // args.estimate_batch_size + 1}: {detail}"
            ) from error
        if not estimates_payload:
            estimates_payload = batch_payload
        estimate_rows.extend(batch_payload.get("estimates", []))
    estimates_payload["estimates"] = estimate_rows
    estimates = {
        str(row["ticker"]).upper(): row
        for row in estimates_payload.get("estimates", [])
    }
    groups: dict[int, list[str]] = {}
    plan_rows: list[dict[str, int | str]] = []
    for ticker in tickers:
        estimate = estimates.get(ticker, {})
        total = int(estimate.get("total_events") or 0)
        maximum = int(estimate.get("max_session_events") or 0)
        days = bootstrap_days(
            total=total,
            maximum_session=maximum,
            event_budget=args.event_budget,
        )
        groups.setdefault(days, []).append(ticker)
        plan_rows.append(
            {
                "bootstrap_days": days,
                "max_session_events": maximum,
                "ticker": ticker,
                "total_events": total,
            }
        )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    group_files: dict[str, str] = {}
    for days, members in sorted(groups.items()):
        path = args.output_dir / f"bootstrap-{days}.json"
        path.write_text(json.dumps(members, indent=2) + "\n", encoding="utf-8")
        group_files[str(days)] = str(path.resolve())
    report = {
        "as_of": estimates_payload.get("as_of"),
        "event_budget": args.event_budget,
        "group_counts": {str(days): len(rows) for days, rows in sorted(groups.items())},
        "group_files": group_files,
        "rows": plan_rows,
        "schema_version": 2,
        "source": estimates_payload.get("source"),
        "ticker_count": len(tickers),
    }
    report_path = args.output_dir / "plan.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("ticker_count", "group_counts", "group_files")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

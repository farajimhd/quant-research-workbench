from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any, Iterable


DEFAULT_OUTPUT_ROOT = Path(r"D:\TradingML\runtimes\qmd_validation")


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp must include a timezone: {value}")
    return parsed.astimezone(UTC)


def timestamp(value: Any, *, end_of_day: bool = False) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) == 10:
        parsed_date = date.fromisoformat(text)
        return datetime.combine(
            parsed_date,
            time.max if end_of_day else time.min,
            tzinfo=UTC,
        )
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def get_json(base_url: str, ticker: str, as_of: datetime, timeout: float) -> dict[str, Any]:
    query = urllib.parse.urlencode({"as_of": as_of.isoformat()})
    url = f"{base_url.rstrip('/')}/api/trading/ticker-facts/{urllib.parse.quote(ticker)}?{query}"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f"backend returned HTTP {response.status}")
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise ValueError("ticker-facts response must be an object")
    return payload


def nested(payload: dict[str, Any], path: str) -> Any:
    value: Any = payload
    for component in path.split("."):
        if isinstance(value, list):
            value = value[0] if value else {}
        if not isinstance(value, dict):
            return None
        value = value.get(component)
    return value


def temporal_evidence(payload: dict[str, Any]) -> Iterable[tuple[str, Any, bool]]:
    raw_facts = payload.get("facts") or {}
    if isinstance(raw_facts, list):
        raw_facts = raw_facts[0] if raw_facts else {}
    facts = dict(raw_facts) if isinstance(raw_facts, dict) else {}
    yield "identity.universe_date", nested(facts, "identity.universe_date"), True
    yield "market.observed_at_utc", nested(facts, "market.observed_at_utc"), False
    yield "float.effective_date", nested(facts, "float.effective_date"), True
    yield "borrow.observed_at_utc", nested(facts, "borrow.observed_at_utc"), False
    yield "short_interest.published_at_utc", nested(
        facts, "short_interest.published_at_utc"
    ) or nested(facts, "short_interest.inserted_at"), False
    for index, row in enumerate(payload.get("fundamentals") or []):
        if isinstance(row, dict):
            yield f"fundamentals[{index}].recorded_at_utc", row.get("recorded_at_utc"), False
    for name, row in dict(payload.get("freshness") or {}).items():
        if isinstance(row, dict):
            yield f"freshness.{name}.available_at", row.get("available_at"), False
    for index, row in enumerate(payload.get("identifiers") or []):
        if isinstance(row, dict):
            yield f"identifiers[{index}].last_seen_at_utc", row.get("last_seen_at_utc"), False


def validate_snapshot(payload: dict[str, Any], requested: datetime) -> dict[str, Any]:
    returned = parse_timestamp(str(payload.get("as_of") or ""))
    failures: list[str] = []
    if returned != requested:
        failures.append(
            f"response as_of drifted: requested={requested.isoformat()} returned={returned.isoformat()}"
        )
    if str(payload.get("status") or "") != "ready":
        failures.append(f"ticker-facts status is {payload.get('status')!r}, expected 'ready'")
    checked = 0
    latest: datetime | None = None
    latest_field = ""
    for field, raw, end_of_day in temporal_evidence(payload):
        observed = timestamp(raw, end_of_day=end_of_day)
        if observed is None:
            continue
        checked += 1
        if latest is None or observed > latest:
            latest = observed
            latest_field = field
        if observed > requested:
            failures.append(
                f"future evidence: {field}={observed.isoformat()} after {requested.isoformat()}"
            )
    return {
        "as_of": requested.isoformat(),
        "status": str(payload.get("status") or ""),
        "temporal_fields_checked": checked,
        "latest_evidence_at": latest.isoformat() if latest else None,
        "latest_evidence_field": latest_field,
        "failures": failures,
    }


def write_report(root: Path, report: dict[str, Any]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    captured = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = root / f"point_in_time_enrichment_{captured}.json"
    fd, temporary = tempfile.mkstemp(prefix=".pit-", suffix=".json", dir=root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate database-backed point-in-time enrichment at two causal cutoffs."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--ticker", default="AAPL")
    parser.add_argument("--before", default="2026-08-07T14:00:00Z")
    parser.add_argument("--after", default="2026-08-07T15:00:00Z")
    parser.add_argument("--change-field", default="facts.borrow.observed_at_utc")
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--json", action="store_true", help="Print the full report to stdout.")
    args = parser.parse_args()

    try:
        before = parse_timestamp(args.before)
        after = parse_timestamp(args.after)
        if before >= after:
            raise ValueError("--before must be earlier than --after")
        ticker = args.ticker.strip().upper()
        if not ticker or len(ticker) > 32 or not all(
            value.isalnum() or value in ".-" for value in ticker
        ):
            raise ValueError("--ticker must be a bounded market symbol")
        before_payload = get_json(args.base_url, ticker, before, args.timeout)
        after_payload = get_json(args.base_url, ticker, after, args.timeout)
        before_result = validate_snapshot(before_payload, before)
        after_result = validate_snapshot(after_payload, after)
        before_change = nested(before_payload, args.change_field)
        after_change = nested(after_payload, args.change_field)
        failures = [*before_result["failures"], *after_result["failures"]]
        if not before_change or not after_change:
            failures.append(f"change evidence is missing at {args.change_field}")
        elif before_change == after_change:
            failures.append(
                f"change evidence did not advance at {args.change_field}: {before_change}"
            )
        report = {
            "schema_version": 1,
            "captured_at": datetime.now(UTC).isoformat(),
            "status": "passed" if not failures else "failed",
            "backend": args.base_url,
            "ticker": ticker,
            "change_evidence": {
                "field": args.change_field,
                "before": before_change,
                "after": after_change,
            },
            "snapshots": [before_result, after_result],
            "failures": failures,
        }
        destination = write_report(args.output_root, report)
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True))
        else:
            print(f"PIT enrichment {report['status'].upper()}  {ticker}")
            print(
                f"cutoffs  {before.isoformat()} -> {after.isoformat()}  "
                f"fields={before_result['temporal_fields_checked']}+{after_result['temporal_fields_checked']}"
            )
            print(f"report   {destination}")
        if failures:
            for failure in failures:
                print(f"failure  {failure}", file=sys.stderr)
            return 1
        return 0
    except Exception as exc:
        print(f"PIT enrichment FAILED  {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

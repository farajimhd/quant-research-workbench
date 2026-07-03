from __future__ import annotations

import argparse
import json
import math
import os
import random
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib import error, parse, request

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.clickhouse import (  # noqa: E402
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    discover_clickhouse_env_files,
)
from research.mlops.env import load_env_files  # noqa: E402


DEFAULT_OUTPUT_ROOT = Path("prepared/reference_data/experiments/sec_public_float_estimate_test")
MASSIVE_AGG_URL = "https://api.massive.com/v2/aggs/ticker/{ticker}/range/1/day/{start}/{end}"


@dataclass(frozen=True, slots=True)
class Candidate:
    ticker: str
    cik: str
    issuer_id: str
    security_id: str
    listing_id: str
    symbol_id: str
    public_float_value: float
    public_float_period_end: str
    public_float_filed_at: str
    accession_number: str
    massive_free_float: int
    massive_float_effective_date: str
    split_factor: float
    split_count: int


@dataclass(frozen=True, slots=True)
class PriceBar:
    price_date: str
    close: float
    open: float | None
    high: float | None
    low: float | None
    volume: float | None
    vwap: float | None
    timestamp_ms: int
    adjusted: bool


@dataclass(frozen=True, slots=True)
class TestRow:
    ticker: str
    cik: str
    symbol_id: str
    public_float_value: float
    public_float_period_end: str
    public_float_filed_at: str
    accession_number: str
    price_date: str | None
    close_price: float | None
    massive_free_float: int
    massive_float_effective_date: str
    raw_estimated_float_shares: float | None
    split_factor: float
    split_count: int
    split_adjusted_estimated_float_shares: float | None
    absolute_error_shares: float | None
    pct_error: float | None
    abs_pct_error: float | None
    sec_lead_days: int | None
    status: str
    error: str


def main() -> None:
    load_env_files(discover_clickhouse_env_files())
    args = parse_args()
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("MASSIVE_API_KEY is required for this experiment.")
    output_root = Path(args.output_root)
    run_root = output_root / datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    run_root.mkdir(parents=True, exist_ok=True)
    client = ClickHouseHttpClient(args.clickhouse_url, args.clickhouse_user, default_clickhouse_password())
    try:
        candidates = load_candidates(client, args)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "Could not load experiment candidates from ClickHouse. "
            f"Check --clickhouse-url={args.clickhouse_url!r}, the ClickHouse service, and network access."
        ) from exc
    if args.shuffle:
        random.Random(args.seed).shuffle(candidates)
    candidates = candidates[: args.sample_size]
    rows: list[TestRow] = []
    started = time.perf_counter()
    print(
        f"sec_public_float_estimate_test candidates={len(candidates):,} "
        f"sample_size={args.sample_size:,} adjusted_prices={args.adjusted_prices}",
        flush=True,
    )
    for index, candidate in enumerate(candidates, 1):
        row = evaluate_candidate(candidate, api_key=api_key, args=args)
        rows.append(row)
        if index == 1 or index % args.progress_every == 0 or index == len(candidates):
            ok_rows = [item for item in rows if item.status == "ok"]
            median_abs = median([item.abs_pct_error for item in ok_rows if item.abs_pct_error is not None])
            print(
                f"progress {index:,}/{len(candidates):,} ok={len(ok_rows):,} "
                f"failed={len(rows) - len(ok_rows):,} median_abs_pct_error={format_pct(median_abs)} "
                f"elapsed={time.perf_counter() - started:.1f}s latest={candidate.ticker} status={row.status}",
                flush=True,
            )
        if args.request_min_interval_seconds > 0 and index < len(candidates):
            time.sleep(args.request_min_interval_seconds)
    write_jsonl(run_root / "sec_public_float_estimate_rows.jsonl", [asdict(row) for row in rows])
    summary = build_summary(rows, args=args, run_root=run_root)
    (run_root / "sec_public_float_estimate_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print("summary=" + json.dumps(summary, sort_keys=True), flush=True)
    print(f"run_root={run_root}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Test SEC EntityPublicFloat-derived float shares against Massive free_float.")
    parser.add_argument("--clickhouse-url", default=default_clickhouse_url())
    parser.add_argument("--clickhouse-user", default=default_clickhouse_user())
    parser.add_argument("--database", default="q_live")
    parser.add_argument("--sample-size", type=int, default=100)
    parser.add_argument("--candidate-pool-size", type=int, default=2000)
    parser.add_argument("--min-period-date", default="2023-01-01")
    parser.add_argument("--max-period-to-massive-days", type=int, default=520)
    parser.add_argument("--price-lookback-days", type=int, default=7)
    parser.add_argument("--request-min-interval-seconds", type=float, default=0.12)
    parser.add_argument("--timeout-seconds", type=float, default=30.0)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--retry-base-seconds", type=float, default=1.0)
    parser.add_argument("--progress-every", type=int, default=10)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--shuffle", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--adjusted-prices", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    return parser.parse_args()


def load_candidates(client: ClickHouseHttpClient, args: argparse.Namespace) -> list[Candidate]:
    db = quote_name(args.database)
    q = f"""
WITH sec_float AS
(
    SELECT
        cik,
        argMax(value, filed_at_utc) AS public_float_value,
        argMax(period_end_date, filed_at_utc) AS public_float_period_end,
        max(filed_at_utc) AS public_float_filed_at,
        argMax(accession_number, filed_at_utc) AS accession_number
    FROM {db}.sec_xbrl_company_fact_v1 FINAL
    WHERE tag = 'EntityPublicFloat'
      AND unit_code = 'USD'
      AND value > 0
      AND filed_at_utc IS NOT NULL
      AND period_end_date >= toDate('{args.min_period_date}')
    GROUP BY cik
),
bridge AS
(
    SELECT
        cik,
        any(issuer_id) AS issuer_id,
        any(security_id) AS security_id,
        any(listing_id) AS listing_id,
        any(symbol_id) AS symbol_id,
        any(ticker) AS ticker
    FROM {db}.id_sec_market_bridge_v1 FINAL
    WHERE mapping_status = 'active'
      AND ambiguity_status = 'unique'
    GROUP BY cik
    HAVING symbol_id IS NOT NULL AND symbol_id != ''
),
massive_float AS
(
    SELECT
        symbol_id,
        argMax(free_float, effective_date) AS massive_free_float,
        max(effective_date) AS massive_float_effective_date
    FROM {db}.market_security_float_v1 FINAL
    WHERE free_float IS NOT NULL AND free_float > 0
    GROUP BY symbol_id
),
split_factors AS
(
    SELECT
        b.symbol_id AS symbol_id,
        exp(sum(log(if(s.split_from > 0, s.split_to / s.split_from, 1.0)))) AS split_factor,
        countIf(s.symbol_id != '') AS split_count
    FROM bridge AS b
    INNER JOIN sec_float AS sf ON sf.cik = b.cik
    INNER JOIN massive_float AS mf ON mf.symbol_id = b.symbol_id
    LEFT JOIN {db}.market_stock_split_v1 AS s FINAL
        ON s.symbol_id = b.symbol_id
       AND s.execution_date > sf.public_float_period_end
       AND s.execution_date <= mf.massive_float_effective_date
    GROUP BY b.symbol_id
)
SELECT
    upper(bridge.ticker) AS ticker,
    bridge.cik AS cik,
    bridge.issuer_id AS issuer_id,
    bridge.security_id AS security_id,
    bridge.listing_id AS listing_id,
    bridge.symbol_id AS symbol_id,
    sec_float.public_float_value AS public_float_value,
    toString(sec_float.public_float_period_end) AS public_float_period_end,
    toString(sec_float.public_float_filed_at) AS public_float_filed_at,
    ifNull(sec_float.accession_number, '') AS accession_number,
    massive_float.massive_free_float AS massive_free_float,
    toString(massive_float.massive_float_effective_date) AS massive_float_effective_date,
    ifNull(split_factors.split_factor, 1.0) AS split_factor,
    ifNull(split_factors.split_count, 0) AS split_count
FROM sec_float
INNER JOIN bridge ON bridge.cik = sec_float.cik
INNER JOIN massive_float ON massive_float.symbol_id = bridge.symbol_id
LEFT JOIN split_factors ON split_factors.symbol_id = bridge.symbol_id
WHERE sec_float.public_float_period_end <= massive_float.massive_float_effective_date
  AND dateDiff('day', sec_float.public_float_period_end, massive_float.massive_float_effective_date) <= {int(args.max_period_to_massive_days)}
ORDER BY cityHash64(bridge.symbol_id)
LIMIT {int(args.candidate_pool_size)}
FORMAT JSONEachRow
"""
    rows = [json.loads(line) for line in client.execute(q).splitlines() if line.strip()]
    return [
        Candidate(
            ticker=str(row["ticker"]),
            cik=str(row["cik"]),
            issuer_id=str(row["issuer_id"]),
            security_id=str(row["security_id"]),
            listing_id=str(row["listing_id"]),
            symbol_id=str(row["symbol_id"]),
            public_float_value=float(row["public_float_value"]),
            public_float_period_end=str(row["public_float_period_end"]),
            public_float_filed_at=str(row["public_float_filed_at"]),
            accession_number=str(row.get("accession_number") or ""),
            massive_free_float=int(float(row["massive_free_float"])),
            massive_float_effective_date=str(row["massive_float_effective_date"]),
            split_factor=float(row.get("split_factor") or 1.0),
            split_count=int(row.get("split_count") or 0),
        )
        for row in rows
    ]


def evaluate_candidate(candidate: Candidate, *, api_key: str, args: argparse.Namespace) -> TestRow:
    try:
        bar = fetch_daily_price(
            candidate.ticker,
            measurement_date=parse_date(candidate.public_float_period_end),
            api_key=api_key,
            lookback_days=args.price_lookback_days,
            adjusted=args.adjusted_prices,
            timeout_seconds=args.timeout_seconds,
            max_retries=args.max_retries,
            retry_base_seconds=args.retry_base_seconds,
        )
        if bar is None:
            return failed_row(candidate, "no_price_bar", "Massive aggregate returned no daily bar in lookback window.")
        raw_estimated = candidate.public_float_value / bar.close if bar.close > 0 else None
        adjusted_estimated = raw_estimated * candidate.split_factor if raw_estimated is not None else None
        pct_error = (
            adjusted_estimated / candidate.massive_free_float - 1.0
            if adjusted_estimated is not None and candidate.massive_free_float > 0
            else None
        )
        sec_lead_days = (
            parse_date(candidate.massive_float_effective_date) - parse_datetime_date(candidate.public_float_filed_at)
        ).days
        return TestRow(
            ticker=candidate.ticker,
            cik=candidate.cik,
            symbol_id=candidate.symbol_id,
            public_float_value=candidate.public_float_value,
            public_float_period_end=candidate.public_float_period_end,
            public_float_filed_at=candidate.public_float_filed_at,
            accession_number=candidate.accession_number,
            price_date=bar.price_date,
            close_price=bar.close,
            massive_free_float=candidate.massive_free_float,
            massive_float_effective_date=candidate.massive_float_effective_date,
            raw_estimated_float_shares=raw_estimated,
            split_factor=candidate.split_factor,
            split_count=candidate.split_count,
            split_adjusted_estimated_float_shares=adjusted_estimated,
            absolute_error_shares=adjusted_estimated - candidate.massive_free_float if adjusted_estimated is not None else None,
            pct_error=pct_error,
            abs_pct_error=abs(pct_error) if pct_error is not None else None,
            sec_lead_days=sec_lead_days,
            status="ok",
            error="",
        )
    except Exception as exc:  # noqa: BLE001
        return failed_row(candidate, "failed", repr(exc))


def fetch_daily_price(
    ticker: str,
    *,
    measurement_date: date,
    api_key: str,
    lookback_days: int,
    adjusted: bool,
    timeout_seconds: float,
    max_retries: int,
    retry_base_seconds: float,
) -> PriceBar | None:
    start = measurement_date - timedelta(days=lookback_days)
    params = {
        "adjusted": "true" if adjusted else "false",
        "sort": "desc",
        "limit": "5000",
        "apiKey": api_key,
    }
    url = MASSIVE_AGG_URL.format(
        ticker=parse.quote(ticker),
        start=start.isoformat(),
        end=measurement_date.isoformat(),
    )
    url = url + "?" + parse.urlencode(params)
    payload = request_json_with_retries(url, timeout_seconds=timeout_seconds, max_retries=max_retries, retry_base_seconds=retry_base_seconds)
    results = payload.get("results") or []
    if not results:
        return None
    row = results[0]
    ts_ms = int(row.get("t") or 0)
    return PriceBar(
        price_date=datetime.fromtimestamp(ts_ms / 1000, UTC).date().isoformat(),
        close=float(row["c"]),
        open=optional_float(row.get("o")),
        high=optional_float(row.get("h")),
        low=optional_float(row.get("l")),
        volume=optional_float(row.get("v")),
        vwap=optional_float(row.get("vw")),
        timestamp_ms=ts_ms,
        adjusted=bool(payload.get("adjusted")),
    )


def request_json_with_retries(url: str, *, timeout_seconds: float, max_retries: int, retry_base_seconds: float) -> dict[str, Any]:
    for attempt in range(max_retries + 1):
        try:
            req = request.Request(url, method="GET", headers={"User-Agent": "quant-research-workbench/sec-float-test"})
            with request.urlopen(req, timeout=timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            if exc.code in {429, 500, 502, 503, 504} and attempt < max_retries:
                time.sleep(min(30.0, retry_base_seconds * (2**attempt)))
                continue
            raise RuntimeError(f"Massive HTTP {exc.code}: {body[:500]}") from exc
        except error.URLError:
            if attempt < max_retries:
                time.sleep(min(30.0, retry_base_seconds * (2**attempt)))
                continue
            raise
    raise RuntimeError("request retry loop exhausted")


def failed_row(candidate: Candidate, status: str, error_text: str) -> TestRow:
    return TestRow(
        ticker=candidate.ticker,
        cik=candidate.cik,
        symbol_id=candidate.symbol_id,
        public_float_value=candidate.public_float_value,
        public_float_period_end=candidate.public_float_period_end,
        public_float_filed_at=candidate.public_float_filed_at,
        accession_number=candidate.accession_number,
        price_date=None,
        close_price=None,
        massive_free_float=candidate.massive_free_float,
        massive_float_effective_date=candidate.massive_float_effective_date,
        raw_estimated_float_shares=None,
        split_factor=candidate.split_factor,
        split_count=candidate.split_count,
        split_adjusted_estimated_float_shares=None,
        absolute_error_shares=None,
        pct_error=None,
        abs_pct_error=None,
        sec_lead_days=None,
        status=status,
        error=error_text,
    )


def build_summary(rows: list[TestRow], *, args: argparse.Namespace, run_root: Path) -> dict[str, Any]:
    ok = [row for row in rows if row.status == "ok" and row.abs_pct_error is not None]
    abs_errors = [row.abs_pct_error for row in ok if row.abs_pct_error is not None]
    pct_errors = [row.pct_error for row in ok if row.pct_error is not None]
    lead_days = [row.sec_lead_days for row in ok if row.sec_lead_days is not None]
    split_rows = [row for row in ok if row.split_count > 0]
    sec_early_or_same_day = [row for row in ok if row.sec_lead_days is not None and row.sec_lead_days >= 0]
    sec_late = [row for row in ok if row.sec_lead_days is not None and row.sec_lead_days < 0]
    return {
        "run_root": str(run_root),
        "sample_size": args.sample_size,
        "candidate_pool_size": args.candidate_pool_size,
        "rows": len(rows),
        "ok_rows": len(ok),
        "failed_rows": len(rows) - len(ok),
        "split_sensitive_rows": len(split_rows),
        "adjusted_prices": args.adjusted_prices,
        "abs_pct_error": distribution(abs_errors),
        "pct_error": distribution(pct_errors),
        "sec_lead_days": distribution([float(value) for value in lead_days]),
        "within_5pct": count_within(abs_errors, 0.05),
        "within_10pct": count_within(abs_errors, 0.10),
        "within_20pct": count_within(abs_errors, 0.20),
        "outlier_gt_50pct": count_above(abs_errors, 0.50),
        "outlier_gt_100pct": count_above(abs_errors, 1.00),
        "sec_filed_before_massive_effective": sum(1 for row in ok if row.sec_lead_days is not None and row.sec_lead_days > 0),
        "sec_filed_on_or_before_massive_effective": len(sec_early_or_same_day),
        "sec_filed_after_massive_effective": sum(1 for row in ok if row.sec_lead_days is not None and row.sec_lead_days < 0),
        "sec_available_on_or_before_massive_effective_subset": subset_summary(sec_early_or_same_day),
        "sec_available_after_massive_effective_subset": subset_summary(sec_late),
        "split_sensitive_subset": subset_summary(split_rows),
        "best_examples": [asdict(row) for row in sorted(ok, key=lambda item: item.abs_pct_error or math.inf)[:10]],
        "worst_examples": [asdict(row) for row in sorted(ok, key=lambda item: item.abs_pct_error or -math.inf, reverse=True)[:10]],
        "failed_status_counts": status_counts(row.status for row in rows if row.status != "ok"),
    }


def subset_summary(rows: list[TestRow]) -> dict[str, Any]:
    abs_errors = [row.abs_pct_error for row in rows if row.abs_pct_error is not None]
    lead_days = [float(row.sec_lead_days) for row in rows if row.sec_lead_days is not None]
    return {
        "rows": len(rows),
        "abs_pct_error": distribution(abs_errors),
        "sec_lead_days": distribution(lead_days),
        "within_10pct": count_within(abs_errors, 0.10),
        "within_20pct": count_within(abs_errors, 0.20),
    }


def distribution(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "min": None, "p10": None, "median": None, "mean": None, "p90": None, "max": None}
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "min": ordered[0],
        "p10": quantile(ordered, 0.10),
        "median": statistics.median(ordered),
        "mean": statistics.fmean(ordered),
        "p90": quantile(ordered, 0.90),
        "max": ordered[-1],
    }


def quantile(ordered: list[float], q: float) -> float:
    if not ordered:
        return float("nan")
    idx = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * q)))
    return ordered[idx]


def count_within(values: list[float], threshold: float) -> dict[str, int | float]:
    count = sum(1 for value in values if value <= threshold)
    return {"count": count, "fraction": count / len(values) if values else 0.0}


def count_above(values: list[float], threshold: float) -> dict[str, int | float]:
    count = sum(1 for value in values if value > threshold)
    return {"count": count, "fraction": count / len(values) if values else 0.0}


def status_counts(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[str(value)] = counts.get(str(value), 0) + 1
    return counts


def median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def format_pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.2f}%"


def optional_float(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


def parse_date(value: str) -> date:
    return date.fromisoformat(str(value)[:10])


def parse_datetime_date(value: str) -> date:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()


def quote_name(value: str) -> str:
    if not value.replace("_", "").isalnum():
        raise ValueError(f"Unsafe ClickHouse identifier: {value!r}")
    return value


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":"), default=str) + "\n")


if __name__ == "__main__":
    main()

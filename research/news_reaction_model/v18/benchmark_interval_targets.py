from __future__ import annotations

import argparse
import datetime as dt
import math
import time
from pathlib import Path

import numpy as np

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
)
from research.mlops.env import discover_env_files, load_env_files
from research.news_reaction_model.v17.prepare_targets import (
    IntervalRequest,
    event_rows_for_tickers,
    interval_aggregates,
    summarize_events,
)
from research.news_reaction_model.v18.config import LoaderConfig
from research.news_reaction_model.v18.prepare_data import (
    extended_open,
    raw_metrics_from_aggregate,
    timestamp_us,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
UTC = dt.timezone.utc


def parse_utc(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark V18 set-based interval aggregation against event materialization."
    )
    parser.add_argument("--ticker", default="AAPL")
    parser.add_argument("--anchor-session", default="2026-07-09")
    parser.add_argument("--target-session", default="2026-07-10")
    parser.add_argument(
        "--event-sessions",
        default="",
        help="Comma-separated target sessions; defaults to --target-session.",
    )
    parser.add_argument("--start", default="2026-07-10T13:30:00Z")
    parser.add_argument("--end", default="2026-07-10T14:30:00Z")
    parser.add_argument("--interval-count", type=int, default=1)
    args = parser.parse_args(argv)
    load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    config = LoaderConfig(workers=1, max_threads_per_query=2)
    client = ClickHouseHttpClient(
        default_clickhouse_url(),
        default_clickhouse_user(),
        default_clickhouse_password(),
    )
    anchor_day = dt.date.fromisoformat(args.anchor_session)
    target_days = [
        dt.date.fromisoformat(value.strip())
        for value in (args.event_sessions or args.target_session).split(",")
        if value.strip()
    ]
    start, end = parse_utc(args.start), parse_utc(args.end)
    requests = [
        IntervalRequest(
            row_index=index,
            ticker=args.ticker,
            anchor_start_us=timestamp_us(extended_open(anchor_day)),
            start_us=timestamp_us(start),
            end_us=timestamp_us(end),
        )
        for index in range(max(1, args.interval_count))
    ]
    request = requests[0]

    began = time.perf_counter()
    aggregates = interval_aggregates(client, config, requests)
    aggregate = aggregates.get(0)
    aggregate_seconds = time.perf_counter() - began
    aggregate_raw, aggregate_valid, _anchor = raw_metrics_from_aggregate(
        request, aggregate
    )

    began = time.perf_counter()
    prior = event_rows_for_tickers(client, config, [args.ticker], anchor_day)[args.ticker]
    event_days = [
        event_rows_for_tickers(client, config, [args.ticker], day)[args.ticker]
        for day in target_days
    ]
    materialized_seconds = time.perf_counter() - began
    anchor_candidates = prior[
        (prior[:, 6] > 0.5) & (prior[:, 0] < request.start_us)
    ]
    current_anchor_candidates = np.concatenate(
        [
            rows[(rows[:, 6] > 0.5) & (rows[:, 0] < request.start_us)]
            for rows in event_days
            if rows.size
        ],
        axis=0,
    ) if any(rows.size for rows in event_days) else np.empty((0, 8))
    if current_anchor_candidates.size:
        anchor = float(current_anchor_candidates[-1, 2])
    elif anchor_candidates.size:
        anchor = float(anchor_candidates[-1, 2])
    else:
        raise RuntimeError("Reference event path found no causal anchor.")
    reference16, reference_valid = summarize_events(
        event_days,
        start=start,
        end=end,
        anchor_price=anchor,
        minimum_observations=3,
    )
    reference = np.delete(reference16, [12, 13]).astype(np.float32, copy=False)
    comparable = aggregate_valid and reference_valid
    maximum_delta = (
        float(np.max(np.abs(aggregate_raw - reference)))
        if comparable
        else math.nan
    )
    print(
        f"AGGREGATE | seconds={aggregate_seconds:.3f} rows={len(aggregates):,} "
        f"valid={int(aggregate_valid)}"
    )
    print(
        f"MATERIALIZED | seconds={materialized_seconds:.3f} "
        f"event_rows={prior.shape[0] + sum(rows.shape[0] for rows in event_days):,} "
        f"valid={int(reference_valid)}"
    )
    print(
        f"EQUIVALENCE | comparable={int(comparable)} max_abs_delta={maximum_delta:.9g}"
    )
    if not comparable or not np.allclose(
        aggregate_raw, reference, rtol=1e-5, atol=1e-6
    ):
        raise RuntimeError(
            "Set-based interval metrics do not match the exact event reference."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

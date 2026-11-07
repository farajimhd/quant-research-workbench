"""Bounded workstation comparison of ordinal chunk sizes and query concurrency.

This is a read-only benchmark: no training or long qualification run is started.
Each case includes liquid and less-liquid names and reports per-timeframe output,
so abundant 100ms bars cannot hide a configuration producing no minute contexts.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import time

from . import source
from .contract import FRAMES, write_json
from .range_sql import range_query
from .sql import clock


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--endpoint")
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)
    c = source.client(args.endpoint)
    try:
        authority = source.preflight(c)
        endpoint = c.base_url
        inventory = {
            ticker: source.index(c, ticker, 2025)
            for ticker in ("AAPL", "GME", "LQD", "SLVO")
        }
    finally:
        source.close(c)
    cases = []
    matrix = [
        (20000, 8),
        (20000, 16),
        (20000, 32),
        (20000, 64),
        (100000, 16),
        (100000, 32),
        (500000, 32),
        (2000000, 16),
    ]
    for events, workers in matrix:
        tasks = list(range(max(16, workers)))

        def query(number):
            ticker = tuple(inventory)[number % 4]
            bounds = inventory[ticker]
            # Vary bounded origins across real sessions; avoid timing only a
            # single cached market block. All cases use the same frozen dates.
            row = bounds[20 + (number // 4) % 20]
            begin = int(row["first_ordinal"]) + int(row["event_count"]) // 3
            stop = min(begin + events, int(bounds[-1]["next_ordinal"]))
            transport = source.client(endpoint, 1)
            started = time.perf_counter()
            try:
                frame = source.arrow(
                    transport,
                    range_query(ticker, bounds, authority["rules"], begin, stop),
                    f"bargpt-profile-{events}-{workers}-{number}",
                    allow_empty=True,
                )
                counts = {
                    str(f): max(0, int((frame["resolution"] == f).sum()) - 63) * 0.2
                    for f in FRAMES[:4]
                }
                return dict(
                    seconds=time.perf_counter() - started,
                    potential_samples=counts,
                    ticker=ticker,
                )
            finally:
                source.close(transport)

        started = time.perf_counter()
        try:
            with ThreadPoolExecutor(workers) as pool:
                results = list(pool.map(query, tasks))
        except RuntimeError as error:
            if "MEMORY_LIMIT_EXCEEDED" not in str(error):
                raise
            # A failed configuration is a benchmark result, never a reason to
            # silently raise campaign memory limits or lose the other cases.
            case = dict(
                chunk_events=events,
                workers=workers,
                rejected="query memory exceeds 1 GiB",
            )
            cases.append(case)
            write_json(args.output / "profile.json", dict(cases=cases))
            print(json.dumps(case), flush=True)
            continue
        elapsed = time.perf_counter() - started
        counts = {
            str(f): sum(r["potential_samples"][str(f)] for r in results)
            for f in FRAMES[:4]
        }
        case = dict(
            chunk_events=events,
            workers=workers,
            queries=len(tasks),
            seconds=elapsed,
            queries_per_second=len(tasks) / elapsed,
            potential_samples_per_second={k: v / elapsed for k, v in counts.items()},
            maximum_query_seconds=max(r["seconds"] for r in results),
        )
        cases.append(case)
        write_json(args.output / "profile.json", dict(cases=cases))
        print(json.dumps(case), flush=True)
    coarse = []
    for days in (5, 20, 60):
        bounds = inventory["LQD"]
        group = bounds[2 : 2 + days]
        transport = source.client(endpoint, 1)
        started = time.perf_counter()
        try:
            frame = source.arrow(
                transport,
                range_query(
                    "LQD",
                    bounds,
                    authority["rules"],
                    int(group[0]["first_ordinal"]),
                    int(group[-1]["next_ordinal"]),
                    begin_us=clock(group[0]["source_date"], 4),
                    end_us=clock(group[-1]["source_date"], 20),
                ),
                f"bargpt-profile-coarse-{days}",
                allow_empty=True,
            )
            coarse.append(
                dict(days=days, seconds=time.perf_counter() - started, bars=len(frame))
            )
        finally:
            source.close(transport)
    write_json(
        args.output / "profile.json",
        dict(
            cases=cases,
            coarse=coarse,
            limitations=[
                "Warm/cold cache mix; bounded comparison, not sustained campaign throughput",
                "Sparse status timeline acquisition is measured by the real campaign pilot",
            ],
        ),
    )
    print(json.dumps(dict(coarse=coarse)), flush=True)


if __name__ == "__main__":
    main()

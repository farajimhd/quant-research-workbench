"""Short real-source concurrency/coalescing profile; no GPU or long qualification."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import time

import numpy as np
import psutil

from .contract import BAR, write_json
from .plan import bounds
from .producer import Gate, Worker


def main():
    """Compare identical three-session outputs at bounded query concurrency.

    Sessions are either queried individually or coalesced. Exact merged output
    equality is required before reporting a faster configuration. Cache warming
    affects timing; these measurements do not predict full-corpus completion.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--ticker", action="append", default=[])
    parser.add_argument("--day", default="2023-06-01")
    args = parser.parse_args()
    plan = json.loads((Path(args.output) / "plan.json").read_text())
    tickers = args.ticker or ["HBI", "AMAL"]
    cases = []
    for ticker in tickers:
        stream = next(
            s
            for s in plan["streams"]
            if s["ticker"] == ticker and s["year"] == int(args.day[:4])
        )
        rows = bounds(args.output, plan, stream)
        start = next(i for i, row in enumerate(rows) if row["source_date"] >= args.day)
        cases.append((stream, rows[start : start + 3]))
    evidence, reference = [], None
    for sessions, workers in ((1, 1), (3, 1), (3, 4)):
        started = time.monotonic()
        gate = Gate(args.output)
        with ThreadPoolExecutor(1) as io, ThreadPoolExecutor(workers) as queries:
            futures = []
            for stream, rows in cases:
                worker = Worker(args.output, plan, stream, gate, None, io, 2_000_000)
                for left in range(0, len(rows), sessions):
                    for coarse in (False, True):
                        futures.append(
                            queries.submit(
                                worker.fetch, rows[left : left + sessions], coarse
                            )
                        )
            # Preserve submission order for deterministic comparison after sort.
            outputs = [future.result() for future in futures]
        merged = []
        for i in range(5):
            data = np.concatenate([output[i] for output in outputs])
            # Ticker identity is not in BAR, so compare the multiset of complete
            # structured records. Sorting bytes is deterministic for equality.
            merged.append(np.sort(data.view(f"V{BAR.itemsize}").ravel()))
        if reference is None:
            reference = merged
        elif any(not np.array_equal(a, b) for a, b in zip(reference, merged)):
            raise ValueError("Profile coalescing changed canonical bars")
        evidence.append(
            dict(
                sessions_per_query=sessions,
                workers=workers,
                seconds=time.monotonic() - started,
                output_bars=sum(len(a) for a in merged),
                exact=True,
                rss_gib=psutil.Process().memory_info().rss / 1024**3,
                available_gib=psutil.virtual_memory().available / 1024**3,
            )
        )
    result = dict(
        cases=[
            dict(ticker=s["ticker"], days=[r["source_date"] for r in rows])
            for s, rows in cases
        ],
        configurations=evidence,
        note="Same-session outputs; later measurements may benefit from cache warming",
    )
    write_json(Path(args.output) / "profile.json", result)
    print(json.dumps(result))


if __name__ == "__main__":
    main()

"""Bounded source-parity and published-content audits, never a corpus scan."""

import argparse
import json
from pathlib import Path
import time

import numpy as np
import polars as pl

from .contract import BAR, BLOCK, FRAMES, SPECS, windows, file_hash, write_json, digest
from .plan import bounds, verify_code
from .producer import Gate, Worker
from .storage import Catalog, read_window
from ..shards import source
from ..shards.range_sql import range_query
from ..shards.sql import clock
from ..shards.origin_samples import arrays as unpack


def content(root, max_receipts=6, latest=False):
    """Hash/check a bounded mix of completed units; reconcile the small ledger.

    Full-corpus payload integrity is checked during ordinary future loader
    access. This pretraining audit reads at most ``max_receipts`` units.
    """
    root = Path(root)
    plan = json.loads((root / "plan.json").read_text())
    verify_code(plan)
    catalog = Catalog(root)
    receipts = []
    try:
        # Sample different liquidity groups and chronological split eras.
        candidates = catalog.db.execute(
            "SELECT stream,min(unit),max(unit) FROM units GROUP BY stream ORDER BY stream LIMIT ?",
            (max_receipts,),
        ).fetchall()
        chosen = []
        for key, first, last in candidates:
            chosen.extend((key, unit) for unit in sorted({first, last}))
        chosen = chosen[:max_receipts]
        if latest:
            chosen = catalog.db.execute(
                "SELECT stream,unit FROM units ORDER BY rowid DESC LIMIT ?",
                (max_receipts,),
            ).fetchall()
        for key, unit in chosen:
            path = root / "streams" / key / f"{unit:04d}"
            receipt = json.loads((path / "receipt.json").read_text())
            if receipt["hash"] != digest(
                {k: v for k, v in receipt.items() if k != "hash"}
            ):
                raise ValueError("Publication receipt seal changed")
            for item in receipt["files"]:
                if file_hash(path / item["name"]) != item["sha256"]:
                    raise ValueError("Published payload hash mismatch")
            available = [0] * 5
            evidence = np.zeros((5, 18), dtype=np.int64)
            carried = [0] * 5
            for segment in receipt["segments"]:
                data = np.load(
                    path / segment["name"], mmap_mode="r", allow_pickle=False
                )
                if data.dtype != BAR or not np.isfinite(data["values"]).all():
                    raise ValueError("Market schema/finite-value mismatch")
                if not data["valid"][:, :6].all() or (data["values"][:, :6] <= 0).any():
                    raise ValueError("Quote-only or incomplete trade bar")
                if (
                    not np.isin(data["valid"], (0, 1)).all()
                    or (np.diff(data["end_us"]) <= 0).any()
                ):
                    raise ValueError("Mask/order mismatch")
                available[segment["frame"]] += len(data)
                evidence[segment["frame"]] += data["valid"].sum(axis=0).astype(np.int64)
                carried[segment["frame"]] += int(
                    (
                        (data["values"][:, 11] == 0)
                        & (data["values"][:, 17] == 0)
                        & data["valid"][:, 9].astype(bool)
                        & data["valid"][:, 15].astype(bool)
                    ).sum()
                )
            block_files = sorted(path.glob("blocks-*.npy"))
            complete = [0] * 5
            if block_files:
                block = np.load(block_files[0], mmap_mode="r", allow_pickle=False)[0]
                if len(block) != BLOCK or (np.diff(block["end_us"]) <= 0).any():
                    raise ValueError("Unordered decision block")
                # Read all 38 views for one final decision, including original
                # phase-one backward split adjustment at each window endpoint.
                actions = [
                    a
                    for a in plan["actions"]["rows"]
                    if a["provider_ticker"] == receipt["ticker"]
                ]
                for i, frame in enumerate(FRAMES):
                    indices, present = windows(np.asarray([block["latest"][-1, i]]), i)
                    for last in indices[0, present[0], -1]:
                        sample = read_window(root, key, int(last), i, actions)
                        if sample["end_us"].max() > block["end_us"][-1]:
                            raise ValueError("Future bar leaked into causal token")
                        complete[i] += 1
            receipts.append(
                dict(
                    stream=key,
                    unit=unit,
                    bars=available,
                    complete_views=complete,
                    valid_observations=evidence.tolist(),
                    carried_quote_bars=carried,
                )
            )
        stats = catalog.stats()
        for split in (0, 1):
            counts = {
                r["liquid"]: r["selected"]
                for r in stats["blocks"]
                if r["split"] == split
            }
            if counts.get(1, 0) != 3 * counts.get(0, 0):
                raise ValueError("Selected blocks violate exact 75/25 ratio")
        return dict(
            receipts=receipts,
            ledger=stats,
            source_math=plan["code"],
            channels=18,
            utc_transform_channels=9,
            timeframes=FRAMES,
        )
    finally:
        catalog.db.close()


def parity(root, ticker, day, endpoint=None, memory_gib=3):
    """Compare one whole session with unmodified phase-one SQL on a wider span.

    Expanding by adjacent source sessions avoids phase one's arbitrary-chunk
    edge cut within the tested day. Compare exact Float32 values, validity and
    UTC clocks for every bar in all five phase-two timeframes.
    """
    from concurrent.futures import ThreadPoolExecutor

    plan = json.loads((Path(root) / "plan.json").read_text())
    stream = next(
        s
        for s in plan["streams"]
        if s["ticker"] == ticker and s["year"] == int(day[:4])
    )
    all_rows = bounds(root, plan, stream)
    position = next(i for i, row in enumerate(all_rows) if row["source_date"] == day)
    if position == 0 or position == len(all_rows) - 1:
        raise ValueError("Parity requires preceding and following source sessions")
    chosen, expanded = (
        all_rows[position : position + 1],
        all_rows[position - 1 : position + 2],
    )
    gate = Gate(root, memory_gib)
    result = []
    with gate, ThreadPoolExecutor(1) as io:
        worker = Worker(root, plan, stream, gate, endpoint, io, 2_000_000)
        for coarse in (False, True):
            candidate = worker.fetch(chosen, coarse)
            c = source.client(endpoint, memory_gib)
            started = time.monotonic()
            try:
                kwargs = (
                    dict(begin_us=clock(day, 4), end_us=clock(day, 20))
                    if coarse
                    else {}
                )
                statement = range_query(
                    ticker,
                    expanded,
                    plan["authority"]["rules"],
                    int(expanded[0]["first_ordinal"]),
                    int(expanded[-1]["next_ordinal"]),
                    **kwargs,
                )
                original = source.arrow(
                    c,
                    statement,
                    f"bargpt-p2-parity-{ticker}-{day}-{int(coarse)}",
                    allow_empty=True,
                )
                original = original.with_columns(pl.col("resolution").cast(pl.Int64))
                for i in (range(3, 5) if coarse else range(3)):
                    part = original.filter(
                        (original["resolution"] == FRAMES[i])
                        & (original["end_us"] > clock(day, 4))
                        & (original["end_us"] <= clock(day, 20))
                    )
                    expected = np.empty(part.height, dtype=BAR)
                    if part.height:
                        expected["values"], expected["valid"], expected["end_us"] = (
                            unpack(part)
                        )
                    if not np.array_equal(expected, candidate[i]):
                        raise ValueError(
                            f"Canonical producer mismatch: {ticker} {day} {FRAMES[i]}"
                        )
                    result.append(dict(frame=FRAMES[i], bars=len(expected), exact=True))
            finally:
                source.close(c)
            result[-1]["reference_seconds"] = time.monotonic() - started
    return dict(ticker=ticker, day=day, comparisons=result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--ticker")
    parser.add_argument("--day")
    parser.add_argument("--max-receipts", type=int, default=4)
    parser.add_argument("--latest", action="store_true")
    parser.add_argument("--query-memory-gib", type=float, default=3)
    args = parser.parse_args()
    started = time.monotonic()
    result = (
        parity(args.output, args.ticker, args.day, memory_gib=args.query_memory_gib)
        if args.ticker
        else content(args.output, args.max_receipts, args.latest)
    )
    result["seconds"] = time.monotonic() - started
    destination = Path(args.output) / (
        f"parity-{args.ticker}-{args.day}.json" if args.ticker else "audit.json"
    )
    write_json(destination, result)
    print(json.dumps(result))


if __name__ == "__main__":
    main()

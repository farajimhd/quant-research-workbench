"""Bounded sparse/dense source audit against already-published phase-two units.

This diagnostic captures new extraction without modifying the production root.
It compares actual bars, causal block references and splits to immutable earlier
publications, and measures publication separately from ClickHouse work.
"""

import argparse
from concurrent.futures import Future, ThreadPoolExecutor
import json
from pathlib import Path
import time

import numpy as np

from .contract import BAR, DECISION, file_hash, write_json
from .producer import Gate, Worker, NoFullBlock
from .storage import publish


class Capture:
    """Hold one bounded unit payload rather than writing to the real dataset."""

    def submit(self, function, *args):
        self.args = args
        result = Future()
        result.set_result(None)
        return result


def compare(
    root,
    output,
    plan,
    ticker,
    year,
    revision,
    threads,
    memory_gib=3,
    fine_events=250_000,
):
    stream = next(
        s for s in plan["streams"] if s["ticker"] == ticker and s["year"] == year
    )
    old = Path(root) / "streams" / stream["key"] / "0000"
    receipt = json.loads((old / "receipt.json").read_text())
    for item in receipt["files"]:
        if file_hash(old / item["name"]) != item["sha256"]:
            raise ValueError("Original audit publication changed")
    layout = json.loads((Path(root) / "layout.json").read_text())
    sink = Capture()
    with Gate(
        output,
        memory_gib=memory_gib,
        query_threads=threads,
        fine_warmup_events=fine_events,
    ) as gate:
        worker = Worker(root, plan, stream, gate, None, sink, layout["chunk_events"])
        worker.revision = revision
        started = time.perf_counter()
        result = worker.step(0)
        seconds = time.perf_counter() - started
    evidence = dict(
        ticker=ticker,
        year=year,
        original_seconds=receipt["timing"]["processing_seconds"],
        seconds=seconds,
        threads=threads,
    )
    if isinstance(result, NoFullBlock):
        if receipt["blocks"]:
            raise ValueError("An impossible-stream proof contradicts existing blocks")
        evidence.update(raw_trade_proof=result.proof, status="proven_no_full_block")
        return evidence
    _, _, _, arrays, starts, blocks, splits, context, timing = sink.args
    original = []
    for frame in range(5):
        pieces = [
            np.load(old / segment["name"], allow_pickle=False)
            for segment in receipt["segments"]
            if segment["frame"] == frame
        ]
        original.append(np.concatenate(pieces) if pieces else np.empty(0, dtype=BAR))
    if not any(len(a) for a in arrays) and not context.initialized:
        # The old producer wrote unused bootstrap history even when no current
        # bars/decisions existed. It is now deferred, not fabricated or dropped
        # from an existing publication. Its eventual indices are tested separately.
        if receipt["blocks"] or receipt["deferred_tail"]:
            raise ValueError("Deferred empty range contained reference decisions")
        evidence["status"] = "empty_range_warmup_deferred"
    else:
        for before, after in zip(original, arrays):
            np.testing.assert_array_equal(before, after)
        pieces = [
            np.load(path, allow_pickle=False)
            for path in sorted(old.glob("blocks-*.npy"))
        ]
        expected = (
            np.concatenate(pieces) if pieces else np.empty((0, 256), dtype=DECISION)
        )
        np.testing.assert_array_equal(expected, blocks)
        np.testing.assert_array_equal(receipt["splits"], splits)
        evidence["status"] = "exact_bars_blocks_masks_and_split"
    target = Path(output) / f"{ticker}-{year}-threads{threads}"
    target.mkdir(exist_ok=True)
    started = time.perf_counter()
    saved = publish(target, stream, 0, arrays, starts, blocks, splits, context, timing)
    evidence.update(
        publication_seconds=time.perf_counter() - started,
        query_count=timing["query_count"],
        query_seconds=timing["query_seconds"],
        bytes=saved["bytes"],
        blocks=len(blocks),
    )
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--cases", default="RNWZ:2025,PFE:2022,OPA.U:2024")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--memory-gib", type=float, default=3)
    parser.add_argument("--fine-events", type=int, default=250_000)
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    plan = json.loads((Path(args.root) / "plan.json").read_text())
    results = []
    for case in args.cases.split(","):
        ticker, year = case.split(":")
        results.append(
            compare(
                args.root,
                output,
                plan,
                ticker,
                int(year),
                args.source_revision,
                args.threads,
                args.memory_gib,
                args.fine_events,
            )
        )
        write_json(output / "performance.json", dict(cases=results, plan=plan["hash"]))
    print(json.dumps(results))


if __name__ == "__main__":
    main()

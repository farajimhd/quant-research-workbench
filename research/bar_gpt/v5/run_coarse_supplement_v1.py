"""Prepare or explicitly extract a coarse supplement. Never starts training.

Preparation is offline metadata work. Extraction must be launched separately,
after competing generator optimization is finished. Neither mode edits a parent,
registers a cohort, opens a terminal, queues a task, or schedules activation.
"""

import argparse
import os
from pathlib import Path
import sys

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
os.environ.setdefault("POLARS_MAX_THREADS", "2")
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from research.bar_gpt.v5.coarse_supplement_v1 import describe, extract, sealed, writer_lease
from research.bar_gpt.v5.shards.phase1_contract import digest, write_json


def arguments(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode", choices=("prepare", "extract"))
    p.add_argument("--runtime-root", type=Path, default=Path("D:/TradingML/runtimes/bar_gpt"))
    p.add_argument("--cohort", type=Path)
    p.add_argument("--split", type=Path)
    p.add_argument("--output", type=Path)
    p.add_argument("--source-revision", required=True)
    p.add_argument("--workers", type=int, default=16)
    p.add_argument("--io-workers", type=int, default=4)
    p.add_argument("--chunk-events", type=int, default=500_000)
    p.add_argument("--chunk-days", type=int, default=5)
    p.add_argument("--query-memory-gib", type=float, default=1)
    p.add_argument("--ram-reserve-gib", type=float, default=64)
    p.add_argument("--disk-reserve-gib", type=float, default=150)
    p.add_argument("--target-bytes", type=int, default=512_000_000_000)
    p.add_argument("--max-ticker-bars", type=int, default=1_000_000)
    p.add_argument("--endpoint")
    a = p.parse_args(argv)
    a.runtime_root = a.runtime_root.resolve()
    if not a.runtime_root.is_dir():
        p.error("Required runtime root unavailable; no alternate location")
    a.cohort = (a.cohort or a.runtime_root / "phase1-v6-cohort-quote-v4").resolve()
    a.split = (a.split or a.runtime_root / "corrected-restart-20261110/split.json").resolve()
    a.output = (a.output or a.runtime_root / "phase1-coarse-supplement-v1").resolve()
    for path in (a.cohort, a.split, a.output):
        if not path.is_relative_to(a.runtime_root) or path == a.runtime_root:
            p.error("Use distinct paths beneath the required runtime root")
    if (a.output == a.cohort or a.output.is_relative_to(a.cohort)
            or a.cohort.is_relative_to(a.output) or a.split.is_relative_to(a.output)):
        p.error("Output must be independent of cohort and split")
    if (not 1 <= a.workers <= 32 or not 1 <= a.io_workers <= 16
            or not 1000 <= a.chunk_events <= 2_000_000 or not 1 <= a.chunk_days <= 14
            or not 0 < a.query_memory_gib <= 4 or a.ram_reserve_gib < 64
            or a.disk_reserve_gib < 150 or a.target_bytes < 1_000_000
            or a.max_ticker_bars < 64):
        p.error("Invalid concurrency/chunk/resources; reserves may not be weakened")
    return a


def main(argv=None):
    args = arguments(argv)
    configuration = {key: getattr(args, key) for key in (
        "workers", "io_workers", "chunk_events", "chunk_days", "query_memory_gib",
        "ram_reserve_gib", "disk_reserve_gib", "target_bytes", "max_ticker_bars")}
    contract, plan, parents = describe(args.cohort, args.split, configuration)
    for _, root in parents:
        if args.output == root or args.output.is_relative_to(root) or root.is_relative_to(args.output):
            raise ValueError("Output overlaps an immutable parent")
    target = args.output / "contract.json"
    if args.mode == "prepare":
        if target.exists():
            if sealed(target, "binding") != contract:
                raise ValueError("Existing supplement contract differs")
        else:
            if args.output.exists() and any(args.output.iterdir()):
                raise ValueError("Refuse a nonempty unowned output directory")
            args.output.mkdir(parents=True, exist_ok=True)
            write_json(target, contract)
        frozen = dict(contract=contract["binding"], source_revision=args.source_revision,
                      split=str(args.split), cohort=str(args.cohort), jobs=plan["jobs"])
        frozen["hash"] = digest(frozen)
        path = args.output / "supplement-plan.json"
        if path.exists() and sealed(path) != frozen:
            raise ValueError("Prepared plan or source revision differs")
        write_json(path, frozen)
        print("Prepared only; no ClickHouse extraction or training activation. Targets:", contract["contract"]["targets"])
    else:
        frozen = sealed(args.output / "supplement-plan.json")
        if (frozen["contract"] != contract["binding"] or frozen["jobs"] != plan["jobs"]
                or frozen["source_revision"] != args.source_revision):
            raise ValueError("Run differs from prepared plan/source")
        print("Explicit extraction; existing training and parent shards remain untouched.", flush=True)
        with writer_lease(args.output):
            print(extract(args.output, contract, plan, parents, endpoint=args.endpoint))


if __name__ == "__main__":
    main()

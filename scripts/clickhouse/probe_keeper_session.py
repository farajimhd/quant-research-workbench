"""Measure the workstation Keeper session lifecycle without creating nodes.

The probe opens a real Kazoo session, reads the root znode, and closes it.
It never claims ownership or writes ClickHouse/Keeper data.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

from src.trading_runtime.keeper_session import open_workstation_keeper_session


def measure(*, cycles: int = 2) -> None:
    if not 1 <= cycles <= 3:
        raise ValueError("Keeper probe needs one to three bounded cycles")
    for cycle in range(1, cycles + 1):
        started = perf_counter()
        session = open_workstation_keeper_session()
        connected = perf_counter()
        try:
            if not session.writable or session.client.exists("/") is None:
                raise RuntimeError("Keeper session cannot read its root znode")
            read = perf_counter()
        finally:
            session.close()
        closed = perf_counter()
        print(f"Keeper cycle {cycle}/{cycles}: connected, root readable; "
              f"open={connected-started:.3f}s "
              f"read={read-connected:.3f}s "
              f"close={closed-read:.3f}s", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, choices=(1, 2, 3), default=2)
    args = parser.parse_args()
    try:
        measure(cycles=args.cycles)
    except Exception as exc:
        print(f"Keeper probe failed: {type(exc).__name__}: {exc}",
              file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

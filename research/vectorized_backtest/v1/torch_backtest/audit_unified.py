"""Independent eager CPU versus captured GPU replay on real certified inputs.

Qualifies both clocks, multiple account lanes, complete fill ledgers, account
balances and risk telemetry over a prefix that includes real entries. Full
session qualification is separately performed by run_unified_backtest repeats.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True
os.environ.setdefault("POLARS_MAX_THREADS", "1")
for name, suffix in (
    ("TORCHINDUCTOR_CACHE_DIR", "torch_inductor"),
    ("TRITON_CACHE_DIR", "torch_triton"),
    ("TORCH_EXTENSIONS_DIR", "torch_extensions"),
):
    os.environ.setdefault(name, "D:/TradingML/runtimes/vectorized_backtest/" + suffix)

import numpy as np
import torch

from .genetic_search import StrategySpace
from .optimize_strategy import RUNTIME, save
from .strategy_one_replay import StrategyOneReplay
from .strategy_one_tape import prepare


def compare(cache, clock, prefix_ms):
    space = StrategySpace()
    population = np.tile(space.default, (3, 1))
    population[:, 3] = np.linspace(space.low[3], space.high[3], 3)
    values = space.decode(population)
    tape = prepare(cache, clock_ms=clock)
    cpu = StrategyOneReplay(
        tape,
        candidates=values["entry"],
        protection_values=values["protection"],
        action_values=values["actions"],
    )
    gpu = StrategyOneReplay(
        tape.to("cuda"),
        candidates=values["entry"],
        protection_values=values["protection"],
        action_values=values["actions"],
    )
    gpu.compile()
    print(
        f"Qualified setup {clock}ms: compile={gpu.compile_seconds:.2f}s capture={gpu.capture_seconds:.2f}s",
        flush=True,
    )
    expected = cpu.run(slots=prefix_ms // clock)
    observed = gpu.run(slots=prefix_ms // clock)
    fields = (
        "cash",
        "net_pnl",
        "gross_realized",
        "fees",
        "fill_count",
        "open_quantities",
        "max_drawdown",
        "position_seconds",
        "entered_episodes",
    )
    for field in fields:
        if not np.allclose(expected[field], observed[field], rtol=0, atol=1e-7):
            raise AssertionError("Eager CPU/captured GPU mismatch: " + field)
    counts = cpu.fill_count.flatten().tolist()
    if not any(counts):
        raise AssertionError("Audit prefix contains no actual fills")
    for candidate, count in enumerate(counts):
        if not torch.allclose(
            cpu.ledger[candidate, 1 : count + 1],
            gpu.ledger[candidate, 1 : count + 1].cpu(),
            rtol=0,
            atol=1e-7,
        ):
            raise AssertionError("Eager CPU/captured GPU fill ledger mismatch")
    repeated = gpu.run(slots=prefix_ms // clock)
    for field in fields:
        if observed[field] != repeated[field]:
            raise AssertionError(
                "Captured account reset is not deterministic: " + field
            )
    return {
        "clock_ms": clock,
        "prefix_ms": prefix_ms,
        "candidates": 3,
        "fill_counts": counts,
        "cpu_gpu_equal": True,
        "reset_equal": True,
        "cpu_seconds": expected["replay_seconds"],
        "gpu_seconds": observed["replay_seconds"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--prefix-ms", type=int, default=600000)
    args = parser.parse_args()
    if args.prefix_ms <= 0 or args.prefix_ms % 1000:
        parser.error("Positive, whole-second audit prefix required")
    output = RUNTIME / "vectorized_backtest" / "causal_qualification" / uuid4().hex
    output.mkdir(parents=True)
    print("Qualification output: " + str(output), flush=True)
    torch.set_num_threads(1)
    for clock in (1000, 500):
        result = compare(args.cache, clock, args.prefix_ms)
        save(output / f"clock_{clock}.json", result)
        print(json.dumps(result), flush=True)
        import gc

        gc.collect()
        torch.cuda.empty_cache()


if __name__ == "__main__":
    main()

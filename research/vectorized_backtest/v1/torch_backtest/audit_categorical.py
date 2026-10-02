"""Qualify actual categorical changes against eager CPU on a certified tape.

The audit includes operation, input/reference and multidimensional protection
class changes. No database reads/writes, strategy search or outcome selection.
All outputs/compiler caches stay under the runtime root.
"""

import os
import sys

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True
os.environ.setdefault("POLARS_MAX_THREADS", "1")
for name, suffix in (
    ("TORCHINDUCTOR_CACHE_DIR", "torch_inductor"),
    ("TRITON_CACHE_DIR", "torch_triton"),
):
    os.environ.setdefault(name, "D:/TradingML/runtimes/vectorized_backtest/" + suffix)

import argparse
from pathlib import Path
from uuid import uuid4

import numpy as np
import torch

from .atomic_graph import Op
from .categorical_search import program_from_manifest
from .grouped_objective import (
    GroupedSessionObjective,
    categorical_space,
    graph_from_dict,
)
from .optimize_strategy import RUNTIME, save
from .strategy_one_replay import StrategyOneReplay
from .strategy_one_tape import prepare


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, required=True)
    parser.add_argument("--clock-ms", type=int, choices=(500, 1000), default=1000)
    parser.add_argument("--prefix-ms", type=int, default=600000)
    args = parser.parse_args()
    if args.prefix_ms <= 0 or args.prefix_ms % 1000:
        parser.error("Prefix must be positive whole seconds")
    output = RUNTIME / "vectorized_backtest" / "categorical_qualification" / uuid4().hex
    output.mkdir(parents=True)
    print("Categorical qualification: " + str(output), flush=True)
    torch.set_num_threads(1)
    session = {"cache": str(args.cache), "session_date": "cached-source"}
    batch = 4
    space = categorical_space(session, batch, args.clock_ms)
    rows = np.tile(space.default, (3, 1))
    # Replace a logical operation, then a typed source reference. The third
    # candidate changes a primitive protection comparison class as well.
    index = next(
        i
        for i, g in enumerate(space.genes)
        if g.component == "entry" and g.field == "operation" and g.default == Op.AND
    )
    rows[1, space.numeric_count + index] = int(Op.OR)
    index = next(
        i
        for i, g in enumerate(space.genes)
        if g.component == "entry"
        and g.field == "a"
        and any(v >= 0 and v != g.default for v in g.allowed)
    )
    gene = space.genes[index]
    rows[2, space.numeric_count + index] = next(
        v for v in gene.allowed if v >= 0 and v != gene.default
    )
    index = next(
        i
        for i, g in enumerate(space.genes)
        if g.component == "protection" and g.field == "operation"
    )
    gene = space.genes[index]
    rows[2, space.numeric_count + index] = next(
        v for v in gene.allowed if v != gene.default
    )
    rows = space.repair(rows)
    candidates = space.decode(rows)["candidates"]
    assert len({c["topology"] for c in candidates}) == 3
    evaluate = GroupedSessionObjective(session, space, batch, args.clock_ms)
    tape = prepare(args.cache, clock_ms=args.clock_ms)
    reports = []
    for index, (row, candidate) in enumerate(zip(rows, candidates)):
        parameters = candidate["parameters"]
        graphs = {
            name: graph_from_dict(value) for name, value in candidate["graphs"].items()
        }
        programs = {
            name: program_from_manifest(value, evaluate.templates[name])
            for name, value in candidate["programs"].items()
        }
        cpu = StrategyOneReplay(
            tape,
            candidates=parameters["entry"] * batch,
            entry_graph=graphs["entry"],
            add_graph=graphs["add"],
            action_graphs={name: graphs[name] for name in space.numeric.actions},
            protection_values=parameters["protection"] * batch,
            action_values={
                name: values * batch for name, values in parameters["actions"].items()
            },
            resistance_program=programs["resistance"],
            protection_program=programs["protection"],
        )
        cpu.observer_step = cpu.resistance_policy
        cpu.protection_step = cpu.protection_policy
        cpu.update_candidates(
            **space.numeric.decode(np.tile(row[: space.base_numeric_count], (batch, 1)))
        )
        expected = cpu.run(slots=args.prefix_ms // args.clock_ms)
        actual = evaluate([row], slots=args.prefix_ms // args.clock_ms)
        for key in (
            "net_pnl",
            "fees",
            "fill_count",
            "entered_episodes",
            "max_drawdown",
            "position_seconds",
            "open_quantities",
        ):
            if not np.allclose(expected[key][0], actual[key][0], rtol=0, atol=1e-7):
                raise AssertionError("Categorical CPU/GPU mismatch: " + key)
        gpu = evaluate.runners[candidate["topology"]]
        count = int(cpu.fill_count[0].item())
        if not torch.allclose(
            cpu.ledger[0, 1 : count + 1],
            gpu.ledger[0, 1 : count + 1].cpu(),
            rtol=0,
            atol=1e-7,
        ):
            raise AssertionError("Categorical full ledger mismatch")
        repeated = evaluate([row], slots=args.prefix_ms // args.clock_ms)
        assert repeated["net_pnl"] == actual["net_pnl"]
        reports.append(
            {
                "candidate": index,
                "topology": candidate["topology"],
                "fills": count,
                "cpu_gpu_equal": True,
                "ledger_equal": True,
                "reset_equal": True,
                "cpu_seconds": expected["replay_seconds"],
                "gpu_seconds": actual["replay_seconds"],
                "compile_seconds": actual["compile_seconds"],
            }
        )
        save(output / "progress.json", reports)
        print(f"Candidate {index}: ledger/reset passed, {count} fills", flush=True)
    save(
        output / "report.json",
        {
            "status": "passed",
            "clock_ms": args.clock_ms,
            "prefix_ms": args.prefix_ms,
            "representation": space.manifest(),
            "candidates": reports,
        },
    )
    print("Categorical qualification passed", flush=True)


if __name__ == "__main__":
    main()

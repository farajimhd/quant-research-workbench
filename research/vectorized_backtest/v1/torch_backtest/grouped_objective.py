"""Bounded topology grouping around the same causal GPU financial engine.

Market tensors are shared by all candidates. Accounts, liquidity budgets and
ledgers remain independent. Different categorical programs are never silently
evaluated as the default strategy: they receive their own compiled topology.
"""

import gc
from collections import OrderedDict
from time import perf_counter

import numpy as np
import torch

from .atomic_graph import Graph, Input, Threshold
from .categorical_search import (
    CategoricalStrategySpace,
    _check_output,
    program_from_manifest,
    tensor_samples,
)
from .genetic_search import StrategySpace
from .strategy_one_replay import StrategyOneReplay
from .strategy_one_tape import prepare


def graph_from_dict(value):
    return Graph(
        tuple(Input(**item) for item in value["inputs"]),
        tuple(tuple(row) for row in value["instructions"]),
        tuple(value["values"]),
        tuple(Threshold(**item) for item in value["thresholds"]),
        value["output"],
        value["version"],
        value["gate"],
        value["output_unit"],
    ).validate()


def lower_session_contracts(tape, size, numeric):
    decoded = numeric.decode(np.tile(numeric.default, (size, 1)))
    runner = StrategyOneReplay(
        tape,
        candidates=decoded["entry"],
        entry_graph=numeric.entry,
        add_graph=numeric.add,
        action_graphs=numeric.actions,
        protection_values=decoded["protection"],
        action_values=decoded["actions"],
    )
    runner.observer_step = runner.resistance_policy
    runner.protection_step = runner.protection_policy
    with torch.inference_mode():
        runner.tick()
    return {
        "resistance": runner.resistance_policy.program,
        "protection": runner.protection_policy.program,
    }


def categorical_space(session, size, clock):
    """Training-source schemas define the catalog before any fitness is read."""
    tape = prepare(session["cache"], clock_ms=clock)
    return CategoricalStrategySpace(
        lower_session_contracts(tape, size, StrategySpace())
    )


class GroupedSessionObjective:
    def __init__(self, session, space, size, clock_ms, maximum_topologies=2):
        self.session, self.space, self.size = session, space, size
        self.maximum_topologies = maximum_topologies
        self.runners = OrderedDict()
        started = perf_counter()
        tape = prepare(session["cache"], clock_ms=clock_ms)
        self.templates = lower_session_contracts(tape, size, space.numeric)
        free, _ = torch.cuda.mem_get_info()
        estimate = (
            tape.manifest["resident_estimate_bytes"]
            + size * 8193 * 6 * 8 * maximum_topologies
        )
        if estimate > free * 0.7:
            raise RuntimeError("Insufficient GPU headroom for grouped objective")
        self.tape = tape.to("cuda")
        self.setup_seconds = perf_counter() - started
        self.compile_seconds = 0.0
        self.compiled_topologies = 0
        print(
            f"Resident categorical tape {session['session_date']}: setup={self.setup_seconds:.2f}s",
            flush=True,
        )

    def _runner(self, candidate):
        key = candidate["topology"]
        if key in self.runners:
            self.runners.move_to_end(key)
            return self.runners[key]
        if len(self.runners) >= self.maximum_topologies:
            _, old = self.runners.popitem(last=False)
            del old
            gc.collect()
            torch.cuda.empty_cache()
        graphs = {
            name: graph_from_dict(value) for name, value in candidate["graphs"].items()
        }
        programs = {
            name: program_from_manifest(value, self.templates[name])
            for name, value in candidate["programs"].items()
        }
        # Session shapes can differ from the training schema used for search.
        # Validate the rebound program ABI before compiling or touching accounts.
        with torch.no_grad():
            for name, program in programs.items():
                _check_output(
                    program.rebuild("cpu")(*tensor_samples(program)),
                    self.templates[name].rebuild("cpu")(
                        *tensor_samples(self.templates[name])
                    ),
                )
        parameters = candidate["parameters"]
        started = perf_counter()
        runner = StrategyOneReplay(
            self.tape,
            candidates=parameters["entry"] * self.size,
            entry_graph=graphs["entry"],
            add_graph=graphs["add"],
            action_graphs={name: graphs[name] for name in self.space.numeric.actions},
            protection_values=parameters["protection"] * self.size,
            action_values={
                name: rows * self.size for name, rows in parameters["actions"].items()
            },
            resistance_program=programs.get("resistance"),
            protection_program=programs.get("protection"),
        )
        runner.update_candidates(
            **{
                name: parameters[name] * self.size
                for name in ("entry", "add", "protection")
            },
            actions={
                name: rows * self.size for name, rows in parameters["actions"].items()
            },
        )
        torch.compiler.reset()
        runner.compile()
        if runner.graph is None or runner.graph_steps != 1:
            raise RuntimeError(
                "Categorical objective requires a captured one-step runner"
            )
        self.compile_seconds += perf_counter() - started
        self.compiled_topologies += 1
        self.runners[key] = runner
        return runner

    def __call__(self, population, *, slots=None):
        rows = self.space.repair(population)
        decoded = self.space.decode(rows)["candidates"]
        groups = OrderedDict()
        for index, candidate in enumerate(decoded):
            groups.setdefault(candidate["topology"], []).append(index)
        keys = (
            "net_pnl",
            "fees",
            "fill_count",
            "entered_episodes",
            "max_drawdown",
            "position_seconds",
            "open_quantities",
        )
        result = {key: [None] * len(rows) for key in keys}
        replay_seconds = 0.0
        before = self.compile_seconds
        for indices in groups.values():
            runner = self._runner(decoded[indices[0]])
            padded = indices + [indices[0]] * (self.size - len(indices))
            if len(padded) != self.size:
                raise ValueError("Population exceeds compiled group batch")
            parameters = self.space.numeric.decode(
                rows[padded, : self.space.base_numeric_count]
            )
            runner.update_candidates(**parameters)
            observed = runner.run(slots=slots)
            for key in keys:
                for lane, index in enumerate(indices):
                    result[key][index] = observed[key][lane]
            replay_seconds += observed["replay_seconds"]
        return dict(
            session_date=self.session["session_date"],
            **result,
            replay_seconds=replay_seconds,
            topology_groups=len(groups),
            compile_seconds=self.compile_seconds - before,
            compiled_topologies=self.compiled_topologies,
            rejected_class_edits=self.space.rejected_edits,
        )

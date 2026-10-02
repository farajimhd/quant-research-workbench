"""Class IDs must change executable behavior, preserve types and round-trip."""

import json
from copy import deepcopy

import numpy as np
import pytest
import torch

from research.vectorized_backtest.v1.torch_backtest.atomic_graph import Op
from research.vectorized_backtest.v1.torch_backtest.categorical_search import (
    CategoricalStrategySpace,
    program_from_manifest,
)
from research.vectorized_backtest.v1.torch_backtest.genetic_search import (
    initial_population,
    next_population,
)
from research.vectorized_backtest.v1.torch_backtest.grouped_objective import (
    graph_from_dict,
)
from research.vectorized_backtest.v1.torch_backtest.tensor_program import (
    OPERATIONS,
    TensorProgram,
)


@pytest.fixture(scope="module")
def space():
    return CategoricalStrategySpace()


def test_default_arrays_and_numeric_values_roundtrip(space):
    encoded = json.loads(json.dumps(space.decode([space.default])))
    candidate = encoded["candidates"][0]
    for name, expected in space.graphs.items():
        assert graph_from_dict(candidate["graphs"][name]) == expected
    assert candidate["parameters"] == space.numeric.decode([space.numeric.default])
    assert len(space.dimensions) > 14
    assert {g.field for g in space.genes} >= {"operation", "a", "b", "output"}
    assert space.manifest()["fixed_fields"]


def test_class_ids_are_discrete_and_bad_ids_fail(space):
    rows = np.tile(space.default, (2, 1))
    rows[0, space.numeric_count] = 0.5
    with pytest.raises(ValueError, match="class ID"):
        space.repair(rows)
    rows[0, space.numeric_count] = 99999999
    with pytest.raises(ValueError, match="class ID"):
        space.repair(rows)


def test_operation_and_input_output_ids_change_real_expressions(space):
    # Constant capital output has a direct alternate typed ratio input node:
    # use an entry Boolean output selector instead, with a named atomic gate.
    index = next(
        i
        for i, g in enumerate(space.genes)
        if g.component == "entry"
        and g.field == "output"
        and any(v >= 0 for v in g.allowed)
    )
    gene = space.genes[index]
    row = space.default.copy()
    row[space.numeric_count + index] = next(v for v in gene.allowed if v >= 0)
    decoded = space.candidate(space.repair([row])[0])
    graph = graph_from_dict(decoded["graphs"]["entry"])
    inputs = {
        item.name: torch.zeros(
            (1, 1), dtype=torch.bool if item.kind == "bool" else torch.float64
        )
        for item in graph.inputs
    }
    inputs[graph.inputs[graph.output].name] = torch.ones((1, 1), dtype=torch.bool)
    assert graph.evaluate(
        inputs, graph.parameters(decoded["parameters"]["entry"], "cpu")
    ).item()
    assert decoded["topology"] != space.candidate(space.default)["topology"]
    index = next(
        i
        for i, g in enumerate(space.genes)
        if g.field == "operation" and g.default == Op.AND
    )
    row = space.default.copy()
    row[space.numeric_count + index] = int(Op.OR)
    decoded = space.candidate(space.repair([row])[0])
    g = space.genes[index]
    assert decoded["graphs"][g.component]["instructions"][g.node][0] == int(Op.OR)


def test_crossover_mutates_ids_without_numeric_interpolation(space):
    rng = np.random.default_rng(33)
    rows = initial_population(space, rng, 4)
    for _ in range(3):
        rows = next_population(space, rng, rows, np.arange(4), diversify=True)
        for i, g in enumerate(space.genes, space.numeric_count):
            assert np.isin(rows[:, i], g.allowed).all()
        space.decode(rows)
    assert len({c["topology"] for c in space.decode(rows)["candidates"]}) > 1


def test_numeric_variants_share_topology(space):
    rows = np.tile(space.default, (2, 1))
    rows[1, space.numeric.indices["capital_mandate_fraction"]] = 0.5
    candidates = space.decode(rows)["candidates"]
    assert candidates[0]["topology"] == candidates[1]["topology"]
    assert candidates[0]["parameters"] != candidates[1]["parameters"]


def test_categorical_rng_restart_preserves_next_population(space):
    rng = np.random.default_rng(123)
    rows = initial_population(space, rng, 4)
    restored = np.random.default_rng()
    restored.bit_generator.state = json.loads(json.dumps(rng.bit_generator.state))
    scores = np.arange(4)
    assert np.array_equal(
        next_population(space, rng, rows, scores, diversify=True),
        next_population(space, restored, rows, scores, diversify=True),
    )


def test_categorical_solver_checkpoint_resumes_identically(space, tmp_path):
    from types import SimpleNamespace

    from research.vectorized_backtest.v1.torch_backtest.optimize_strategy import (
        run_phase,
    )

    args = SimpleNamespace(
        seed=71, population=4, generations=3, tolerance=1e-6, weights={}
    )

    def evaluate(rows):
        # Both numeric values and instruction IDs contribute to this test fitness.
        pnl = -(rows != space.default).sum(-1).astype(float)
        return {
            "net_pnl": pnl.tolist(),
            "max_drawdown": [0] * 4,
            "entered_episodes": [1] * 4,
            "position_seconds": [0] * 4,
        }

    complete, interrupted = tmp_path / "complete", tmp_path / "interrupted"
    complete.mkdir()
    interrupted.mkdir()
    space.rejected_edits = 0
    expected = run_phase([evaluate], space, args, complete, 1, None, None)
    space.rejected_edits = 0
    calls = 0

    def fail_once(rows):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("interruption")
        return evaluate(rows)

    with pytest.raises(RuntimeError, match="interruption"):
        run_phase([fail_once], space, args, interrupted, 1, None, None)
    checkpoint = json.loads((interrupted / "checkpoint.json").read_text())
    actual = run_phase([evaluate], space, args, interrupted, 1, checkpoint, None)
    assert actual == expected
    assert json.loads((complete / "checkpoint.json").read_text()) == json.loads(
        (interrupted / "checkpoint.json").read_text()
    )


def test_grouped_objective_pads_and_scatters_original_candidate_order(space):
    from research.vectorized_backtest.v1.torch_backtest.grouped_objective import (
        GroupedSessionObjective,
    )

    rows = np.tile(space.default, (4, 1))
    i = next(
        i
        for i, g in enumerate(space.genes)
        if g.field == "operation" and g.default == Op.AND
    )
    rows[[0, 2], space.numeric_count + i] = int(Op.OR)
    capital = space.numeric.indices["capital_mandate_fraction"]
    rows[:, capital] = [0.2, 0.3, 0.4, 0.5]
    objective = object.__new__(GroupedSessionObjective)
    objective.space, objective.size = space, 4
    objective.session = {"session_date": "test"}
    objective.compile_seconds = 0
    objective.compiled_topologies = 0
    batches = []

    class Runner:
        def update_candidates(self, **parameters):
            self.capital = parameters["actions"]["capital_fraction"]

        def run(self, slots=None):
            batches.append(self.capital)
            values = [row[0] for row in self.capital]
            return {
                **{
                    key: values
                    for key in (
                        "net_pnl",
                        "fees",
                        "fill_count",
                        "entered_episodes",
                        "max_drawdown",
                        "position_seconds",
                        "open_quantities",
                    )
                },
                "replay_seconds": 1,
            }

    objective._runner = lambda candidate: Runner()
    observed = objective(rows)
    assert observed["net_pnl"] == [0.2, 0.3, 0.4, 0.5]
    assert observed["topology_groups"] == 2
    assert all(len(batch) == 4 for batch in batches)


def test_tensor_class_mutation_rebuilds_and_rebinds_shapes():
    program = TensorProgram.lower(
        lambda a, b: ((a + b,), ()),
        ("state.stop", "evidence.bid"),
        (torch.ones(4, 2), torch.ones(4, 2) * 3),
    )
    space = CategoricalStrategySpace({"protection": program})
    i = next(
        i
        for i, g in enumerate(space.genes)
        if g.component == "protection" and g.field == "operation"
    )
    row = space.default.copy()
    row[space.numeric_count + i] = OPERATIONS.index("sub.Tensor")
    encoded = space.candidate(space.repair([row])[0])["programs"]["protection"]
    rebuilt = program_from_manifest(json.loads(json.dumps(encoded)))
    out = rebuilt.rebuild("cpu")(torch.ones(4, 2), torch.ones(4, 2) * 3)
    assert torch.equal(out[0][0], torch.full((4, 2), -2.0))
    other = TensorProgram.lower(
        lambda a, b: ((a + b,), ()),
        program.names,
        (torch.ones(4, 3), torch.ones(4, 3) * 3),
    )
    adapted = program_from_manifest(encoded, other)
    assert adapted.schema == other.schema
    assert adapted.rebuild("cpu")(torch.ones(4, 3), torch.ones(4, 3) * 3)[0][
        0
    ].shape == (4, 3)
    broken = deepcopy(encoded)
    broken["instructions"][0][0] = 9999
    with pytest.raises(ValueError):
        program_from_manifest(broken).rebuild("cpu")


def test_tensor_output_class_changes_behavior_and_compile_key():
    program = TensorProgram.lower(
        lambda a, b: ((a + b,), ()),
        ("state.stop", "evidence.bid"),
        (torch.ones(4, 2), torch.ones(4, 2) * 3),
    )
    space = CategoricalStrategySpace({"protection": program})
    index, gene = next(
        (i, g)
        for i, g in enumerate(space.genes)
        if g.component == "protection" and g.field == "output_reference"
    )
    row = space.default.copy()
    row[space.numeric_count + index] = next(
        v for v in gene.allowed if v != gene.default
    )
    candidate = space.candidate(space.repair([row])[0])
    assert candidate["topology"] != space.candidate(space.default)["topology"]
    encoded = candidate["programs"]["protection"]
    rebuilt = program_from_manifest(encoded, program)
    output = rebuilt.rebuild("cpu")(torch.ones(4, 2), torch.ones(4, 2) * 3)[0][0]
    assert not torch.equal(output, torch.ones(4, 2) * 4)
    corrupted = deepcopy(encoded)
    corrupted["output"] = program.manifest()["output"]
    with pytest.raises(ValueError, match="hash"):
        program_from_manifest(corrupted)


def test_searchable_entry_cannot_bypass_causal_source_or_account_ownership():
    from research.vectorized_backtest.v1.torch_backtest.strategy_one_replay import (
        StrategyOneReplay,
    )
    from research.vectorized_backtest.v1.torch_backtest.strategy_one_tape import (
        FACT_FIELDS,
        MARKET_FIELDS,
    )
    from research.vectorized_backtest.v1.torch_backtest.tests.test_unified_clock import (
        tape,
    )

    runner = StrategyOneReplay(tape(1000))
    # Deliberately permissive searched policy, with executable brackets.
    runner.entry_step = lambda x, theta: torch.ones_like(x["pending_entry"])
    runner.action_steps = {
        "initial_stop": lambda x, theta: x["bid"] * 0.9,
        "initial_target": lambda x, theta: x["ask"] * 1.1,
        "capital_fraction": lambda x, theta: torch.ones_like(x["bid"]) * 0.1,
    }
    zero = torch.zeros_like(runner.state["quantity"])
    flags = zero.bool()
    market = {key: zero.clone() for key in MARKET_FIELDS}
    fact = {key: zero.clone() for key in FACT_FIELDS}
    geometry = torch.zeros_like(runner.protection.accepted)
    pending = flags.clone()

    def admitted():
        return runner._decisions(
            market,
            market,
            fact,
            zero + 1000,
            zero + 10,
            zero + 10.1,
            ~flags,
            pending,
            flags,
            geometry,
            geometry,
            geometry.double(),
            geometry.double(),
            flags,
        )[1].item()

    assert not admitted()
    fact["candidate_valid"].fill_(1)
    fact["episode_start_ms"].fill_(500)
    assert admitted()
    fact["episode_start_ms"].fill_(2000)
    assert not admitted()
    fact["episode_start_ms"].fill_(500)
    runner.state["quantity"].fill_(1)
    assert not admitted()
    runner.state["quantity"].zero_()
    pending.fill_(True)
    assert not admitted()

"""Search constraints, financial scoring and train/validation separation."""

import numpy as np
import pytest
import torch

from research.vectorized_backtest.v1.torch_backtest.genetic_search import (
    StrategySpace,
    initial_population,
    next_population,
    objective,
)
from research.vectorized_backtest.v1.torch_backtest.search_sessions import (
    validate_split,
)


@pytest.mark.skipif(
    not torch.cuda.is_available(), reason="Requires captured CUDA graphs"
)
def test_compiler_reset_preserves_resident_graph_across_session_shapes():
    """Clearing Python guards must not invalidate an earlier session's GPU work."""
    torch.compiler.reset()

    def operation(value):
        return value.square() + 1

    first = torch.ones(8, device="cuda", dtype=torch.float64)
    compiled = torch.compile(operation, fullgraph=True, dynamic=False)
    stream = torch.cuda.Stream()
    stream.wait_stream(torch.cuda.current_stream())
    with torch.cuda.stream(stream):
        for _ in range(3):
            compiled(first)
    torch.cuda.current_stream().wait_stream(stream)
    graph = torch.cuda.CUDAGraph()
    with torch.cuda.graph(graph, stream=stream):
        output = compiled(first)
    torch.cuda.synchronize()

    # A new market tape has a different listing shape. Its compilation should
    # not consume the former session's guard budget or disable its captured work.
    torch.compiler.reset()
    second = torch.full((13,), 3.0, device="cuda", dtype=torch.float64)
    assert torch.equal(compiled(second), torch.full_like(second, 10.0))
    first.fill_(2)
    graph.replay()
    torch.cuda.synchronize()
    assert torch.equal(output, torch.full_like(first, 5.0))
    torch.compiler.reset()


def test_default_roundtrip_and_every_evolved_candidate_satisfies_engine_contract():
    space = StrategySpace()
    decoded = space.decode([space.default])
    assert decoded["entry"] == [list(space.entry.values)]
    assert decoded["actions"]["capital_fraction"] == [
        [space.actions["capital_fraction"].values[0]]
    ]
    rng = np.random.default_rng(17)
    population = initial_population(space, rng, 8)
    for _ in range(20):
        # A smooth synthetic objective verifies the bounded solver separately
        # from real-market profitability and expensive GPU compilation.
        scores = -np.square(
            (population - space.default) / (space.high - space.low)
        ).sum(-1)
        population = next_population(space, rng, population, scores, diversify=True)
        space.decode(population)
        assert np.all(
            population[:, space.indices["first_target_breaks"]]
            <= population[:, space.indices["second_target_breaks"]]
        )
        assert np.array_equal(population[0], space.default)


def test_full_numeric_catalog_scatter_and_bracket_distances():
    """Every eligible graph slot must be searched; structural literals stay fixed."""
    space = StrategySpace()
    assert len(space.dimensions) == 14
    assert len(space.slots) == sum(
        t.minimum < t.maximum for g in space.graphs.values() for t in g.thresholds
    )
    decoded = space.decode([space.default])
    for name, graph in space.graphs.items():
        rows = decoded[name] if name in ("entry", "add") else decoded["actions"][name]
        assert rows == [list(graph.values)]
    inputs = {
        name: torch.tensor([[value]], dtype=torch.float64)
        for name, value in {"bid": 10, "ask": 10.1, "stop": 9, "target": 12}.items()
    }
    for name, expected in (("initial_stop", 9), ("initial_target", 12)):
        graph = space.actions[name]
        assert (
            graph.evaluate(inputs, graph.parameters([graph.values], "cpu")).item()
            == expected
        )
    genome = space.default.copy()
    genome[space.indices["initial_stop_distance_multiplier"]] = 0.5
    genome[space.indices["initial_target_distance_multiplier"]] = 2
    changed = space.decode([genome])
    for name, expected in (("initial_stop", 9.5), ("initial_target", 13.9)):
        graph = space.actions[name]
        value = graph.evaluate(
            inputs, graph.parameters(changed["actions"][name], "cpu")
        )
        assert value.item() == pytest.approx(expected)
    # An add range [3,2] is repaired to [3,3], never allowing purchase 4.
    genome[space.indices["minimum_add_purchase_ordinal"]] = 3
    genome[space.indices["maximum_add_purchase_ordinal"]] = 2
    repaired = space.repair([genome])
    assert repaired[0, space.indices["maximum_add_purchase_ordinal"]] == 3


def test_rng_checkpoint_continues_identically():
    space = StrategySpace()
    rng = np.random.default_rng(2)
    population = initial_population(space, rng, 8)
    restored = np.random.default_rng()
    restored.bit_generator.state = rng.bit_generator.state
    scores = np.arange(8)
    assert np.array_equal(
        next_population(space, rng, population, scores),
        next_population(space, restored, population, scores),
    )


def test_objective_penalizes_drawdown_and_session_inconsistency():
    result = lambda pnl, dd: {
        "net_pnl": pnl,
        "max_drawdown": dd,
        "entered_episodes": [1, 1],
        "position_seconds": [10, 10],
    }
    # Same mean return; the second candidate loses on one day and has drawdown.
    score = objective([result([100, 300], [0, 50]), result([100, -100], [0, 50])])
    assert score[0] == pytest.approx(0.01)
    assert score[1] < score[0]
    with pytest.raises(ValueError):
        objective([result([float("nan"), 1], [0, 0])])


def test_same_day_other_run_cannot_be_validation():
    session = lambda day: {"session_date": day, "configuration_hash": "same"}
    validate_split(
        [session("2026-08-18"), session("2026-08-19")], [session("2026-08-20")]
    )
    with pytest.raises(ValueError, match="disjoint"):
        validate_split([session("2026-08-18")], [session("2026-08-18")])
    with pytest.raises(ValueError, match="later"):
        validate_split([session("2026-08-19")], [session("2026-08-18")])


def test_interrupted_generation_resumes_same_winner_and_population(tmp_path):
    """Exercise the real solver/checkpoint path without a costly market replay."""
    import json
    from types import SimpleNamespace

    from research.vectorized_backtest.v1.torch_backtest.optimize_strategy import (
        run_phase,
    )

    space = StrategySpace()
    args = SimpleNamespace(
        seed=42, population=4, generations=4, tolerance=1e-6, weights={}
    )

    def evaluate(population):
        pnl = (
            -np.square((population - space.default) / (space.high - space.low)).sum(-1)
            * 100
        )
        return {
            "net_pnl": pnl.tolist(),
            "max_drawdown": [0] * 4,
            "entered_episodes": [1] * 4,
            "position_seconds": [0] * 4,
        }

    complete, interrupted = tmp_path / "complete", tmp_path / "interrupted"
    complete.mkdir()
    interrupted.mkdir()
    first = run_phase([evaluate], space, args, complete, 1, None, None)
    calls = 0

    def fail_once(population):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulated interruption")
        return evaluate(population)

    with pytest.raises(RuntimeError, match="simulated"):
        run_phase([fail_once], space, args, interrupted, 1, None, None)
    checkpoint = json.loads((interrupted / "checkpoint.json").read_text())
    resumed = run_phase([evaluate], space, args, interrupted, 1, checkpoint, None)
    assert first == resumed
    assert json.loads((complete / "checkpoint.json").read_text()) == json.loads(
        (interrupted / "checkpoint.json").read_text()
    )

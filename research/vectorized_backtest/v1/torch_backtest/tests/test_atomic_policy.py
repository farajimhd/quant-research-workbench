"""Search mutations, unknown-data fences and reconstruction validation."""

from dataclasses import replace
from types import SimpleNamespace

import polars as pl
import pytest
import torch

from research.vectorized_backtest.v1.strategy_encoding.core import EncodingError
from research.vectorized_backtest.v1.torch_backtest.atomic_graph import Input, trace
from research.vectorized_backtest.v1.torch_backtest.feature_bank import (
    FeatureAtom,
    relative_volume_rows,
)
from research.vectorized_backtest.v1.torch_backtest.strategy_one_broker import Book
from research.vectorized_backtest.v1.torch_backtest.strategy_one_program import (
    released_action_graphs,
    released_entry_graph,
)
from research.vectorized_backtest.v1.torch_backtest.strategy_one_replay import (
    StrategyOneReplay,
)
from research.vectorized_backtest.v1.torch_backtest.strategy_one_tape import (
    MARKET_FIELDS,
)
from research.vectorized_backtest.v1.torch_backtest.strategy_one_tensor_policy import (
    AtomicReducer,
)
from research.vectorized_backtest.v1.torch_backtest.tensor_program import TensorProgram


def test_search_cannot_drop_constraints_or_reference_future_nodes():
    graph = released_entry_graph()
    values = list(graph.values)
    index = next(
        i
        for i, bound in enumerate(graph.thresholds)
        if bound.name == "episode_lifetime_ms"
    )
    values[index] = 300_001
    with pytest.raises(EncodingError, match="Threshold outside"):
        replace(graph, values=tuple(values)).validate()
    rows = list(graph.instructions)
    rows[0] = (7, -2, 0, 2**31 - 1)
    with pytest.raises(EncodingError, match="backward"):
        replace(graph, instructions=tuple(rows)).validate()


def test_not_cannot_turn_unknown_float_into_entry_permission():
    graph = trace(
        lambda x: ~(x["float_shares"] > 10_000_000),
        (Input(0, "float_shares", "real", "shares"),),
    )
    actual = graph.evaluate(
        {"float_shares": torch.tensor([[float("nan"), 1_000_000]])},
        graph.parameters(graph.values, "cpu"),
    )
    assert actual.tolist() == [[False, True]]


def test_price_scale_is_not_an_optimizable_reentry_duration():
    graph = released_entry_graph()
    scale = next(
        i
        for i, bound in enumerate(graph.thresholds)
        if bound.name == "integer_price_scale"
    )
    duration = next(
        i
        for i, bound in enumerate(graph.thresholds)
        if bound.name == "rapid_reentry_ms"
    )
    assert scale != duration
    assert graph.thresholds[scale].minimum == graph.thresholds[scale].maximum == 10000


def test_physical_units_reject_cross_domain_search_mutation():
    with pytest.raises(EncodingError, match="operand units"):
        trace(
            lambda x: x["price"] > x["float_shares"],
            (
                Input(0, "price", "real", "price"),
                Input(1, "float_shares", "real", "shares"),
            ),
        )


def test_atomic_tensor_program_rebuilds_and_rejects_future_operand():
    sample = torch.tensor([[2.0, -1.0]])
    program = TensorProgram.lower(
        lambda value: torch.maximum(value, torch.zeros_like(value)),
        ("close",),
        (sample,),
    )
    assert program.module(sample).tolist() == [[2.0, 0.0]]
    assert program.manifest()["instructions"]
    program.operands = (
        {"args": {"tuple": [{"node": 99}]}, "kwargs": {"dict": {}}},
        *program.operands[1:],
    )
    with pytest.raises(ValueError, match="earlier nodes"):
        program.rebuild("cpu")


def test_protection_search_preserves_ordered_breakpoints():
    with pytest.raises(ValueError, match="ordered"):
        AtomicReducer("protection", (1, 1), "cpu", [3, 6, 5, 3, 2, 1])


def test_history_is_bounded_in_source_observations():
    FeatureAtom(
        "close_12_mean", "close_int", "integer_price", window=12, reduction="mean"
    ).validate()
    with pytest.raises(ValueError, match="64-bar"):
        FeatureAtom("close", "close_int", "integer_price", window=64, lag=1).validate()


def test_numeric_action_parameters_broadcast_across_tickers():
    graph = released_action_graphs()["capital_fraction"]
    theta = graph.parameters([[1 / 3], [0.5]], "cpu")
    assert graph.evaluate({}, theta).flatten().tolist() == [1 / 3, 0.5]
    with pytest.raises(EncodingError, match="Threshold outside"):
        graph.parameters([[1.1]], "cpu")


def test_protection_thresholds_are_independent_candidate_lanes():
    reducer = AtomicReducer("protection", (2, 3), "cpu")
    reducer.update([[3, 3, 5, 3, 2, 1], [4, 4, 6, 4, 3, 2]])
    assert reducer.parameters[0].tolist() == [[3, 3, 3], [4, 4, 4]]


def test_completed_bar_asof_never_exposes_next_bucket():
    replay = object.__new__(StrategyOneReplay)
    replay.b = 1
    bank = torch.stack(
        [
            torch.full((1, len(MARKET_FIELDS)), 1.0),
            torch.full((1, len(MARKET_FIELDS)), 2.0),
        ]
    )
    replay.tape = SimpleNamespace(market={1000: bank})
    for index, expected in ((8, 0), (9, 1), (11, 1), (19, 2)):
        replay.index = torch.tensor([index])
        assert replay._market(1000)["present"].item() == expected


def test_funding_cache_updates_reservations_without_crossing_candidates():
    replay = object.__new__(StrategyOneReplay)
    replay.cash = torch.tensor([1000.0, 2000.0], dtype=torch.float64)
    quantity = torch.tensor([[10.0, 0], [0, 20]], dtype=torch.float64)
    average = torch.tensor([[5.0, 0], [0, 4]], dtype=torch.float64)
    replay.state = {
        "quantity": quantity,
        "average": average,
        "allocated_average": average.clone(),
        "allocated_risk": quantity.clone(),
        "mark": torch.tensor([[6.0, 0], [0, 5]], dtype=torch.float64),
    }
    replay.books = tuple(Book.empty(2, "cpu") for _ in range(2))
    replay.root_indices = torch.tensor([0, 5, 10])
    for candidate, book in enumerate(replay.books):
        book.remaining[candidate, 0] = 4 + candidate
        book.reference[candidate, 0] = 3 + candidate
        book.reserved_risk_per_share[candidate, 0] = 1
        book.active[candidate, 0] = True
    replay.funding = {
        name: torch.zeros_like(replay.cash)
        for name in (
            "reserved",
            "reserved_risk",
            "gross",
            "equity",
            "capital",
            "allocated",
            "allocated_risk",
            "occupied_count",
        )
    }
    replay.funding_occupied = torch.zeros((2, 2), dtype=torch.bool)
    replay._prepare_funding()
    assert replay.funding["reserved"].tolist() == [12, 20]
    assert replay.funding["capital"].tolist() == [1050, 2080]
    assert replay.funding["occupied_count"].tolist() == [1, 1]
    price, stop = torch.tensor([10.0, 10.0]), torch.tensor([9.0, 9.0])
    replay._submit(
        1, 1, torch.tensor([40.0, 0.0]), price, stop, price + 10, torch.tensor([100])
    )
    assert replay.funding["reserved"].tolist() == [412, 20]
    assert replay.funding["reserved_risk"].tolist() == [44, 5]
    assert replay.funding["occupied_count"].tolist() == [2, 1]
    actual = replay._fund(1, torch.tensor([True, True]), price, stop, torch.ones(2))
    # Candidate zero is now constrained by outstanding risk (84-10-44).
    assert actual.tolist() == [30, 141]


def test_engine_inputs_cannot_be_relabelled_with_incompatible_units():
    graph = trace(
        lambda x: x["available_cash"] > 1,
        (Input(0, "available_cash", "real", "price"),),
    )
    with pytest.raises(ValueError, match="atomic source contract"):
        StrategyOneReplay(SimpleNamespace(), entry_graph=graph)


def test_cash_cannot_be_relabelled_as_a_boolean_input():
    graph = trace(
        lambda x: ~x["available_cash"], (Input(0, "available_cash", "bool", "money"),)
    )
    with pytest.raises(ValueError, match="atomic engine contract"):
        StrategyOneReplay(SimpleNamespace(), entry_graph=graph)


@pytest.mark.parametrize(
    "device", ["cpu"] + (["cuda"] if torch.cuda.is_available() else [])
)
def test_returns_and_bps_can_form_numeric_price_actions(device):
    graph = trace(
        lambda x: (
            x["price"] * (1 - (x["price"] / x["prior"] - 1).to_bps().from_bps() * 0.5)
        ),
        (Input(0, "price", "real", "price"), Input(1, "prior", "real", "price")),
        gate=False,
        output_unit="price",
    )
    inputs = {
        "price": torch.tensor([[11.0]], device=device, dtype=torch.float64),
        "prior": torch.tensor([[10.0]], device=device, dtype=torch.float64),
    }
    step = (
        torch.compile(graph.evaluate, fullgraph=True)
        if device == "cuda"
        else graph.evaluate
    )
    assert step(inputs, graph.parameters(graph.values, device)).item() == pytest.approx(
        10.45
    )
    with pytest.raises(EncodingError, match="normalized ratio/return"):
        trace(lambda x: x["price"].to_bps() > 1, (Input(0, "price", "real", "price"),))


def test_rvol_advances_during_quiet_seconds_and_masks_missing_denominator():
    source = pl.DataFrame({"boundary_ms": [1000, 3000], "volume": [10.0, 10.0]})
    actual = relative_volume_rows(source, [None, 10, 20, 40, None], 4000)
    assert actual["boundary_ms"].to_list() == [1000, 2000, 3000, 4000]
    assert actual["value"].to_list() == [1.0, 0.5, 0.5, None]

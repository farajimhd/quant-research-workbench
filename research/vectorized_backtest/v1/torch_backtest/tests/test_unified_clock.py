"""Clock separation, aggregation and fail-closed strategy contracts."""

import polars as pl
import pytest
import torch

from research.vectorized_backtest.v1.torch_backtest.feature_bank import (
    FeatureAtom,
    FeatureBank,
)
from research.vectorized_backtest.v1.torch_backtest.stage_profile import summarize_trace
from research.vectorized_backtest.v1.torch_backtest.strategy_one_program import (
    released_add_graph,
)
from research.vectorized_backtest.v1.torch_backtest.strategy_one_replay import (
    StrategyOneReplay,
)
from research.vectorized_backtest.v1.torch_backtest.strategy_one_tape import (
    FACT_FIELDS,
    MARKET_FIELDS,
    Tape,
    aggregate_liquidity,
)
from research.vectorized_backtest.v1.torch_backtest.unified_clock import (
    unified_add_graph,
    validate_clock,
)


def tape(clock):
    """Two seconds, one listing; longer-timeframe evidence is not yet complete."""
    slots = 2000 // clock
    market = {
        clock: torch.zeros(slots, 1, len(MARKET_FIELDS)),
        30000: torch.zeros(1, 1, len(MARKET_FIELDS)),
    }
    market.setdefault(1000, torch.zeros(2, 1, len(MARKET_FIELDS)))
    return Tape(
        ("TEST",),
        2000,
        market,
        torch.zeros(slots, 1, len(FACT_FIELDS)),
        torch.zeros(3, 1, 1, 4),
        torch.zeros(slots, 1, dtype=torch.int64),
        torch.zeros(slots, 1, dtype=torch.int64),
        {
            clock: {
                "market": market[clock],
                "pointers": torch.zeros(slots, 1, 2, dtype=torch.int64),
                "prices": torch.zeros(1, 2),
                "maximum_prices": 1,
            }
        },
        (("",),),
        {"clock_ms": clock},
    )


def test_capacity_and_quote_age_are_aggregated_without_inventing_liquidity():
    rows = pl.DataFrame(
        {
            "boundary_ms": [100, 200, 300],
            "listing": [0, 0, 0],
            "present": [1, 1, 1],
            "price_valid": [1, 0, 1],
            "extremes_valid": [1, 0, 1],
            "open_int": [100, 0, 110],
            "close_int": [105, 0, 120],
            "high_int": [106, 0, 123],
            "low_int": [99, 0, 109],
            "quote_valid": [1, 1, 0],
            "quote_timestamp_us": [90000, 190000, 0],
            "bid_int": [100, 110, 0],
            "ask_int": [101, 111, 0],
            "bid_size": [10, 20, 0],
            "ask_size": [11, 21, 0],
            "execution_volume": [3, 4, 5],
            "execution_price_levels": [
                [{"1": 100, "2": 3}],
                [{"1": 100, "2": 4}],
                [{"1": 120, "2": 5}],
            ],
        }
    )
    row = aggregate_liquidity(rows, 500, 0).row(0, named=True)
    assert (
        row["boundary_ms"],
        row["open_int"],
        row["close_int"],
        row["high_int"],
        row["low_int"],
    ) == (500, 100, 120, 123, 99)
    assert row["execution_volume"] == 12
    assert row["execution_price_levels"] == [{"1": 100, "2": 7}, {"1": 120, "2": 5}]
    assert (row["bid_size"], row["ask_size"], row["quote_age_us"]) == (20, 21, 310000)


@pytest.mark.parametrize("clock", [500, 1000])
def test_replay_advances_on_the_main_clock_and_never_reads_future_seconds(clock):
    source = tape(clock)
    source.market[1000][0, 0, MARKET_FIELDS.index("close_int")] = 123
    replay = StrategyOneReplay(source)
    first = replay._market(1000)["close_int"].item()
    assert first == (123 if clock == 1000 else 0)
    if clock == 500:
        replay.index.fill_(1)
        assert replay._market(1000)["close_int"].item() == 123
    assert replay.broker_ms == clock
    assert all("100" not in item.name for item in replay.add_graph.inputs)
    with pytest.raises(ValueError, match="clocks must match"):
        StrategyOneReplay(source, broker_ms=100)
    with pytest.raises(ValueError, match="declared engine/source dependency"):
        StrategyOneReplay(source, add_graph=released_add_graph())


def test_subclock_bar_features_are_rejected_before_replay():
    atom = FeatureAtom(
        name="fast_close",
        source="bars",
        field="close_int",
        resolution_ms=100,
        unit="integer_price",
    )
    bank = FeatureBank((atom,), torch.zeros(2, 1, 1), {})
    with pytest.raises(ValueError, match="below the main clock"):
        StrategyOneReplay(tape(1000), feature_bank=bank)
    assert unified_add_graph().validate()
    with pytest.raises(ValueError):
        validate_clock(750)


def test_profiler_correlates_driver_and_runtime_launches_without_double_counting():
    events = [
        {"ph": "X", "name": "stage:broker", "ts": 10, "dur": 20},
        {
            "cat": "cuda_driver",
            "name": "cuLaunchKernel",
            "ts": 11,
            "args": {"correlation": 1},
        },
        {
            "cat": "cuda_runtime",
            "name": "cudaLaunchKernel",
            "ts": 12,
            "args": {"correlation": 2},
        },
        # Device execution is later than the CPU range; match launch times.
        {"cat": "kernel", "name": "a", "ts": 40, "dur": 3, "args": {"correlation": 1}},
        {"cat": "kernel", "name": "b", "ts": 43, "dur": 4, "args": {"correlation": 2}},
    ]
    summary = summarize_trace(events, ("broker",))
    assert summary["kernel_count"] == 2
    assert summary["stages"]["broker"] == {"kernel_count": 2, "device_us": 7}
    assert summary["stages"]["unattributed"]["kernel_count"] == 0

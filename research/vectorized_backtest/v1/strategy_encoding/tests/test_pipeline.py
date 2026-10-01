from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl
import pytest

from research.vectorized_backtest.v1.strategy_encoding import (
    AtomicInput,
    Catalog,
    Constraint,
    EncodingError,
    Parameter,
    Program,
    Unit,
    compile_strategy,
)
from research.vectorized_backtest.v1.strategy_encoding import (
    Instruction as I,
)
from research.vectorized_backtest.v1.strategy_encoding import (
    Operation as O,
)
from research.vectorized_backtest.v1.strategy_encoding.clickhouse import (
    PreparedSession,
    admission_sql,
    prepare_session,
)
from research.vectorized_backtest.v1.strategy_encoding.config import (
    Broker,
    Funnel,
    Session,
)
from research.vectorized_backtest.v1.strategy_encoding.examples import momentum_example
from research.vectorized_backtest.v1.strategy_encoding.replay import evaluate


def simple_catalog(parameters=()):
    return Catalog(
        (
            AtomicInput(
                0, "close", "close", Unit.PRICE, 1000, "valid", "available", 1.0, "bars"
            ),
        ),
        parameters,
    )


def observation():
    return pl.DataFrame(
        {
            "listing_id": ["A"] * 4,
            "session_date": ["2026-08-18"] * 4,
            "time_us": [1, 2, 3, 4],
            "available": [1, 5, 3, 4],
            "valid": [True] * 4,
            "close": [10.0, 20.0, 0.0, float("inf")],
        }
    )


def test_units_padding_serialization_and_future_inputs():
    catalog = simple_catalog((Parameter("price", Unit.PRICE, 1, 100),))
    program = Program.encode(
        [I(O.PARAMETER, parameter=0), I(O.GREATER, 0, -1), I(O.EXIT, -2)],
        (5.0,),
        pad_to=8,
    )
    compiled = compile_strategy(program, catalog)
    assert compiled.evaluate(observation())["action_2_exit"].to_list() == [
        True,
        False,
        False,
        False,
    ]
    restored = Program.from_dict(program.to_dict())
    assert compile_strategy(restored, catalog).fingerprint == compiled.fingerprint


@pytest.mark.parametrize(
    "instructions,values",
    [
        ([I(O.PARAMETER, parameter=0), I(O.GREATER, 0, -1), I(O.EXIT, -2)], (5.0,)),
        ([I(O.GREATER, 0, -1), I(O.EXIT, -1)], (0.5,)),
        ([I(999), I(O.EXIT, -1)], (0.5,)),
    ],
)
def test_invalid_units_references_and_labels(instructions, values):
    with pytest.raises(EncodingError):
        compile_strategy(
            Program.encode(instructions, values),
            simple_catalog((Parameter("fraction", Unit.FRACTION, 0, 1),)),
        )


def test_cross_parameter_constraints():
    catalog = replace(
        simple_catalog(
            (Parameter("min", Unit.PRICE, 1, 100), Parameter("max", Unit.PRICE, 1, 100))
        ),
        constraints=(Constraint(0, "<", 1),),
    )
    p = Program.encode(
        [I(O.PARAMETER, parameter=0), I(O.GREATER, 0, -1), I(O.EXIT, -2)], (50, 20)
    )
    with pytest.raises(EncodingError, match="constraint violated"):
        compile_strategy(p, catalog)


def test_numpy_arrays_vocabulary_and_noninteger_label_rejection():
    from research.vectorized_backtest.v1.strategy_encoding import describe

    program, catalog = momentum_example()
    instructions, values, mask = program.to_arrays()
    assert (
        instructions.shape == (16, 4) and values.shape == (3,) and mask.shape == (16,)
    )
    restored = Program.from_arrays(instructions, values, mask)
    assert (
        compile_strategy(restored, catalog).fingerprint
        == compile_strategy(program, catalog).fingerprint
    )
    assert len(describe(catalog)["inputs"]) > 200
    with pytest.raises(EncodingError):
        Program.encode([I(8.1, 0, 1)], ())
    with pytest.raises(EncodingError):
        Program.from_arrays([1, 2, 3], [], [])


def test_invalid_negation_remains_unknown_and_division_zero_is_not_permission():
    catalog = simple_catalog((Parameter("price", Unit.PRICE, 1, 100),))
    p = Program.encode(
        [I(O.PARAMETER, parameter=0), I(O.GREATER, 0, -1), I(O.NOT, -2), I(O.EXIT, -3)],
        (5,),
    )
    assert compile_strategy(p, catalog).evaluate(observation())[
        "action_3_exit"
    ].to_list() == [False, False, True, False]
    # Even negating a comparison on 0/0 must remain unknown, not grant exit.
    divided = Program.encode(
        [I(O.DIVIDE, 0, 0), I(O.GREATER, -1, -1), I(O.NOT, -2), I(O.EXIT, -3)],
        (),
    )
    assert compile_strategy(divided, simple_catalog()).evaluate(observation())[
        "action_3_exit"
    ].to_list() == [True, False, False, False]


def test_lookback_is_bounded_and_session_reset_is_not_a_crossing():
    catalog = simple_catalog(
        (Parameter("observations", Unit.COUNT, 1, 4096, integer=True),)
    )
    p = Program.encode(
        [
            I(O.LAG, 0, parameter=0),
            I(O.LAG, -1, parameter=0),
            I(O.GREATER, 0, -2),
            I(O.EXIT, -3),
        ],
        (4096,),
    )
    with pytest.raises(EncodingError, match="Composed temporal history"):
        compile_strategy(p, catalog)
    p = Program.encode(
        [I(O.LAG, 0, parameter=0), I(O.GREATER, 0, -1), I(O.EXIT, -2)], (1,)
    )
    data = observation().with_columns(
        pl.Series("session_date", ["D1", "D1", "D2", "D2"]),
        pl.lit(True).alias("valid"),
        pl.col("time_us").alias("available"),
        pl.Series("close", [10.0, 20.0, 100.0, 101.0]),
    )
    assert compile_strategy(p, catalog).evaluate(data)["action_2_exit"].to_list() == [
        False,
        True,
        False,
        True,
    ]


def test_composed_windows_partition_by_session_and_identity():
    catalog = simple_catalog(
        (Parameter("observations", Unit.COUNT, 1, 5, integer=True),)
    )
    p = Program.encode(
        [
            I(O.LAG, 0, parameter=0),
            I(O.ROLLING_MAX, -1, parameter=0),
            I(O.GREATER, 0, -2),
            I(O.EXIT, -3),
        ],
        (2,),
    )
    compiled = compile_strategy(p, catalog)
    assert compiled.history_rows == 4
    f = pl.DataFrame(
        {
            "listing_id": ["A"] * 5 + ["B"] * 5,
            "session_date": ["D"] * 10,
            "time_us": [1, 2, 3, 4, 5] * 2,
            "available": [1, 2, 3, 4, 5] * 2,
            "valid": [True] * 10,
            "close": [1.0, 2.0, 3.0, 4.0, 5.0, 100.0, 99.0, 98.0, 97.0, 96.0],
        }
    )
    result = compiled.evaluate(f.reverse())
    assert result.filter(pl.col("listing_id") == "A")["action_3_exit"].to_list() == [
        False,
        False,
        False,
        True,
        True,
    ]
    assert not result.filter(pl.col("listing_id") == "B")["action_3_exit"].any()


def prepared_fixture(strategy_ms=1000, broker_ms=1000):
    start = datetime(2026, 8, 18, 4, tzinfo=ZoneInfo("America/New_York"))
    config = Session(
        Path("manifest"),
        Path("ledger"),
        Path("D:/TradingML/runtimes/vectorized_backtest"),
        start,
        start + timedelta(seconds=3),
        strategy_ms,
        broker_ms,
        warmup_seconds=0,
    )
    first = int(start.timestamp() * 1_000_000)
    times = list(range(first + broker_ms * 1000, first + 3_000_001, broker_ms * 1000))
    bars = pl.DataFrame(
        {
            "listing_id": ["A"] * len(times),
            "time_us": times,
            "close": [10.0] * len(times),
            "valid": [True] * len(times),
            "volume": [20.0] * len(times),
            "notional": [200.0] * len(times),
            "vwap": [10.0] * len(times),
        }
    )
    features = pl.DataFrame(
        {
            "listing_id": ["A"] * 3,
            "time_us": [first + 1_000_000, first + 2_000_000, first + 3_000_000],
            "close_int_1000": [100000] * 3,
            "price_valid_1000": [True] * 3,
            "bar_at_1000": [first + 1_000_000, first + 2_000_000, first + 3_000_000],
            "ema_20_1000": [9.0] * 3,
            "rsi_14_1000": [70.0] * 3,
            "rsi_ready_1000": [True] * 3,
            "macd_line_1000": [1.0] * 3,
            "macd_signal_1000": [0.0] * 3,
            "indicator_valid_1000": [True] * 3,
            "indicator_at_1000": [
                first + 1_000_000,
                first + 2_000_000,
                first + 3_000_000,
            ],
        }
    )
    program, catalog = momentum_example(cash_fraction=0.1)
    compiled = compile_strategy(program, catalog)
    prepared = PreparedSession(
        config,
        pl.DataFrame(
            {
                "listing_id": ["A"],
                "admitted_at_us": [first + 1_000_000],
                "ticker": ["A"],
            }
        ),
        bars,
        {1000: features},
        compiled.dependencies,
        "fixture",
        {},
    )
    return compiled, prepared


@pytest.mark.parametrize(
    "strategy_ms,broker_ms", [(1000, 1000), (100, 1000), (1000, 100)]
)
def test_clock_separation_delay_capacity_and_conservation(strategy_ms, broker_ms):
    compiled, prepared = prepared_fixture(strategy_ms, broker_ms)
    result = evaluate(compiled, prepared, Broker(initial_cash=1000), keep_fills=True)
    fills = result["fills"]
    assert not fills.is_empty()
    assert fills.select(
        (pl.col("submitted_us") <= pl.col("time_us") - broker_ms * 1000).all()
    ).item()
    assert fills.select(
        ((pl.col("buy") + pl.col("sell")) <= pl.col("capacity")).all()
    ).item()
    assert abs(result["accounts"]["cash"][-1] - 1000 - fills["cash_delta"].sum()) < 1e-8
    assert (
        result["state"]["quantity"].sum()
        == fills.select((pl.col("buy") - pl.col("sell")).sum()).item()
    )
    assert result["accounts"]["cash"].min() >= -1e-8


def test_no_admission_no_actions_and_future_tail_invariance():
    compiled, prepared = prepared_fixture()
    base = evaluate(compiled, prepared)
    future = replace(
        prepared,
        features={
            1000: prepared.features[1000].with_columns(
                pl.when(pl.col("time_us") == pl.col("time_us").max())
                .then(1.0)
                .otherwise(pl.col("ema_20_1000"))
                .alias("ema_20_1000")
            )
        },
    )
    assert base["accounts"].equals(evaluate(compiled, future)["accounts"])
    excluded = replace(
        prepared,
        watchlist=prepared.watchlist.with_columns(
            (pl.col("admitted_at_us") + 10_000_000).alias("admitted_at_us")
        ),
    )
    assert evaluate(compiled, excluded)["filled_shares"] == 0


def test_admission_query_keeps_price_gate_after_lag_and_is_read_only():
    source = {
        "build_id": "B",
        "units": {
            "2026-08-18": {
                "A": {"bars": {"attempt_id": "00000000-0000-0000-0000-000000000001"}}
            }
        },
    }
    statement = admission_sql(source, "2026-08-18", ["A"], Funnel(), 34_200_000_000)
    from research.rl_trading.v1.arte_sql import _approved

    assert _approved(statement) == statement
    assert "arrayFold" in statement and "x.1+3000" in statement
    assert "WHERE prior_close>0" in statement
    assert "AND close_int/10000. BETWEEN" not in statement


def test_invalid_funnel_is_rejected_before_accessing_source_or_runtime():
    compiled, prepared = prepared_fixture()
    # These fixture paths are intentionally absent; a bad gate must fail first,
    # even when the eventual watchlist would contain no candidates.
    with pytest.raises(EncodingError, match="Early Squeeze"):
        prepare_session(
            prepared.config, Funnel(min_price=float("nan")), compiled.dependencies
        )

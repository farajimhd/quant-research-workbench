"""Matched reference trajectories on CPU/CUDA, not merely speed smoke tests."""

from dataclasses import replace

import numpy as np
import polars as pl
import pytest
import torch

from research.vectorized_backtest.v1.strategy_encoding import (
    Broker,
    Parameter,
    Program,
    Unit,
)
from research.vectorized_backtest.v1.strategy_encoding import (
    Instruction as I,
)
from research.vectorized_backtest.v1.strategy_encoding import (
    Operation as O,
)
from research.vectorized_backtest.v1.strategy_encoding import (
    compile_strategy as polars_compile,
)
from research.vectorized_backtest.v1.strategy_encoding.examples import momentum_example
from research.vectorized_backtest.v1.strategy_encoding.replay import (
    evaluate as polars_replay,
)
from research.vectorized_backtest.v1.strategy_encoding.tests.test_pipeline import (
    prepared_fixture,
    simple_catalog,
)
from research.vectorized_backtest.v1.torch_backtest import (
    HistoryOperation,
    ReplayRunner,
    compile_strategy,
    to_tensors,
)
from research.vectorized_backtest.v1.torch_backtest.examples import history_example
from research.vectorized_backtest.v1.torch_backtest.export import to_frames
from research.vectorized_backtest.v1.torch_backtest.reference import evaluate_reference

torch.set_num_threads(1)
DEVICES = ["cpu"] + (["cuda"] if torch.cuda.is_available() else [])


def assert_reference(strategy, prepared, device, backend="eager", values=None):
    tape = to_tensors(prepared, strategy, device=device)
    runner = ReplayRunner(
        strategy, tape, Broker(initial_cash=1000), values=values, backend=backend
    )
    actual = runner.run()
    for candidate in range(actual["batch"]):
        program = replace(
            strategy.program, values=tuple(runner.theta[candidate].cpu().tolist())
        )
        expected = polars_replay(
            polars_compile(program, strategy.catalog),
            prepared,
            Broker(initial_cash=1000),
        )
        accounts, state = to_frames(actual, tape, candidate=candidate)
        assert accounts["time_us"].equals(expected["accounts"]["time_us"])
        np.testing.assert_allclose(
            accounts.select(
                "cash", "realized_pnl", "equity", "market_value"
            ).to_numpy(),
            expected["accounts"]
            .select("cash", "realized_pnl", "equity", "market_value")
            .to_numpy(),
            rtol=0,
            atol=1e-8,
        )
        reference = expected["state"].sort("listing_id")
        assert state["listing_id"].equals(reference["listing_id"])
        for name in ("quantity", "side", "remaining", "submitted_us"):
            assert state[name].equals(reference[name])
        for name in ("book_cost", "stop", "target", "mark"):
            np.testing.assert_allclose(
                state[name].to_numpy(), reference[name].to_numpy(), rtol=0, atol=1e-8
            )
        assert actual["filled_shares"][candidate].item() == expected["filled_shares"]
    # Captured pointers/state must reset: a second run is the same trajectory.
    torch.testing.assert_close(
        actual["accounts"], runner.run()["accounts"], rtol=0, atol=0
    )
    return actual


@pytest.mark.parametrize("device", DEVICES)
@pytest.mark.parametrize(
    "strategy_ms,broker_ms", [(1000, 1000), (100, 1000), (1000, 100)]
)
def test_mixed_clocks_partial_fills_and_accounting(device, strategy_ms, broker_ms):
    _, prepared = prepared_fixture(strategy_ms, broker_ms)
    p, c = momentum_example(cash_fraction=0.1)
    assert_reference(compile_strategy(p, c), prepared, device)


@pytest.mark.parametrize("device", DEVICES)
def test_independent_candidate_accounts(device):
    _, prepared = prepared_fixture()
    p, c = momentum_example(cash_fraction=0.1)
    assert_reference(
        compile_strategy(p, c),
        prepared,
        device,
        values=[[2, 50, 0.1], [2, 50, 0.8], [100, 99, 0.2]],
    )


@pytest.mark.parametrize("device", DEVICES)
def test_admission_and_future_invalid_inputs(device):
    _, prepared = prepared_fixture()
    p, c = momentum_example(cash_fraction=0.1)
    masked = replace(
        prepared,
        features={
            1000: prepared.features[1000].with_columns(
                (pl.col("indicator_at_1000") + 10_000_000).alias("indicator_at_1000")
            )
        },
    )
    assert_reference(compile_strategy(p, c), masked, device)
    excluded = replace(
        prepared,
        watchlist=prepared.watchlist.with_columns(
            (pl.col("admitted_at_us") + 10_000_000).alias("admitted_at_us")
        ),
    )
    assert_reference(compile_strategy(p, c), excluded, device)


@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA unavailable")
def test_cudagraph_matches_eager_and_resets():
    _, prepared = prepared_fixture()
    p, c = momentum_example(cash_fraction=0.1)
    assert_reference(compile_strategy(p, c), prepared, "cuda", backend="cudagraph")


@pytest.mark.parametrize(
    "operation", [O.LAG, O.ROLLING_MIN, O.ROLLING_MAX, O.CROSS_ABOVE, O.CROSS_BELOW]
)
def test_temporal_and_unknown_logic_match_polars(operation):
    parameters = (
        Parameter("window", Unit.COUNT, 1, 5, integer=True),
        Parameter("price", Unit.PRICE, 1, 100),
    )
    catalog = simple_catalog(parameters)
    if operation in {O.CROSS_ABOVE, O.CROSS_BELOW}:
        instructions = [
            I(O.PARAMETER, parameter=1),
            I(operation, 0, -1),
            I(O.NOT, -2),
            I(O.EXIT, -3),
        ]
    else:
        instructions = [
            I(operation, 0, parameter=0),
            I(O.GREATER, 0, -1),
            I(O.NOT, -2),
            I(O.EXIT, -3),
        ]
    program = Program.encode(instructions, (2, 10))
    strategy = compile_strategy(program, catalog)
    values = [9.0, 11.0, float("nan"), 12.0, 8.0, 10.0]
    history = strategy.history(1, 1, "cpu")
    theta = strategy.parameters(program.values, "cpu")
    actual = []
    for i, x in enumerate(values):
        actions = strategy.evaluate(
            {0: torch.tensor([[x]], dtype=torch.float64)},
            theta,
            history,
            torch.tensor(i),
            torch.tensor(True),
        )
        actual.append(bool(actions[0, 0, 2]))
    frame = pl.DataFrame(
        {
            "listing_id": ["A"] * 6,
            "session_date": ["D"] * 6,
            "time_us": list(range(6)),
            "available": list(range(6)),
            "valid": [True] * 6,
            "close": values,
        }
    )
    expected = polars_compile(program, catalog).evaluate(frame)
    assert actual == expected[expected.columns[-1]].to_list()


def test_explicit_memory_and_device_guards():
    _, prepared = prepared_fixture()
    p, c = momentum_example()
    strategy = compile_strategy(p, c)
    with pytest.raises(MemoryError):
        to_tensors(prepared, strategy, max_gib=1e-12)
    with pytest.raises(ValueError):
        ReplayRunner(strategy, to_tensors(prepared, strategy), backend="cudagraph")


def test_alignment_shared_scan_respects_distinct_null_and_nan_updates():
    _, prepared = prepared_fixture()
    p, c = momentum_example()
    prepared = replace(
        prepared,
        features={
            1000: prepared.features[1000].with_columns(
                pl.Series("ema_20_1000", [9.0, None, float("nan")], dtype=pl.Float64)
            )
        },
    )
    strategy = compile_strategy(p, c)
    tape = to_tensors(prepared, strategy)
    label = next(f.label for f in strategy.dependencies if f.name == "ema_20@1000ms")
    np.testing.assert_allclose(
        tape.market[:, 0, tape.labels.index(label)].numpy(),
        [float("nan"), 9.0, 9.0, float("nan")],
        equal_nan=True,
    )


@pytest.mark.parametrize("device", DEVICES)
def test_shared_cash_pro_rata_across_competing_listings(device):
    _, prepared = prepared_fixture()
    p, c = momentum_example(cash_fraction=0.8)
    prepared = replace(
        prepared,
        watchlist=pl.concat(
            [
                prepared.watchlist,
                prepared.watchlist.with_columns(
                    pl.lit("B").alias("listing_id"), pl.lit("B").alias("ticker")
                ),
            ]
        ),
        features={
            1000: pl.concat(
                [
                    prepared.features[1000],
                    prepared.features[1000].with_columns(
                        pl.lit("B").alias("listing_id")
                    ),
                ]
            )
        },
        broker_bars=pl.concat(
            [
                prepared.broker_bars,
                prepared.broker_bars.with_columns(pl.lit("B").alias("listing_id")),
            ]
        ).with_columns(
            pl.lit(1000.0).alias("volume"), pl.lit(10000.0).alias("notional")
        ),
    )
    result = assert_reference(compile_strategy(p, c), prepared, device)
    assert result["filled_shares"].cpu().tolist() == [98]
    assert result["state"]["quantity"].cpu().tolist() == [[49, 49]]


def test_parameterless_policy_and_invalid_execution_contract():
    _, prepared = prepared_fixture()
    from research.vectorized_backtest.v1.strategy_encoding import arte_catalog

    c = arte_catalog()
    label = next(f.label for f in c.inputs if f.name == "watchlisted")
    p = Program.encode([I(O.EXIT, label)], ())
    s = compile_strategy(p, c)
    result = ReplayRunner(s, to_tensors(prepared, s)).run()
    assert result["filled_shares"].tolist() == [0]
    invalid = replace(
        prepared,
        broker_bars=prepared.broker_bars.with_columns(pl.lit(0.0).alias("vwap")),
    )
    with pytest.raises(ValueError, match="execution"):
        to_tensors(invalid, s)


@pytest.mark.parametrize("device", DEVICES)
@pytest.mark.parametrize("strategy_ms", [100, 1000])
def test_source_windows_count_bars_not_decision_rows_and_optimize_lookback(
    device, strategy_ms
):
    _, prepared = prepared_fixture(strategy_ms, 1000)
    p, c = history_example(2)
    strategy = compile_strategy(p, c)
    features = prepared.features[1000].with_columns(
        pl.lit(105000).alias("high_int_1000"), pl.lit(True).alias("extremes_valid_1000")
    )
    prepared = replace(
        prepared, features={1000: features}, dependencies=strategy.dependencies
    )
    tape = to_tensors(prepared, strategy, device=device)
    values = [[1, -1, 50, 0.1], [2, -1, 50, 0.1], [3, -1, 50, 0.1]]
    runner = ReplayRunner(strategy, tape, Broker(initial_cash=1000), values=values)
    roundtrip = compile_strategy(
        replace(p, values=tuple(float(v) for v in p.values)), c
    )
    assert roundtrip.fingerprint == strategy.fingerprint
    assert runner.candidate_fingerprints[1] != runner.candidate_fingerprints[0]
    actual = runner.run()
    assert actual["filled_shares"].cpu().tolist() == [4, 2, 0]
    for candidate, row in enumerate(values):
        oracle = evaluate_reference(
            replace(p, values=tuple(row)), c, prepared, Broker(initial_cash=1000)
        )
        account, _ = to_frames(actual, tape, candidate=candidate)
        np.testing.assert_allclose(
            account["equity"].to_numpy(),
            oracle["accounts"]["equity"].to_numpy(),
            rtol=0,
            atol=1e-8,
        )
    if device == "cuda" and strategy_ms == 1000:
        captured = ReplayRunner(
            strategy,
            tape,
            Broker(initial_cash=1000),
            values=values,
            backend="compiled_graph",
        )
        torch.testing.assert_close(
            actual["accounts"], captured.run()["accounts"], rtol=0, atol=1e-8
        )
        captured.set_parameters(list(reversed(values)))
        assert captured.run()["filled_shares"].cpu().tolist() == [0, 2, 4]
    bank = tape.windows[("bars", 1000)]
    view = bank.gather(
        torch.tensor([tape.market.shape[0] - 1], device=tape.device), tape.end_us, 12
    )
    assert view.shape == (1, 2, 12)
    assert torch.isnan(view[:, :, 3:]).all()


@pytest.mark.parametrize("device", DEVICES)
@pytest.mark.parametrize(
    "operation,expected",
    [
        (HistoryOperation.BAR_LAG, [True, True]),
        (HistoryOperation.BAR_MIN, [False, True]),
        (HistoryOperation.BAR_MAX, [False, False]),
        (HistoryOperation.BAR_MEAN, [False, True]),
        (HistoryOperation.BAR_SUM, [False, False]),
    ],
)
def test_atomic_source_window_operations_and_missing_history(
    device, operation, expected
):
    _, catalog = history_example()
    label = next(f.label for f in catalog.inputs if f.name == "close@1000ms")
    program = Program.encode(
        [I(operation, label, parameter=0), I(O.GREATER, label, -1), I(O.EXIT, -2)],
        (2, 2.0, 50.0, 0.02),
    )
    strategy = compile_strategy(program, catalog)
    theta = strategy.parameters([[1, 2, 50, 0.02], [2, 2, 50, 0.02]], device)
    inputs = {label: torch.full((2, 1), 3.0, dtype=torch.float64, device=device)}
    window = torch.tensor([[3.0, 2.0, 1.0]], dtype=torch.float64, device=device)
    arguments = (
        inputs,
        theta,
        (),
        torch.tensor(0, device=device),
        torch.tensor(True, device=device),
    )
    actions = strategy.evaluate(*arguments, source_windows={0: window})
    assert actions[:, 0, 2].bool().cpu().tolist() == expected
    # Unknown required observations propagate through comparisons and suppress
    # actions, rather than silently reducing the requested window length.
    actions = strategy.evaluate(
        *arguments, source_windows={0: torch.full_like(window, float("nan"))}
    )
    assert actions[:, 0, 2].bool().cpu().tolist() == [False, False]

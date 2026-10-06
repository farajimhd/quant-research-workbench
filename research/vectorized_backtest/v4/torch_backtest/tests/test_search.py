"""Semantic grammar, tensor policy execution, causal replay and solver witnesses."""

import json

import numpy as np
import pytest
import torch

from research.vectorized_backtest.v4.torch_backtest.fixtures import synthetic_tape
from research.vectorized_backtest.v4.torch_backtest.genome import NAMES, StrategySpace
from research.vectorized_backtest.v4.torch_backtest.grid import Candidate
from research.vectorized_backtest.v4.torch_backtest.rules import (
    Compare,
    Temporal,
    evaluate,
)
from research.vectorized_backtest.v4.torch_backtest.runner import SqueezeRunner
from research.vectorized_backtest.v4.torch_backtest.search_objective import (
    SessionObjective,
    score,
)
from research.vectorized_backtest.v4.torch_backtest.search_runner import SearchRunner
from research.vectorized_backtest.v4.torch_backtest.semantics import (
    Kind,
    arithmetic,
    compare_threshold,
)


def test_absolute_timestamp_cannot_replace_duration_threshold():
    assert arithmetic("subtract", Kind.TIMESTAMP, Kind.TIMESTAMP) == Kind.DURATION
    assert arithmetic("subtract", Kind.PRICE, Kind.PRICE) == Kind.PRICE_DELTA
    with pytest.raises(ValueError):
        compare_threshold(Kind.DURATION, Kind.TIMESTAMP)
    with pytest.raises(ValueError):
        compare_threshold(Kind.PRICE_DELTA, Kind.PRICE)
    with pytest.raises(ValueError):
        arithmetic("add", Kind.TIMESTAMP, Kind.TIMESTAMP)


def test_random_genomes_and_class_mutations_are_valid_and_discrete():
    space = StrategySpace()
    rng = np.random.default_rng(4)
    rows = space.sample(rng, 16)
    assert np.all(np.any(rows != space.default, axis=1))
    for _ in range(5):
        row = space.offspring(rng, rows[0], rows[1])
        space.decode([row])
    bad = space.default.copy()
    bad[0] = 1.5
    with pytest.raises(ValueError):
        space.validate([bad])
    bad = space.default.copy()
    bad[space.rules_start + 2] = 999
    with pytest.raises(ValueError):
        space.repair([bad])
    bad = space.default.copy()
    bad[space.rules_start + 3] = 999
    with pytest.raises(ValueError):
        space.repair([bad])
    assert not any(
        a["kind"] == Kind.TIMESTAMP for a in space.manifest()["atomic_inputs"]
    )


def test_default_reproduces_copied_portfolio_engine():
    tape = synthetic_tape(seconds=55)
    space = StrategySpace()
    reference = SqueezeRunner(tape, [Candidate("signal", positions=10)])
    tested = SearchRunner(tape, space, [space.default])
    a, b = reference.run(), tested.run()
    for key in ("cash", "fees", "drawdown", "entered", "fill_count", "net_pnl"):
        assert torch.allclose(a[key], b[key], atol=1e-7, rtol=0)
    assert torch.equal(reference.ledger, tested.ledger)


def test_numeric_value_lanes_match_separate_accounts_and_update_in_place():
    space = StrategySpace()
    rows = np.tile(space.default, (2, 1))
    j = space.policy_start + NAMES.index("initial_stop_fraction")
    rows[1, j] = 0.08
    k = space.policy_start + NAMES.index("target_step_fraction")
    rows[1, k] = 0.06
    tape = synthetic_tape(seconds=65)
    runner = SearchRunner(tape, space, rows)
    ptr = runner.numeric.data_ptr()
    observed = runner.run()
    for lane, row in enumerate(rows):
        single = SearchRunner(tape, space, [row]).run()
        for key in ("cash", "fees", "drawdown", "entered", "fill_count"):
            assert torch.allclose(
                observed[key][lane], single[key][0], rtol=0, atol=1e-7
            )
    runner.set_genomes(rows[::-1].copy())
    assert runner.numeric.data_ptr() == ptr
    repeated = runner.run()
    assert torch.allclose(repeated["net_pnl"], observed["net_pnl"].flip(0))


def test_rule_operations_input_ids_and_completed_history_change_execution():
    clauses = torch.tensor(
        [
            [
                [1, Compare.GREATER, 0, Temporal.LAG, 2, 10],
                [0, 0, 0, 0, 1, 1],
                [0, 0, 0, 0, 1, 1],
                [0, 0, 0, 0, 1, 1],
            ]
        ],
        dtype=torch.float64,
    )
    inputs = torch.zeros(1, 1, 8)
    inputs[0, 0, 0] = 20
    history = torch.tensor([[20, 15, 9] + [float("nan")] * 10], dtype=torch.float64)
    assert not evaluate(clauses, torch.zeros(1, 3), inputs, history).item()
    clauses[0, 0, 4] = 1
    assert evaluate(clauses, torch.zeros(1, 3), inputs, history).item()
    clauses[0, 0, 1] = Compare.LESS
    assert not evaluate(clauses, torch.zeros(1, 3), inputs, history).item()
    clauses[0, 0, 3] = Temporal.MINIMUM
    clauses[0, 0, 4] = 4
    assert not evaluate(
        clauses, torch.zeros(1, 3), inputs, history
    ).item()  # Missing window evidence.


def test_future_market_and_v7_targets_cannot_change_prefix_orders():
    tape = synthetic_tape(seconds=60)
    targets = tape.level_lower[None].expand(60, -1, -1).clone()
    tape.structural_targets = targets
    space = StrategySpace()
    row = space.default.copy()
    row[6] = 1
    a = SearchRunner(tape, space, [row])
    a.run(steps=15)
    other = synthetic_tape(seconds=60)
    other.structural_targets = targets.clone()
    other.structural_targets[15:] += 100
    other.close[15:] *= 3
    b = SearchRunner(other, space, [row])
    b.run(steps=15)
    assert torch.equal(a.ledger, b.ledger)
    assert torch.equal(a.requested_quantity, b.requested_quantity)
    assert torch.equal(a.target, b.target)


def test_residual_positions_invalid_and_activity_constraint_is_explicit():
    result = dict(
        net_pnl=[100, 0],
        drawdown=[10, 0],
        entered=[1, 0],
        positions_opened=[1, 0],
        exposure_seconds=[1, 0],
        stop_risk_dollar_seconds=[0, 0],
        capital_dollar_seconds=[0, 0],
        terminal_valid=[False, True],
    )
    values, reasons = score([result])
    assert values[0] is None and reasons[0] == "residual_exposure"
    assert values[1] == 0
    values, reasons = score([result], minimum_training_entries=1)
    assert reasons[1] == "minimum_training_activity"


def test_grouped_evaluation_scatter_and_reset():
    space = StrategySpace()
    rows = np.tile(space.default, (4, 1))
    j = space.policy_start + NAMES.index("adaptive_window")
    rows[[0, 2], j] = 12
    evaluator = SessionObjective(synthetic_tape(seconds=55), space, 4)
    observed = evaluator(rows)
    assert observed["shape_groups"] == 1
    for lane, row in enumerate(rows):
        reference = SearchRunner(evaluator.tape, space, [row]).run()
        assert observed["net_pnl"][lane] == pytest.approx(
            reference["net_pnl"][0].item()
        )
    assert evaluator(rows)["net_pnl"] == observed["net_pnl"]


def test_optimizer_synthetic_main_runs_and_freezes_before_evaluation(tmp_path):
    from research.vectorized_backtest.v4.torch_backtest.optimize import main

    assert (
        main(
            [
                "--synthetic",
                "--runtime",
                str(tmp_path),
                "--device",
                "cpu",
                "--backend",
                "eager",
                "--population",
                "4",
                "--generations",
                "1",
                "--minimum-training-entries",
                "0",
            ]
        )
        == 0
    )
    run = next((tmp_path / "experiments").iterdir())
    report = json.loads((run / "report.json").read_text())
    assert report["synthetic"] and not report["validation_used_for_selection"]
    assert (run / "winner.json").exists()
    assert report["candidate_order"] == ["default", "optimized"]
    assert all(v is not None for v in report["validation_scores"])


@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA unavailable")
@pytest.mark.parametrize("backend", ["cudagraph", "compiled_graph"])
def test_search_tensor_buffers_captured_cuda_match_eager_cpu(backend, tmp_path):
    from research.vectorized_backtest.v4.torch_backtest.runtime import configure_caches

    configure_caches(tmp_path)
    space = StrategySpace()
    rows = np.tile(space.default, (4, 1))
    rows[1, space.policy_start + NAMES.index("target_step_fraction")] = 0.04
    rows[2, space.rules_start : space.rules_start + 6] = [
        1,
        Compare.GREATER,
        0,
        Temporal.CURRENT,
        1,
        1000,
    ]
    reference = SearchRunner(synthetic_tape(seconds=35, listings=1), space, rows)
    expected = reference.run()
    tested = SearchRunner(
        synthetic_tape(seconds=35, listings=1, device="cuda"),
        space,
        rows,
        backend=backend,
    ).compile()
    observed = tested.run()
    assert torch.allclose(
        expected["net_pnl"], observed["net_pnl"].cpu(), rtol=0, atol=1e-7
    )
    assert torch.allclose(reference.ledger, tested.ledger.cpu(), rtol=0, atol=1e-7)
    tested.set_genomes(rows[::-1].copy())
    assert torch.allclose(
        tested.run()["net_pnl"].cpu(), expected["net_pnl"].flip(0), rtol=0, atol=1e-7
    )


def test_atomic_history_supports_liquidity_not_only_close():
    clauses = torch.tensor(
        [
            [
                [1, Compare.GREATER, 3, Temporal.MEAN, 2, 1500],
                [0, 0, 0, 0, 1, 1],
                [0, 0, 0, 0, 1, 1],
                [0, 0, 0, 0, 1, 1],
            ]
        ],
        dtype=torch.float64,
    )
    features = torch.zeros(1, 1, 13, dtype=torch.float64)
    history = torch.full((1, 13, 13), float("nan"), dtype=torch.float64)
    history[0, :2, 3] = torch.tensor([1000.0, 3000.0])
    assert evaluate(clauses, torch.zeros(1, 3), features, history).item()
    history[0, 1, 3] = float("nan")
    assert not evaluate(clauses, torch.zeros(1, 3), features, history).item()


def test_v7_atomic_input_fails_closed_until_causal_clock_available():
    space = StrategySpace()
    row = space.default.copy()
    row[space.rules_start : space.rules_start + 6] = [
        1,
        Compare.GREATER,
        8,
        Temporal.CURRENT,
        1,
        0,
    ]
    tape = synthetic_tape(seconds=55)
    tape.structural_clock.fill_(False)
    assert SearchRunner(tape, space, [row]).run()["entered"].item() == 0
    tape.structural_clock.fill_(True)
    assert SearchRunner(tape, space, [row]).run()["entered"].item() > 0


def test_phase_checkpoint_resumes_exact_population_rng_and_fitness(tmp_path):
    from types import SimpleNamespace

    from research.vectorized_backtest.v4.torch_backtest.optimize import phase

    class DeterministicObjective:
        def __init__(self, fail=False):
            self.calls = 0
            self.fail = fail

        def __call__(self, rows):
            self.calls += 1
            if self.fail and self.calls == 2:
                raise RuntimeError("injected interruption")
            size = len(rows)
            return dict(
                net_pnl=rows[:, 10].tolist(),
                drawdown=[0.0] * size,
                entered=[1] * size,
                positions_opened=[1] * size,
                exposure_seconds=[0.0] * size,
                stop_risk_dollar_seconds=[0.0] * size,
                capital_dollar_seconds=[0.0] * size,
                terminal_valid=[True] * size,
                compile_seconds=0.0,
                replay_seconds=0.0,
            )

    args = SimpleNamespace(
        seed=41, population=4, generations=3, weights={}, minimum_training_entries=0
    )
    complete = tmp_path / "complete"
    complete.mkdir()
    interrupted = tmp_path / "interrupted"
    interrupted.mkdir()
    expected = phase([DeterministicObjective()], StrategySpace(), args, complete, 1)
    with pytest.raises(RuntimeError, match="injected"):
        phase([DeterministicObjective(True)], StrategySpace(), args, interrupted, 1)
    checkpoint = json.loads((interrupted / "checkpoint.json").read_text())
    assert checkpoint["next_generation"] == 1
    actual = phase(
        [DeterministicObjective()],
        StrategySpace(),
        args,
        interrupted,
        1,
        checkpoint=checkpoint,
    )
    assert actual == expected
    for index in range(3):
        a = json.loads((complete / f"generation_{index:03d}.json").read_text())
        b = json.loads((interrupted / f"generation_{index:03d}.json").read_text())
        for key in ("population", "scores", "best_score", "repair_counts"):
            assert a[key] == b[key]


def test_all_history_and_deadline_lanes_match_original_scalar_settings():
    space = StrategySpace()
    rows = np.tile(space.default, (4, 1))
    for lane, row in enumerate(rows):
        row[0] = 3
        row[1] = 1
        row[7] = lane % 2
        row[8] = lane % 2
        for name, value in dict(
            adaptive_window=2 + lane * 3,
            swing_left_seconds=1 + lane,
            swing_right_seconds=4 - lane,
            retest_lookback_seconds=1 + lane,
            momentum_lookback_seconds=2 + lane,
            attention_lookback_seconds=3 + lane,
            terminal_exit_lead_seconds=5 + lane * 5,
        ).items():
            row[space.policy_start + NAMES.index(name)] = value
    tape = synthetic_tape(seconds=65, listings=3)
    runner = SearchRunner(tape, space, rows)
    actual = runner.run()
    for lane, decoded in enumerate(space.decode(rows)):
        reference = SqueezeRunner(tape, [decoded.candidate], decoded.settings)
        expected = reference.run()
        for name in ("cash", "fees", "drawdown", "entered", "fill_count", "net_pnl"):
            assert torch.allclose(
                actual[name][lane], expected[name][0], rtol=0, atol=1e-7
            ), name
        assert torch.allclose(
            runner.ledger[lane], reference.ledger[0], rtol=0, atol=1e-7
        )
    assert runner.numeric.shape[1] == 40 and space.size == 77


def test_search_state_checkpoint_preserves_atomic_history_and_swing_book():
    space = StrategySpace()
    rows = np.tile(space.default, (2, 1))
    rows[1, 0] = 3
    rows[1, 1] = 1
    rows[1, 8] = 1
    tape = synthetic_tape(seconds=65)
    complete = SearchRunner(tape, space, rows)
    expected = complete.run()
    prefix = SearchRunner(tape, space, rows)
    prefix.run(steps=18)
    recovered = SearchRunner(tape, space, rows)
    recovered.load_state_dict(prefix.state_dict())
    actual = recovered.run(reset=False)
    assert torch.allclose(actual["net_pnl"], expected["net_pnl"], rtol=0, atol=1e-7)
    assert torch.equal(recovered.ledger, complete.ledger)


def test_launcher_resume_skips_frozen_selection(tmp_path, monkeypatch):
    from research.vectorized_backtest.v4.torch_backtest import optimize

    real_phase = optimize.phase

    def freeze_then_interrupt(*args, **kwargs):
        real_phase(*args, **kwargs)
        raise RuntimeError("injected frozen boundary")

    monkeypatch.setattr(optimize, "phase", freeze_then_interrupt)
    command = [
        "--synthetic",
        "--runtime",
        str(tmp_path),
        "--device",
        "cpu",
        "--backend",
        "eager",
        "--population",
        "4",
        "--generations",
        "1",
        "--minimum-training-entries",
        "0",
    ]
    with pytest.raises(RuntimeError, match="frozen boundary"):
        optimize.main(command)
    run = next((tmp_path / "experiments").iterdir())
    frozen = (run / "winner.json").read_bytes()

    def forbidden(*args, **kwargs):
        raise AssertionError("Frozen winner must not be reselected")

    monkeypatch.setattr(optimize, "phase", forbidden)
    assert optimize.main(command + ["--resume", str(run)]) == 0
    assert frozen == (run / "winner.json").read_bytes()
    with pytest.raises(RuntimeError, match="immutable"):
        optimize.main(command + ["--resume", str(run)])


def test_submitted_but_unfilled_orders_do_not_satisfy_activity():
    result = dict(
        net_pnl=[0],
        drawdown=[0],
        entered=[1],
        positions_opened=[0],
        exposure_seconds=[0],
        stop_risk_dollar_seconds=[0],
        capital_dollar_seconds=[0],
        terminal_valid=[True],
    )
    values, reasons = score([result], minimum_training_entries=1)
    assert values == [None] and reasons == ["minimum_training_activity"]


@pytest.mark.parametrize("backend", ["eager", "compiled_graph"])
def test_adaptive_activation_uses_each_candidate_window_before_capacity(
    backend, tmp_path
):
    if backend == "compiled_graph" and not torch.cuda.is_available():
        pytest.skip("CUDA unavailable")
    space = StrategySpace()
    rows = np.tile(space.default, (2, 1))
    rows[:, 4] = 1
    rows[:, 7] = 1
    for name, values in (
        ("adaptive_window", [2, 5]),
        ("adaptive_multiplier", [0.5, 0.5]),
        ("target_step_fraction", [0.2, 0.2]),
    ):
        rows[:, space.policy_start + NAMES.index(name)] = values
    tape = synthetic_tape([10.0] * 9 + [10.4] * 55)
    reference = SearchRunner(tape, space, rows)
    reference.run(steps=9)
    initial = reference.stop[:, 0, 0].clone()
    assert reference.settings.adaptive_window == 32
    assert reference.first_fill[:, 0, 0].tolist() == [9, 9]
    reference.run(reset=False, steps=2)  # t=11: first-fill age exactly 2.
    assert reference.stop[0, 0, 0] > initial[0]
    assert reference.stop[1, 0, 0] == initial[1]
    reference.run(reset=False, steps=3)  # t=14: age exactly 5.
    assert reference.stop[1, 0, 0] > initial[1]
    if backend == "compiled_graph":
        from research.vectorized_backtest.v4.torch_backtest.runtime import (
            configure_caches,
        )

        configure_caches(tmp_path)
        expected = SearchRunner(tape, space, rows)
        financial = expected.run()
        actual = SearchRunner(tape.to("cuda"), space, rows, backend=backend).compile()
        result = actual.run()
        assert torch.allclose(actual.ledger.cpu(), expected.ledger, rtol=0, atol=1e-7)
        assert torch.allclose(
            result["net_pnl"].cpu(), financial["net_pnl"], rtol=0, atol=1e-7
        )


def test_boundary_t_reads_completed_candle_not_candle_opening_at_t():
    space = StrategySpace()
    row = space.default.copy()
    row[space.rules_start : space.rules_start + 6] = [
        1,
        Compare.GREATER_EQUAL,
        0,
        Temporal.CURRENT,
        1,
        9,
    ]
    base = synthetic_tape([10.0] * 40)
    a = SearchRunner(base, space, [row])
    a.run(steps=8)
    assert a.buy_submitted[0, 0, 0] == 8 and a.fill_count.item() == 0
    # Array row 8 ends at t=9: its interval [8,9) is not available at t=8.
    future = synthetic_tape([10.0] * 40)
    for name in (
        "close",
        "high",
        "low",
        "volume",
        "notional",
        "trades",
        "macd_line",
        "macd_signal",
    ):
        getattr(future, name)[8:] *= 2
    b = SearchRunner(future, space, [row])
    b.run(steps=8)
    assert torch.equal(a.requested_quantity, b.requested_quantity)
    assert torch.equal(a.buy_limit, b.buy_limit)
    assert torch.equal(a.ledger, b.ledger)
    # Row 7 ends at t=8, so changing it can legitimately change the t=8 rule.
    completed = synthetic_tape([10.0] * 7 + [8.0] + [10.0] * 32)
    c = SearchRunner(completed, space, [row])
    c.run(steps=8)
    assert c.entered.item() == 0
    # Only the following interval may execute the new t=8 submission.
    a.run(reset=False, steps=1)
    assert a.fill_count.item() > 0
    assert torch.all(
        a.ledger[0, : int(a.fill_count[0]), 0] == 9
    )  # UTC interval-END receipt.


def test_tape_requires_declared_end_labelled_clock_contract():
    tape = synthetic_tape(seconds=40)
    tape.provenance = dict(tape.provenance)
    tape.provenance.pop("timing_contract")
    with pytest.raises(ValueError, match="completed-boundary"):
        tape.validate()
    from research.vectorized_backtest.v4.torch_backtest.timing import TIMING_CONTRACT

    tape.provenance["timing_contract"] = {
        **TIMING_CONTRACT,
        "clock_label": "interval_start",
    }
    with pytest.raises(ValueError, match="open-labelled"):
        tape.validate()

    tape.provenance["timing_contract"] = dict(TIMING_CONTRACT)
    tape.provenance["timing_fingerprint"] = "tampered"
    with pytest.raises(ValueError, match="integrity seal"):
        tape.validate()

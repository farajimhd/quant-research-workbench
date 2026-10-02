"""Financial and causal witnesses; no historical experiments in this suite."""
from dataclasses import replace
from decimal import Decimal
import ast
from pathlib import Path

import pytest
import torch

from research.vectorized_backtest.v2.torch_backtest import Candidate, Settings, SqueezeRunner, build_grid, grid_manifest
from research.vectorized_backtest.v2.torch_backtest.fixtures import synthetic_tape
from research.vectorized_backtest.v2.torch_backtest.runner import proportional_fill
from research.vectorized_backtest.v2.torch_backtest.run_grid import approval_check, main


def candidate(**kwargs):
    return Candidate("signal", positions=5, **kwargs)


def test_direct_cumulative_fills_preserve_shifted_allocation_exactly():
    generator = torch.Generator().manual_seed(482)
    wanted = torch.randint(0,1000000,(4,833,15),generator=generator)
    capacity = torch.randint(0,15000000,(4,833),generator=generator)
    wanted[0,0]=0; capacity[0,1]=0
    cumulative = wanted.cumsum(-1)
    total = cumulative[..., -1:]
    budget = torch.minimum(capacity[...,None],total).clamp_min(0)
    allocated = torch.floor(cumulative.to(torch.float64)*budget/total.clamp_min(1))
    previous = torch.cat((torch.zeros_like(allocated[...,:1]),allocated[...,:-1]),-1)
    assert torch.equal(proportional_fill(wanted,capacity),(allocated-previous).to(torch.int64))


def test_grid_exact_unique_and_approval_binds_settings():
    grid = build_grid()
    assert len(grid) == len({c.identity for c in grid}) == 4320
    assert len({(c.macd_mask, c.macd_all) for c in grid if c.entry == "macd"}) == 26
    for m in (5, 10, 15):
        assert sum(c.positions == m for c in grid) == 1440
    assert grid_manifest()["approval_digest"] != grid_manifest(replace(Settings(), minimum_trade_count=6))["approval_digest"]
    with pytest.raises(ValueError, match="explicit approval"):
        approval_check(None, grid_manifest())
    with pytest.raises(ValueError, match="explicit approval"):
        main(["--execute"])


def test_v2_has_no_original_package_imports():
    base = Path(__file__).parents[1]
    for path in base.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("research.vectorized_backtest.v1")
            if isinstance(node, ast.Import):
                assert all(not n.name.startswith("research.vectorized_backtest.v1") for n in node.names)


@pytest.mark.parametrize("m", [5, 10, 15])
@pytest.mark.parametrize("weights", ["equal", "increasing", "decreasing"])
def test_one_batch_has_exactly_m_independent_orders(m, weights):
    runner = SqueezeRunner(synthetic_tape(prices=torch.full((90, 1), 10.0)),
                            [Candidate("signal", positions=m, allocation=weights)])
    result = runner.run()
    ledger = runner.ledger[0, :runner.fill_count[0]]
    buys = ledger[ledger[:, 3] == 1]
    assert set(buys[:, 2].tolist()) == set(range(m))
    assert buys[:, 0].min() == 9  # signal/submit 8; never fills its observed bar.
    assert result["entered"].tolist() == [1]
    assert result["terminal_valid"].tolist() == [True]
    assert runner.used.tolist() == [[True]]
    if weights != "equal":
        q = buys[:, 4]
        assert (q[0] < q[-1]) == (weights == "increasing")


def test_independent_decimal_accounting_oracle():
    runner = SqueezeRunner(synthetic_tape(prices=torch.full((90, 1), 10.0)), [candidate()])
    result = runner.run()
    # Independently derived equal budget: 5 orders ×197 shares, one buy and
    # one terminal sell per order. $0.01 round-trip spread and $10 commissions.
    expected_quantity = int((Decimal("9995") / 5 / Decimal("10.11005")))
    assert expected_quantity == 197
    expected = Decimal("10000") - Decimal(expected_quantity * 5) * Decimal("0.01") - Decimal("10")
    assert abs(float(result["cash"][0]) - float(expected)) < 1e-7
    assert abs(float(result["realized"][0]) - (float(expected) - 10000)) < 1e-7
    assert result["fees"].tolist() == [10]


def test_capacity_remainder_partial_fills_and_per_order_commission():
    wanted = torch.tensor([[[19, 19, 19, 19, 19]]])
    fill = proportional_fill(wanted, torch.tensor([[3]]))
    assert fill.sum() == 3
    assert bool((fill <= wanted).all())
    tape = synthetic_tape(prices=torch.full((90, 1), 10.0))
    tape.volume[:] = 10
    tape.notional[:] = 100
    runner = SqueezeRunner(tape, [candidate()], replace(Settings(), participation=1.0, minimum_dollar_volume=100))
    runner.run(steps=10)
    assert runner.quantity.sum() == 20
    assert runner.remaining.sum() > 0
    assert runner.fees.tolist() == [5]  # second partial fills do not pay five new minima.
    runner.run(reset=False, steps=4)
    assert runner.remaining.sum() == 0  # deadline expires; no replacement entry order.


def test_missing_liquidity_cannot_fill_and_does_not_fabricate_cash():
    tape = synthetic_tape(prices=torch.full((30, 1), 10.0))
    tape.volume[:] = 0
    tape.notional[:] = 0
    tape.fill_price[:] = float("nan")
    runner = SqueezeRunner(tape, [candidate()], replace(Settings(), minimum_dollar_volume=0))
    result = runner.run()
    assert result["fill_count"].tolist() == [0]
    assert result["cash"].tolist() == [10000]


@pytest.mark.parametrize("hold,submit", [(2, 11), (5, 14)])
def test_vwap_hold_uses_elapsed_time_after_actual_cross(hold, submit):
    prices = [9.9] * 8 + [10.1] * 32
    tape = synthetic_tape(prices)
    tape.vwap[:] = 10
    runner = SqueezeRunner(tape, [Candidate("hold", hold_seconds=hold, positions=5)])
    runner.run(steps=submit)
    assert runner.entered.tolist() == [1]
    assert runner.quantity.sum() == 0
    assert runner.buy_submitted[runner.remaining > 0].unique().tolist() == [submit]
    runner.run(reset=False, steps=1)
    assert runner.quantity.sum() > 0


def test_missing_observation_resets_hold_and_unknown_macd_cannot_trade():
    tape = synthetic_tape([9.9] * 8 + [10.1] * 32)
    tape.vwap[:] = 10
    tape.observed[9] = False
    runner = SqueezeRunner(tape, [Candidate("hold", hold_seconds=2, positions=5)])
    assert runner.run()["entered"].tolist() == [0]
    tape.macd_line[:] = float("nan")
    runner = SqueezeRunner(tape, [Candidate("macd", macd_mask=15, macd_all=True, positions=5)])
    assert runner.run()["entered"].tolist() == [0]


@pytest.mark.parametrize("all_required,expected", [(False, 1), (True, 0)])
def test_macd_any_all_and_30s_lane(all_required, expected):
    tape = synthetic_tape(listings=1)
    tape.macd_line[:, :, :3] = float("nan")
    runner = SqueezeRunner(tape, [Candidate("macd", macd_mask=9, macd_all=all_required, positions=5)])
    assert runner.run()["entered"].tolist() == [expected]
    runner = SqueezeRunner(tape, [Candidate("macd", macd_mask=8, positions=5)])
    assert runner.run()["entered"].tolist() == [1]


def test_breakout_retest_requires_a_later_continuation():
    tape = synthetic_tape([10.0] * 8 + [10.2, 10.04, 10.10] + [10.10] * 29)
    tape.vwap[:] = 9.9
    tape.low[9] = 10.01
    tape.high[9] = 10.07
    runner = SqueezeRunner(tape, [Candidate("retest", positions=5)])
    runner.run(steps=10)
    assert runner.entered.tolist() == [0]
    runner.run(reset=False, steps=1)
    assert runner.entered.tolist() == [1]
    assert runner.quantity.sum() == 0


def test_swing_stop_needs_confirmed_history():
    tape = synthetic_tape(prices=torch.full((90, 1), 10.0))
    tape.low[:5, 0] = torch.tensor([10, 9.98, 9.9, 9.98, 10])
    runner = SqueezeRunner(tape, [candidate(initial_stop="swing")])
    runner.run(steps=8)
    assert torch.allclose(runner.stop[0, 0, :5], torch.full((5,), 9.89, dtype=torch.float64), atol=1e-6)
    tape.admission[:] = 2
    tape.low[:] = float("nan")
    runner = SqueezeRunner(tape, [candidate(initial_stop="swing")])
    assert runner.run()["entered"].tolist() == [0]


def test_structural_targets_frozen_and_insufficient_levels_reject():
    tape = synthetic_tape()
    runner = SqueezeRunner(tape, [candidate(target="structural")])
    runner.run(steps=8)
    frozen = runner.target.clone()
    tape.level_lower.add_(100)
    runner.run(reset=False, steps=2)
    assert torch.equal(frozen, runner.target)
    tape = synthetic_tape()
    tape.level_resistance[:, 4:] = False
    runner = SqueezeRunner(tape, [candidate(target="structural")])
    result = runner.run()
    assert result["entered"].tolist() == [0]
    assert result["rejected_geometry"][0] > 0


def test_step_trail_raises_one_percent_per_three_percent_and_never_lowers():
    tape = synthetic_tape([10.0] * 9 + [10.4, 10.0] + [10.0] * 30)
    runner = SqueezeRunner(tape, [candidate()], replace(Settings(), target_step_fraction=0.25))
    runner.run(steps=9)
    original = runner.stop.clone()
    runner.run(reset=False, steps=1)
    raised = runner.stop.clone()
    assert torch.allclose(raised[0, 0, :5], original[0, 0, :5] + runner.average[0, 0, :5] * .01)
    runner.run(reset=False, steps=1)
    assert bool((runner.stop >= raised).all())


def test_target_closes_only_its_own_position_and_stop_wins_ambiguous_bar():
    tape = synthetic_tape([10.0] * 9 + [10.3] * 31)
    runner = SqueezeRunner(tape, [candidate()])
    runner.run(steps=11)
    assert runner.quantity[0, 0, 0] == 0
    assert bool((runner.quantity[0, 0, 1:5] > 0).all())
    tape = synthetic_tape([10.0] * 40)
    tape.high[9] = 11
    tape.low[9] = 9
    runner = SqueezeRunner(tape, [candidate()])
    runner.run(steps=10)
    assert runner.exit_kind[0, 0, :5].tolist() == [2] * 5
    assert runner.quantity.sum() > 0  # trigger cannot fill retrospectively.


def test_rotation_waits_for_individual_exit_and_revalidates_new_batch():
    prices = torch.full((90, 2), 10.0, dtype=torch.float64)
    prices[:, 1] = 30
    prices[34:, 1] = 40
    tape = synthetic_tape(prices)
    tape.admission[:] = torch.tensor([8, 35])
    runner = SqueezeRunner(tape, [Candidate("macd", macd_mask=1, positions=5, replacement=True)],
                            replace(Settings(), target_step_fraction=.25))
    result = runner.run()
    assert result["rotations"][0] >= 1
    rows = runner.ledger[0, :runner.fill_count[0]]
    rotation = rows[rows[:, 7] == 3]
    new_buys = rows[(rows[:, 1] == 1) & (rows[:, 3] == 1)]
    assert rotation.shape[0] >= 1
    assert new_buys.shape[0] >= 5
    assert new_buys[:, 0].min() > rotation[:, 0].min()
    assert result["entered"].tolist() == [2]


def test_batch_independence_reset_and_checkpoint_equality():
    tape = synthetic_tape()
    configs = [candidate(), Candidate("macd", macd_mask=8, positions=10, trailing="adaptive")]
    batched = SqueezeRunner(tape, configs)
    expected = batched.run()
    for i, config in enumerate(configs):
        single = SqueezeRunner(tape, [config]).run()
        for key in ("cash", "fees", "drawdown", "entered", "fill_count"):
            assert torch.allclose(single[key][0], expected[key][i], atol=1e-7, rtol=0)
    batched.run(steps=20)
    snapshot = batched.state_dict()
    resumed = SqueezeRunner(tape, configs)
    resumed.load_state_dict(snapshot)
    observed = resumed.run(reset=False)
    assert torch.equal(resumed.ledger, SqueezeRunner(tape, configs).ledger) is False
    for key in ("cash", "fees", "drawdown", "entered", "fill_count"):
        assert torch.equal(observed[key], expected[key])
    assert torch.equal(batched.run()["cash"], expected["cash"])


def test_future_tail_cannot_change_prefix_and_ledger_overflow_fails_closed():
    tape = synthetic_tape()
    a = SqueezeRunner(tape, [candidate()])
    a.run(steps=15)
    changed = synthetic_tape()
    changed.close[15:] = 1000
    changed.high[15:] = 1000
    changed.low[15:] = 999
    b = SqueezeRunner(changed, [candidate()])
    b.run(steps=15)
    for name in a._state_names:
        assert torch.allclose(getattr(a, name), getattr(b, name), equal_nan=True)
    with pytest.raises(RuntimeError, match="ledger full"):
        SqueezeRunner(synthetic_tape(), [candidate()], maximum_fills=1).run()


@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA unavailable")
def test_cpu_cuda_captured_ledger_parity():
    configs = [candidate(), Candidate("macd", macd_mask=8, positions=15, target="structural")]
    cpu = SqueezeRunner(synthetic_tape(), configs)
    reference = cpu.run()
    gpu = SqueezeRunner(synthetic_tape(device="cuda"), configs, backend="cudagraph", graph_steps=16).compile()
    observed = gpu.run()
    for key in ("cash", "fees", "drawdown", "entered", "fill_count"):
        assert torch.allclose(reference[key], observed[key].cpu(), atol=1e-7, rtol=0)
    assert torch.allclose(cpu.ledger, gpu.ledger.cpu(), atol=1e-7, rtol=0)

"""Risk reservations, elapsed holding and objective units have independent oracles."""

from dataclasses import replace

import numpy as np
import pytest
import torch

from research.vectorized_backtest.v4.torch_backtest.fixtures import synthetic_tape
from research.vectorized_backtest.v4.torch_backtest.grid import Candidate, Settings
from research.vectorized_backtest.v4.torch_backtest.runner import SqueezeRunner
from research.vectorized_backtest.v4.torch_backtest.search_objective import score


def result(pnl=100, risk=0, capital=0):
    return dict(net_pnl=[pnl], drawdown=[0], positions_opened=[1],
                exposure_seconds=[1], filled_batches=[1], terminal_valid=[True],
                stop_risk_dollar_seconds=[risk], capital_dollar_seconds=[capital])


def test_upside_is_not_dispersion_penalized_and_units_are_account_hours():
    values, _, components = score([result(100), result(10000)], with_components=True)
    assert components['downside_penalty'] == [0.]
    assert values[0] == pytest.approx(.505)
    values, _, components = score([result(0, 360000, 36000000)], with_components=True)
    assert components['stop_risk_penalty'][0] == pytest.approx(.001)
    assert components['capital_time_penalty'][0] == pytest.approx(.002)
    assert values[0] == pytest.approx(-.003)
    with pytest.raises(ValueError, match='requires replay metric'):
        missing = result()
        del missing['stop_risk_dollar_seconds']
        score([missing])


def test_pending_and_filled_orders_share_stop_risk_and_expiry_is_next_interval():
    tape = synthetic_tape(prices=torch.full((40, 1), 10.0))
    settings = replace(Settings(), maximum_position_hold_seconds=6)
    runner = SqueezeRunner(tape, [Candidate('signal', positions=1)], settings)
    for _ in range(20):
        runner.run(reset=runner.completed == 0, steps=1)
        reserved = (runner.quantity * (runner.average-runner.stop).clamp_min(0)
                    + runner.remaining * (runner.buy_limit-runner.stop).clamp_min(0)).sum()
        assert reserved <= runner.equity[0] * .02 + 1e-7
    ledger = runner.ledger[0, :runner.fill_count[0]]
    buy = ledger[ledger[:, 3] == 1][0, 0]
    sells = ledger[ledger[:, 3] == -1]
    assert len(sells) > 0
    assert sells[0, 0] == buy + 7  # six-second intent, next one-second fill.
    assert sells[0, 7] == 3
    assert runner.capital_dollar_seconds[0] > 0
    assert runner.stop_risk_dollar_seconds[0] > 0


def test_risk_and_expiry_gpu_financial_ledger_parity():
    if not torch.cuda.is_available():
        pytest.skip('CUDA unavailable')
    tape = synthetic_tape(prices=torch.full((28, 2), 10.0))
    settings = replace(Settings(), maximum_position_hold_seconds=6)
    candidates = [Candidate('signal', positions=1), Candidate('signal', positions=3)]
    cpu = SqueezeRunner(tape, candidates, settings)
    gpu = SqueezeRunner(tape.to('cuda'), candidates, settings, backend='compiled_graph', graph_steps=4)
    gpu.compile()
    left, right = cpu.run(), gpu.run()
    for name in ('cash', 'equity', 'realized', 'fees', 'drawdown', 'fill_count',
                 'capital_dollar_seconds', 'stop_risk_dollar_seconds', 'peak_reserved_stop_risk'):
        np.testing.assert_allclose(left[name].numpy(), right[name].cpu().numpy(), rtol=0, atol=1e-7)
    torch.testing.assert_close(cpu.ledger, gpu.ledger.cpu(), rtol=0, atol=1e-7)

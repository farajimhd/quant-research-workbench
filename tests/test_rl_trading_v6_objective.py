import math

import pytest
import torch

from research.rl_trading.v6.objective import bracket_loss


def test_conditional_size_and_stop_target_have_gradients():
    # N=2 listings, H=1 held position, action axis is [HOLD, 2 ENTER,
    # EXIT, SET_STOP, SET_TARGET]. Only the chosen conditional head trains.
    logits = torch.tensor([0., 1., 0., 0., 0., 0.], requires_grad=True)
    size = torch.tensor([.4, .6], requires_grad=True)
    stop = torch.tensor([.1], requires_grad=True)
    target = torch.tensor([.2], requires_grad=True)
    loss, _ = bracket_loss(logits, size, stop, target, token=1,
                           size_fraction=.5)
    loss.backward()
    assert size.grad[0] != 0 and size.grad[1] == 0
    assert stop.grad is None and target.grad is None
    logits.grad = None
    stop_loss, _ = bracket_loss(logits, size, stop, target, token=4,
        oracle_log_distance=math.log(10 / 9))
    stop_loss.backward()
    assert stop.grad[0] != 0 and target.grad is None
    target_loss, _ = bracket_loss(logits, size, stop, target, token=5,
        oracle_log_distance=math.log(11 / 10))
    target_loss.backward()
    assert target.grad[0] != 0


def test_invalid_conditional_label_fails_closed():
    logits = torch.zeros(3)
    with pytest.raises(ValueError):
        bracket_loss(logits, torch.zeros(2), torch.zeros(0),
                     torch.zeros(0), token=1)


def test_full_cash_boundary_allows_only_one_float64_rounding_step():
    args = (torch.zeros(3), torch.zeros(2), torch.zeros(0), torch.zeros(0))
    exact, _ = bracket_loss(*args, token=1, size_fraction=1.)
    rounded = math.nextafter(1., math.inf)
    adjusted, _ = bracket_loss(*args, token=1, size_fraction=rounded)
    assert torch.equal(exact, adjusted)
    for invalid in (math.nextafter(rounded, math.inf), 1.01, -.01, math.nan):
        with pytest.raises(ValueError, match='cash fraction'):
            bracket_loss(*args, token=1, size_fraction=invalid)

from types import SimpleNamespace

import pytest
import torch
from torch import nn

from research.rl_trading.v6.causal_feedback import IntentFeedback


class Heads(nn.Module):
    def __init__(self):
        super().__init__()
        self.action = nn.Linear(4, 4)
        self.quality = nn.Linear(4, 2)

    def forward(self, context, held):
        return SimpleNamespace(logits=self.action(context),
                               quality=self.quality(context).sigmoid())


def fixture():
    torch.manual_seed(11)
    model, heads, size = IntentFeedback(4), Heads(), nn.Linear(4, 1)
    base = torch.randn(2, 9, 4)
    clocks = torch.arange(9, dtype=torch.int64)[None].repeat(2, 1) * 1_000_000
    available = torch.ones(2, 9, dtype=torch.bool)
    reset = torch.zeros_like(available)
    held = torch.zeros_like(available)
    return model, heads, size, base, clocks, available, reset, held


def test_future_suffix_cannot_change_earlier_predictions():
    m, h, s, b, c, a, r, p = fixture()
    _, first = m(b, c, a, r, p, h, s)
    changed = b.clone(); changed[:, 5:] += 100
    _, second = m(changed, c, a, r, p, h, s)
    assert torch.equal(first[:, :5], second[:, :5])


@pytest.mark.parametrize('boundary', ['reset', 'missing'])
def test_boundaries_remove_prior_intent(boundary):
    m, h, s, b, c, a, r, p = fixture()
    if boundary == 'reset': r[:, 5] = True
    else: a[:, 4] = False
    _, first = m(b, c, a, r, p, h, s)
    changed = b.clone(); changed[:, :5] += 100
    _, second = m(changed, c, a, r, p, h, s)
    assert torch.equal(first[:, 5:], second[:, 5:])


def test_sparse_sampled_clocks_are_rejected():
    m, h, s, b, c, a, r, p = fixture()
    c[:, 4:] += 1_000_000
    with pytest.raises(ValueError, match='contiguous'):
        m(b, c, a, r, p, h, s)


def test_target_loss_trains_feedback_fuse_without_backpropagating_prior_intent():
    m, h, s, b, c, a, r, p = fixture()
    b.requires_grad_()
    context, _ = m(b, c, a, r, p, h, s)
    context[:, -1].square().sum().backward()
    assert b.grad[:, :-1].abs().sum() == 0
    assert b.grad[:, -1].abs().sum() > 0
    assert m.fuse[0].weight.grad.abs().sum() > 0

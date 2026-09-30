from copy import deepcopy
import pytest
import torch
from research.rl_trading.v6.actor_critic import BracketActorCritic
from research.rl_trading.v6.model import ExecutedActionState


@pytest.mark.parametrize('holdings', [0, 2])
def test_batch_preserves_values_masks_likelihoods_and_parameter_gradients(holdings):
    torch.manual_seed(18)
    model = BracketActorCritic(8)
    reference = deepcopy(model)
    packets, expected = [], []
    for i in range(3):
        market = torch.randn(5, 8)
        account = torch.tensor([1000.+i, 2000., -3., .2, 1., 0., 0.])
        indices = torch.tensor([4, 1][:holdings], dtype=torch.long)
        features = torch.rand(holdings, 11)
        memory = torch.randn(8)
        masks = dict(enter_allowed=torch.tensor([True, False, True, False, True]),
                     exit_allowed=torch.ones(holdings, dtype=torch.bool),
                     stop_allowed=torch.zeros(holdings, dtype=torch.bool),
                     target_allowed=torch.ones(holdings, dtype=torch.bool))
        packets.append((market, account, indices, features, memory, masks))
        expected.append(reference.distribution_and_value(market, account, indices,
                        features, ExecutedActionState(memory), **masks))
    actual = model.decode_batch(packets)
    loss, baseline = 0., 0.
    for (dist, value), (ref, rv) in zip(actual, expected):
        torch.testing.assert_close(dist.logits, ref.logits, atol=2e-6, rtol=2e-6)
        torch.testing.assert_close(dist.locations, ref.locations, atol=2e-6, rtol=2e-6)
        torch.testing.assert_close(value, rv, atol=2e-6, rtol=2e-6)
        loss = loss + dist.log_prob(1, torch.tensor(.2)) + .3*dist.categorical.entropy() + value
        baseline = baseline + ref.log_prob(1, torch.tensor(.2)) + .3*ref.categorical.entropy() + rv
    loss.backward(); baseline.backward()
    for (name, parameter), (_, rp) in zip(model.named_parameters(), reference.named_parameters()):
        if parameter.grad is None:
            assert rp.grad is None, name
        else:
            torch.testing.assert_close(parameter.grad, rp.grad, atol=3e-6, rtol=3e-5, msg=name)


def test_batch_does_not_mix_observations():
    torch.manual_seed(21)
    model = BracketActorCritic(8)
    masks = dict(enter_allowed=torch.ones(2, dtype=torch.bool),
        exit_allowed=torch.zeros(0, dtype=torch.bool), stop_allowed=torch.zeros(0, dtype=torch.bool),
        target_allowed=torch.zeros(0, dtype=torch.bool))
    def packet():
        return (torch.randn(2, 8, requires_grad=True), torch.ones(7),
                torch.empty(0, dtype=torch.long), torch.empty(0, 11), torch.zeros(8), masks)
    packets = [packet(), packet()]
    result = model.decode_batch(packets)
    result[0][0].log_prob(1, torch.tensor(.2)).backward()
    assert packets[0][0].grad.abs().sum() > 0
    assert torch.count_nonzero(packets[1][0].grad) == 0

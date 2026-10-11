import pytest
import torch
from torch import nn
from research.rl_trading.v6.hierarchical_heads import HierarchicalTickerHeads, HierarchicalForecast
from research.rl_trading.v6.staged_heads import configure, objective


def fixture():
    torch.manual_seed(17)
    policy = nn.Module()
    policy.encoder = nn.Linear(8, 8)
    policy.decoder = nn.Module()
    policy.decoder.heads = HierarchicalTickerHeads(8)
    policy.decoder.size_head = nn.Linear(8, 1)
    policy.teacher_forecast = HierarchicalForecast(8, HierarchicalTickerHeads(8))
    policy.action_gru = nn.GRUCell(19, 8)
    x = torch.randn(4, 8)
    actions = torch.arange(4)
    quality = torch.tensor([.8, .2, .3, .9])
    allocation = torch.tensor([.4, float('nan'), float('nan'), float('nan')])
    hard = torch.tensor([[0, 2], [1, 0], [2, 3], [3, 1]])
    forecasts = [(torch.arange(4), hard, torch.nn.functional.one_hot(hard, 4).float())]
    return policy, (x, actions, quality, allocation, forecasts, torch.ones(4))


def test_all_supervised_heads_receive_gradients_without_encoder_updates():
    policy, packet = fixture()
    parameters = configure(policy)
    assert len(parameters) == len({id(p) for p in parameters})
    loss = objective(policy, *packet)
    loss.backward()
    assert all(p.grad is None for p in policy.encoder.parameters())
    for module in (policy.decoder.heads.entry, policy.decoder.heads.exit,
                   policy.decoder.heads.quality, policy.decoder.size_head,
                   policy.teacher_forecast.reference_held, policy.action_gru):
        assert any(p.grad is not None and p.grad.abs().sum() > 0 for p in module.parameters())
    optimizer = torch.optim.Adam(parameters, lr=.01)
    before = {k: v.clone() for k, v in policy.encoder.state_dict().items()}
    optimizer.step()
    assert all(torch.equal(before[k], v) for k, v in policy.encoder.state_dict().items())


@pytest.mark.parametrize('case', ['duplicate', 'missing', 'context_grad', 'invalid_ratio', 'invalid_future'])
def test_fail_closed_targets(case):
    policy, packet = fixture()
    x, actions, quality, ratio, forecasts, weights = packet
    if case == 'duplicate': forecasts[0][0][0] = 1
    if case == 'missing': forecasts = []
    if case == 'context_grad': x.requires_grad_()
    if case == 'invalid_ratio': ratio[1] = .5
    if case == 'invalid_future': forecasts[0][2][0, 0, :] = 0
    with pytest.raises(ValueError): objective(policy, x, actions, quality, ratio, forecasts, weights)

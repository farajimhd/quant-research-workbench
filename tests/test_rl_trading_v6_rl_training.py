import pytest
import torch
from research.rl_trading.v6.rl_training import PolicyRollout, update_on_policy


def test_real_optimizer_path_and_stale_likelihood_rejected():
    model = torch.nn.Linear(1, 2)
    x = torch.tensor([[1.], [2.]])
    def reconstruct(m):
        result = m(x)
        return result[:, 0], result[:, 1]
    old, values = reconstruct(model)
    rollout = PolicyRollout(old.detach(), values.detach(), torch.tensor([.1, .2]),
        torch.tensor([False, True]), torch.tensor([1., 1.]), torch.tensor(0.))
    initial = model.weight.detach().clone()
    metrics = update_on_policy(model, torch.optim.Adam(model.parameters(), lr=1e-4),
                               rollout, reconstruct, epochs=2)
    assert metrics['update_epochs'] == 2
    assert not torch.equal(initial, model.weight)
    with pytest.raises(ValueError, match='likelihood differs'):
        update_on_policy(model, torch.optim.Adam(model.parameters()), rollout,
                         lambda m: (reconstruct(m)[0]+1, reconstruct(m)[1]))

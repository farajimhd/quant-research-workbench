import torch
from research.rl_trading.v6.actor_critic import (
    BracketActorCritic, elapsed_gae, clipped_ppo_loss)


def observation(model):
    return (torch.randn(3, 8), torch.tensor([10000., 10000., 0., 0., 0., 0., 0.]),
            torch.tensor([1]), torch.zeros(1, 9),
            model.initial_action_state(device=torch.device('cpu'), dtype=torch.float32))


def test_hybrid_sampling_masks_and_recomputed_likelihood():
    model = BracketActorCritic(8)
    dist, value = model.distribution_and_value(*observation(model),
        enter_allowed=torch.tensor([True, False, True]), exit_allowed=torch.tensor([True]),
        stop_allowed=torch.tensor([False]), target_allowed=torch.tensor([True]))
    for _ in range(30):
        token, latent, parameter, likelihood = dist.sample()
        assert token not in (2, 5)
        assert torch.equal(likelihood, dist.log_prob(token, latent))
        if dist.parameter_kind(token) == 1:
            assert 0 < parameter < 1
        elif dist.parameter_kind(token) == 2:
            assert parameter > 0
    assert value.ndim == 0


def test_critic_cannot_update_actor_and_ppo_updates_both_heads():
    model = BracketActorCritic(8)
    dist, value = model.distribution_and_value(*observation(model),
        enter_allowed=torch.ones(3, dtype=torch.bool), exit_allowed=torch.ones(1, dtype=torch.bool),
        stop_allowed=torch.ones(1, dtype=torch.bool), target_allowed=torch.ones(1, dtype=torch.bool))
    value.backward(retain_graph=True)
    assert model.decoder.enter_head.weight.grad is None
    model.zero_grad()
    lp = dist.log_prob(1, torch.tensor(.3))
    loss, _ = clipped_ppo_loss(lp[None], lp.detach()[None], value[None],
                              torch.tensor([2.]), torch.tensor([1.]))
    loss.backward()
    assert model.decoder.enter_head.weight.grad.abs().sum() > 0
    assert model.critic[-1].weight.grad.abs().sum() > 0


def test_elapsed_time_terminal_and_same_clock_orders():
    adv, returns = elapsed_gae(torch.tensor([1., 2., 3.]), torch.zeros(3),
        torch.tensor([False, True, False]), torch.tensor([0., 1., 2.]),
        bootstrap=torch.tensor(4.), gamma=.5, trace_decay=1.)
    assert torch.equal(adv, torch.tensor([3., 2., 4.]))
    assert torch.equal(returns, adv)


def test_future_candles_have_zero_prefix_gradient_and_no_prediction_effect():
    model = BracketActorCritic(8)
    scalar = torch.randn(140, 37, requires_grad=True)
    levels = torch.randn(140, 2, 5, 11, requires_grad=True)
    encoded = model.encoder.encode_listing(scalar, levels)
    encoded[:125].square().sum().backward()
    assert torch.count_nonzero(scalar.grad[125:]) == 0
    assert torch.count_nonzero(levels.grad[125:]) == 0
    changed = scalar.detach().clone()
    changed[125:] = 999
    assert torch.equal(encoded[:125].detach(),
                       model.encoder.encode_listing(changed, levels.detach())[:125])

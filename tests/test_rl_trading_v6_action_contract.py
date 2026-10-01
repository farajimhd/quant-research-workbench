import pytest

from research.rl_trading.v6.action_contract import ActionAxes, ACTION_NAMES


def test_wait_and_hold_have_distinct_identity_axes_without_order_offset_changes():
    axes = ActionAxes(1000, 2)
    assert axes.width == 1009
    assert ACTION_NAMES[axes.action_class(0)] == 'wait'
    for slot in range(2):
        hold = axes.hold_base + slot
        assert ACTION_NAMES[axes.action_class(hold)] == 'hold'
        assert axes.held_slot(hold) == slot
        assert axes.parameter_kind(hold) == 0
        assert axes.execution_action(hold) == 0
        for action in range(2, 5):
            token = 1001 + (action - 2) * 2 + slot
            assert axes.action_class(token) == action
            assert axes.execution_action(token) == action
            assert axes.held_slot(token) == slot


def test_flat_account_has_wait_and_entries_but_no_hold_tokens():
    axes = ActionAxes(3, 0)
    assert axes.width == 4
    assert [axes.action_class(t) for t in range(4)] == [0, 1, 1, 1]
    with pytest.raises(ValueError):
        axes.action_class(4)
    with pytest.raises(ValueError):
        axes.held_slot(0)


@pytest.mark.parametrize('n,h', [(0, 0), (2, 3), (2, -1), (True, 0)])
def test_invalid_axes_fail_closed(n, h):
    with pytest.raises(ValueError):
        ActionAxes(n, h)


def test_separate_heads_batched_gradients_and_hold_identity_likelihood():
    import copy
    import torch
    from research.rl_trading.v6.model import BracketActionDecoder
    from research.rl_trading.v6.actor_critic import HybridDistribution, tensor_batch_statistics
    from research.rl_trading.v6.objective import bracket_loss
    from research.rl_trading.v6.action_adapter import decode_proposal

    torch.manual_seed(91)
    single = BracketActionDecoder(8, wait_hold=True)
    batched = copy.deepcopy(single)
    listings = torch.randn(2, 3, 8)
    accounts = torch.ones(2, 7)
    identities = torch.tensor([[2, 0], [0, 1]])
    features = torch.randn(2, 2, 11)
    masks = dict(enter_allowed=torch.ones(2, 3, dtype=torch.bool),
                 exit_allowed=torch.zeros(2, 2, dtype=torch.bool),
                 stop_allowed=torch.zeros(2, 2, dtype=torch.bool),
                 target_allowed=torch.zeros(2, 2, dtype=torch.bool))
    result = batched.forward_batch(listings, accounts, identities, features, **masks)
    reference = [single(listings[i], accounts[i], identities[i], features[i],
                        **{k:v[i] for k,v in masks.items()}) for i in range(2)]
    for axis, value in enumerate(result):
        torch.testing.assert_close(value, torch.stack([r[axis] for r in reference]))
    axes = ActionAxes(3, 2)
    tokens = [0, axes.hold_base+1]
    losses = [bracket_loss(*(v[i] for v in result), token=tokens[i], wait_hold=True)[0]
              for i in range(2)]
    sum(losses).backward()
    sum(bracket_loss(*reference[i], token=tokens[i], wait_hold=True)[0]
        for i in range(2)).backward()
    for (_, p), (_, q) in zip(single.named_parameters(), batched.named_parameters()):
        if p.grad is not None:
            torch.testing.assert_close(p.grad, q.grad, atol=2e-6, rtol=2e-5)
    assert single.wait_head.weight.grad.abs().sum() > 0
    assert single.hold_head.weight.grad.abs().sum() > 0
    logits = result[0][0].detach().clone()
    logits.fill_(-100); logits[axes.hold_base+1] = 100
    proposal = decode_proposal(logits, *(v[0].detach() for v in result[1:]),
        held_index=identities[0], entry_prices=(10., 20.),
        held_tick_sizes=(.01, .01), wait_hold=True)
    assert (proposal.action, proposal.listing_index) == ('hold', 0)
    distribution = HybridDistribution(logits, torch.zeros(axes.width),
        torch.ones(axes.width), 3, 2)
    token = axes.hold_base+1
    assert distribution.parameter_kind(token) == 0
    a = distribution.log_prob(token, torch.tensor(50.))
    b = distribution.tensor_log_prob(torch.tensor(token), torch.tensor(-50.))
    torch.testing.assert_close(a, b)
    c, _, _ = tensor_batch_statistics([(distribution, torch.tensor(0.))],
        torch.tensor([token]), torch.tensor([123.]))
    torch.testing.assert_close(c[0], a)


def test_wait_hold_actor_flat_account_has_no_hold_slots():
    import torch
    from research.rl_trading.v6.actor_critic import BracketActorCritic
    model = BracketActorCritic(8, wait_hold=True)
    state = model.initial_action_state(device=torch.device('cpu'), dtype=torch.float32)
    dist, _ = model.distribution_and_value(torch.randn(3, 8), torch.ones(7),
        torch.empty(0, dtype=torch.long), torch.empty(0, 11), state,
        enter_allowed=torch.ones(3, dtype=torch.bool),
        **{name:torch.empty(0, dtype=torch.bool) for name in
           ('exit_allowed', 'stop_allowed', 'target_allowed')})
    assert dist.logits.shape == dist.locations.shape == dist.scales.shape == (4,)

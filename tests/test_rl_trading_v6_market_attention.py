import numpy as np
import pytest
import torch
from research.rl_trading.v6.market_attention import MarketAttentionConfig, VolumeRanker
from research.rl_trading.v6.market_attention import RankedMarketAttention
from research.rl_trading.v6.ranked_policy import RankedBracketActorCritic
from research.rl_trading.v6.candle_stream import SparseCandleState
from research.rl_trading.v6.features import SCALAR_NAMES


def rows(volumes):
    result = np.zeros((len(volumes), 37), dtype=np.float32)
    result[:, SCALAR_NAMES.index('log_volume')] = np.log1p(volumes)
    return result


def test_clock_window_gaps_refresh_and_mandatory_identities():
    ranker = VolumeRanker(4, MarketAttentionConfig(top_r=1, sort_secs=2))
    ranker.observe(1_000_000, [0, 1], rows([100, 5]))
    assert ranker.select(1_000_000).tolist() == [0]
    ranker.observe(2_000_000, [1], rows([500]))
    assert ranker.select(2_000_000, held=[3], pending=[2]).tolist() == [0, 2, 3]
    assert ranker.select(3_000_000).tolist() == [1]
    ranker.observe(17_000_000, [2], rows([1]))
    assert ranker.select(17_000_000).tolist() == [2]
    with pytest.raises(ValueError):
        ranker.observe(16_000_000, [0], rows([1]))


def test_ranked_actor_critic_full_identity_mask_and_attention_gradients():
    torch.manual_seed(3)
    policy = RankedBracketActorCritic(8, config=MarketAttentionConfig(top_r=1, heads=2))
    policy.reset_market(3)
    state = SparseCandleState.empty(policy.encoder, 3, device=torch.device('cpu'), dtype=torch.float32)
    scalar = torch.from_numpy(rows([1, 10, 5]))
    state.advance(policy.encoder, torch.arange(3), scalar, torch.zeros(3, 2, 5, 11))
    policy.observe_market(state, 1_000_000, np.arange(3), scalar.numpy())
    policy.set_pending([2])
    args = (state.encoded, torch.tensor([10000.,10000.,0.,0.,0.,0.,0.]),
            torch.tensor([0]), torch.zeros(1,9),
            policy.initial_action_state(device=torch.device('cpu'),dtype=torch.float32))
    masks = dict(enter_allowed=torch.ones(3,dtype=torch.bool),
        exit_allowed=torch.ones(1,dtype=torch.bool), stop_allowed=torch.ones(1,dtype=torch.bool),
        target_allowed=torch.ones(1,dtype=torch.bool))
    dist, value = policy.distribution_and_value(*args, **masks)
    assert dist.logits.shape == (7,)
    dist.log_prob(2, torch.tensor(.1)).backward()
    assert policy.market_attention.temporal.in_proj_weight.grad.abs().sum() > 0
    assert policy.market_attention.market.in_proj_weight.grad.abs().sum() > 0
    assert policy.market_attention.broadcast.in_proj_weight.grad.abs().sum() > 0
    # Remove mandatory identities: only volume leader listing1 may enter.
    policy.set_pending([])
    args = (*args[:2], torch.empty(0,dtype=torch.long), torch.empty(0,9), args[-1])
    masks.update(exit_allowed=torch.zeros(0,dtype=torch.bool),
        stop_allowed=torch.zeros(0,dtype=torch.bool),target_allowed=torch.zeros(0,dtype=torch.bool))
    dist, _ = policy.distribution_and_value(*args, **masks)
    assert dist.logits[1] == torch.finfo(torch.float32).min
    assert dist.logits[3] == torch.finfo(torch.float32).min


def test_same_clock_market_cache_reuses_attention_and_accumulates_gradients():
    torch.manual_seed(41)
    policy = RankedBracketActorCritic(8, config=MarketAttentionConfig(top_r=2, heads=2))
    policy.reset_market(2)
    state = SparseCandleState.empty(policy.encoder, 2, device=torch.device('cpu'), dtype=torch.float32)
    scalar = torch.from_numpy(rows([2, 5]))
    state.advance(policy.encoder, torch.arange(2), scalar, torch.zeros(2, 2, 5, 11))
    policy.observe_market(state, 1_000_000, np.arange(2), scalar.numpy())
    args = (state.encoded, torch.tensor([10000., 10000., 0., 0., 0., 0., 0.]),
            torch.empty(0, dtype=torch.long), torch.empty(0, 9),
            policy.initial_action_state(device=torch.device('cpu'), dtype=torch.float32))
    masks = dict(enter_allowed=torch.ones(2, dtype=torch.bool),
                 exit_allowed=torch.zeros(0, dtype=torch.bool),
                 stop_allowed=torch.zeros(0, dtype=torch.bool),
                 target_allowed=torch.zeros(0, dtype=torch.bool))
    calls = []
    hook = policy.market_attention.register_forward_hook(lambda *unused: calls.append(1))
    first, _ = policy.distribution_and_value(*args, **masks)
    second, _ = policy.distribution_and_value(*args, **masks)
    assert len(calls) == 1
    torch.testing.assert_close(first.logits, second.logits)
    weight = policy.market_attention.temporal.in_proj_weight
    loss = first.log_prob(1, torch.tensor(.2))
    expected = torch.autograd.grad(loss, weight, retain_graph=True)[0] * 2
    (loss + second.log_prob(1, torch.tensor(.2))).backward()
    torch.testing.assert_close(weight.grad, expected)
    policy.observe_market(state, 2_000_000, np.arange(2), scalar.numpy())
    policy.distribution_and_value(*args, **masks)
    assert len(calls) == 2
    hook.remove()


def test_attention_masks_padding_and_is_equivariant_to_listing_permutation():
    torch.manual_seed(11)
    attention = RankedMarketAttention(8, MarketAttentionConfig(heads=2))
    history = torch.randn(3, 120, 8, requires_grad=True)
    counts = torch.tensor([3, 7, 120])
    embeddings = torch.randn(3, 8)
    selected = torch.tensor([0, 1])
    original = attention(history, counts, embeddings, selected)
    original.square().sum().backward()
    assert torch.count_nonzero(history.grad[0, :117]) == 0
    changed = history.detach().clone()
    changed[0, :117] = 999
    assert torch.equal(original.detach(), attention(changed, counts, embeddings, selected))
    permutation = torch.tensor([1, 2, 0])
    permuted = attention(history.detach()[permutation], counts[permutation],
                        embeddings[permutation], torch.tensor([2, 0]))
    assert torch.allclose(original, permuted, atol=1e-6)


def test_selection_does_not_change_history_or_use_future_events():
    config = MarketAttentionConfig(top_r=1, sort_secs=1)
    ranker = VolumeRanker(2, config)
    ranker.observe(1_000_000, [0, 1], rows([1, 10]))
    prefix = ranker.select(1_000_000).copy()
    ranker.observe(2_000_000, [0], rows([1000]))
    assert prefix.tolist() == [1]
    assert ranker.select(2_000_000).tolist() == [0]
    with pytest.raises(ValueError):
        ranker.select(1_000_000)

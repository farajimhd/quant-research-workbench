import torch
import pytest
from copy import deepcopy

from research.rl_trading.v6.candle_stream import SparseCandleState
from research.rl_trading.v6.model import ActualCandleEncoder


@pytest.mark.parametrize('indices', [[0, 0], [-1, 0], [0, 3]])
def test_invalid_identity_axis_rejected_before_state_mutation(indices):
    encoder = ActualCandleEncoder(width=8)
    state = SparseCandleState.empty(encoder, 3, device=torch.device('cpu'), dtype=torch.float32)
    with pytest.raises(ValueError, match='listing axis'):
        state.advance(encoder, torch.tensor(indices), torch.zeros(2, 37),
                      torch.zeros(2, 2, 5, 11))
    assert not state._updates
    assert not state.history.any() and not state.seen.any()


def test_sparse_training_state_matches_actual_candle_encoder_and_detaches():
    torch.manual_seed(23)
    encoder = ActualCandleEncoder(width=8)
    state = SparseCandleState.empty(encoder, 2, device=torch.device('cpu'),
                                   dtype=torch.float32)
    scalar = torch.randn(5, 37)
    levels = torch.randn(5, 2, 5, 11)
    listing = [0, 1, 0, 1, 0]
    for row, identity in enumerate(listing):
        state.advance(encoder, torch.tensor([identity], dtype=torch.long),
                      scalar[row:row+1], levels[row:row+1])
        history_rows = [i for i, value in enumerate(listing[:row+1])
                        if value == identity]
        expected = encoder.encode_listing(scalar[history_rows],
                                           levels[history_rows])[-1]
        assert torch.allclose(state.embeddings()[identity], expected,
                              atol=1e-5)
    state.embeddings()[0].sum().backward()
    assert encoder.project.weight.grad is not None
    state.detach()
    assert not state.embeddings().requires_grad


def test_batched_sparse_updates_preserve_independent_listing_histories():
    torch.manual_seed(29)
    encoder = ActualCandleEncoder(width=8)
    state = SparseCandleState.empty(encoder, 3, device=torch.device('cpu'),
                                   dtype=torch.float32)
    base_pointer = state.history.data_ptr()
    scalar = torch.randn(5, 37)
    levels = torch.randn(5, 2, 5, 11)
    state.advance(encoder, torch.tensor([0, 2]), scalar[:2], levels[:2])
    state.advance(encoder, torch.tensor([1, 2]), scalar[2:4], levels[2:4])
    state.advance(encoder, torch.tensor([0]), scalar[4:], levels[4:])
    for listing, rows in ((0, [0, 4]), (1, [2]), (2, [1, 3])):
        expected = encoder.encode_listing(scalar[rows], levels[rows])[-1]
        assert torch.allclose(state.embeddings()[listing], expected, atol=1e-5)
    state.embeddings().sum().backward()
    assert encoder.lag.grad is not None
    assert state.history.data_ptr() == base_pointer


def test_grouped_history_gather_preserves_projection_gradients_with_reranking():
    torch.manual_seed(71)
    encoder = ActualCandleEncoder(width=8)
    reference = deepcopy(encoder)
    state = SparseCandleState.empty(encoder, 3, device=torch.device('cpu'),
                                   dtype=torch.float32)
    base_pointer = state.history.data_ptr()
    scalar, levels = torch.randn(4, 37), torch.randn(4, 2, 5, 11)
    state.advance(encoder, torch.tensor([0, 2]), scalar[:2], levels[:2])
    state.advance(encoder, torch.tensor([1, 2]), scalar[2:], levels[2:])
    selected = torch.tensor([2, 0, 1])
    actual = state.history_for(selected)
    projected = reference.project(reference._input(scalar, levels))
    expected = torch.stack([
        torch.cat((projected.new_zeros(118, 8), projected[[1, 3]])),
        torch.cat((projected.new_zeros(119, 8), projected[[0]])),
        torch.cat((projected.new_zeros(119, 8), projected[[2]]))])
    torch.testing.assert_close(actual, expected)
    actual.square().sum().backward()
    expected.square().sum().backward()
    torch.testing.assert_close(encoder.project.weight.grad,
                               reference.project.weight.grad)
    assert state.history.grad_fn is None
    assert state._updates
    state.detach()
    assert not state._updates
    assert state.history.data_ptr() == base_pointer

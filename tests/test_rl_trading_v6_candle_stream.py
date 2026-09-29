import torch

from research.rl_trading.v6.candle_stream import SparseCandleState
from research.rl_trading.v6.model import ActualCandleEncoder


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
    assert not state.changed_history and not state.changed_encoded
    assert not state.embeddings().requires_grad

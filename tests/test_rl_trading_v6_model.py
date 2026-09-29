import torch

from research.rl_trading.v6.model import ActualCandleEncoder


def test_actual_candle_training_serving_parity_with_clock_gaps():
    torch.manual_seed(17)
    model = ActualCandleEncoder(width=8).eval()
    scalar = torch.randn(5, 37)
    levels = torch.randn(5, 2, 5, 11)
    # Listing 0 has three persisted candles; listing 1 has two. Sparse
    # observations at intervening clock seconds leave each history unchanged.
    a = model.encode_listing(scalar[[0, 2, 4]], levels[[0, 2, 4]])
    b = model.encode_listing(scalar[[1, 3]], levels[[1, 3]])
    state = model.initial_state(2, device=torch.device('cpu'),
                                dtype=torch.float32)
    expected = [a[0], b[0], a[1], b[1], a[2]]
    for row, listing in enumerate([0, 1, 0, 1, 0]):
        previous_other = state.encoded[1-listing].clone()
        state = model.observe(state, torch.tensor([listing]),
                              scalar[row:row+1], levels[row:row+1])
        assert torch.allclose(state.encoded[listing], expected[row], atol=1e-5)
        assert torch.equal(state.encoded[1-listing], previous_other)
    assert state.seen.tolist() == [3, 2]


def test_causal_sequence_ignores_future_candle():
    torch.manual_seed(4)
    model = ActualCandleEncoder(width=4).eval()
    scalar = torch.randn(3, 37)
    levels = torch.randn(3, 2, 5, 11)
    before = model.encode_listing(scalar, levels)
    scalar[-1] *= 100
    after = model.encode_listing(scalar, levels)
    assert torch.equal(before[:2], after[:2])
